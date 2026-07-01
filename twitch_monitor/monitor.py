import asyncio
import datetime
import logging
import os
import time

from .chat import ChatMessage, ChatReader
from .chapters import ChapterTracker
from .config import Config
from .highlights import HighlightEngine
from .output import format_offset, write_snapshot
from .twitch_api import HelixClient

log = logging.getLogger("twitch_monitor")


def _parse_started_at(iso: str) -> float:
    dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.timestamp()


class StreamSession:
    """ライブ中の1配信を追跡する: チャット集計・章立て・スナップショット出力。"""

    def __init__(self, config: Config, stream: dict):
        self.config = config
        self.login = stream["user_login"]
        self.stream_id = stream["id"]
        self.user_name = stream.get("user_name", self.login)
        self.started_at_iso = stream["started_at"]
        self.start_epoch = _parse_started_at(stream["started_at"])

        self.chapters = ChapterTracker()
        self.engine = HighlightEngine(bucket_seconds=config.bucket_seconds)
        self.chat = ChatReader(self.login, self._on_message)

        date = datetime.datetime.fromtimestamp(self.start_epoch).strftime("%Y%m%d")
        self.out_dir = os.path.join(config.output_dir, self.login, f"{date}_{self.stream_id}")

        self.update_channel_info(stream)
        self._tasks: list[asyncio.Task] = []

    def start(self):
        self._tasks = [
            asyncio.create_task(self.chat.run(), name=f"chat:{self.login}"),
            asyncio.create_task(self._snapshot_loop(), name=f"snapshot:{self.login}"),
        ]
        log.info("[%s] 配信を検知、追跡開始 (stream_id=%s)", self.login, self.stream_id)

    def _on_message(self, msg: ChatMessage):
        self.engine.add_message(msg.user, msg.text, msg.emote_count, msg.ts - self.start_epoch)

    def update_channel_info(self, stream: dict):
        offset = time.time() - self.start_epoch
        chapter = self.chapters.update(stream.get("title", ""), stream.get("game_name", ""), offset)
        if chapter:
            log.info(
                "[%s] 新しい章: %s / %s (%s)",
                self.login, chapter.title, chapter.game, format_offset(chapter.start_offset),
            )

    async def _snapshot_loop(self):
        while True:
            await asyncio.sleep(self.config.snapshot_interval_seconds)
            self.write_snapshot()

    def write_snapshot(self, final: bool = False):
        self.engine.flush()
        highlights = self.engine.ranked_highlights(self.config.top_highlights)
        meta = {
            "user_name": self.user_name,
            "login": self.login,
            "stream_id": self.stream_id,
            "started_at": self.started_at_iso,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "final": final,
        }
        write_snapshot(self.out_dir, meta, self.chapters.chapters, highlights)
        log.info(
            "[%s] スナップショット更新: 章=%d 切り抜き候補=%d -> %s",
            self.login, len(self.chapters.chapters), len(highlights), self.out_dir,
        )

    async def stop(self):
        self.chat.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.chapters.chapters:
            self.chapters.chapters[-1].end_offset = time.time() - self.start_epoch
        self.write_snapshot(final=True)
        log.info("[%s] 配信終了、最終レポートを書き出しました", self.login)


async def run_monitor(config: Config):
    api = HelixClient(config.client_id, config.client_secret)
    sessions: dict[str, StreamSession] = {}
    log.info("監視開始: %s (ポーリング間隔 %ds)", ", ".join(config.channels), config.poll_interval_seconds)
    try:
        while True:
            try:
                streams = await api.get_streams(config.channels)
            except Exception as e:  # ネットワーク一時障害などは次のポーリングで回復
                log.warning("配信状態の取得に失敗: %s", e)
                streams = None

            if streams is not None:
                live = {s["user_login"].lower(): s for s in streams}
                for login, stream in live.items():
                    if login in sessions and sessions[login].stream_id != stream["id"]:
                        await sessions.pop(login).stop()  # 同チャンネルの新配信
                    if login not in sessions:
                        sessions[login] = StreamSession(config, stream)
                        sessions[login].start()
                    else:
                        sessions[login].update_channel_info(stream)
                for login in [l for l in sessions if l not in live]:
                    await sessions.pop(login).stop()

            await asyncio.sleep(config.poll_interval_seconds)
    finally:
        for session in sessions.values():
            await session.stop()
        await api.close()
