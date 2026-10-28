"""Названия, описание и хэштеги для клипов (локальные эвристики + опционально LLM)."""
from __future__ import annotations

import re
from collections import Counter

from .text_features import content_tokens, stem


def detect_lang(text: str) -> str:
    letters = re.findall(r"[^\W\d_]", text)
    if not letters:
        return "unknown"
    cyr = sum(1 for c in letters if "а" <= c.lower() <= "я" or c.lower() == "ё")
    return "ru" if cyr / len(letters) > 0.5 else "en"


def keywords(text: str, n: int = 5) -> list[str]:
    toks = content_tokens(text)
    groups: dict[str, Counter] = {}
    for t in toks:
        groups.setdefault(stem(t), Counter())[t] += 1
    ranked = sorted(groups.items(), key=lambda kv: -sum(kv[1].values()))
    return [c.most_common(1)[0][0] for _, c in ranked[:n]]


def shorten(text: str, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", text).strip(" -—.,;:!?…\"'«»")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-—") + "…"


def make_metadata(text: str, mode: str, anchor_text: str = "", llm=None) -> dict:
    lang = detect_lang(text)
    kws = keywords(text, 6)
    if llm is not None:
        res = llm.metadata(text, mode)
        if res and res.get("titles"):
            tags = [re.sub(r"\W", "", str(t)) for t in res.get("hashtags", [])][:8]
            return {"titles": [shorten(str(t), 70) for t in res["titles"]][:3], "description": str(res.get("description", ""))[:300],
                    "hashtags": [t for t in tags if t] or ["shorts"]}
    base = shorten(anchor_text or text, 60)
    k = kws[0] if kws else ""
    ru = lang != "en"
    if mode == "funny":
        titles = [base, f"Смешной момент: {k}" if ru and k else f"Funny moment: {k}", "Без этого не обойтись 😂" if ru else "You can't miss this 😂"]
    elif mode == "serious":
        titles = [base, f"Главное про {k}" if ru and k else f"What matters about {k}", "Запомните это" if ru else "Remember this"]
    else:
        titles = [base, f"Это стоит увидеть: {k}" if ru and k else f"Worth watching: {k}", "Лучший момент" if ru else "Best moment"]
    titles = [shorten(t, 70) for t in dict.fromkeys(t for t in titles if t.strip(": "))]
    desc = shorten(text, 180)
    tags = ["shorts", "reels", "tiktok"] + kws[:4] + (["юмор" if ru else "funny"] if mode == "funny" else ["факты" if ru else "facts"] if mode == "serious" else [])
    return {"titles": titles, "description": desc, "hashtags": list(dict.fromkeys(tags))}
