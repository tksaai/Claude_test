import asyncio
import random
import time
from dataclasses import dataclass
from typing import Callable

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6667


@dataclass
class ChatMessage:
    ts: float
    user: str
    text: str
    emote_count: int


def parse_privmsg(line: str) -> ChatMessage | None:
    """IRCv3タグ付きPRIVMSG行をパースする。対象外の行はNone。"""
    tags = {}
    rest = line
    if line.startswith("@"):
        tag_part, _, rest = line[1:].partition(" ")
        for kv in tag_part.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = v
    if " PRIVMSG #" not in rest:
        return None
    prefix, _, tail = rest.lstrip(":").partition(" PRIVMSG #")
    user = tags.get("display-name") or prefix.split("!", 1)[0]
    _, _, text = tail.partition(" :")

    emote_count = 0
    emotes_tag = tags.get("emotes", "")
    if emotes_tag:
        for entry in emotes_tag.split("/"):
            _, _, positions = entry.partition(":")
            if positions:
                emote_count += len(positions.split(","))

    return ChatMessage(ts=time.time(), user=user, text=text.rstrip("\r\n"), emote_count=emote_count)


class ChatReader:
    """匿名(justinfan)でTwitch IRCに接続し、チャットをコールバックに流す。

    認証トークン不要・無料。切断時は自動再接続する。
    """

    def __init__(self, channel: str, on_message: Callable[[ChatMessage], None]):
        self.channel = channel.lower()
        self.on_message = on_message
        self._stopped = asyncio.Event()

    def stop(self):
        self._stopped.set()

    async def run(self):
        backoff = 1
        while not self._stopped.is_set():
            try:
                await self._connect_and_read()
                backoff = 1
            except (OSError, asyncio.IncompleteReadError, ConnectionError):
                if self._stopped.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _connect_and_read(self):
        reader, writer = await asyncio.open_connection(IRC_HOST, IRC_PORT)
        try:
            nick = f"justinfan{random.randint(10000, 99999)}"
            writer.write(
                (
                    "CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"
                    f"NICK {nick}\r\n"
                    f"JOIN #{self.channel}\r\n"
                ).encode()
            )
            await writer.drain()

            while not self._stopped.is_set():
                raw = await asyncio.wait_for(reader.readline(), timeout=360)
                if not raw:
                    raise ConnectionError("IRC connection closed")
                line = raw.decode("utf-8", errors="replace")
                if line.startswith("PING"):
                    writer.write(b"PONG :tmi.twitch.tv\r\n")
                    await writer.drain()
                    continue
                msg = parse_privmsg(line)
                if msg:
                    self.on_message(msg)
        finally:
            writer.close()
