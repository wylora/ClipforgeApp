"""Транскрибация (faster-whisper), импорт готовых субтитров, смысловые единицы, смена говорящих."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

import numpy as np

from .config import AnalysisSettings, subdir
from .errors import DependencyError, TranscriptionError
from .models import Segment, Transcript, Unit, Word
from .resources import cuda_available
from .text_features import TERMINAL_RE
from .util import CancelToken

log = logging.getLogger("clipforge.transcribe")

_TS = re.compile(r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)")


def _ts(s: str) -> float:
    m = _TS.search(s)
    if not m:
        raise ValueError(s)
    h, mi, se, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")[:3]) / 1000


def parse_subtitle_file(path: Path) -> Transcript:
    """SRT/VTT → Transcript. Времена слов распределяются равномерно внутри реплики."""
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    segs: list[Segment] = []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n")):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        for i, l in enumerate(lines):
            if "-->" in l:
                try:
                    a, b = l.split("-->")[:2]
                    start, end = _ts(a), _ts(b.split()[0] if b.split() else b)
                except ValueError:
                    break
                text = re.sub(r"<[^>]+>", "", " ".join(lines[i + 1:])).strip()
                if text and end > start:
                    segs.append(Segment(start, end, text, spread_words(text, start, end)))
                break
    if not segs:
        raise TranscriptionError(f"Не удалось разобрать файл субтитров {path.name}.")
    return Transcript("unknown", segs[-1].end, segs, source=f"subtitle:{path.name}")


def spread_words(text: str, start: float, end: float) -> list[Word]:
    toks = text.split()
    if not toks:
        return []
    weights = np.array([max(1, len(re.sub(r"\W", "", t))) for t in toks], dtype=float)
    edges = start + np.concatenate([[0], np.cumsum(weights)]) / weights.sum() * (end - start)
    return [Word(float(edges[i]), float(edges[i + 1]), t) for i, t in enumerate(toks)]


def find_sidecar(video: Path) -> Path | None:
    for ext in (".srt", ".vtt"):
        for cand in (video.with_suffix(ext), *video.parent.glob(video.stem + ".*" + ext)):
            if cand.exists():
                return cand
    return None


def model_cache_dir() -> Path:
    return subdir("models")


def whisper_model_present(size: str) -> bool:
    d = model_cache_dir()
    return any(d.glob(f"models--*faster-whisper-{size}*")) or (d / size).exists()


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def transcribe_audio(audio_path: Path, cfg: AnalysisSettings, token: CancelToken | None = None,
                     progress_cb: Callable[[float], None] | None = None, duration: float | None = None,
                     language: str | None = None, task: str = "transcribe") -> Transcript:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        raise DependencyError(
            "Не установлен faster-whisper — нечем распознавать речь.",
            hint="Выполните: pip install faster-whisper. Либо положите файл .srt/.vtt с тем же именем рядом с видео.")
    if not whisper_model_present(cfg.whisper_model) and not cfg.whisper_consent:
        raise DependencyError(
            f"Модель Whisper «{cfg.whisper_model}» не загружена, а согласие на скачивание не дано.",
            hint="Подтвердите загрузку модели в окне запроса или в настройках («Анализ»).")
    device = cfg.device if cfg.device != "auto" else ("cuda" if cuda_available() else "cpu")
    compute = "float16" if device == "cuda" else "int8"
    threads = 0
    if cfg.ram_limit_mb and cfg.ram_limit_mb < 4000:
        threads = 2
    log.info("Whisper: модель=%s устройство=%s тип=%s", cfg.whisper_model, device, compute)
    try:
        model = WhisperModel(cfg.whisper_model, device=device, compute_type=compute, cpu_threads=threads,
                             download_root=str(model_cache_dir()))
        lang = language or (None if cfg.language == "auto" else cfg.language)
        seg_iter, info = model.transcribe(str(audio_path), language=lang, task=task, word_timestamps=True,
                                          vad_filter=True, beam_size=1 if device == "cpu" else 5,
                                          condition_on_previous_text=False)
        total = duration or getattr(info, "duration", 0) or 1.0
        out: list[Segment] = []
        for s in seg_iter:
            if token:
                token.raise_if_cancelled()
            words = [Word(float(w.start), float(w.end), w.word.strip(), float(getattr(w, "probability", 1.0)))
                     for w in (s.words or []) if w.word.strip()]
            text = s.text.strip()
            if not words and text:
                words = spread_words(text, float(s.start), float(s.end))
            out.append(Segment(float(s.start), float(s.end), text, words))
            if progress_cb:
                progress_cb(min(1.0, float(s.end) / total))
        return Transcript(getattr(info, "language", lang or "unknown"), float(total), out, source=f"whisper:{cfg.whisper_model}")
    except (DependencyError, TranscriptionError):
        raise
    except Exception as e:
        if type(e).__name__ == "CancelledError":
            raise
        raise TranscriptionError(f"Ошибка распознавания речи: {e}", hint="Попробуйте модель поменьше (tiny/base) или CPU.") from e


# ---------------------------------------------------------------- смысловые единицы
def build_units(tr: Transcript, max_words: int = 32, pause_split: float = 0.8) -> list[Unit]:
    units: list[Unit] = []
    cur: list[Word] = []

    def flush() -> None:
        if cur:
            units.append(Unit(len(units), cur[0].start, max(cur[-1].end, cur[0].start + 0.05),
                              " ".join(w.text for w in cur).strip(), list(cur)))
            cur.clear()

    for seg in tr.segments:
        words = seg.words or spread_words(seg.text, seg.start, seg.end)
        for w in words:
            if cur and (w.start - cur[-1].end > pause_split or len(cur) >= max_words):
                flush()
            cur.append(w)
            if TERMINAL_RE.search(w.text) and len(cur) >= 2:
                flush()
        flush()
    return units


def pseudo_units_from_audio(audio) -> list[Unit]:
    """Резервный вариант без речи: фразы по энергии звука (текста нет)."""
    out: list[Unit] = []
    if audio is None or not audio.n:
        return out
    active = audio.rms_db > audio.silence_db
    start = None
    from .audio_analysis import HOP
    for i, a in enumerate(list(active) + [False]):
        if a and start is None:
            start = i
        elif not a and start is not None:
            if (i - start) * HOP >= 0.4:
                out.append(Unit(len(out), start * HOP, i * HOP, "", []))
            start = None
    merged: list[Unit] = []
    for u in out:
        if merged and u.start - merged[-1].end < 0.5:
            merged[-1].end = u.end
        else:
            u.idx = len(merged)
            merged.append(u)
    return merged


def assign_speakers(units: list[Unit], audio, max_speakers: int = 4) -> int:
    """Грубая эвристика смены говорящего по высоте тона и тембру. Возвращает число найденных «голосов»."""
    if audio is None or not audio.n or not units:
        return 0
    centers: list[np.ndarray] = []
    counts: list[int] = []
    for u in units:
        s = audio.sl(u.start, u.end)
        f0 = audio.f0[s]
        f0 = f0[f0 > 0]
        if len(f0) < 5:
            u.speaker = units[u.idx - 1].speaker if u.idx else 0
            continue
        vec = np.array([np.log(np.median(f0)) * 4, float(np.mean(audio.centroid[s])) / 1500])
        if not centers:
            centers.append(vec); counts.append(1); u.speaker = 0
            continue
        d = [float(np.linalg.norm(vec - c)) for c in centers]
        k = int(np.argmin(d))
        if d[k] > 0.9 and len(centers) < max_speakers:
            centers.append(vec); counts.append(1); u.speaker = len(centers) - 1
        else:
            counts[k] += 1
            centers[k] = centers[k] + (vec - centers[k]) / counts[k]
            u.speaker = k
    return len(centers)
