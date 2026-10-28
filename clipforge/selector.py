"""Алгоритм отбора моментов.

1. Оценка «якорных» реплик (текст + звук) → кандидаты-якоря с подавлением соседей.
2. Для каждого якоря перебор границ (начало/конец по смысловым единицам): подводка, кульминация, реакция,
   естественные паузы, законченность предложений → выбирается лучшая пара границ.
3. Многосигнальная оценка эпизода: semantic / humor / reaction / audio / completeness / visual.
4. Итоговая quality = взвешенная сумма (веса настраиваются) + мягкая персонализация по отметкам пользователя.
5. Отсев слабых, удаление дублей (пересечение, похожие кадры, похожий текст), выбор лучших N.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .audio_analysis import AudioFeatures
from .config import Settings
from .errors import AnalysisError
from .feedback import PersonalModel
from .humor import score_humor
from .llm import LLMClient
from .models import Candidate, Episode, Transcript, Unit
from .serious import score_serious
from .text_features import (CONCLUSION_RE, content_tokens, ends_terminal, humor_cues, serious_cues, starts_dependent,
                            stem)
from .util import CancelToken, clamp
from .video_analysis import VideoFeatures, signature_similarity

log = logging.getLogger("clipforge.selector")
MAX_GAP = 2.5


@dataclass
class Ctx:
    transcript: Transcript
    units: list[Unit]
    audio: AudioFeatures | None
    video: VideoFeatures | None
    settings: Settings
    media_duration: float
    llm: LLMClient | None = None
    personal: PersonalModel | None = None
    log: Callable[[str], None] = field(default=lambda m: None)


# ---------------------------------------------------------------- свой сценарий
@dataclass
class PromptInfo:
    keywords: set[str]
    humor: bool = False
    serious: bool = False
    story: bool = False
    surprise: bool = False
    reaction: bool = False


_INTENT = {
    "humor": re.compile(r"смеш|шут|юмор|прикол|ржач|funny|joke|laugh|humou?r", re.I),
    "serious": re.compile(r"совет|польз|факт|важн|вывод|advice|fact|insight|lesson|useful", re.I),
    "story": re.compile(r"истори|рассказ|случа|story|anecdote", re.I),
    "surprise": re.compile(r"неожиданн|развязк|поворот|twist|surpris|ending|punchline", re.I),
    "reaction": re.compile(r"реакц|react|удивл|шок", re.I),
}


def parse_prompt(prompt: str) -> PromptInfo:
    flags = {k: bool(rx.search(prompt)) for k, rx in _INTENT.items()}
    skip = {"найди", "выбери", "моменты", "момент", "все", "самые", "find", "moments", "show", "pick"}
    kws = {stem(w) for w in content_tokens(prompt) if w not in skip}
    return PromptInfo(kws, **flags)


def _keyword_cover(text: str, pinfo: PromptInfo) -> float:
    if not pinfo.keywords:
        return 0.0
    stems = {stem(w) for w in content_tokens(text)}
    return len(pinfo.keywords & stems) / len(pinfo.keywords)


# ---------------------------------------------------------------- быстрая оценка якорей
def _unit_text_score(ctx: Ctx, i: int, mode: str, pinfo: PromptInfo) -> float:
    u = ctx.units[i]
    nxt = " ".join(x.text for x in ctx.units[i + 1:i + 3] if x.start - u.end < 3.0)

    def funny() -> float:
        c, cn = humor_cues(u.text), humor_cues(nxt)
        return 1 - math.exp(-(0.7 * c.marker + 0.5 * c.surprise + 0.25 * min(c.excl, 3) + 1.1 * c.laugh
                               + 1.0 * cn.laugh + 0.3 * cn.marker + 0.2 * c.hyper))

    def serious() -> float:
        c = serious_cues(u.text + " " + nxt)
        v = 0.3 * c.insight + 0.3 * c.advice + 0.2 * c.fact + 0.25 * c.emotion + 0.2 * c.motivation + 0.15 * c.conclusion
        return clamp(v + (0.25 * c.density if c.n_words >= 8 else 0.0))

    if mode == "funny":
        return funny()
    if mode == "serious":
        return serious()
    if mode == "best":
        return max(funny(), serious())
    kw = _keyword_cover(u.text + " " + nxt, pinfo)
    extra = max(funny() if pinfo.humor or pinfo.reaction or pinfo.surprise else 0.0,
                serious() if pinfo.serious or pinfo.story else 0.0)
    return clamp(0.7 * kw + 0.5 * extra)


def _anchor_scores(ctx: Ctx, mode: str, pinfo: PromptInfo) -> np.ndarray:
    out = np.zeros(len(ctx.units))
    au = ctx.audio
    for i, u in enumerate(ctx.units):
        t = _unit_text_score(ctx, i, mode, pinfo)
        if au is not None and au.n:
            lm, _ = au.laugh_in(u.end - 0.3, u.end + 4.0)
            st = au.stats(u.start, u.end)
            a = 0.6 * lm + 0.4 * clamp(st["peak_z"] / 3.0)
            w = 0.25 if mode == "serious" else 0.4
            out[i] = (1 - w) * t + w * a
        else:
            out[i] = t
    return out


def _pick_anchors(ctx: Ctx, scores: np.ndarray, n: int, min_sep: float) -> list[int]:
    order = np.argsort(-scores)
    chosen: list[int] = []
    for i in order:
        t = ctx.units[i].start
        if all(abs(t - ctx.units[j].start) > min_sep for j in chosen):
            chosen.append(int(i))
        if len(chosen) >= n:
            break
    return chosen


# ---------------------------------------------------------------- границы клипа
def _start_quality(units: list[Unit], i: int) -> float:
    u = units[i]
    prev = units[i - 1] if i > 0 else None
    gap = (u.start - prev.end) if prev else 5.0
    q = 0.35 if gap >= 0.5 else (0.1 if gap >= 0.25 else 0.0)
    q += 0.15 if (prev is None or ends_terminal(prev.text) or not prev.text) else 0.0
    q += 0.3 if not starts_dependent(u.text) else 0.0
    return q / 0.8


def _end_quality(units: list[Unit], j: int) -> float:
    u = units[j]
    nxt = units[j + 1] if j + 1 < len(units) else None
    gap = (nxt.start - u.end) if nxt else 5.0
    q = 0.35 if (ends_terminal(u.text) or not u.text) else 0.0
    q += 0.3 if gap >= 0.6 else (0.1 if gap >= 0.3 else 0.0)
    q += 0.15 if (nxt is None or not starts_dependent(nxt.text)) else 0.0
    q += 0.1 if CONCLUSION_RE.search(u.text) else 0.0
    return q / 0.9


def _duration_fit(dur: float, mode: str, auto: bool, target: float) -> float:
    if auto:
        if mode == "funny":
            return (0.15 if 6 <= dur <= 25 else 0.0) - 0.03 * max(0.0, dur - 30)
        return 0.3 * math.exp(-((dur - 22) / 12) ** 2) - 0.02 * max(0.0, dur - 45)
    return 0.5 * math.exp(-((dur - target) / (0.3 * target)) ** 2)


def _limits(ctx: Ctx, mode: str) -> tuple[float, float, bool, float]:
    c = ctx.settings.clip
    if c.auto_duration:
        return (5.0 if mode == "funny" else 9.0), c.max_duration, True, 0.0
    return c.min_duration, c.max_duration, False, c.target_duration


def build_episode(ctx: Ctx, ai: int, mode: str) -> tuple[Episode, dict] | None:
    units = ctx.units
    min_d, max_d, auto, target = _limits(ctx, mode)
    humorish = mode == "funny"
    back_max = 18.0 if humorish else 12.0
    lo = ai
    while lo > 0 and units[lo].start - units[lo - 1].end <= MAX_GAP and units[ai].start - units[lo - 1].start <= back_max \
            and ai - lo < 8:
        lo -= 1
    hi = ai
    while hi + 1 < len(units) and units[hi + 1].start - units[hi].end <= MAX_GAP and hi - ai < 14 \
            and units[hi + 1].end - units[lo].start <= max_d:
        hi += 1
    au = ctx.audio
    best, best_score = None, -1e9
    for i0 in range(lo, ai + 1):
        sq = _start_quality(units, i0)
        for j in range(ai, hi + 1):
            dur = units[j].end - units[i0].start + 0.8
            if dur > max_d:
                continue
            eq = _end_quality(units, j)
            s = sq + eq + _duration_fit(dur, mode, auto, target)
            if dur < min_d:
                s -= 0.08 * (min_d - dur)
            if humorish:
                s += 0.3 * min(units[ai].start - units[i0].start, 10.0) / 10.0
                if au is not None and au.n:
                    lm, _ = au.laugh_in(units[ai].end, units[j].end + 0.5)
                    s += 0.25 * (lm > 0.4) + (0.1 if j > ai else 0.0)
            if s > best_score:
                best, best_score = (i0, j, sq, eq), s
    if best is None:
        return None
    i0, j, sq, eq = best
    first, last = units[i0], units[j]
    lead, tail = 0.25, (0.5 if humorish else 0.3)
    start_t = first.start - lead
    end_t = last.end + tail
    if au is not None and au.n:
        start_t = au.nearest_quiet(start_t, 0.4, hi=first.start - 0.03)
        if humorish:       # досмотреть смеховую реакцию до конца
            f = au.idx(last.end)
            lim = au.idx(last.end + 4.0)
            run_end = None
            while f < min(lim, au.n) and au.laugh[f] > 0.35:
                run_end = f
                f += 1
            if run_end is not None:
                end_t = max(end_t, (run_end + 1) * (au.duration / max(au.n, 1)) + 0.3)
        end_t = au.nearest_quiet(end_t, 0.4, lo=last.end + 0.02)
    if i0 > 0:
        start_t = max(start_t, units[i0 - 1].end + 0.02)
    if j + 1 < len(units):
        end_t = min(end_t, units[j + 1].start - 0.05)
    start_t = max(0.0, start_t)
    end_t = min(ctx.media_duration, max(end_t, last.end))
    if end_t - start_t < 3.0:
        return None
    ep = Episode(start_t, end_t, units[i0:j + 1], ai - i0, mode)
    return ep, {"start_quality": sq, "end_quality": eq}


# ---------------------------------------------------------------- оценка кандидата
def _score_candidate(ctx: Ctx, ep: Episode, binfo: dict, mode: str, pinfo: PromptInfo) -> Candidate:
    hr = score_humor(ep, ctx)
    sr = score_serious(ep, ctx)
    reasons: list[str] = []
    sem_h = 100 * (0.5 * hr.parts.get("text", 0) + 0.5 * hr.parts.get("structure", 0))
    if mode == "funny":
        semantic, reasons = sem_h, hr.reasons
    elif mode == "serious":
        semantic, reasons = sr.score, sr.reasons
    elif mode == "best":
        semantic = max(sem_h, sr.score)
        reasons = hr.reasons if sem_h >= sr.score else sr.reasons
    else:
        kw = _keyword_cover(ep.text, pinfo)
        extra = max(sem_h if (pinfo.humor or pinfo.reaction or pinfo.surprise) else 0.0,
                    sr.score if (pinfo.serious or pinfo.story) else 0.0)
        semantic = 100 * clamp(0.7 * kw) + 0.5 * extra
        semantic = min(100.0, semantic)
        reasons = [f"совпадение с запросом по ключевым словам: {kw:.0%}"] + (hr.reasons if pinfo.humor else sr.reasons)

    p = hr.parts
    react_parts = [(0.4, p.get("laugh")), (0.35, p.get("reaction")), (0.25, p.get("visual"))]
    av = [(w, v) for w, v in react_parts if v is not None]
    reaction = 100 * sum(w * v for w, v in av) / sum(w for w, _ in av) if av else 0.0
    if mode != "funny":
        reaction = max(reaction, 100 * sr.parts.get("emotion", 0))

    au = ctx.audio
    audio_score = 50.0
    st: dict = {}
    if au is not None and au.n:
        st = au.stats(ep.start, ep.end)
        dyn = (0.35 * clamp(st["peak_z"] / 3) + 0.25 * clamp(st["loud_std"] / 8) + 0.2 * clamp(st["pitch_std_st"] / 4)
               + 0.2 * (1 - clamp(st["silence_ratio"] * 2)))
        words = sum(u.n_words for u in ep.units)
        if words / max(ep.duration, 1) < 0.8 and st["laugh_max"] < 0.5:
            dyn *= 0.6
        audio_score = 100 * dyn
    else:
        st = {"silence_ratio": 0.0}

    sil_pen = 1 - clamp((st.get("silence_ratio", 0) - 0.2) * 3)
    _, max_d, auto, target = _limits(ctx, mode)
    ok_dur = 1.0 if ep.duration <= max_d else 0.0
    completeness = 100 * clamp(0.3 * binfo["start_quality"] + 0.35 * binfo["end_quality"] + 0.2 * sil_pen + 0.15 * ok_dur)

    visual = None
    sig: list[int] = []
    if ctx.video is not None and len(ctx.video.times):
        vs = ctx.video.stats(ep.start, ep.end)
        cut_rate = vs["cuts"] / max(ep.duration / 10, 0.5)
        visual = 100 * (0.35 * clamp(vs["motion"] / 0.08) + 0.3 * vs["face_ratio"] + 0.2 * clamp(vs["act_mean"] * 15)
                        + 0.15 * (1 - clamp(abs(cut_rate - 1) / 3)))
        sig = ctx.video.signature(ep.start, ep.end)

    scores = {"semantic": semantic, "humor": hr.score, "reaction": reaction, "audio": audio_score,
              "completeness": completeness}
    if visual is not None:
        scores["visual"] = visual
    cand = Candidate(ep.start, ep.end, ep.units[ep.anchor].start, mode, ep.text, scores,
                     details={"humor_parts": hr.parts, "serious_parts": sr.parts,
                              "audio": {k: round(v, 3) for k, v in st.items()}, **binfo},
                     reasons=list(dict.fromkeys(reasons)), signature=sig)
    if completeness < 60:
        cand.reasons.append("границы клипа неидеальны — возможна неполная фраза")
    _finalize_quality(ctx, cand)
    return cand


def _finalize_quality(ctx: Ctx, cand: Candidate) -> None:
    w = ctx.settings.analysis.weights.get(cand.mode, {})
    num = den = 0.0
    for k in ("semantic", "humor", "reaction", "audio", "completeness", "visual"):
        if k in cand.scores and w.get(k, 0) > 0:
            num += w[k] * cand.scores[k]
            den += w[k]
    q = num / den if den else 0.0
    cand.details["base_quality"] = round(q, 2)
    if ctx.personal is not None:
        adj = ctx.personal.adjust(q, cand.scores, cand.mode)
        if abs(adj - q) > 0.01:
            cand.reasons.append(f"учтена ваша обратная связь (поправка {adj - q:+.1f})")
        q = adj
    cand.scores["quality"] = clamp(q, 0, 100)


# ---------------------------------------------------------------- отбор
def _jaccard(a: str, b: str) -> float:
    sa, sb = set(content_tokens(a)), set(content_tokens(b))
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def select_best(cands: list[Candidate], ctx: Ctx) -> list[Candidate]:
    c = ctx.settings.clip
    chosen: list[Candidate] = []
    for cand in sorted(cands, key=lambda x: -x.quality):
        cand.selected = False
        if cand.quality < c.min_score:
            continue
        skip = None
        for o in chosen:
            overlap = min(cand.end, o.end) - max(cand.start, o.start)
            if overlap > c.max_overlap:
                skip = "пересекается с более сильным клипом"
            elif signature_similarity(cand.signature, o.signature) > 0.7:
                skip = "визуальный дубль"
            elif _jaccard(cand.text, o.text) > 0.8:
                skip = "повтор по тексту"
            if skip:
                break
        if skip:
            cand.details["rejected"] = skip
            continue
        cand.selected = True
        chosen.append(cand)
        if len(chosen) >= c.max_clips:
            break
    return chosen


def find_candidates(ctx: Ctx, token: CancelToken | None = None,
                    progress: Callable[[float, str], None] | None = None) -> list[Candidate]:
    mode = ctx.settings.clip.mode
    if not ctx.units:
        raise AnalysisError("В видео не найдено речи для анализа.",
                            hint="Проверьте звуковую дорожку/язык или положите рядом файл субтитров .srt.")
    pinfo = parse_prompt(ctx.settings.clip.custom_prompt) if mode == "custom" else PromptInfo(set())
    if mode == "custom" and not ctx.settings.clip.custom_prompt.strip():
        raise AnalysisError("Для режима «Свой сценарий» нужно описать, какие моменты искать.")
    anchors = _anchor_scores(ctx, mode, pinfo)
    n_anchor = max(15, 5 * ctx.settings.clip.max_clips)
    picked = _pick_anchors(ctx, anchors, n_anchor, 6.0 if mode == "funny" else 12.0)
    ctx.log(f"Кандидатов-якорей: {len(picked)} из {len(ctx.units)} реплик")
    cands: list[Candidate] = []
    for k, ai in enumerate(picked):
        if token:
            token.raise_if_cancelled()
        built = build_episode(ctx, ai, mode)
        if built is not None:
            ep, binfo = built
            if sum(u.n_words for u in ep.units) >= 4 or not any(u.text for u in ep.units):
                cands.append(_score_candidate(ctx, ep, binfo, mode, pinfo))
        if progress:
            progress((k + 1) / max(1, len(picked)), f"Оценка эпизодов {k + 1}/{len(picked)}")
    cands = _merge_duplicates(cands)
    if ctx.llm is not None and cands:
        _llm_rerank(ctx, cands, mode, token)
    select_best(cands, ctx)
    cands.sort(key=lambda x: -x.quality)
    return cands


def _merge_duplicates(cands: list[Candidate]) -> list[Candidate]:
    cands = sorted(cands, key=lambda x: -x.quality)
    out: list[Candidate] = []
    for c in cands:
        dup = False
        for o in out:
            ov = min(c.end, o.end) - max(c.start, o.start)
            if ov > 0.6 * min(c.duration, o.duration):
                dup = True
                break
        if not dup:
            out.append(c)
    return out


def _llm_rerank(ctx: Ctx, cands: list[Candidate], mode: str, token: CancelToken | None) -> None:
    top = sorted(cands, key=lambda x: -x.quality)[:max(6, 3 * ctx.settings.clip.max_clips)]
    ctx.log(f"LLM оценивает {len(top)} лучших кандидатов…")
    ok = 0
    for c in top:
        if token:
            token.raise_if_cancelled()
        res = ctx.llm.rate(c.text, mode, ctx.settings.clip.custom_prompt)  # type: ignore[union-attr]
        if res is None:
            continue
        ok += 1
        c.scores["semantic"] = 0.5 * c.scores["semantic"] + 0.5 * res["score"]
        c.details["llm"] = res
        c.reasons.append(f"LLM ({res['score']:.0f}/100): {res['reason']}")
        _finalize_quality(ctx, c)
    if ok == 0:
        ctx.log("⚠ LLM недоступна или вернула неверный ответ — использована локальная оценка.")
