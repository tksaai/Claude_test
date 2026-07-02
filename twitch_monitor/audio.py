import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

log = logging.getLogger("twitch_monitor")

SAMPLE_RATE = 16000
FRAME_SECONDS = 1.0
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_SECONDS)
VOICE_BAND = (300.0, 3400.0)   # 人声(叫び/笑い声)の主要帯域
TOTAL_BAND = (50.0, 8000.0)

CHANNEL_RE = re.compile(r"^[a-z0-9_]{1,25}$")
VOD_ID_RE = re.compile(r"^\d+$")


@dataclass
class AudioFrame:
    offset: float       # 配信開始からの秒数
    rms_db: float
    voice_ratio: float  # 全帯域エネルギーに占める人声帯域の割合
    excitement: float   # 0.0〜2.0程度の「声の盛り上がり」スコア


class AudioAnalyzer:
    """1秒フレームの音声から「声の盛り上がり」を推定する(全てローカル計算)。

    BGM/ゲーム音への対策:
    - 音量ベースラインをEMAで追従させ、定常的なBGM・ゲーム音は平常値として吸収する
    - ベースラインから突出した音量のうち、人声帯域が支配的なものを高スコアにする
    - 音量上昇が長く続く場合(BGM切替・戦闘シーン等)はスコアを減衰させ、
      瞬間的な絶叫・笑い声だけが残るようにする
    """

    def __init__(
        self,
        baseline_alpha: float = 0.08,
        baseline_max_step_db: float = 3.0,
        min_baseline_db: float = -50.0,
        start_delta_db: float = 3.0,
        full_delta_db: float = 9.0,
        sustain_frames: int = 15,
    ):
        self.baseline_alpha = baseline_alpha
        self.baseline_max_step_db = baseline_max_step_db
        self.min_baseline_db = min_baseline_db
        self.start_delta_db = start_delta_db
        self.full_delta_db = full_delta_db
        self.sustain_frames = sustain_frames
        self.baseline_db: float | None = None
        self._hot_streak = 0

    def process_frame(self, samples: np.ndarray, offset: float) -> AudioFrame:
        x = samples.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x)))
        rms_db = 20.0 * np.log10(rms + 1e-6)

        spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        freqs = np.fft.rfftfreq(len(x), 1.0 / SAMPLE_RATE)
        voice = float(spectrum[(freqs >= VOICE_BAND[0]) & (freqs < VOICE_BAND[1])].sum())
        total = float(spectrum[(freqs >= TOTAL_BAND[0]) & (freqs < TOTAL_BAND[1])].sum()) + 1e-9
        voice_ratio = voice / total

        if self.baseline_db is None:
            self.baseline_db = max(rms_db, self.min_baseline_db)
        base = max(self.baseline_db, self.min_baseline_db)
        delta = rms_db - base

        raw = (delta - self.start_delta_db) / (self.full_delta_db - self.start_delta_db)
        raw = min(max(raw, 0.0), 2.0)
        voice_factor = 0.3 + 0.7 * min(voice_ratio / 0.6, 1.0)
        excitement = raw * voice_factor

        # 音量上昇が続きすぎる場合は「場面の変化」とみなして減衰
        if raw > 0:
            self._hot_streak += 1
            if self._hot_streak > self.sustain_frames:
                excitement *= max(0.2, self.sustain_frames / self._hot_streak)
        else:
            self._hot_streak = 0

        # 絶叫でベースラインが釣り上がらないよう、1フレームの上昇幅を制限してEMA更新
        sample_db = min(rms_db, base + self.baseline_max_step_db)
        self.baseline_db = (1 - self.baseline_alpha) * base + self.baseline_alpha * sample_db

        return AudioFrame(offset=offset, rms_db=rms_db, voice_ratio=voice_ratio, excitement=excitement)


class AudioCapture:
    """streamlink + ffmpeg で配信音声をローカル取得しAudioAnalyzerに流す(無料)。

    streamlinkでHLSのURLを解決し、ffmpegで音声を16kHzモノラルPCMにデコードする。
    どちらも無料のOSSで、外部の課金APIは使わない。
    """

    def __init__(self, channel: str, start_epoch: float, on_frame: Callable[[AudioFrame], None]):
        if not CHANNEL_RE.match(channel):
            raise ValueError(f"不正なチャンネル名: {channel!r}")
        self.channel = channel
        self.start_epoch = start_epoch
        self.on_frame = on_frame
        self._stopped = asyncio.Event()
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("streamlink") is not None and shutil.which("ffmpeg") is not None

    def stop(self):
        self._stopped.set()
        if self._proc and self._proc.returncode is None:
            self._proc.kill()

    async def run(self):
        backoff = 5
        while not self._stopped.is_set():
            try:
                url = await self._resolve_stream_url()
                await self._read_pcm(url)
                backoff = 5
            except Exception as e:
                if self._stopped.is_set():
                    return
                log.warning("[%s] 音声取得エラー(%ss後に再接続): %s", self.channel, backoff, e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _resolve_stream_url(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            "streamlink", "--stream-url", f"twitch.tv/{self.channel}", "audio_only",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"streamlink失敗: {err.decode(errors='replace').strip()[:200]}")
        return out.decode().strip()

    async def _read_pcm(self, url: str):
        analyzer = AudioAnalyzer()
        self._proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-i", url,
            "-vn", "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # HLSのライブエッジには数秒〜十数秒の遅延があるため、オフセットは目安
        base_offset = time.time() - self.start_epoch
        frame_idx = 0
        try:
            while not self._stopped.is_set():
                data = await self._proc.stdout.readexactly(FRAME_SAMPLES * 2)
                samples = np.frombuffer(data, dtype=np.int16)
                frame = analyzer.process_frame(samples, base_offset + frame_idx * FRAME_SECONDS)
                self.on_frame(frame)
                frame_idx += 1
        except asyncio.IncompleteReadError:
            raise ConnectionError("音声ストリームが終了しました")
        finally:
            if self._proc.returncode is None:
                self._proc.kill()


async def analyze_vod_audio(vod_id: str, on_frame: Callable[[AudioFrame], None]) -> int:
    """アーカイブ(VOD)の音声を実時間より高速に解析する。

    VODはライブと違い先頭からのデコードなので、オフセットはフレーム番号から
    正確に求まる(HLSライブエッジの遅延ずれがない)。処理フレーム数(≒秒数)を返す。
    """
    if not VOD_ID_RE.match(vod_id):
        raise ValueError(f"不正なVOD ID: {vod_id!r}")
    proc = await asyncio.create_subprocess_exec(
        "streamlink", "--stream-url", f"https://www.twitch.tv/videos/{vod_id}", "audio_only",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"streamlink失敗: {err.decode(errors='replace').strip()[:200]}")
    url = out.decode().strip()

    analyzer = AudioAnalyzer()
    ffmpeg = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-i", url,
        "-vn", "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    frame_idx = 0
    try:
        while True:
            try:
                data = await ffmpeg.stdout.readexactly(FRAME_SAMPLES * 2)
            except asyncio.IncompleteReadError:
                break  # VOD末尾
            samples = np.frombuffer(data, dtype=np.int16)
            on_frame(analyzer.process_frame(samples, frame_idx * FRAME_SECONDS))
            frame_idx += 1
    finally:
        if ffmpeg.returncode is None:
            ffmpeg.kill()
        await ffmpeg.wait()
    return frame_idx
