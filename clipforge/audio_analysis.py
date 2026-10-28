"""Анализ аудио на чистом NumPy: громкость, тон, спектр, паузы и эвристика смеха.

Аудио декодируется потоком через FFmpeg (16 кГц, моно), поэтому длинные видео не требуют много памяти.
Детектор смеха — эвристика (периодичная амплитудная модуляция 3–7 Гц с выраженной глубиной);
он даёт вероятность, а не уверенный ответ, и всегда комбинируется с другими сигналами.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from .ffmpeg_utils import decode_pcm_stream
from .util import CancelToken

SR = 16000
FRAME = 1024
HOPN = 800
HOP = HOPN / SR          # 0.05 с


class AudioFeatures:
    def __init__(self, rms_db, centroid, flatness, f0, voiced, laugh=None):
        self.rms_db = np.asarray(rms_db, dtype=np.float32)
        self.centroid = np.asarray(centroid, dtype=np.float32)
        self.flatness = np.asarray(flatness, dtype=np.float32)
        self.f0 = np.asarray(f0, dtype=np.float32)
        self.voiced = np.asarray(voiced, dtype=np.float32)
        self.n = len(self.rms_db)
        self.duration = self.n * HOP
        if self.n:
            p10, p90 = (float(x) for x in np.percentile(self.rms_db, [10, 90]))
            # порог тишины относителен уровню шумового пола, поэтому работает и при фоновом шуме
            self.silence_db = max(-62.0, p10 + min(8.0, 0.3 * (p90 - p10)))
            speech = self.rms_db[self.rms_db > self.silence_db]
        else:
            self.silence_db, speech = -62.0, np.zeros(0)
        self.baseline_db = float(np.mean(speech)) if len(speech) else -40.0
        self.loud_std = float(np.std(speech)) if len(speech) > 5 else 3.0
        self.loud_std = max(self.loud_std, 1.5)
        self.laugh = np.asarray(laugh, dtype=np.float32) if laugh is not None else _compute_laughter(self)

    # ------------------------------------------------------------ доступ
    def idx(self, t: float) -> int:
        return int(np.clip(round(t / HOP), 0, self.n))

    def sl(self, a: float, b: float) -> slice:
        i = self.idx(a)
        j = max(i + 1, self.idx(b))
        return slice(i, min(j, self.n))

    def stats(self, a: float, b: float) -> dict:
        s = self.sl(a, b)
        r = self.rms_db[s]
        if len(r) == 0:
            return dict(mean_db=-80.0, peak_db=-80.0, peak_z=0.0, loud_std=0.0, pitch_std_st=0.0,
                        silence_ratio=1.0, laugh_max=0.0, laugh_mean=0.0, voiced_ratio=0.0)
        f = self.f0[s]
        f = f[f > 0]
        pitch_std = float(np.std(12 * np.log2(f / np.median(f)))) if len(f) > 5 else 0.0
        return dict(
            mean_db=float(r.mean()), peak_db=float(r.max()),
            peak_z=float((r.max() - self.baseline_db) / self.loud_std),
            loud_std=float(r.std()), pitch_std_st=pitch_std,
            silence_ratio=float(np.mean(r < self.silence_db)),
            laugh_max=float(self.laugh[s].max()), laugh_mean=float(self.laugh[s].mean()),
            voiced_ratio=float(np.mean(self.voiced[s] > 0.3)))

    def laugh_in(self, a: float, b: float) -> tuple[float, float]:
        s = self.sl(a, b)
        seg = self.laugh[s]
        return (float(seg.max()), float(seg.mean())) if len(seg) else (0.0, 0.0)

    def silences(self, a: float, b: float, min_len: float = 0.4) -> list[tuple[float, float]]:
        s = self.sl(a, b)
        mask = self.rms_db[s] < self.silence_db
        out, start = [], None
        for i, m in enumerate(mask):
            if m and start is None:
                start = i
            elif not m and start is not None:
                if (i - start) * HOP >= min_len:
                    out.append(((s.start + start) * HOP, (s.start + i) * HOP))
                start = None
        if start is not None and (len(mask) - start) * HOP >= min_len:
            out.append(((s.start + start) * HOP, (s.start + len(mask)) * HOP))
        return out

    def nearest_quiet(self, t: float, radius: float = 0.5, lo: float | None = None, hi: float | None = None) -> float:
        """Ближайшая к t «тихая» точка (для аккуратного начала/конца клипа)."""
        a, b = t - radius, t + radius
        if lo is not None:
            a = max(a, lo)
        if hi is not None:
            b = min(b, hi)
        if b <= a:
            return t
        s = self.sl(a, b)
        r = self.rms_db[s]
        if len(r) == 0:
            return t
        quiet = np.where(r < self.silence_db + 3)[0]
        if len(quiet):
            cand = (s.start + quiet) * HOP
            return float(cand[np.argmin(np.abs(cand - t))])
        return float((s.start + int(np.argmin(r))) * HOP)

    # ------------------------------------------------------------ кэш
    def save(self, path: Path) -> None:
        np.savez_compressed(path, rms_db=self.rms_db, centroid=self.centroid, flatness=self.flatness,
                            f0=self.f0, voiced=self.voiced, laugh=self.laugh)

    @classmethod
    def load(cls, path: Path) -> "AudioFeatures":
        z = np.load(path)
        return cls(z["rms_db"], z["centroid"], z["flatness"], z["f0"], z["voiced"], z["laugh"])


def _compute_laughter(af: AudioFeatures) -> np.ndarray:
    n = af.n
    out = np.zeros(n, dtype=np.float32)
    W, STEP = 40, 5                      # окно 2 с, шаг 0.25 с
    if n < W + 1:
        return out
    env = (10 ** (af.rms_db / 20.0)).astype(np.float32)
    starts = np.arange(0, n - W + 1, STEP)
    idx = starts[:, None] + np.arange(W)[None, :]
    win = env[idx]
    mean = win.mean(axis=1) + 1e-9
    x = win - mean[:, None]
    depth = x.std(axis=1) / mean
    xh = x * np.hanning(W)[None, :]
    ac = np.fft.irfft(np.abs(np.fft.rfft(xh, n=2 * W, axis=1)) ** 2, axis=1)[:, :10]
    reg = ac[:, 3:8].max(axis=1) / (ac[:, 0] + 1e-12)          # регулярность на 3–7 Гц
    lvl_db = 20 * np.log10(mean)
    level = np.clip((lvl_db - af.silence_db) / max(af.baseline_db - af.silence_db, 6.0), 0, 1)
    val = np.clip((reg - 0.38) / 0.40, 0, 1) * np.clip(depth / 0.7, 0, 1) * level
    half = STEP * 2                       # значение окна относится к его центру ±0.5 с
    for k, s in enumerate(starts):
        if val[k] > 0:
            c = s + W // 2
            np.maximum(out[c - half:c + half], val[k], out=out[c - half:c + half])
    active = (af.rms_db > af.silence_db).astype(np.float32)
    kernel = np.ones(8, dtype=np.float32)
    active = (np.convolve(active, kernel, mode="same") > 0).astype(np.float32)
    return out * active


def analyze_audio(path: str | Path, token: CancelToken | None = None,
                  progress_cb: Callable[[float], None] | None = None, total_duration: float | None = None) -> AudioFeatures:
    win = np.hanning(FRAME).astype(np.float32)
    freqs = np.fft.rfftfreq(FRAME, 1 / SR).astype(np.float32)
    lag_lo, lag_hi = SR // 400, SR // 60
    buf = np.zeros(0, dtype=np.int16)
    rms_l, cen_l, flat_l, f0_l, voi_l = [], [], [], [], []
    done = 0
    for chunk in decode_pcm_stream(path, SR, token=token):
        buf = np.concatenate([buf, chunk])
        done += len(chunk)
        if len(buf) >= FRAME:
            n = (len(buf) - FRAME) // HOPN + 1
            idx = np.arange(n)[:, None] * HOPN + np.arange(FRAME)[None, :]
            fr = buf[idx].astype(np.float32) / 32768.0
            rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
            rms_db = 20 * np.log10(rms)
            w = fr * win
            spec = np.abs(np.fft.rfft(w, axis=1)) + 1e-9
            centroid = (spec * freqs).sum(axis=1) / spec.sum(axis=1)
            flat = np.exp(np.log(spec).mean(axis=1)) / spec.mean(axis=1)
            ac = np.fft.irfft(spec ** 2, n=FRAME, axis=1)[:, :lag_hi + 1]
            ac = ac / (ac[:, :1] + 1e-12)
            seg = ac[:, lag_lo:lag_hi + 1]
            best = seg.argmax(axis=1)
            strength = seg[np.arange(n), best]
            f0 = SR / (best + lag_lo)
            f0 = np.where((strength > 0.35) & (rms_db > -50), f0, 0.0)
            rms_l.append(rms_db); cen_l.append(centroid); flat_l.append(flat); f0_l.append(f0)
            voi_l.append(np.where(rms_db > -50, strength, 0.0))
            buf = buf[n * HOPN:]
        if progress_cb and total_duration:
            progress_cb(min(1.0, done / SR / total_duration))
    if not rms_l:
        return AudioFeatures([], [], [], [], [])
    cat = lambda l: np.concatenate(l).astype(np.float32)
    return AudioFeatures(cat(rms_l), cat(cen_l), cat(flat_l), cat(f0_l), cat(voi_l))
