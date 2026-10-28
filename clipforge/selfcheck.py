"""Проверка работоспособности: зависимости + сквозной прогон на сгенерированном тестовом видео."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def run() -> int:
    from .resources import check_dependencies
    print("== Зависимости ==")
    fatal = False
    for d in check_dependencies():
        print(f"  [{'OK' if d.ok else ('!!' if d.required else '--')}] {d.name}: {d.note}")
        fatal |= d.required and not d.ok and d.name != "PySide6"
    if fatal:
        print("Обязательные компоненты отсутствуют — см. README.")
        return 1
    tests_dir = Path(__file__).resolve().parent.parent / "tests"
    sys.path.insert(0, str(tests_dir))
    from helpers import make_media
    tmp = tempfile.mkdtemp(prefix="clipforge_selfcheck_")
    os.environ["CLIPFORGE_HOME"] = str(Path(tmp) / "home")
    print("== Генерация тестового видео и субтитров ==")
    video = make_media(Path(tmp) / "media")
    from .config import Settings
    from .db import Database
    from .pipeline import Pipeline
    s = Settings()
    s.clip.mode, s.clip.min_score = "funny", 30
    s.export.output_dir, s.export.short_side, s.export.preset = str(Path(tmp) / "out"), 540, "ultrafast"
    print("== Прогон конвейера ==")
    res = Pipeline(Database(":memory:"), s, str(video), on_log=lambda m: print("  ", m)).run()
    ok = bool(res.clips) and all(Path(c["file_path"]).exists() for c in res.clips)
    print(f"\nРезультат: {'УСПЕХ' if ok else 'ОШИБКА'} — клипов: {len(res.clips)}; папка: {res.out_dir}")
    return 0 if ok else 1
