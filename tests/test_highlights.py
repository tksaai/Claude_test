from twitch_monitor.highlights import HighlightEngine, LAUGH_RE, CLIP_RE


def feed(engine, offset, count, text="hello", user_prefix="user"):
    for i in range(count):
        engine.add_message(f"{user_prefix}{i}", text, 0, offset + i * 0.01)


def test_spike_detected_over_quiet_baseline():
    engine = HighlightEngine(bucket_seconds=10)
    # 平常時: 60秒間、10秒あたり3メッセージ
    for t in range(0, 60, 10):
        feed(engine, t, 3)
    # スパイク: 10秒間に40メッセージ
    feed(engine, 60, 40, text="草www")
    # 平常に戻る
    for t in range(70, 120, 10):
        feed(engine, t, 3)

    highlights = engine.ranked_highlights()
    assert len(highlights) == 1
    h = highlights[0]
    assert h.start_offset == 60
    assert h.end_offset == 70
    assert h.messages == 40
    assert h.category == "笑い"


def test_no_highlight_when_flat():
    engine = HighlightEngine(bucket_seconds=10)
    for t in range(0, 300, 10):
        feed(engine, t, 5)
    assert engine.ranked_highlights() == []


def test_clip_requests_rank_higher_than_plain_spike():
    engine = HighlightEngine(bucket_seconds=10)
    for t in range(0, 60, 10):
        feed(engine, t, 3)
    feed(engine, 60, 30, text="ナイスプレイ")          # 盛り上がりスパイク
    for t in range(70, 130, 10):
        feed(engine, t, 3)
    feed(engine, 130, 30, text="ここクリップして")      # クリップ要望スパイク(同規模)
    for t in range(140, 200, 10):
        feed(engine, t, 3)

    highlights = engine.ranked_highlights()
    assert len(highlights) == 2
    assert highlights[0].category == "クリップ希望"
    assert highlights[0].start_offset == 130


def test_unique_users_counted_across_window():
    engine = HighlightEngine(bucket_seconds=10)
    for t in range(0, 60, 10):
        feed(engine, t, 3)
    feed(engine, 60, 20, user_prefix="a")
    feed(engine, 70, 20, user_prefix="a")  # 同じユーザー群
    h = engine.ranked_highlights()[0]
    assert h.unique_users == 20
    assert h.messages == 40


def test_signal_regexes():
    assert LAUGH_RE.search("草")
    assert LAUGH_RE.search("wwww")
    assert LAUGH_RE.search("KEKW")
    assert not LAUGH_RE.search("wow")       # 英単語のwを誤検知しない
    assert CLIP_RE.search("切り抜きお願いします")
    assert CLIP_RE.search("Clip it!")
    assert not CLIP_RE.search("eclipse")     # 単語境界チェック


def test_audio_only_highlight():
    """チャットが静かでも音声の盛り上がりだけで候補になる。"""
    engine = HighlightEngine(bucket_seconds=10)
    for t in range(0, 120):
        engine.add_audio(float(t), 0.1)
    for t in range(120, 130):
        engine.add_audio(float(t), 1.5)
    for t in range(130, 200):
        engine.add_audio(float(t), 0.1)

    highlights = engine.ranked_highlights()
    assert len(highlights) == 1
    h = highlights[0]
    assert h.start_offset == 120
    assert h.category == "音声の盛り上がり"
    assert h.audio_peak == 1.5
    assert h.messages == 0


def test_audio_boosts_matching_chat_spike():
    """同規模のチャットスパイクでも、音声も盛り上がっている方が上位になる。"""
    engine = HighlightEngine(bucket_seconds=10)
    for t in range(0, 60, 10):
        feed(engine, t, 3)
    feed(engine, 60, 30)                      # チャットのみのスパイク
    for t in range(70, 130, 10):
        feed(engine, t, 3)
    feed(engine, 130, 30)                     # チャット+音声のスパイク
    engine.add_audio(135.0, 1.8)
    for t in range(140, 200, 10):
        feed(engine, t, 3)

    highlights = engine.ranked_highlights()
    assert len(highlights) == 2
    assert highlights[0].start_offset == 130
    assert highlights[0].audio_peak == 1.8
