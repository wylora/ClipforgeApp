"""Обёртки над FFmpeg/ffprobe: поиск, probe, запуск с прогрессом и отменой, декодирование потоков."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from .errors import DependencyError, FFmpegError, InvalidMediaError
from .util import CancelToken

log = logging.getLogger("clipforge.ffmpeg")
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_cache: dict[str, object] = {}


def set_ffmpeg_dir(path: str) -> None:
    if path:
        os.environ["CLIPFORGE_FFMPEG_DIR"] = path
    else:
        os.environ.pop("CLIPFORGE_FFMPEG_DIR", None)
    _cache.clear()


def find_binary(name: str) -> str | None:
    exe = name + (".exe" if sys.platform == "win32" else "")
    env = os.environ.get(f"CLIPFORGE_{name.upper()}")
    if env and Path(env).exists():
        return env
    d = os.environ.get("CLIPFORGE_FFMPEG_DIR")
    if d and (Path(d) / exe).exists():
        return str(Path(d) / exe)
    found = shutil.which(name)
    if found:
        return found
    local = Path(__file__).resolve().parent.parent / "bin" / exe
    if local.exists():
        return str(local)
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg  # type: ignore
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return None


def require_binary(name: str) -> str:
    p = find_binary(name)
    if not p:
        raise DependencyError(
            f"Не найден {name}.",
            hint="Установите FFmpeg (см. README: winget install Gyan.FFmpeg) и перезапустите программу, "
                 "либо укажите папку с ffmpeg.exe в настройках.")
    return p


def ffmpeg_has_filter(name: str) -> bool:
    key = f"filter:{name}"
    if key not in _cache:
        try:
            r = subprocess.run([require_binary("ffmpeg"), "-hide_banner", "-filters"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", creationflags=_NOWIN, timeout=30)
            _cache[key] = any(line.split()[1:2] == [name] for line in r.stdout.splitlines() if line.strip())
        except Exception:
            _cache[key] = False
    return bool(_cache[key])


def amix_supports_normalize() -> bool:
    if "amix_norm" not in _cache:
        try:
            r = subprocess.run([require_binary("ffmpeg"), "-hide_banner", "-h", "filter=amix"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", creationflags=_NOWIN, timeout=30)
            _cache["amix_norm"] = "normalize" in r.stdout
        except Exception:
            _cache["amix_norm"] = False
    return bool(_cache["amix_norm"])


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    size_bytes: int
    has_audio: bool
    has_video: bool
    video_codec: str = ""
    title: str = ""

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}" if self.width else "—"


def _parse_fps(s: str) -> float:
    try:
        if "/" in s:
            a, b = s.split("/")
            return float(a) / float(b) if float(b) else 0.0
        return float(s)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    p = Path(path)
    if not p.exists():
        raise InvalidMediaError(f"Файл не найден: {p}")
    cmd = [require_binary("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=_NOWIN, timeout=90)
    except subprocess.TimeoutExpired:
        raise InvalidMediaError("ffprobe завис при чтении файла.", hint="Файл может быть повреждён.")
    if r.returncode != 0:
        raise InvalidMediaError(f"Не удалось прочитать файл «{p.name}». Возможно, он повреждён или формат не поддерживается.",
                                details=r.stderr[-500:])
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise InvalidMediaError(f"ffprobe вернул некорректный ответ для «{p.name}».")
    streams = data.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    fmt = data.get("format", {})
    dur = 0.0
    for cand in (fmt.get("duration"), (vs or {}).get("duration")):
        try:
            dur = float(cand)
            break
        except (TypeError, ValueError):
            continue
    return MediaInfo(
        path=str(p), duration=dur, width=int((vs or {}).get("width", 0)), height=int((vs or {}).get("height", 0)),
        fps=_parse_fps((vs or {}).get("avg_frame_rate", "0")) or _parse_fps((vs or {}).get("r_frame_rate", "0")),
        size_bytes=int(fmt.get("size", p.stat().st_size)), has_audio=has_audio, has_video=vs is not None,
        video_codec=(vs or {}).get("codec_name", ""), title=(fmt.get("tags", {}) or {}).get("title", p.stem))


def run_ffmpeg(args: list[str], token: CancelToken | None = None, duration: float | None = None,
               on_progress: Callable[[float], None] | None = None, cwd: str | Path | None = None) -> None:
    """Запускает FFmpeg. Прогресс 0..1 (если задана duration). Отмена убивает процесс."""
    cmd = [require_binary("ffmpeg"), "-hide_banner", "-y", "-nostdin", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", *args]
    log.debug("ffmpeg: %s", " ".join(cmd))
    if token:
        token.raise_if_cancelled()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", cwd=str(cwd) if cwd else None, creationflags=_NOWIN)
    if token:
        token.register(proc)
    err_lines: list[str] = []

    def _drain() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            err_lines.append(line)
            del err_lines[:-60]

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            if on_progress and duration and (line.startswith("out_time_us=") or line.startswith("out_time_ms=")):
                try:
                    sec = int(line.split("=", 1)[1]) / 1_000_000
                    on_progress(max(0.0, min(1.0, sec / duration)))
                except ValueError:
                    pass
        proc.wait()
    finally:
        if token:
            token.unregister(proc)
        if proc.poll() is None:
            proc.kill()
        t.join(timeout=2)
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()  # type: ignore[union-attr]
            except Exception:
                pass
    if token:
        token.raise_if_cancelled()
    if proc.returncode != 0:
        tail = "".join(err_lines)[-1500:]
        raise FFmpegError("FFmpeg завершился с ошибкой при обработке видео.", details=tail,
                          hint="Подробности в логе. Часто помогает другой формат или пересборка клипа.")


def decode_pcm_stream(path: str | Path, sr: int = 16000, start: float | None = None, duration: float | None = None,
                      token: CancelToken | None = None, chunk_sec: float = 10.0) -> Iterator[np.ndarray]:
    """Потоково декодирует аудио в моно int16 (экономит память на длинных видео)."""
    cmd = [require_binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NOWIN)
    if token:
        token.register(proc)
    nbytes = int(sr * chunk_sec) * 2
    try:
        while True:
            if token:
                token.raise_if_cancelled()
            buf = proc.stdout.read(nbytes)  # type: ignore[union-attr]
            if not buf:
                break
            if len(buf) % 2:
                buf = buf[:-1]
            yield np.frombuffer(buf, dtype=np.int16)
    finally:
        if token:
            token.unregister(proc)
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        except Exception:
            pass


def decode_frames(path: str | Path, fps: float, width: int, height: int, start: float | None = None,
                  duration: float | None = None, token: CancelToken | None = None) -> Iterator[np.ndarray]:
    """Кадры BGR (height×width×3) с заданной частотой."""
    cmd = [require_binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(path), "-an", "-vf", f"fps={fps},scale={width}:{height}", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NOWIN)
    if token:
        token.register(proc)
    fsize = width * height * 3
    try:
        while True:
            if token:
                token.raise_if_cancelled()
            buf = proc.stdout.read(fsize)  # type: ignore[union-attr]
            if len(buf) < fsize:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
    finally:
        if token:
            token.unregister(proc)
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        except Exception:
            pass


def extract_audio_wav(src: str | Path, out: str | Path, token: CancelToken | None = None,
                      on_progress: Callable[[float], None] | None = None, duration: float | None = None) -> None:
    run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
               token=token, duration=duration, on_progress=on_progress)


def make_thumbnail(src: str | Path, t: float, out: str | Path, width: int = 360) -> bool:
    try:
        run_ffmpeg(["-ss", f"{max(0, t):.2f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out)])
        return Path(out).exists() and Path(out).stat().st_size > 0
    except Exception as e:  # миниатюра не критична
        log.warning("Не удалось создать миниатюру: %s", e)
        return False
