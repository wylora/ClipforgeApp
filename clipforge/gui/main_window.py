"""Главное окно: навигация, страницы, тема, проверка зависимостей."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QListWidget, QMainWindow, QMessageBox, QStackedWidget, QWidget)

from .. import __version__
from ..resources import check_dependencies
from .editor_page import EditorPage
from .gallery_page import GalleryPage
from .history_page import HistoryPage
from .home_page import HomePage
from .progress_page import ProgressPage
from .settings_page import SettingsPage
from .theme import build_qss

PAGES = [("home", "🏠  Главная"), ("settings", "⚙  Настройки"), ("progress", "⏳  Анализ"), ("gallery", "🎞  Клипы"),
         ("editor", "✂  Редактор"), ("history", "🕘  История")]


class MainWindow(QMainWindow):
    def __init__(self, services) -> None:
        super().__init__()
        self.sv = services
        self.setWindowTitle(f"ClipForge {__version__} — AI-нарезка видео")
        self.resize(1280, 820)
        self.setMinimumSize(980, 640)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(190)
        for _k, t in PAGES:
            self.nav.addItem(t)
        self.stack = QStackedWidget()
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.home_page = HomePage(services, self)
        self.settings_page = SettingsPage(services, self)
        self.progress_page = ProgressPage(services, self)
        self.gallery_page = GalleryPage(services, self)
        self.editor_page = EditorPage(services, self)
        self.history_page = HistoryPage(services, self)
        self.pages = {"home": self.home_page, "settings": self.settings_page, "progress": self.progress_page,
                      "gallery": self.gallery_page, "editor": self.editor_page, "history": self.history_page}
        for k, _ in PAGES:
            self.stack.addWidget(self.pages[k])
        self.nav.currentRowChanged.connect(self._nav)
        self.nav.setCurrentRow(0)
        services.bridge.job_event.connect(self._job_event)
        self._menu()
        self.apply_theme()
        self.statusBar().showMessage(self._dep_summary())

    def _menu(self) -> None:
        m = self.menuBar().addMenu("Справка")
        a = QAction("Проверка зависимостей…", self)
        a.triggered.connect(self.show_deps)
        m.addAction(a)
        t = self.menuBar().addMenu("Вид")
        ta = QAction("Переключить тему (тёмная/светлая)", self)
        ta.triggered.connect(self.toggle_theme)
        t.addAction(ta)

    def apply_theme(self) -> None:
        QApplication.instance().setStyleSheet(build_qss(self.sv.settings.ui.theme))

    def toggle_theme(self) -> None:
        u = self.sv.settings.ui
        u.theme = "light" if u.theme == "dark" else "dark"
        self.sv.save_settings()
        self.settings_page.binder.refresh()
        self.apply_theme()

    def _dep_summary(self) -> str:
        bad = [d.name for d in check_dependencies() if d.required and not d.ok]
        opt = [d.name for d in check_dependencies() if not d.required and not d.ok]
        s = "Все обязательные компоненты найдены." if not bad else "НЕ ХВАТАЕТ: " + ", ".join(bad)
        return s + (f"  Опционально не установлено: {', '.join(opt)}" if opt else "")

    def show_deps(self) -> None:
        lines = [f"{'✔' if d.ok else ('✘' if d.required else '○')}  {d.name} — {d.note}" for d in check_dependencies()]
        QMessageBox.information(self, "Зависимости", "\n".join(lines) + "\n\n✔ найдено · ✘ обязательно, не найдено · ○ необязательно, не найдено")

    def show_page(self, key: str) -> None:
        self.nav.setCurrentRow([k for k, _ in PAGES].index(key))

    def _nav(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        key = PAGES[row][0]
        if key == "gallery":
            self.gallery_page.refresh_projects()
        elif key == "history":
            self.history_page.reload()
        if key != "editor":
            self.editor_page.leave()

    def open_editor(self, clip_id: int) -> None:
        self.editor_page.load(clip_id)
        self.show_page("editor")

    def _job_event(self, ev: str, job_id: int) -> None:
        self.progress_page.on_job_event(ev, job_id)
        job = self.sv.job(job_id)
        text = {"queued": "в очереди", "started": "выполняется", "finished": "готово ✔", "failed": "ошибка",
                "cancelled": "остановлено", "no_clips": "моментов не найдено"}.get(ev, ev)
        self.home_page.update_item_status(job_id, text)
        if ev == "finished" and job and job.result:
            self.gallery_page.refresh_projects(job.result.project_id)
            QMessageBox.information(self, "Готово", f"{job.result.message}\n\nПапка: {job.result.out_dir}")
            self.show_page("gallery")
        elif ev in ("failed", "no_clips") and job:
            QMessageBox.warning(self, "Обработка не завершена", job.error)
        if ev in ("finished", "failed", "cancelled", "no_clips"):
            QTimer.singleShot(0, self.history_page.reload)

    def closeEvent(self, e) -> None:
        running = [j for j in self.sv.manager.jobs if j.status in ("queued", "running")]
        if running and QMessageBox.question(self, "Выход", "Идёт обработка. Остановить и выйти?\nПрогресс сохранится, проект можно продолжить из истории.") != QMessageBox.Yes:
            e.ignore()
            return
        self.editor_page.leave()
        self.sv.shutdown()
        e.accept()
