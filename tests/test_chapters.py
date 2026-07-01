from twitch_monitor.chapters import ChapterTracker


def test_first_update_creates_chapter():
    tracker = ChapterTracker()
    c = tracker.update("雑談", "Just Chatting", 0)
    assert c is not None
    assert len(tracker.chapters) == 1


def test_no_new_chapter_when_unchanged():
    tracker = ChapterTracker()
    tracker.update("雑談", "Just Chatting", 0)
    assert tracker.update("雑談", "Just Chatting", 60) is None
    assert len(tracker.chapters) == 1


def test_game_change_closes_previous_chapter():
    tracker = ChapterTracker()
    tracker.update("雑談", "Just Chatting", 0)
    c2 = tracker.update("APEXやる", "Apex Legends", 1800)
    assert c2 is not None
    assert tracker.chapters[0].end_offset == 1800
    assert tracker.chapters[1].start_offset == 1800
    assert tracker.chapters[1].end_offset is None
