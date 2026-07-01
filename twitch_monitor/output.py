import json
import os
from dataclasses import asdict

from .chapters import Chapter
from .highlights import Highlight


def format_offset(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def render_markdown(meta: dict, chapters: list[Chapter], highlights: list[Highlight]) -> str:
    lines = [
        f"# {meta['user_name']} 配信タイムライン",
        "",
        f"- 配信開始: {meta['started_at']}",
        f"- ストリームID: {meta['stream_id']}",
        f"- 最終更新: {meta['updated_at']}",
        "",
        "## 章立てタイムライン",
        "",
        "| 開始 | 終了 | 章タイトル | カテゴリ |",
        "|------|------|-----------|----------|",
    ]
    for c in chapters:
        end = format_offset(c.end_offset) if c.end_offset is not None else "(配信中)"
        lines.append(f"| {format_offset(c.start_offset)} | {end} | {c.title} | {c.game} |")

    lines += [
        "",
        "## 切り抜き候補ランキング",
        "",
        "| # | 時間 | タイプ | スコア | コメント数 | 人数 | ピーク倍率 | 音声 | 代表コメント |",
        "|---|------|--------|--------|-----------|------|-----------|------|--------------|",
    ]
    for i, h in enumerate(highlights, 1):
        span = f"{format_offset(h.start_offset)}〜{format_offset(h.end_offset)}"
        sample = h.samples[0].replace("|", "\\|") if h.samples else ""
        lines.append(
            f"| {i} | {span} | {h.category} | {h.score} | {h.messages} | {h.unique_users}"
            f" | x{h.peak_spike} | {h.audio_peak} | {sample} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_snapshot(out_dir: str, meta: dict, chapters: list[Chapter], highlights: list[Highlight]):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "timeline.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(meta, chapters, highlights))
    payload = {
        "meta": meta,
        "chapters": [asdict(c) for c in chapters],
        "highlights": [asdict(h) for h in highlights],
    }
    with open(os.path.join(out_dir, "highlights.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
