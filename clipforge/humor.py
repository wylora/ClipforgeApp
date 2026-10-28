"""Оценка юмора 0–100 по нескольким независимым сигналам.

Сигналы: текст (маркеры шуток/неожиданности/токены смеха), структура (подводка → пауза → короткая реплика),
звук (эвристика смеха, всплеск громкости и интонации), реакция собеседников, видео (активность рта/улыбка).
Один сигнал не может дать высокую оценку: число «сильных семейств сигналов» ограничивает потолок.
Результат — вероятность юмористической ценности, а не утверждение; пользователь может её исправить.
"""
from __future__ import annotations

import math

from .models import Episode, ScoreResult
from .text_features import humor_cues
from .util import clamp

WEIGHTS = {"text": 0.20, "structure": 0.18, "laugh": 0.22, "prosody": 0.08, "reaction": 0.12, "visual": 0.12}


def score_humor(ep: Episode, ctx) -> ScoreResult:
    reasons: list[str] = []
    parts: dict[str, float] = {}
    a = ep.units[ep.anchor]
    before, after = ep.units[:ep.anchor], ep.units[ep.anchor + 1:]
    ca = humor_cues(a.text)
    cafter = humor_cues(" ".join(u.text for u in after[:3]))
    call = humor_cues(ep.text)

    # 1. текст
    t = 1 - math.exp(-(0.7 * ca.marker + 0.5 * ca.surprise + 0.25 * min(ca.excl, 3) + 0.15 * ca.hyper + 1.1 * ca.laugh
                       + 1.0 * cafter.laugh + 0.3 * cafter.marker + 0.15 * call.marker))
    parts["text"] = t
    if ca.laugh or cafter.laugh:
        reasons.append("в транскрипте есть метки смеха/реакции")
    if ca.marker or ca.surprise:
        reasons.append("в реплике есть маркеры шутки или неожиданности")

    # 2. структура: подводка → пауза → короткая реплика
    s = 0.0
    if before:
        setup_len = sum(u.n_words for u in before[-2:])
        if a.n_words <= 14 and setup_len >= a.n_words * 1.5 + 3:
            s += 0.35
            reasons.append("короткая реплика после длинной подводки (структура «сетап → панчлайн»)")
        pause = a.start - before[-1].end
        if 0.35 <= pause <= 2.5:
            s += 0.2
            reasons.append(f"пауза {pause:.1f} с перед репликой (комичная пауза)")
        if before[-1].text.rstrip().endswith("?") and a.n_words <= 12:
            s += 0.25
            reasons.append("короткий ответ на вопрос")
    if ca.contrast or ca.surprise:
        s += 0.2
    parts["structure"] = min(1.0, s)

    # 3–4. звук
    au = ctx.audio
    if au is not None and au.n:
        lm, _ = au.laugh_in(a.end - 0.3, min(a.end + 4.5, ep.end + 0.5))
        wa = sum(u.n_words for u in after if u.start < a.end + 4.5)
        wps = wa / 4.5
        eff = lm * (1 - 0.5 * min(1.0, wps / 3.0))
        parts["laugh"] = clamp(eff)
        if eff > 0.4:
            reasons.append(f"после реплики слышна смеховая реакция (оценка {eff:.2f})")
        sa, se = au.stats(a.start, a.end), au.stats(ep.start, ep.end)
        z = clamp(sa["peak_z"] / 3.0)
        pr = clamp(sa["pitch_std_st"] / max(2.0, se["pitch_std_st"] * 1.6 + 1e-6)) if sa["pitch_std_st"] > 0 else 0.0
        parts["prosody"] = 0.6 * z + 0.4 * pr
        if z > 0.6:
            reasons.append("заметный всплеск громкости на реплике")

    # 5. реакция собеседников
    r = 0.0
    near = [u for u in after if u.start - a.end < 5.0]
    if any(u.n_words <= 4 for u in near[:2]):
        r += 0.35
        reasons.append("после реплики есть короткая реакция")
    if a.speaker is not None and any(u.speaker is not None and u.speaker != a.speaker for u in near[:2]):
        r += 0.3
        reasons.append("смена говорящего сразу после реплики")
    if cafter.surprise or cafter.excl:
        r += 0.2
    if cafter.laugh:
        r += 0.3
    parts["reaction"] = min(1.0, r)

    # 6. видео
    v = ctx.video
    if v is not None and len(v.times):
        aft = v.stats(a.end, a.end + 3.0)
        bef = v.stats(max(0, a.start - 3.0), a.start)
        if aft["face_ratio"] > 0.2:
            delta = clamp((aft["act_mean"] - bef["act_mean"]) * 12 + aft["act_max"] * 4)
            vis = 0.6 * delta + 0.4 * clamp(aft["smile"] * 2)
            parts["visual"] = vis
            if vis > 0.4:
                reasons.append("после реплики заметно изменилась мимика/активность лиц")

    avail = {k: w for k, w in WEIGHTS.items() if k in parts}
    score = 100.0 * sum(avail[k] * parts[k] for k in avail) / (sum(avail.values()) or 1.0)
    families = {"text": parts.get("text", 0), "structure": parts.get("structure", 0),
                "audio": max(parts.get("laugh", 0), parts.get("prosody", 0)),
                "reaction": parts.get("reaction", 0), "visual": parts.get("visual", 0)}
    strong = sum(1 for x in families.values() if x >= 0.4)
    cap = {0: 30.0, 1: 50.0, 2: 75.0}.get(strong, 100.0)
    if score > cap:
        reasons.append(f"оценка ограничена: сильных независимых сигналов только {strong}")
        score = cap
    return ScoreResult(score=clamp(score, 0, 100), parts=parts, reasons=reasons)
