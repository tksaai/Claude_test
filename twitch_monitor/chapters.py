from dataclasses import dataclass


@dataclass
class Chapter:
    start_offset: float  # 配信開始からの秒数
    title: str
    game: str
    end_offset: float | None = None


class ChapterTracker:
    """配信タイトル/ゲームカテゴリの変化から章を自動生成する。"""

    def __init__(self):
        self.chapters: list[Chapter] = []

    def update(self, title: str, game: str, offset: float) -> Chapter | None:
        """最新のタイトル/カテゴリを反映し、新章が始まった場合はそれを返す。"""
        current = self.chapters[-1] if self.chapters else None
        if current and current.title == title and current.game == game:
            return None
        if current:
            current.end_offset = offset
        chapter = Chapter(start_offset=max(offset, 0.0), title=title, game=game)
        self.chapters.append(chapter)
        return chapter
