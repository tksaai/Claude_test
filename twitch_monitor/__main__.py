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
    parser.add_argument(
        "--no-audio", action="store_true",
        help="配信音声の解析を無効化する(チャットのみで判定)",
    )
    parser.add_argument("--verbose", action="store_true", help="デバッグログを出す")

    sub = parser.add_subparsers(dest="cmd")
    verify_parser = sub.add_parser(
        "verify",
        help="配信終了後、アーカイブ(VOD)を元にライブ解析結果の整合性を検証する",
    )
    verify_parser.add_argument(
        "session_dir",
        help="検証するセッションディレクトリ (例: out/<channel>/<日付>_<配信ID>)",
    )
    verify_parser.add_argument("--vod", default=None, help="VOD ID(省略時はHelixで自動特定)")
    verify_parser.add_argument(
        "--no-audio", action="store_true", dest="verify_no_audio",
        help="VOD音声の再解析をスキップする(チャットリプレイのみで検証)",
    )
    verify_parser.add_argument(
        "--tolerance", type=float, default=45.0,
        help="照合時に同一とみなす時間ずれの上限秒数 (デフォルト: 45)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.cmd == "verify":
        from .verify import run_verify
        asyncio.run(
            run_verify(
                args.session_dir,
                vod_id=args.vod,
                audio_enabled=not args.verify_no_audio,
                tolerance=args.tolerance,
            )
        )
        return

    config = load_config(args.config, args.channel, audio_enabled_override=False if args.no_audio else None)
    try:
        asyncio.run(run_monitor(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
