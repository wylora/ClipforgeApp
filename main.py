"""ClipForge — запуск.

  python main.py                 — графический интерфейс
  python main.py --selfcheck     — проверка зависимостей и сквозной прогон на синтетическом видео (без GUI)
  python main.py --cli ВИДЕО --mode funny   — обработка без GUI (для автоматизации/отладки)
"""
from __future__ import annotations

import argparse
import sys


def run_gui() -> int:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        print("Не установлен PySide6. Выполните: pip install -r requirements.txt")
        return 2
    from clipforge.app import Services
    from clipforge.gui.bridge import Bridge
    from clipforge.gui.main_window import MainWindow
    from clipforge.logging_setup import setup_logging
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("ClipForge")
    bridge = Bridge()
    sv = Services(bridge)
    win = MainWindow(sv)
    win.show()
    from clipforge.resources import check_dependencies
    miss = [d for d in check_dependencies() if d.required and not d.ok and d.name != "PySide6"]
    if miss:
        QMessageBox.warning(win, "Не хватает компонентов",
                            "\n".join(f"• {d.name}: {d.note}" for d in miss) + "\n\nСм. README → «Установка FFmpeg».")
    return app.exec()


def run_cli(args) -> int:
    from clipforge.config import Settings, apply_profile
    from clipforge.db import Database
    from clipforge.errors import ClipForgeError
    from clipforge.logging_setup import setup_logging
    from clipforge.pipeline import Pipeline
    setup_logging()
    s = Settings.load()
    s.clip.mode, s.clip.custom_prompt = args.mode, args.prompt or ""
    s.clip.min_score = args.min_score
    s.clip.max_clips = args.clips
    s.ui.rights_confirmed = args.i_have_rights
    s.analysis.whisper_consent = args.allow_model_download
    if args.out:
        s.export.output_dir = args.out
    apply_profile(s, args.profile)
    try:
        res = Pipeline(Database(), s, args.cli, on_progress=lambda e: print(f"\r[{e.overall * 100:5.1f}%] {e.stage_label}: {e.message[:60]:60}", end=""),
                       on_log=print).run()
    except ClipForgeError as e:
        print("\n" + e.user_text())
        return 1
    print(f"\n{res.message}\nПапка: {res.out_dir}")
    for c in res.clips:
        print(f"  {c['idx']:02d}. {c['title']} — {c['file_path']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ClipForge")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--cli", metavar="ВИДЕО_ИЛИ_ССЫЛКА")
    ap.add_argument("--mode", default="best", choices=["funny", "serious", "best", "custom"])
    ap.add_argument("--prompt", default="")
    ap.add_argument("--clips", type=int, default=5)
    ap.add_argument("--min-score", type=float, default=45.0)
    ap.add_argument("--profile", default="minimal", choices=["minimal", "dynamic", "meme", "cinematic"])
    ap.add_argument("--out")
    ap.add_argument("--i-have-rights", action="store_true", help="подтверждаю право на загрузку видео по ссылке")
    ap.add_argument("--allow-model-download", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        from clipforge.selfcheck import run
        return run()
    if a.cli:
        return run_cli(a)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
