"""Оценка «серьёзных» эпизодов: содержательность, законченность, польза, эмоции, самодостаточность."""
from __future__ import annotations

from .models import Episode, ScoreResult
from .text_features import ends_terminal, serious_cues, starts_dependent
from .util import clamp


def score_serious(ep: Episode, ctx) -> ScoreResult:
    reasons: list[str] = []
    parts: dict[str, float] = {}
    text = ep.text
    c = serious_cues(text)
    first, last = ep.units[0], ep.units[-1]

    # содержательность: плотность содержательных слов, длина мысли, факты/числа
    substance = clamp(0.5 * c.density / 0.65 + 0.3 * clamp(c.n_words / 60) + 0.1 * clamp(c.fact / 2) + 0.1 * clamp(c.numbers / 3))
    substance *= 1 - clamp(c.filler_ratio * 3, 0, 0.5)
    parts["substance"] = substance
    if c.fact or c.numbers:
        reasons.append("есть факты, цифры или ссылки на исследования")
    if c.filler_ratio > 0.12:
        reasons.append("много слов-паразитов — оценка снижена")

    # польза/вывод
    useful = clamp(0.35 * clamp(c.insight / 2) + 0.35 * clamp(c.advice / 2) + 0.15 * clamp(c.motivation / 1.5)
                   + 0.15 * clamp(c.conclusion / 1.5))
    parts["useful"] = useful
    if c.insight:
        reasons.append("в эпизоде сформулирован вывод или важный тезис")
    if c.advice:
        reasons.append("содержит совет или рекомендацию")
    if c.motivation:
        reasons.append("мотивационная формулировка")

    # эмоциональность: текст + интонация
    emo = clamp(c.emotion / 2) * 0.6
    au = ctx.audio
    if au is not None and au.n:
        st = au.stats(ep.start, ep.end)
        emo += 0.4 * clamp(st["pitch_std_st"] / 4.0) * 0.5 + 0.4 * clamp(st["loud_std"] / 8.0) * 0.5
    parts["emotion"] = clamp(emo)
    if c.emotion:
        reasons.append("эмоциональное признание или личная история")

    # законченность мысли и самодостаточность
    comp = 0.0
    comp += 0.35 if ends_terminal(last.text) else 0.0
    comp += 0.2 if c.conclusion or c.insight else 0.0
    comp += 0.3 if not starts_dependent(first.text) else 0.0
    comp += 0.15 if len(ep.units) >= 2 else 0.0
    parts["standalone"] = clamp(comp)
    if starts_dependent(first.text):
        reasons.append("эпизод начинается со ссылки на предыдущий контекст — понятность снижена")
    if ends_terminal(last.text):
        reasons.append("мысль закончена (завершающая пунктуация)")

    score = 100 * (0.32 * substance + 0.26 * useful + 0.14 * parts["emotion"] + 0.28 * parts["standalone"])
    return ScoreResult(score=clamp(score, 0, 100), parts=parts, reasons=reasons)
