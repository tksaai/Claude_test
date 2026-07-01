import os
from dataclasses import dataclass, field

import yaml


@dataclass
class Config:
    channels: list
    client_id: str
    client_secret: str
    poll_interval_seconds: int = 60
    bucket_seconds: int = 10
    top_highlights: int = 20
    snapshot_interval_seconds: int = 60
    output_dir: str = "out"


def load_config(path: str | None, channel_overrides: list[str] | None = None) -> Config:
    data = {}
    if path:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    channels = channel_overrides or data.get("channels") or []
    channels = [c.lower().lstrip("#") for c in channels]
    if not channels:
        raise SystemExit("監視対象チャンネルが未指定です (config の channels か --channel)")

    client_id = os.environ.get("TWITCH_CLIENT_ID") or data.get("client_id")
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET") or data.get("client_secret")
    if not client_id or not client_secret:
        raise SystemExit(
            "TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET が未設定です。"
            " https://dev.twitch.tv/console/apps で無料のアプリ登録をしてください。"
        )

    return Config(
        channels=channels,
        client_id=client_id,
        client_secret=client_secret,
        poll_interval_seconds=int(data.get("poll_interval_seconds", 60)),
        bucket_seconds=int(data.get("bucket_seconds", 10)),
        top_highlights=int(data.get("top_highlights", 20)),
        snapshot_interval_seconds=int(data.get("snapshot_interval_seconds", 60)),
        output_dir=data.get("output_dir", "out"),
    )
