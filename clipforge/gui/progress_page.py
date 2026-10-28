"""Страница анализа: прогресс, этап, ETA, лог, остановка."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from ..pipeline import ProgressEvent, STAGES
from ..util import fmt_time
from .widgets import Card, label


class ProgressPage(QWidget):
    def __init__(self, services, main) -> None:
        super().__init__()
        self.setObjectName("page")
        self.sv, self.main = services, main
        self.logs: dict[int, list[str]] = {}
        self.last: dict[int, ProgressEvent] = {}
        self.names: dict[int, str] = {}
        self.current: int | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        root.addWidget(label("Анализ и монтаж", "h1"))
        top = QHBoxLayout()
        top.addWidget(QLabel("Задача:"))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._combo)
        top.addWidget(self.combo, 1)
        root.addLayout(top)
        c = Card()
        self.stage = label("Нет активных задач", "stage")
        self.msg = label("", "muted")
        self.bar = QProgressBar(); self.bar.setRange(0, 1000)
        self.eta = label("", "muted")
        self.stages = label(" → ".join(x[1] for x in STAGES), "muted")
        for w in (self.stage, self.msg, self.bar, self.eta, self.stages):
            c.lay.addWidget(w)
        root.addWidget(c)
        self.warn = label("", "")
        self.warn.setStyleSheet("color:#f5b942;")
        root.addWidget(self.warn)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(5000)
        root.addWidget(self.log, 1)
        row = QHBoxLayout()
        self.stop = QPushButton("■ Остановить")
        self.stop.setObjectName("danger")
        self.stop.clicked.connect(self.stop_current)
        self.stop_all = QPushButton("Остановить все")
        self.stop_all.clicked.connect(self.sv.manager.cancel_all)
        row.addWidget(self.stop); row.addWidget(self.stop_all); row.addStretch(1)
        root.addLayout(row)
        sv = services.bridge
        sv.progress.connect(self.on_progress)
        sv.log.connect(self.on_log)

    def focus_job(self, job_id: int) -> None:
        job = self.sv.job(job_id)
        name = (job.source.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if job else str(job_id))[:60]
        self.names[job_id] = f"#{job_id} · {name}"
        self.logs.setdefault(job_id, [])
        if self.combo.findData(job_id) < 0:
            self.combo.addItem(self.names[job_id], job_id)
        self.combo.setCurrentIndex(self.combo.findData(job_id))

    def _combo(self, _i) -> None:
        self.current = self.combo.currentData()
        self.log.setPlainText("\n".join(self.logs.get(self.current, [])))
        if self.current in self.last:
            self._show(self.last[self.current])
        self._buttons()

    def _buttons(self) -> None:
        job = self.sv.job(self.current) if self.current else None
        self.stop.setEnabled(bool(job and job.status in ("queued", "running")))

    def stop_current(self) -> None:
        if self.current:
            self.sv.manager.cancel(self.current)
            self.on_log(self.current, "Остановка по запросу пользователя…")

    def on_progress(self, job_id: int, ev: ProgressEvent) -> None:
        self.last[job_id] = ev
        if job_id not in self.names:
            self.focus_job(job_id)
        if job_id == self.current:
            self._show(ev)

    def _show(self, ev: ProgressEvent) -> None:
        self.stage.setText(f"{ev.stage_label}")
        self.msg.setText(ev.message)
        self.bar.setValue(int(ev.overall * 1000))
        self.bar.setFormat(f"{ev.overall * 100:.0f}%")
        self.eta.setText(f"Примерно осталось: {fmt_time(ev.eta)}" if ev.eta else "Оценка времени появится позже…")
        self._buttons()

    def on_log(self, job_id: int, msg: str) -> None:
        self.logs.setdefault(job_id, []).append(msg)
        if job_id == self.current:
            self.log.appendPlainText(msg)
        if msg.startswith("⚠") and job_id == self.current:
            self.warn.setText(msg)

    def on_job_event(self, ev: str, job_id: int) -> None:
        job = self.sv.job(job_id)
        if job is None:
            return
        if ev == "started":
            self.focus_job(job_id) if self.current is None else None
        if ev in ("finished", "failed", "cancelled", "no_clips") and job_id == self.current:
            texts = {"finished": "Готово ✔", "failed": "Ошибка", "cancelled": "Остановлено", "no_clips": "Хороших моментов не найдено"}
            self.stage.setText(texts[ev])
            if job.error:
                self.log.appendPlainText("\n" + job.error)
                self.msg.setText(job.error.split("\n")[0])
            if ev == "finished":
                self.bar.setValue(1000); self.eta.setText("")
        self._buttons()
