"""配信終了後、アーカイブ(VOD)を元にライブ解析結果の整合性を検証する。

- VODのチャットリプレイ(chat-downloader、無料OSS)と音声を正確なVODタイムスタンプで再解析
- ライブ版リストと突き合わせて、ずれ推定・一致/ライブのみ/アーカイブのみを判定
- 補正済みのVODリンク付き検証レポートを出力

すべて無料(公開データ + ローカル計算)で、従量課金は発生しない。
"""

import asyncio
import datetime
import json
import logging
import os
import statistics
from dataclasses import asdict, dataclass

from .audio import AudioCapture, analyze_vod_audio
from .highlights import HighlightEngine
from .output import format_offset
from .twitch_api import HelixClient

log = logging.getLogger("twitch_monitor")

MATCH_SLACK_SECONDS = 15.0  # ドリフト補正後、この秒数以内の近接は同一ウィンドウ扱い

STATUS_LABELS = {
    "match": "一致",
    "live_only": "ライブのみ(要確認)",
    "archive_only": "アーカイブのみ(ライブで見逃し)",
}


def vod_timestamp_url(vod_id: str, seconds: float) -> str:
    s = max(int(seconds), 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"https://www.twitch.tv/videos/{vod_id}?t={h}h{m}m{sec}s"


@dataclass
class VerificationResult:
    drift: float          # ライブ時刻 + drift ≒ VOD時刻
    matched: int
    live_total: int
    archive_total: int
    items: list           # {"status", "live", "archive", "corrected_start"} の時系列リスト


def _closest_index(archive: list[dict], start: float, tolerance: float, used: set | None = None) -> int | None:
    best, best_d = None, None
    for i, a in enumerate(archive):
        if used is not None and i in used:
            continue
        d = abs(a["start_offset"] - start)
        if d <= tolerance and (best_d is None or d < best_d):
            best, best_d = i, d
    return best


def estimate_drift(live: list[dict], archive: list[dict], tolerance: float = 45.0) -> float:
    """ライブ時刻とVOD時刻の全体的なずれ(IRC遅延・VOD開始差)を近傍ペアの中央値で推定。"""
    diffs = []
    for l in live:
        i = _closest_index(archive, l["start_offset"], tolerance)
        if i is not None:
            diffs.append(archive[i]["start_offset"] - l["start_offset"])
    return statistics.median(diffs) if diffs else 0.0


def compare_highlights(live: list[dict], archive: list[dict], tolerance: float = 45.0) -> VerificationResult:
    drift = estimate_drift(live, archive, tolerance)
    used: set[int] = set()
    items = []
    for l in sorted(live, key=lambda x: x["start_offset"]):
        adj_start = l["start_offset"] + drift
        adj_end = l["end_offset"] + drift
        best, best_d = None, None
        for i, a in enumerate(archive):
            if i in used:
                continue
            overlaps = (
                adj_start - MATCH_SLACK_SECONDS <= a["end_offset"]
                and a["start_offset"] <= adj_end + MATCH_SLACK_SECONDS
            )
            if overlaps:
                d = abs(a["start_offset"] - adj_start)
                if best_d is None or d < best_d:
                    best, best_d = i, d
        if best is not None:
            used.add(best)
            items.append({
                "status": "match",
                "live": l,
                "archive": archive[best],
                "corrected_start": archive[best]["start_offset"],
            })
        else:
            items.append({
                "status": "live_only",
                "live": l,
                "archive": None,
                "corrected_start": adj_start,
            })
    for i, a in enumerate(archive):
        if i not in used:
            items.append({
                "status": "archive_only",
                "live": None,
                "archive": a,
                "corrected_start": a["start_offset"],
            })
    items.sort(key=lambda x: x["corrected_start"])
    matched = sum(1 for it in items if it["status"] == "match")
    return VerificationResult(
        drift=round(drift, 1),
        matched=matched,
        live_total=len(live),
        archive_total=len(archive),
        items=items,
    )


def _replay_chat_sync(vod_id: str, engine: HighlightEngine) -> int:
    try:
        from chat_downloader import ChatDownloader
    except ImportError:
        raise SystemExit("VODチャットの取得には chat-downloader が必要です: pip install chat-downloader")
    count = 0
    chat = ChatDownloader().get_chat(f"https://www.twitch.tv/videos/{vod_id}")
    for m in chat:
        offset = m.get("time_in_seconds")
        if offset is None or offset < 0:
            continue
        author = (m.get("author") or {})
        user = author.get("display_name") or author.get("name") or "?"
        text = m.get("message") or ""
        emotes = len(m.get("emotes") or [])
        engine.add_message(user, text, emotes, float(offset))
        count += 1
    return count


async def _find_vod_id(meta: dict) -> str:
    client_id = os.environ.get("TWITCH_CLIENT_ID")
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(
            "VODの自動特定には TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET が必要です"
            "(または --vod でVOD IDを直接指定してください)"
        )
    api = HelixClient(client_id, client_secret)
    try:
        users = await api.get_users([meta["login"]])
        if not users:
            raise SystemExit(f"チャンネルが見つかりません: {meta['login']}")
        videos = await api.get_videos(users[0]["id"])
        for v in videos:
            if v.get("stream_id") == meta["stream_id"]:
                return v["id"]
    finally:
        await api.close()
    raise SystemExit(
        "対象配信のアーカイブが見つかりませんでした。"
        "アーカイブが非公開/削除済みか、まだ処理中の可能性があります(--vod で直接指定も可能)"
    )


def _render_report(meta: dict, vod_id: str, result: VerificationResult, chapters: list[dict]) -> str:
    rate = f"{result.matched / result.live_total * 100:.0f}%" if result.live_total else "-"
    lines = [
        f"# アーカイブ検証レポート: {meta['user_name']}",
        "",
        f"- VOD: https://www.twitch.tv/videos/{vod_id}",
        f"- ストリームID: {meta['stream_id']} / 配信開始: {meta['started_at']}",
        f"- 推定タイムスタンプずれ(ライブ→VOD): {result.drift:+.1f}秒",
        f"- ライブ検知 {result.live_total}件 / アーカイブ再解析 {result.archive_total}件 /"
        f" 一致 {result.matched}件 (ライブ検知の一致率 {rate})",
        f"- 検証日時: {datetime.datetime.now().isoformat(timespec='seconds')}",
        "",
        "## ハイライト照合(時系列)",
        "",
        "| 判定 | ライブ時間 | 補正後時間 | タイプ | スコア(ライブ/アーカイブ) | VODリンク |",
        "|------|-----------|-----------|--------|--------------------------|-----------|",
    ]
    for it in result.items:
        live, archive = it["live"], it["archive"]
        live_time = format_offset(live["start_offset"]) if live else "-"
        corrected = format_offset(it["corrected_start"])
        src = archive or live
        category = src["category"]
        live_score = live["score"] if live else "-"
        archive_score = archive["score"] if archive else "-"
        url = vod_timestamp_url(vod_id, max(it["corrected_start"] - 10, 0))  # 少し手前から再生
        lines.append(
            f"| {STATUS_LABELS[it['status']]} | {live_time} | {corrected} | {category}"
            f" | {live_score} / {archive_score} | {url} |"
        )

    if chapters:
        lines += [
            "",
            "## 章タイムライン(補正済みVODリンク)",
            "",
            "| 開始(補正後) | 章タイトル | カテゴリ | VODリンク |",
            "|--------------|-----------|----------|-----------|",
        ]
        for c in chapters:
            start = c["start_offset"] + result.drift
            lines.append(
                f"| {format_offset(start)} | {c['title']} | {c['game']}"
                f" | {vod_timestamp_url(vod_id, start)} |"
            )
    lines.append("")
    return "\n".join(lines)


async def run_verify(
    session_dir: str,
    vod_id: str | None = None,
    audio_enabled: bool = True,
    tolerance: float = 45.0,
) -> VerificationResult:
    path = os.path.join(session_dir, "highlights.json")
    if not os.path.exists(path):
        raise SystemExit(f"セッションデータが見つかりません: {path}")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    meta = payload["meta"]
    live_highlights = payload["highlights"]
    chapters = payload.get("chapters", [])

    if vod_id is None:
        vod_id = await _find_vod_id(meta)
        log.info("アーカイブを特定: https://www.twitch.tv/videos/%s", vod_id)

    engine = HighlightEngine()
    log.info("VODチャットを再取得して再解析中...")
    count = await asyncio.to_thread(_replay_chat_sync, vod_id, engine)
    log.info("チャット %d 件を再解析しました", count)

    if audio_enabled:
        if AudioCapture.available():
            log.info("VOD音声を解析中(実時間より高速に処理されます)...")
            seconds = await analyze_vod_audio(vod_id, lambda fr: engine.add_audio(fr.offset, fr.excitement))
            log.info("音声 %s 分を解析しました", seconds // 60)
        else:
            log.warning("streamlink / ffmpeg が無いためVOD音声の再解析をスキップします")

    top_n = max(len(live_highlights) * 2, 40)
    archive = [asdict(h) for h in engine.ranked_highlights(top_n)]
    result = compare_highlights(live_highlights, archive, tolerance)

    report = _render_report(meta, vod_id, result, chapters)
    with open(os.path.join(session_dir, "verification.md"), "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(session_dir, "verification.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"meta": meta, "vod_id": vod_id, **asdict(result)},
            f, ensure_ascii=False, indent=2,
        )
    log.info(
        "検証完了: ずれ %+.1f秒 / 一致 %d / ライブのみ %d / アーカイブのみ %d -> %s",
        result.drift,
        result.matched,
        result.live_total - result.matched,
        result.archive_total - result.matched,
        os.path.join(session_dir, "verification.md"),
    )
    return result
