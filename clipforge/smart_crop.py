"""Умное кадрирование: слежение за лицом/активным говорящим и плавный путь кропа для FFmpeg."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import ASPECTS
from .ffmpeg_utils import MediaInfo
from .util import CancelToken
from .video_analysis import HAVE_CV2, scan_video

log = logging.getLogger("clipforge.crop")


@dataclass
class CropPlan:
    mode: str                                   # crop | fit | none
    src_w: int = 0
    src_h: int = 0
    cw: int = 0
    ch: int = 0
    axis: str = "x"                             # какая ось отслеживается
    keys: list[tuple[float, float]] = field(default_factory=list)   # (t, смещение левого/верхнего края)
    face_bottom: float | None = None            # низ лиц в долях высоты кадра результата
    note: str = ""


def piecewise_expr(points: list[tuple[float, float]]) -> str:
    """Кусочно-линейное выражение от t для фильтра crop."""
    if not points:
        return "0"
    if len(points) == 1:
        return f"{points[0][1]:.0f}"
    expr = f"{points[-1][1]:.1f}"
    for i in range(len(points) - 2, -1, -1):
        t0, v0 = points[i]
        t1, v1 = points[i + 1]
        seg = f"({v0:.1f}+({v1 - v0:.2f})*(t-{t0:.2f})/{max(t1 - t0, 0.01):.2f})"
        expr = f"if(lt(t,{t1:.2f}),{seg},{expr})"
    return expr


def _simplify(points: list[tuple[float, float]], tol: float = 4.0, max_pts: int = 36) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        (t0, v0), (t1, v1), (t2, v2) = out[-1], points[i], points[i + 1]
        interp = v0 + (v2 - v0) * (t1 - t0) / max(t2 - t0, 1e-6)
        if abs(interp - v1) > tol:
            out.append(points[i])
    out.append(points[-1])
    while len(out) > max_pts:
        out = out[::2] + ([out[-1]] if len(out) % 2 == 0 else [])
    return out


def plan_crop(src: str | Path, info: MediaInfo, start: float, end: float, aspect: str, mode: str = "auto",
              manual_x: float = 0.5, use_faces: bool = True, token: CancelToken | None = None) -> CropPlan:
    a, b = ASPECTS.get(aspect, (9, 16))
    ar = a / b
    sw, sh = info.width, info.height
    src_ar = sw / max(sh, 1)
    if mode == "fit":
        return CropPlan("fit", sw, sh, note="без обрезки, размытый фон")
    if abs(src_ar - ar) < 0.01:
        return CropPlan("none", sw, sh, sw, sh)
    if src_ar > ar:
        ch, cw, axis = sh, int(round(sh * ar / 2) * 2), "x"
        span, frac = sw - cw, cw / sw
    else:
        cw, ch, axis = sw, int(round(sw / ar / 2) * 2), "y"
        span, frac = sh - ch, ch / sh
    plan = CropPlan("crop", sw, sh, min(cw, sw), min(ch, sh), axis)
    total = sw if axis == "x" else sh
    crop_len = plan.cw if axis == "x" else plan.ch
    dur = end - start

    def to_off(center_norm: float) -> float:
        return float(np.clip(center_norm * total - crop_len / 2, 0, span))

    if mode == "manual":
        plan.keys = [(0.0, to_off(manual_x))]
        plan.note = "ручное кадрирование"
        return plan
    if mode == "center" or not (use_faces and HAVE_CV2):
        plan.keys = [(0.0, to_off(0.5))]
        plan.note = "центральный кроп" + ("" if mode == "center" else " (анализ лиц недоступен)")
        return plan
    try:
        vf = scan_video(src, info, fps=5.0, start=start, end=end, use_faces=True, width=480, token=token)
    except Exception as e:
        if type(e).__name__ == "CancelledError":
            raise
        log.warning("Анализ лиц для кропа не удался: %s", e)
        plan.keys = [(0.0, to_off(0.5))]
        plan.note = "центральный кроп (ошибка анализа лиц)"
        return plan
    centers: list[float | None] = []
    bottoms: list[float] = []
    n = len(vf.times)
    for i in range(n):
        faces = vf.faces_at(i)
        if not faces:
            centers.append(None)
            continue
        faces.sort(key=lambda f: -(f[2] * f[3]))
        top = faces[:3]
        pos = [(f[0] + f[2] / 2) if axis == "x" else (f[1] + f[3] / 2) for f in top]
        ext = [(f[0], f[0] + f[2]) if axis == "x" else (f[1], f[1] + f[3]) for f in top]
        c = pos[0]
        if len(top) >= 2:
            lo, hi = min(e[0] for e in ext[:2]), max(e[1] for e in ext[:2])
            if hi - lo <= frac * 0.9:
                c = (lo + hi) / 2                              # обе главные фигуры помещаются
            else:
                k = int(np.argmax([f[4] for f in top]))        # активный говорящий (движение рта)
                c = pos[k] if top[k][4] > 0.01 else pos[0]
        centers.append(c)
        if axis == "x":
            bottoms.append(max(f[1] + f[3] for f in top[:2]))
    valid = [c for c in centers if c is not None]
    if not valid:
        plan.keys = [(0.0, to_off(0.5))]
        plan.note = "лица не найдены — центральный кроп"
        return plan
    arr = np.array([c if c is not None else np.nan for c in centers], dtype=float)
    idx = np.arange(n)
    ok = ~np.isnan(arr)
    arr = np.interp(idx, idx[ok], arr[ok])
    k = min(7, n)
    pad = np.pad(arr, (k // 2, k - 1 - k // 2), mode="edge")
    arr = np.convolve(pad, np.ones(k) / k, mode="valid")
    cur, smooth = arr[0], []
    dead = 0.05 * frac
    for v in arr:                                              # мёртвая зона: камера не дёргается
        if abs(v - cur) > dead:
            cur += (v - cur) * 0.5
        smooth.append(cur)
    step = 3
    pts = [(float(vf.times[i] - start), to_off(smooth[i])) for i in range(0, n, step)]
    if pts[-1][0] < dur - 0.2:
        pts.append((dur, pts[-1][1]))
    plan.keys = _simplify(pts)
    plan.face_bottom = float(np.mean(bottoms)) if bottoms else None
    plan.note = f"слежение за лицом ({len(plan.keys)} точек пути)"
    return plan


def crop_filter(plan: CropPlan) -> str:
    if plan.axis == "x":
        return f"crop=w={plan.cw}:h={plan.ch}:x='{piecewise_expr(plan.keys)}':y=0"
    return f"crop=w={plan.cw}:h={plan.ch}:x=0:y='{piecewise_expr(plan.keys)}'"
