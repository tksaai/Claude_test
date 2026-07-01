import re
from dataclasses import dataclass, field

# 日英両対応のシグナル判定(全てローカル正規表現、外部API不使用)
LAUGH_RE = re.compile(r"(草|ｗ{2,}|\bw{3,}\b|笑|ワロタ|くさ|LUL|KEKW|OMEGALUL|\blol\b|\blmao\b)", re.IGNORECASE)
HYPE_RE = re.compile(
    r"(やば|すご|神|えぐ|うま|つよ|きた|ナイス|nice|pog|hype|let'?s\s?go|clutch|クラッチ|\bgg\b|うおお|おおお)",
    re.IGNORECASE,
)
CLIP_RE = re.compile(r"(クリップ|切り抜き|\bclip\b)", re.IGNORECASE)

SAMPLE_MESSAGES_PER_BUCKET = 3


@dataclass
class Bucket:
    start_offset: float
    messages: int = 0
    users: set = field(default_factory=set)
    emotes: int = 0
    laughs: int = 0
    hype: int = 0
    clip_requests: int = 0
    audio_excitement: float = 0.0  # 音声解析による「声の盛り上がり」ピーク(0〜2程度)
    spike: float = 0.0
    samples: list = field(default_factory=list)


@dataclass
class Highlight:
    start_offset: float
    end_offset: float
    score: float
    category: str
    messages: int
    unique_users: int
    laughs: int
    hype: int
    clip_requests: int
    emotes: int
    peak_spike: float
    audio_peak: float
    samples: list


class HighlightEngine:
    """チャットと配信音声をバケット集計し、切り抜き候補をスコアリングする。

    チャット: コメント速度のスパイク(平常時EMA比) + 笑い/盛り上がり/クリップ要望シグナル。
    音声: AudioAnalyzerが算出した「声の盛り上がり」(BGM/ゲーム音はベースラインに吸収済み)。
    両者は同じ時間バケットに合流し、複合スコアでランク付けされる。
    """

    def __init__(
        self,
        bucket_seconds: int = 10,
        baseline_alpha: float = 0.05,
        spike_threshold: float = 2.5,
        min_baseline: float = 3.0,
        min_messages: int = 5,
        audio_threshold: float = 0.8,
        audio_weight: float = 1.5,
    ):
        self.bucket_seconds = bucket_seconds
        self.baseline_alpha = baseline_alpha
        self.spike_threshold = spike_threshold
        self.min_baseline = min_baseline
        self.min_messages = min_messages
        self.audio_threshold = audio_threshold
        self.audio_weight = audio_weight
        self._buckets: dict[float, Bucket] = {}

    def _bucket(self, offset: float) -> Bucket:
        start = (offset // self.bucket_seconds) * self.bucket_seconds
        bucket = self._buckets.get(start)
        if bucket is None:
            bucket = Bucket(start_offset=start)
            self._buckets[start] = bucket
        return bucket

    def add_message(self, user: str, text: str, emote_count: int, offset: float):
        b = self._bucket(offset)
        b.messages += 1
        b.users.add(user)
        b.emotes += emote_count
        if LAUGH_RE.search(text):
            b.laughs += 1
        if HYPE_RE.search(text):
            b.hype += 1
        if CLIP_RE.search(text):
            b.clip_requests += 1
        if len(b.samples) < SAMPLE_MESSAGES_PER_BUCKET:
            b.samples.append(f"{user}: {text[:60]}")

    def add_audio(self, offset: float, excitement: float):
        b = self._bucket(offset)
        b.audio_excitement = max(b.audio_excitement, excitement)

    def _sorted_with_spikes(self) -> list[Bucket]:
        """時系列順のバケットにEMAベースライン比のスパイク倍率を付与して返す。"""
        buckets = [self._buckets[k] for k in sorted(self._buckets)]
        baseline: float | None = None
        for b in buckets:
            base = max(baseline if baseline is not None else float(b.messages), self.min_baseline)
            b.spike = b.messages / base
            # スパイク中のバケットでベースラインを吊り上げないよう、上限つきでEMA更新
            sample = min(float(b.messages), base * 2)
            baseline = sample if baseline is None else (1 - self.baseline_alpha) * baseline + self.baseline_alpha * sample
        return buckets

    def _is_hot(self, b: Bucket) -> bool:
        chat_hot = b.spike >= self.spike_threshold and b.messages >= self.min_messages
        audio_hot = b.audio_excitement >= self.audio_threshold
        return chat_hot or audio_hot

    def ranked_highlights(self, top_n: int = 20) -> list[Highlight]:
        """盛り上がった連続バケットを1つの候補ウィンドウにまとめ、スコア順で返す。"""
        buckets = self._sorted_with_spikes()
        windows: list[list[Bucket]] = []
        current: list[Bucket] = []
        for b in buckets:
            if self._is_hot(b):
                # 1バケット分の谷間までは同じ盛り上がりとして連結する
                if current and b.start_offset - current[-1].start_offset > self.bucket_seconds * 2:
                    windows.append(current)
                    current = []
                current.append(b)
        if current:
            windows.append(current)

        highlights = [self._score_window(w) for w in windows]
        highlights.sort(key=lambda h: h.score, reverse=True)
        return highlights[:top_n]

    def _score_window(self, window: list[Bucket]) -> Highlight:
        messages = sum(b.messages for b in window)
        users = set()
        for b in window:
            users.update(b.users)
        laughs = sum(b.laughs for b in window)
        hype = sum(b.hype for b in window)
        clips = sum(b.clip_requests for b in window)
        emotes = sum(b.emotes for b in window)
        peak_spike = max(b.spike for b in window)
        audio_peak = max(b.audio_excitement for b in window)

        laugh_ratio = laughs / messages if messages else 0.0
        hype_ratio = hype / messages if messages else 0.0
        clip_ratio = clips / messages if messages else 0.0
        emote_ratio = min(emotes / messages, 3.0) if messages else 0.0

        score = (
            peak_spike
            + 2.0 * laugh_ratio
            + 2.0 * hype_ratio
            + 4.0 * clip_ratio
            + 0.5 * emote_ratio
            + 0.02 * len(users)
            + self.audio_weight * audio_peak
        )

        category = "チャット急増"
        best = 0.05  # シグナル比率がこの閾値以下なら単なる急増扱い
        for ratio, label in (
            (laugh_ratio, "笑い"),
            (hype_ratio, "盛り上がり"),
            (clip_ratio, "クリップ希望"),
        ):
            if ratio > best:
                best = ratio
                category = label
        # チャットのシグナルが弱く、音声主導で検知したウィンドウは音声カテゴリにする
        if category == "チャット急増" and audio_peak >= self.audio_threshold and peak_spike < self.spike_threshold:
            category = "音声の盛り上がり"

        samples = []
        for b in sorted(window, key=lambda b: b.spike, reverse=True):
            samples.extend(b.samples)
        return Highlight(
            start_offset=window[0].start_offset,
            end_offset=window[-1].start_offset + self.bucket_seconds,
            score=round(score, 2),
            category=category,
            messages=messages,
            unique_users=len(users),
            laughs=laughs,
            hype=hype,
            clip_requests=clips,
            emotes=emotes,
            peak_spike=round(peak_spike, 2),
            audio_peak=round(audio_peak, 2),
            samples=samples[:5],
        )
