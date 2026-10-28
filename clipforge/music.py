"""Фонотека: встроенные сгенерированные треки (CC0), пользовательский каталог с лицензиями, выбор по настроению."""
from __future__ import annotations

import json
import logging
import zlib
from pathlib import Path

from .audio_synth import MOOD_TITLES, MOODS, gen_sfx, generate_track, write_wav
from .config import subdir

log = logging.getLogger("clipforge.music")
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}
MODE_MOOD = {"funny": "cheerful", "serious": "calm", "best": "energetic", "custom": "motivational"}


def builtin_dir() -> Path:
    return subdir("music") / "builtin"


def catalog_dir() -> Path:
    return subdir("music") / "catalog"


def ensure_builtin_library(progress_cb=None) -> dict[str, Path]:
    d = builtin_dir()
    d.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for i, mood in enumerate(MOODS):
        p = d / f"{mood}.wav"
        if not p.exists():
            write_wav(p, generate_track(mood))
        out[mood] = p
        if progress_cb:
            progress_cb((i + 1) / len(MOODS))
    (d / "catalog.json").write_text(json.dumps(
        [{"file": f"{m}.wav", "mood": m, "license": "CC0 — синтезировано кодом ClipForge",
          "source": "ClipForge audio_synth"} for m in MOODS], ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def ensure_sfx() -> dict[str, Path]:
    d = subdir("music") / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in ("pop", "whoosh", "impact"):
        p = d / f"{name}.wav"
        if not p.exists():
            write_wav(p, gen_sfx(name))
        out[name] = p
    return out


def verified_catalog() -> list[dict]:
    """Только треки пользовательского каталога, у которых указана лицензия."""
    f = catalog_dir() / "catalog.json"
    if not f.exists():
        return []
    try:
        items = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("catalog.json повреждён")
        return []
    out = []
    for it in items:
        p = catalog_dir() / it.get("file", "")
        if p.exists() and p.suffix.lower() in AUDIO_EXT and str(it.get("license", "")).strip():
            out.append({**it, "path": p})
    return out


def pick_music(mood: str, content_mode: str, seed_text: str = "") -> tuple[Path | None, str]:
    """Возвращает (путь, описание). Автовыбор использует только трек с подтверждённой лицензией."""
    if mood == "auto" or mood not in MOODS:
        mood = MODE_MOOD.get(content_mode, "calm")
    pool = [Path(i["path"]) for i in verified_catalog() if i.get("mood") == mood]
    label = f"каталог ({MOOD_TITLES.get(mood, mood)})"
    if not pool:
        lib = ensure_builtin_library()
        pool = [lib[mood]]
        label = f"встроенная подборка ({MOOD_TITLES.get(mood, mood)})"
    return pool[zlib.crc32(seed_text.encode("utf-8")) % len(pool)], label
