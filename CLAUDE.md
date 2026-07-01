# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A zero-recurring-cost Twitch stream monitor: detects when watched channels go live, builds a real-time chaptered timeline of each stream, and ranks clip-worthy moments from chat activity. A hard project constraint is **no metered/usage-based billing** — only the free Twitch Helix API, anonymous IRC chat, and local heuristic analysis (regex + statistics). Do not introduce paid APIs (LLMs, transcription services, etc.) without explicit approval.

## Commands

```bash
pip install -r requirements.txt          # install deps (aiohttp, PyYAML)
python -m pytest tests/ -q               # run all tests
python -m pytest tests/test_highlights.py::test_spike_detected_over_quiet_baseline -q  # single test
python -m twitch_monitor --config config.yaml            # run the monitor
python -m twitch_monitor --channel some_streamer         # run against ad-hoc channels
```

Requires `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` env vars (free app registration at dev.twitch.tv). User-facing strings (CLI help, logs, output) are in Japanese.

## Architecture

Data flows through `twitch_monitor/` like this:

1. `monitor.py` — orchestrator. `run_monitor()` polls Helix `Get Streams` (via `twitch_api.HelixClient`, app-access-token with auto-refresh) every `poll_interval_seconds`. It creates a `StreamSession` per live channel and tears it down (writing a final report) when the stream ends or a new stream id appears for the same channel.
2. `chat.py` — `ChatReader` connects to Twitch IRC **anonymously** (`justinfan` nick, no token) with auto-reconnect, parses IRCv3-tagged PRIVMSGs into `ChatMessage`s, and pushes them into the session callback.
3. `highlights.py` — `HighlightEngine` aggregates messages into fixed-width buckets (default 10s) keyed by stream offset. Each closed bucket gets a `spike` ratio vs. an EMA baseline (clamped so spikes don't inflate the baseline). `ranked_highlights()` merges consecutive hot buckets into windows and scores them by peak spike + laugh/hype/clip-request ratios (Japanese + English regexes at module top) + emote density + unique users. Categories: 笑い / 盛り上がり / クリップ希望 / チャット急増.
4. `chapters.py` — `ChapterTracker` opens a new chapter whenever the stream title or game category changes (fed from the same Get Streams poll).
5. `output.py` — renders `timeline.md` (human-readable, Japanese) and `highlights.json` (structured) into `out/<login>/<date>_<streamid>/`, rewritten every `snapshot_interval_seconds`.

All timestamps in the analysis pipeline are **offsets in seconds from stream start** (`started_at` from Helix), not epoch times — keep new code consistent with that.

## Testing Conventions

Tests cover the pure logic (scoring, chapter transitions, IRC parsing) without network access. The scoring tests build a quiet baseline first, then inject a spike — follow that pattern when adjusting weights/thresholds so ranking behavior stays pinned.
