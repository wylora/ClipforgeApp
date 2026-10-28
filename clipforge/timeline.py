"""План таймлайна клипа: удаление тишины, замедление на панчлайне, общая скорость.
Нужен и для FFmpeg-графа, и для пересчёта времён субтитров."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Piece:
    s: float
    e: float
    speed: float = 1.0

    @property
    def out_len(self) -> float:
        return (self.e - self.s) / self.speed


class TimelinePlan:
    def __init__(self, pieces: list[Piece]):
        self.pieces = pieces

    @classmethod
    def identity(cls, dur: float) -> "TimelinePlan":
        return cls([Piece(0.0, dur, 1.0)])

    @property
    def duration(self) -> float:
        return sum(p.out_len for p in self.pieces)

    @property
    def is_identity(self) -> bool:
        return len(self.pieces) == 1 and abs(self.pieces[0].speed - 1.0) < 1e-6

    def map(self, t: float, side: str = "start") -> float:
        """Время исходного клипа → время результата. Для t в вырезанной зоне — ближайшая граница."""
        off = 0.0
        prev_end_out = 0.0
        for p in self.pieces:
            if t < p.s:
                return off if side == "start" else prev_end_out
            if t <= p.e:
                return off + (t - p.s) / p.speed
            off += p.out_len
            prev_end_out = off
        return off


def _subtract(ranges: list[tuple[float, float]], cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = ranges
    for cs, ce in sorted(cuts):
        nxt = []
        for s, e in out:
            if ce <= s or cs >= e:
                nxt.append((s, e))
                continue
            if cs > s:
                nxt.append((s, cs))
            if ce < e:
                nxt.append((ce, e))
        out = nxt
    return out


def build_plan(dur: float, *, gaps: list[tuple[float, float]] | None = None, protect: list[tuple[float, float]] | None = None,
               punch_t: float | None = None, remove_silence: bool = False, slowmo: bool = False,
               speed: float = 1.0, keep: float = 0.3, min_gap: float = 0.7, max_cuts: int = 30) -> TimelinePlan:
    cuts: list[tuple[float, float]] = []
    if remove_silence and gaps:
        cand = []
        for s, e in gaps:
            if e - s < min_gap:
                continue
            if any(not (e <= ps or s >= pe) for ps, pe in (protect or [])):
                continue
            cand.append((s + keep / 2, e - keep / 2))
        cand.sort(key=lambda g: -(g[1] - g[0]))
        cuts = cand[:max_cuts]
    kept = _subtract([(0.0, dur)], cuts)
    pieces: list[Piece] = []
    sm = (max(0.0, punch_t - 0.25), min(dur, punch_t + 0.6)) if (slowmo and punch_t is not None) else None
    for s, e in kept:
        if e - s < 0.05:
            continue
        if sm and sm[0] < e and sm[1] > s:
            a, b = max(s, sm[0]), min(e, sm[1])
            for ps, pe, sp in ((s, a, 1.0), (a, b, 0.75), (b, e, 1.0)):
                if pe - ps > 0.04:
                    pieces.append(Piece(ps, pe, sp))
        else:
            pieces.append(Piece(s, e, 1.0))
    for p in pieces:
        p.speed = min(2.0, max(0.5, p.speed * speed))
    merged: list[Piece] = []
    for p in pieces:
        if merged and abs(merged[-1].e - p.s) < 1e-6 and abs(merged[-1].speed - p.speed) < 1e-6:
            merged[-1].e = p.e
        else:
            merged.append(p)
    return TimelinePlan(merged or [Piece(0.0, dur, 1.0)])
