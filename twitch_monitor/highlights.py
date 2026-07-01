import re
from dataclasses import dataclass, field

# 日英両対応のシグナル判定(全てローカル正規表現、外部API不使用)
LAUGH_RE = re.compile(r"(草|ｗ{2,}|\bw{3,}\b|笑|ワロタ|くさ|LUL|KEKW|OMEGALUL|\blol\b|\blmao\b)", re.IGNORECASE)
HYPE_RE = re.compile(
    r"(やば|すご|神|えぐ|うま|つよ|きた|ナイス|nice|pog|hype|let'?s\s?go|clutch|クラッチ|\bgg\b|うおお|おおお)",
    re.IGNORECASE,
)
CLIP_RE = re.compile(r"(クリップ|切り抜き|\bclip\b)", re.IGNORECASE)

SAMPLE_MESSAGES_PER_BUCKET = 3


@dataclass
class Bucket:
    start_offset: float
    messages: int = 0
    users: set = field(default_factory=set)
    emotes: int = 0
    laughs: int = 0
    hype: int = 0
    clip_requests: int = 0
    spike: float = 0.0
    samples: list = field(default_factory=list)


@dataclass
class Highlight:
    start_offset: float
    end_offset: float
    score: float
    category: str
    messages: int
    unique_users: int
    laughs: int
    hype: int
    clip_requests: int
    emotes: int
    peak_spike: float
    samples: list


class HighlightEngine:
    """チャットをバケット集計し、スパイク+内容シグナルで切り抜き候補をスコアリングする。"""

    def __init__(
        self,
        bucket_seconds: int = 10,
        baseline_alpha: float = 0.05,
        spike_threshold: float = 2.5,
        min_baseline: float = 3.0,
        min_messages: int = 5,
    ):
        self.bucket_seconds = bucket_seconds
        self.baseline_alpha = baseline_alpha
        self.spike_threshold = spike_threshold
        self.min_baseline = min_baseline
        self.min_messages = min_messages
        self.buckets: list[Bucket] = []
        self.baseline: float | None = None  # 1バケットあたりの平常メッセージ数(EMA)
        self._current: Bucket | None = None

    def add_message(self, user: str, text: str, emote_count: int, offset: float):
        bucket_start = (offset // self.bucket_seconds) * self.bucket_seconds
        if self._current is None or self._current.start_offset != bucket_start:
            self._roll_to(bucket_start)
        b = self._current
        b.messages += 1
        b.users.add(user)
        b.emotes += emote_count
        if LAUGH_RE.search(text):
            b.laughs += 1
        if HYPE_RE.search(text):
            b.hype += 1
        if CLIP_RE.search(text):
            b.clip_requests += 1
        if len(b.samples) < SAMPLE_MESSAGES_PER_BUCKET:
            b.samples.append(f"{user}: {text[:60]}")

    def _roll_to(self, bucket_start: float):
        if self._current is not None:
            self._finalize_bucket(self._current)
        self._current = Bucket(start_offset=bucket_start)
        self.buckets.append(self._current)

    def _finalize_bucket(self, b: Bucket):
        base = max(self.baseline if self.baseline is not None else b.messages, self.min_baseline)
        b.spike = b.messages / base
        # スパイク中のバケットでベースラインを吊り上げないよう、上限つきでEMA更新
        sample = min(b.messages, base * 2)
        if self.baseline is None:
            self.baseline = float(b.messages)
        else:
            self.baseline = (1 - self.baseline_alpha) * self.baseline + self.baseline_alpha * sample

    def flush(self):
        """現在のバケットを確定する(スナップショット出力や配信終了時に呼ぶ)。"""
        if self._current is not None:
            self._finalize_bucket(self._current)
            self._current = None

    def ranked_highlights(self, top_n: int = 20) -> list[Highlight]:
        """スパイクした連続バケットを1つの候補ウィンドウにまとめ、スコア順で返す。"""
        windows: list[list[Bucket]] = []
        current_window: list[Bucket] = []
        for b in self.buckets:
            is_hot = b.spike >= self.spike_threshold and b.messages >= self.min_messages
            if is_hot:
                current_window.append(b)
            elif current_window:
                windows.append(current_window)
                current_window = []
        if current_window:
            windows.append(current_window)

        highlights = [self._score_window(w) for w in windows]
        highlights.sort(key=lambda h: h.score, reverse=True)
        return highlights[:top_n]

    def _score_window(self, window: list[Bucket]) -> Highlight:
        messages = sum(b.messages for b in window)
        users = set()
        for b in window:
            users.update(b.users)
        laughs = sum(b.laughs for b in window)
        hype = sum(b.hype for b in window)
        clips = sum(b.clip_requests for b in window)
        emotes = sum(b.emotes for b in window)
        peak_spike = max(b.spike for b in window)

        laugh_ratio = laughs / messages
        hype_ratio = hype / messages
        clip_ratio = clips / messages
        emote_ratio = min(emotes / messages, 3.0)

        score = (
            peak_spike
            + 2.0 * laugh_ratio
            + 2.0 * hype_ratio
            + 4.0 * clip_ratio
            + 0.5 * emote_ratio
            + 0.02 * len(users)
        )

        category = "チャット急増"
        best = 0.05  # シグナル比率がこの閾値以下なら単なる急増扱い
        for ratio, label in (
            (laugh_ratio, "笑い"),
            (hype_ratio, "盛り上がり"),
            (clip_ratio, "クリップ希望"),
        ):
            if ratio > best:
                best = ratio
                category = label

        samples = []
        for b in sorted(window, key=lambda b: b.spike, reverse=True):
            samples.extend(b.samples)
        return Highlight(
            start_offset=window[0].start_offset,
            end_offset=window[-1].start_offset + self.bucket_seconds,
            score=round(score, 2),
            category=category,
            messages=messages,
            unique_users=len(users),
            laughs=laughs,
            hype=hype,
            clip_requests=clips,
            emotes=emotes,
            peak_spike=round(peak_spike, 2),
            samples=samples[:5],
        )
