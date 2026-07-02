from twitch_monitor.verify import compare_highlights, estimate_drift, vod_timestamp_url


def hl(start, end, score=5.0, category="笑い"):
    return {"start_offset": float(start), "end_offset": float(end), "score": score, "category": category}


def test_drift_estimated_from_matching_pairs():
    live = [hl(100, 120), hl(500, 510)]
    archive = [hl(112, 132), hl(512, 522)]
    assert abs(estimate_drift(live, archive) - 12.0) < 0.01


def test_compare_classifies_match_live_only_and_archive_only():
    live = [hl(100, 120), hl(500, 510), hl(900, 930)]      # 900はアーカイブに対応なし
    archive = [hl(112, 132), hl(512, 522), hl(1500, 1510)]  # 1500はライブで見逃し

    result = compare_highlights(live, archive)

    assert abs(result.drift - 12.0) < 0.01
    assert result.matched == 2
    statuses = {it["corrected_start"]: it["status"] for it in result.items}
    assert statuses[112.0] == "match"
    assert statuses[512.0] == "match"
    assert statuses[912.0] == "live_only"        # 補正後時刻で報告される
    assert statuses[1500.0] == "archive_only"
    # 時系列順に並んでいる
    starts = [it["corrected_start"] for it in result.items]
    assert starts == sorted(starts)


def test_compare_with_empty_archive():
    live = [hl(100, 120)]
    result = compare_highlights(live, [])
    assert result.drift == 0.0
    assert result.matched == 0
    assert result.items[0]["status"] == "live_only"


def test_archive_matched_at_most_once():
    """近接する2つのライブ候補が同じアーカイブ候補を奪い合わない。"""
    live = [hl(100, 110), hl(115, 125)]
    archive = [hl(100, 110)]
    result = compare_highlights(live, archive)
    assert result.matched == 1
    assert sum(1 for it in result.items if it["status"] == "live_only") == 1


def test_vod_timestamp_url():
    assert vod_timestamp_url("123456", 3725) == "https://www.twitch.tv/videos/123456?t=1h2m5s"
    assert vod_timestamp_url("123456", -5) == "https://www.twitch.tv/videos/123456?t=0h0m0s"
