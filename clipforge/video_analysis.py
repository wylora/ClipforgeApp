"""Анализ видеоряда (OpenCV): смена сцен, динамика, лица, активность рта, хэши кадров.

Кадры декодирует FFmpeg в малом разрешении, поэтому анализ быстрый. Мимика оценивается косвенно —
по изменению области рта между соседними выборками и (опционально) каскаду улыбки Хаара. Это слабые
сигналы, они используются только вместе со звуком и текстом.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .ffmpeg_utils import MediaInfo, decode_frames
from .util import CancelToken

try:
    import cv2  # type: ignore
    HAVE_CV2 = True
except Exception:  # pragma: no cover
    cv2 = None
    HAVE_CV2 = False


@dataclass
class VideoFeatures:
    fps: float
    t0: float
    times: np.ndarray
    hist_diff: np.ndarray
    motion: np.ndarray
    dhash: np.ndarray              # uint64
    face_rows: np.ndarray          # K×6: sample, x, y, w, h (норм.), activity
    smile: np.ndarray
    scene_cuts: list[float]

    def _sl(self, a: float, b: float) -> slice:
        i = int(np.searchsorted(self.times, a, "left"))
        j = int(np.searchsorted(self.times, b, "right"))
        return slice(i, max(i, j))

    def stats(self, a: float, b: float) -> dict:
        s = self._sl(a, b)
        n = s.stop - s.start
        if n <= 0:
            return dict(motion=0.0, cuts=0, face_ratio=0.0, act_mean=0.0, act_max=0.0, smile=0.0, n=0)
        rows = self.face_rows[(self.face_rows[:, 0] >= s.start) & (self.face_rows[:, 0] < s.stop)] if len(self.face_rows) else np.zeros((0, 6))
        face_samples = len(set(rows[:, 0].astype(int))) if len(rows) else 0
        acts = rows[:, 5] if len(rows) else np.zeros(1)
        return dict(motion=float(self.motion[s].mean()), cuts=int(sum(a <= c <= b for c in self.scene_cuts)),
                    face_ratio=face_samples / n, act_mean=float(acts.mean()), act_max=float(acts.max()),
                    smile=float(self.smile[s].mean()), n=n)

    def faces_at(self, i: int) -> list[tuple[float, float, float, float, float]]:
        if not len(self.face_rows):
            return []
        r = self.face_rows[self.face_rows[:, 0] == i]
        return [tuple(x[1:6]) for x in r]

    def signature(self, a: float, b: float, k: int = 8) -> list[int]:
        s = self._sl(a, b)
        h = self.dhash[s]
        if len(h) == 0:
            return []
        pick = np.linspace(0, len(h) - 1, min(k, len(h))).astype(int)
        return [int(h[i]) for i in pick]

    def save(self, path: Path) -> None:
        np.savez_compressed(path, fps=self.fps, t0=self.t0, times=self.times, hist_diff=self.hist_diff,
                            motion=self.motion, dhash=self.dhash, face_rows=self.face_rows, smile=self.smile,
                            scene_cuts=np.array(self.scene_cuts))

    @classmethod
    def load(cls, path: Path) -> "VideoFeatures":
        z = np.load(path)
        return cls(float(z["fps"]), float(z["t0"]), z["times"], z["hist_diff"], z["motion"], z["dhash"],
                   z["face_rows"], z["smile"], [float(x) for x in z["scene_cuts"]])


def hamming(a: int, b: int) -> int:
    return bin(int(a) ^ int(b)).count("1")


def signature_similarity(a: list[int], b: list[int], thr: int = 6) -> float:
    """Доля кадров одного клипа, у которых есть почти идентичный кадр в другом."""
    if not a or not b:
        return 0.0
    hit = sum(1 for x in a if min(hamming(x, y) for y in b) <= thr)
    return hit / len(a)


def scan_video(path: str | Path, info: MediaInfo, fps: float = 2.0, start: float = 0.0, end: float | None = None,
               use_faces: bool = True, detect_smile: bool = False, width: int = 320,
               token: CancelToken | None = None, progress_cb: Callable[[float], None] | None = None) -> VideoFeatures:
    if not HAVE_CV2:
        raise RuntimeError("opencv-python не установлен")
    ar = info.height / max(1, info.width)
    w = width - width % 2
    h = max(2, int(round(w * ar / 2)) * 2)
    end = end if end is not None else info.duration
    dur = max(0.1, end - start)
    face_cc = smile_cc = None
    if use_faces:
        face_cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if detect_smile:
            smile_cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_smile.xml")
    times, hd, mo, hs, sm, rows = [], [], [], [], [], []
    prev_hist = prev_gray = None
    prev_faces: list[tuple[float, float, np.ndarray]] = []
    for i, frame in enumerate(decode_frames(path, fps, w, h, start=start, duration=dur, token=token)):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
        hist = hist / (hist.sum() + 1e-9)
        hd.append(0.0 if prev_hist is None else 0.5 * float(np.abs(hist - prev_hist).sum()))
        mo.append(0.0 if prev_gray is None else float(np.abs(gray.astype(np.int16) - prev_gray).mean() / 255.0))
        prev_hist, prev_gray = hist, gray.astype(np.int16)
        small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        bits = (small[:, 1:] > small[:, :-1]).flatten()
        hs.append(int(np.packbits(bits).view(">u8")[0]))
        times.append(start + i / fps)
        smile_hit = 0.0
        cur_faces: list[tuple[float, float, np.ndarray]] = []
        if face_cc is not None:
            found = face_cc.detectMultiScale(gray, 1.2, 5, minSize=(max(16, w // 20), max(16, w // 20)))
            for (x, y, fw, fh) in found:
                my0 = y + int(fh * 0.55)
                mouth = cv2.resize(gray[my0:y + fh, x:x + fw], (24, 12), interpolation=cv2.INTER_AREA).astype(np.int16)
                cx, cy = x + fw / 2, y + fh / 2
                act = 0.0
                if prev_faces:
                    d = [(abs(cx - pcx) + abs(cy - pcy), pm) for pcx, pcy, pm in prev_faces]
                    dist, pm = min(d, key=lambda z: z[0])
                    if dist < fw * 1.2:
                        act = float(np.abs(mouth - pm).mean() / 255.0)
                cur_faces.append((cx, cy, mouth))
                rows.append([i, x / w, y / h, fw / w, fh / h, act])
                if smile_cc is not None:
                    roi = gray[my0:y + fh, x:x + fw]
                    if len(smile_cc.detectMultiScale(roi, 1.7, 22, minSize=(fw // 4, fh // 10))):
                        smile_hit = 1.0
        prev_faces = cur_faces
        sm.append(smile_hit)
        if progress_cb and i % 20 == 0:
            progress_cb(min(1.0, (i / fps) / dur))
    hd_a = np.array(hd, dtype=np.float32)
    cuts: list[float] = []
    if len(hd_a) > 5:
        thr = max(0.35, float(hd_a.mean() + 4 * hd_a.std()))
        cuts = [float(times[k]) for k in np.where(hd_a > thr)[0]]
    return VideoFeatures(fps, start, np.array(times, dtype=np.float64), hd_a, np.array(mo, dtype=np.float32),
                         np.array(hs, dtype=np.uint64), np.array(rows, dtype=np.float32).reshape(-1, 6),
                         np.array(sm, dtype=np.float32), cuts)
