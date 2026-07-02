# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A zero-recurring-cost Twitch stream monitor: detects when watched channels go live, builds a real-time chaptered timeline of each stream, ranks clip-worthy moments from chat activity and stream audio, and can post-hoc verify the live results against the VOD archive. A hard project constraint is **no metered/usage-based billing** — only the free Twitch Helix API, anonymous IRC chat, VOD chat replay (chat-downloader), local audio decoding (streamlink + ffmpeg), and local heuristic analysis (regex + statistics + DSP). Do not introduce paid APIs (LLMs, transcription services, etc.) without explicit approval.

## Commands

```bash
pip install -r requirements.txt          # install deps (aiohttp, PyYAML, numpy, streamlink)
python -m pytest tests/ -q               # run all tests
python -m pytest tests/test_highlights.py::test_spike_detected_over_quiet_baseline -q  # single test
python -m twitch_monitor --config config.yaml            # run the monitor
python -m twitch_monitor --channel some_streamer         # run against ad-hoc channels
python -m twitch_monitor verify out/<login>/<dir>        # post-stream verification against the VOD
```

Requires `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` env vars (free app registration at dev.twitch.tv). Audio analysis additionally needs the `ffmpeg` binary; if `streamlink`/`ffmpeg` are missing the monitor logs a warning and degrades to chat-only (`--no-audio` forces this). User-facing strings (CLI help, logs, output) are in Japanese.

## Architecture

Data flows through `twitch_monitor/` like this:

1. `monitor.py` — orchestrator. `run_monitor()` polls Helix `Get Streams` (via `twitch_api.HelixClient`, app-access-token with auto-refresh) every `poll_interval_seconds`. It creates a `StreamSession` per live channel and tears it down (writing a final report) when the stream ends or a new stream id appears for the same channel.
2. `chat.py` — `ChatReader` connects to Twitch IRC **anonymously** (`justinfan` nick, no token) with auto-reconnect, parses IRCv3-tagged PRIVMSGs into `ChatMessage`s, and pushes them into the session callback.
3. `audio.py` — `AudioCapture` resolves the audio-only HLS URL via the `streamlink` CLI and decodes it to 16kHz mono PCM via `ffmpeg` (both free OSS, subprocess-based). `AudioAnalyzer` scores 1-second frames for "voice excitement": loudness delta vs. a slow EMA baseline (which absorbs constant BGM/game audio), weighted by voice-band (300–3400Hz) energy ratio, with a sustain decay so prolonged loudness (BGM changes, combat) fades out. Pure-logic class, fully testable without network/binaries.
4. `highlights.py` — `HighlightEngine` aggregates chat messages **and** audio excitement into fixed-width buckets (default 10s) keyed by stream offset (`_buckets` dict — chat and audio arrive independently). Spike ratios vs. an EMA baseline are computed lazily in `_sorted_with_spikes()` on each `ranked_highlights()` call. Hot buckets (chat spike ≥ threshold OR audio excitement ≥ threshold) merge into windows (gaps up to one bucket are bridged) scored by peak spike + laugh/hype/clip-request ratios (Japanese + English regexes at module top) + emote density + unique users + weighted audio peak. Categories: 笑い / 盛り上がり / クリップ希望 / 音声の盛り上がり / チャット急増.
5. `chapters.py` — `ChapterTracker` opens a new chapter whenever the stream title or game category changes (fed from the same Get Streams poll).
6. `output.py` — renders `timeline.md` (human-readable, Japanese) and `highlights.json` (structured) into `out/<login>/<date>_<streamid>/`, rewritten every `snapshot_interval_seconds`.
7. `verify.py` — post-stream verification (`verify` subcommand). Locates the VOD via Helix `Get Videos` (matching `stream_id`; skipped when `--vod` is given), replays VOD chat through a fresh `HighlightEngine` via `chat-downloader` (sync, run in a thread), optionally re-analyzes VOD audio with `analyze_vod_audio` (faster than realtime, exact offsets), then `compare_highlights()` estimates the live→VOD timestamp drift (median of nearest-pair deltas) and classifies each highlight as match / live_only / archive_only. Writes `verification.md` (with seekable VOD `?t=` links) and `verification.json` into the session dir. The comparison functions are pure and operate on `asdict(Highlight)`-shaped dicts so live JSON and re-analysis results share one code path.

All timestamps in the analysis pipeline are **offsets in seconds from stream start** (`started_at` from Helix), not epoch times — keep new code consistent with that.

## Testing Conventions

Tests cover the pure logic (scoring, chapter transitions, IRC parsing, audio analysis) without network access or external binaries. The scoring tests build a quiet baseline first, then inject a spike — follow that pattern when adjusting weights/thresholds so ranking behavior stays pinned. Audio tests synthesize PCM frames with numpy sine tones (low-frequency "BGM" vs. voice-band "shout") to pin the BGM-absorption and voice-band discrimination behavior.
