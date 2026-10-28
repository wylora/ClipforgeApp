"""Загрузка по ссылке через yt-dlp. Только для видео, которые пользователь вправе скачивать."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

from .errors import CancelledError, DependencyError, DownloadError
from .ffmpeg_utils import find_binary
from .util import CancelToken

log = logging.getLogger("clipforge.download")
URL_RE = re.compile(r"^https?://", re.I)


def is_url(s: str) -> bool:
    return bool(URL_RE.match(s.strip()))


def _yt():
    try:
        import yt_dlp  # type: ignore
        return yt_dlp
    except Exception:
        raise DependencyError("Не установлен yt-dlp — загрузка по ссылке недоступна.",
                              hint="Выполните: pip install yt-dlp. Локальные файлы работают без него.")


def _friendly(msg: str) -> str:
    low = msg.lower()
    table = [("private video", "Видео приватное — доступ закрыт."), ("video unavailable", "Видео недоступно или удалено."),
             ("sign in", "Для этого видео требуется вход в аккаунт (возрастное ограничение или приватность)."),
             ("403", "Сервер отклонил запрос (HTTP 403). Обновите yt-dlp: pip install -U yt-dlp."),
             ("unsupported url", "Эта ссылка не поддерживается."), ("urlopen error", "Нет соединения с интернетом."),
             ("geo", "Видео недоступно в вашем регионе.")]
    for k, v in table:
        if k in low:
            return v
    return "Не удалось загрузить видео: " + re.sub(r"\x1b\[[0-9;]*m", "", msg)[:300]


def fetch_info(url: str) -> dict:
    yt = _yt()
    try:
        with yt.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
                           "socket_timeout": 20}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise DownloadError(_friendly(str(e)), hint="Проверьте ссылку и подключение. Можно выбрать локальный файл.") from e
    return {"id": info.get("id"), "title": info.get("title", ""), "duration": float(info.get("duration") or 0),
            "uploader": info.get("uploader", ""), "width": info.get("width"), "height": info.get("height"),
            "filesize": info.get("filesize") or info.get("filesize_approx") or 0}


def download(url: str, dest: Path, token: CancelToken | None = None,
             progress_cb: Callable[[float, str], None] | None = None, max_height: int = 1080) -> tuple[Path, dict]:
    yt = _yt()
    dest.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if token and token.cancelled:
            raise CancelledError()
        if progress_cb and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = (d.get("downloaded_bytes", 0) / total) if total else 0.0
            progress_cb(min(1.0, frac), f"Загрузка {frac * 100:.0f}% · {d.get('_speed_str', '').strip()}")

    ff = find_binary("ffmpeg")
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "retries": 3, "socket_timeout": 20,
            "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b", "merge_output_format": "mp4",
            "outtmpl": str(dest / "%(id)s.%(ext)s"), "progress_hooks": [hook], "restrictfilenames": True}
    if ff:
        opts["ffmpeg_location"] = str(Path(ff).parent)
    try:
        with yt.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except CancelledError:
        raise
    except Exception as e:
        if token and token.cancelled:
            raise CancelledError() from e
        raise DownloadError(_friendly(str(e)), hint="Проверьте ссылку и подключение. Можно выбрать локальный файл.") from e
    files = [p for p in dest.glob(f"{info.get('id')}.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not files:
        raise DownloadError("Загрузка завершилась, но файл не найден.")
    return max(files, key=lambda p: p.stat().st_size), {"title": info.get("title", ""), "id": info.get("id")}
