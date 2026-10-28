"""Генерация короткого тестового видео: «речь» тонами, смех = шум с модуляцией 5 Гц, сопроводительный SRT."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SR = 16000
# (начало, конец, текст) — шутка около 36–44 с, серьёзный фрагмент 60–76 с
LINES = [
    (2.0, 6.0, "Всем привет, сегодня мы поговорим о разном."),
    (7.0, 11.0, "Вчера я решил приготовить ужин для всей семьи."),
    (11.3, 16.5, "Я купил продукты, включил плиту и посмотрел рецепт на два часа."),
    (30.0, 35.0, "Мы с другом спорили, кто лучше готовит, и он сказал, что никогда не ошибается в рецептах."),
    (35.4, 38.0, "Я говорю, давай проверим."),
    (39.2, 40.3, "Хахаха, он посолил торт!"),
    (45.5, 47.0, "Серьёзно?! Ничего себе!"),
    (60.0, 65.0, "Главное, что я понял за десять лет работы, это важное правило."),
    (65.3, 71.0, "Исследования показывают, что дисциплина важнее таланта, поэтому начинайте с малого каждый день."),
    (71.4, 75.0, "Вот почему результат приходит к тем, кто не сдаётся."),
]
LAUGH = (40.4, 44.0)


def make_media(dirpath: Path, duration: float = 90.0, name: str = "test_video") -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(5)
    t = np.arange(int(duration * SR)) / SR
    sig = rng.normal(0, 0.002, len(t))
    for a, b, _ in LINES:
        env = np.zeros_like(t)
        pos = a
        while pos < b:
            d = rng.uniform(0.12, 0.35)
            env[(t >= pos) & (t < min(pos + d, b))] = rng.uniform(0.3, 0.7)
            pos += d + rng.uniform(0.03, 0.12)
        sig += env * np.sin(2 * np.pi * (130 + 25 * np.sin(2 * np.pi * 0.6 * t)) * t)
    la, lb = LAUGH
    m = (t >= la) & (t < lb)
    sig += m * (0.5 + 0.5 * np.sin(2 * np.pi * 5 * t)) ** 2 * rng.normal(0, 0.3, len(t))
    wav = dirpath / f"{name}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(sig, -1, 1) * 30000).astype(np.int16).tobytes())
    video = dirpath / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=960x540:rate=25:duration={duration}",
                    "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(video)], check=True)
    srt = []
    for i, (a, b, text) in enumerate(LINES, 1):
        f = lambda x: f"00:{int(x // 60):02d}:{int(x % 60):02d},{int(round((x % 1) * 1000)):03d}"
        srt.append(f"{i}\n{f(a)} --> {f(b)}\n{text}\n")
    (dirpath / f"{name}.srt").write_text("\n".join(srt), encoding="utf-8")
    return video
