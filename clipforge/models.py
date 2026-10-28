"""Базовые структуры данных: транскрипт, смысловые единицы, кандидаты."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    language: str
    duration: float
    segments: list[Segment]
    source: str = "whisper"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        segs = [Segment(s["start"], s["end"], s["text"], [Word(**w) for w in s.get("words", [])]) for s in d["segments"]]
        return cls(d.get("language", "unknown"), d.get("duration", 0.0), segs, d.get("source", "whisper"))

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def all_words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]


@dataclass
class Unit:
    """Смысловая единица (предложение/реплика) с временными метками."""
    idx: int
    start: float
    end: float
    text: str
    words: list[Word]
    speaker: int | None = None

    @property
    def n_words(self) -> int:
        return len(self.words) if self.words else len(self.text.split())

    @property
    def duration(self) -> float:
        return max(0.01, self.end - self.start)


@dataclass
class Episode:
    start: float
    end: float
    units: list[Unit]
    anchor: int                       # индекс в units
    mode: str = "funny"

    @property
    def text(self) -> str:
        return " ".join(u.text.strip() for u in self.units)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class ScoreResult:
    score: float                      # 0..100
    parts: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    start: float
    end: float
    anchor_time: float
    mode: str
    text: str
    scores: dict[str, float] = field(default_factory=dict)   # semantic/humor/reaction/audio/completeness/visual/quality
    details: dict = field(default_factory=dict)              # внутренние показатели (для отчёта)
    reasons: list[str] = field(default_factory=list)
    selected: bool = False
    signature: list[int] = field(default_factory=list)       # dHash-кадры для поиска дублей

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def quality(self) -> float:
        return self.scores.get("quality", 0.0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signature"] = [int(x) for x in self.signature]
        return d
