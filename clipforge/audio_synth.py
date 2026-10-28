"""Встроенная фонотека: оригинальные музыкальные петли и звуковые эффекты, синтезируемые кодом.

Поскольку звук создаётся самой программой, на него нет чужих прав (лицензия CC0). Это простые фоновые
подложки, а не студийная музыка; для лучшего качества подключите свой каталог с разрешённой музыкой.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100

MOODS = {
    "cheerful":     dict(bpm=118, minor=False, prog=[0, 5, 7, 5], pattern="arp",   drums=True,  root=60),
    "energetic":    dict(bpm=136, minor=True,  prog=[0, 8, 3, 7], pattern="pulse", drums=True,  root=57),
    "calm":         dict(bpm=68,  minor=False, prog=[0, 9, 5, 7], pattern="pad",   drums=False, root=57),
    "dramatic":     dict(bpm=84,  minor=True,  prog=[0, 8, 5, 7], pattern="pad",   drums=True,  root=52),
    "motivational": dict(bpm=104, minor=False, prog=[0, 7, 9, 5], pattern="arp",   drums=True,  root=58),
}
MOOD_TITLES = {"cheerful": "Весёлое", "energetic": "Энергичное", "calm": "Спокойное",
               "dramatic": "Драматичное", "motivational": "Мотивационное"}


def _hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _tone(freq: float, dur: float, kind: str) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    if kind == "pluck":
        w = np.sin(2 * np.pi * freq * t) + 0.4 * np.sin(4 * np.pi * freq * t) + 0.15 * np.sin(6 * np.pi * freq * t)
        env = np.exp(-t * 7.0)
    elif kind == "bass":
        w = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)
        env = np.exp(-t * 3.0)
    else:  # pad
        w = (np.sin(2 * np.pi * freq * t) + np.sin(2 * np.pi * freq * 1.004 * t) + 0.3 * np.sin(4 * np.pi * freq * t)) / 2
        a, r = min(0.5, dur / 3), min(0.6, dur / 3)
        env = np.minimum(1.0, t / a) * np.minimum(1.0, (dur - t) / r)
    return (w * env).astype(np.float32)


def _kick() -> np.ndarray:
    t = np.arange(int(0.25 * SR)) / SR
    return (np.sin(2 * np.pi * (45 * t + 75 * (1 - np.exp(-t * 30)) / 30)) * np.exp(-t * 14)).astype(np.float32)


def _hat(rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(0.05 * SR)) / SR
    n = rng.normal(0, 1, len(t))
    n = np.diff(n, prepend=0)
    return (n * np.exp(-t * 90) * 0.35).astype(np.float32)


def _add(buf: np.ndarray, snd: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * SR)
    if i >= len(buf):
        return
    n = min(len(snd), len(buf) - i)
    buf[i:i + n] += snd[:n] * gain


def generate_track(mood: str, seconds: float = 32.0, seed: int = 7) -> np.ndarray:
    cfg = MOODS[mood]
    rng = np.random.default_rng(seed)
    beat = 60.0 / cfg["bpm"]
    bar = beat * 4
    bars = max(4, int(round(seconds / bar / 4)) * 4)
    buf = np.zeros(int(bars * bar * SR) + SR, dtype=np.float32)
    third = 3 if cfg["minor"] else 4
    for b in range(bars):
        root = cfg["root"] + cfg["prog"][b % len(cfg["prog"])]
        notes = [root, root + third, root + 7]
        t0 = b * bar
        _add(buf, _tone(_hz(root - 24), bar * 0.95, "bass" if cfg["pattern"] != "pad" else "pad"), t0, 0.5)
        if cfg["pattern"] == "pad":
            for nt in notes:
                _add(buf, _tone(_hz(nt), bar, "pad"), t0, 0.22)
        elif cfg["pattern"] == "arp":
            seq = [notes[0], notes[1], notes[2], notes[1] + 12, notes[2], notes[1], notes[0] + 12, notes[1]]
            for k, nt in enumerate(seq):
                _add(buf, _tone(_hz(nt), beat * 0.6, "pluck"), t0 + k * beat / 2, 0.30)
        else:  # pulse
            for k in range(8):
                _add(buf, _tone(_hz(notes[k % 3]), beat * 0.45, "pluck"), t0 + k * beat / 2, 0.26)
        if cfg["drums"]:
            for k in range(4):
                if cfg["pattern"] == "pad" and k not in (0, 2):
                    continue
                _add(buf, _kick(), t0 + k * beat, 0.55)
            for k in range(8):
                _add(buf, _hat(rng), t0 + k * beat / 2 + (beat / 4 if cfg["pattern"] != "pad" else 0), 0.5)
    out = buf[:int(bars * bar * SR)]
    out = np.tanh(out * 1.1)
    out = out / (np.abs(out).max() + 1e-9) * 0.5
    fade = int(0.02 * SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out.astype(np.float32)


def gen_sfx(name: str) -> np.ndarray:
    rng = np.random.default_rng(3)
    if name == "pop":
        t = np.arange(int(0.14 * SR)) / SR
        return (np.sin(2 * np.pi * (900 - 2500 * t) * t) * np.exp(-t * 28)).astype(np.float32) * 0.8
    if name == "whoosh":
        n = int(0.55 * SR)
        t = np.arange(n) / SR
        noise = rng.normal(0, 1, n)
        k = np.convolve(noise, np.ones(40) / 40, mode="same")
        return (k * np.sin(np.pi * t / 0.55) ** 2 * 3.0).astype(np.float32).clip(-1, 1) * 0.6
    if name == "impact":
        t = np.arange(int(0.45 * SR)) / SR
        w = np.sin(2 * np.pi * 60 * t) * np.exp(-t * 9) + rng.normal(0, 0.25, len(t)) * np.exp(-t * 25)
        return w.astype(np.float32).clip(-1, 1) * 0.9
    raise KeyError(name)


def write_wav(path: Path, data: np.ndarray, sr: int = SR) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(data, -1, 1) * 32000).astype(np.int16).tobytes())
