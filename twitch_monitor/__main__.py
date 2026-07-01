import argparse
import asyncio
import logging

from .config import load_config
from .monitor import run_monitor


def main():
    parser = argparse.ArgumentParser(
        prog="twitch_monitor",
        description="Twitch配信を検知し、章立てタイムラインと切り抜き候補ランキングを生成する(従量課金なし)",
    )
    parser.add_argument("--config", default=None, help="設定ファイル (config.yaml)")
    parser.add_argument(
        "--channel", action="append", default=None,
        help="監視するチャンネル(複数指定可、configのchannelsより優先)",
    )
    parser.add_argument("--verbose", action="store_true", help="デバッグログを出す")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = load_config(args.config, args.channel)
    try:
        asyncio.run(run_monitor(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
