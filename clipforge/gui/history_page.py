"""История проектов: список, повторный запуск, продолжение, удаление."""
from __future__ import annotations

import datetime
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..config import MODES, Settings
from ..errors import ClipForgeError
from .widgets import label

STATUS = {"done": "готово", "error": "ошибка", "cancelled": "остановлено", "running": "выполняется", "queued": "в очереди",
          "interrupted": "прервано", "no_clips": "моментов не найдено"}


class HistoryPage(QWidget):
    def __init__(self, services, main) -> None:
        super().__init__()
        self.setObjectName("page")
        self.sv, self.main = services, main
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.addWidget(label("История проектов", "h1"))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["№", "Видео", "Режим", "Статус", "Клипов", "Дата"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 380)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_clips())
        root.addWidget(self.table, 1)
        self.note = label("", "muted")
        root.addWidget(self.note)
        row = QHBoxLayout()
        for text, fn, name in (("Открыть клипы", self.open_clips, ""), ("Продолжить / запустить заново", self.rerun, "primary"),
                               ("Удалить проект", self.delete, "danger")):
            b = QPushButton(text)
            if name:
                b.setObjectName(name)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)
        self.table.itemSelectionChanged.connect(self._sel)

    def reload(self) -> None:
        self.projects = self.sv.db.list_projects()
        self.table.setRowCount(len(self.projects))
        for i, p in enumerate(self.projects):
            n = len([c for c in self.sv.db.get_clips(p["id"]) if c["status"] == "done"])
            vals = (str(p["id"]), p["title"], MODES.get(p["mode"], p["mode"]), STATUS.get(p["status"], p["status"]), str(n),
                    datetime.datetime.fromtimestamp(p["created"]).strftime("%d.%m.%Y %H:%M"))
            for c, v in enumerate(vals):
                self.table.setItem(i, c, QTableWidgetItem(v))
        self._sel()

    def current(self) -> dict | None:
        r = self.table.currentRow()
        return self.projects[r] if 0 <= r < len(self.projects) else None

    def _sel(self) -> None:
        p = self.current()
        self.note.setText((p.get("message") or "") if p else "")

    def open_clips(self) -> None:
        p = self.current()
        if p:
            self.main.gallery_page.show_project(p["id"])
            self.main.show_page("gallery")

    def rerun(self) -> None:
        p = self.current()
        if not p:
            return
        if p["status"] in ("running", "queued"):
            QMessageBox.information(self, "Проект", "Этот проект уже выполняется.")
            return
        settings = Settings.from_dict(p["settings"])
        settings.ui.rights_confirmed = self.sv.settings.ui.rights_confirmed
        settings.export.output_dir = self.sv.settings.export.output_dir if not p.get("out_dir") else settings.export.output_dir
        try:
            job = self.sv.submit(p["source"], settings, project_id=p["id"])
        except ClipForgeError as e:
            QMessageBox.warning(self, "Очередь", e.user_text())
            return
        self.main.progress_page.focus_job(job.id)
        self.main.show_page("progress")

    def delete(self) -> None:
        p = self.current()
        if not p:
            return
        r = QMessageBox.question(self, "Удаление проекта", f"Удалить проект «{p['title']}» из истории и очистить временные файлы?\nГотовые клипы в папке результатов останутся.")
        if r != QMessageBox.Yes:
            return
        if p["status"] in ("running", "queued"):
            QMessageBox.information(self, "Проект", "Сначала остановите обработку.")
            return
        root = Path(p["work_dir"]).parent if p.get("work_dir") else None
        self.sv.db.delete_project(p["id"])
        if root and root.exists():
            shutil.rmtree(root, ignore_errors=True)
        self.reload()
