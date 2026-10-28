"""Проверка зависимостей и ресурсов (диск, память)."""
from __future__ import annotations

import importlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import DiskSpaceError
from .ffmpeg_utils import ffmpeg_has_filter, find_binary


@dataclass
class DepStatus:
    name: str
    ok: bool
    required: bool
    note: str


def _has(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


def check_dependencies() -> list[DepStatus]:
    ff, fp = find_binary("ffmpeg"), find_binary("ffprobe")
    res = [
        DepStatus("FFmpeg", bool(ff), True, ff or "не найден — нужен для всей обработки"),
        DepStatus("ffprobe", bool(fp), True, fp or "не найден (идёт вместе с FFmpeg)"),
        DepStatus("FFmpeg: фильтр ass (субтитры)", bool(ff) and ffmpeg_has_filter("ass"), False,
                  "нужна сборка FFmpeg с libass (gyan.dev full/essentials)"),
        DepStatus("numpy", _has("numpy"), True, "анализ данных"),
        DepStatus("PySide6", _has("PySide6"), True, "графический интерфейс"),
        DepStatus("opencv-python", _has("cv2"), False, "анализ видеоряда, лица, умный кроп (без него — центральный кроп)"),
        DepStatus("faster-whisper", _has("faster_whisper"), False, "распознавание речи (без него — только готовые .srt/.vtt)"),
        DepStatus("yt-dlp", _has("yt_dlp"), False, "загрузка по ссылке (локальные файлы работают без него)"),
        DepStatus("psutil", _has("psutil"), False, "контроль памяти"),
    ]
    return res


def check_disk_space(path: str | Path, needed_bytes: int) -> None:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(p).free
    if free < needed_bytes:
        raise DiskSpaceError(
            f"Недостаточно места на диске: свободно {free / 1e9:.1f} ГБ, нужно около {needed_bytes / 1e9:.1f} ГБ.",
            hint="Освободите место или выберите другую папку для результатов.")


def available_ram_mb() -> int | None:
    try:
        import psutil  # type: ignore
        return int(psutil.virtual_memory().available / 1e6)
    except Exception:
        return None


def cuda_available() -> bool:
    try:
        import ctranslate2  # type: ignore
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
