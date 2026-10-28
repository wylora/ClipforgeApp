"""Галерея готовых клипов: миниатюры, оценки, действия, обратная связь."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QTextEdit, QVBoxLayout, QWidget)

from ..config import Settings
from ..feedback import LABEL_TITLES
from ..ffmpeg_utils import probe
from ..pipeline import load_assets, render_clip_row
from ..util import fmt_time
from .widgets import Card, label


def open_path(p: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(p))


class GalleryPage(QWidget):
    def __init__(self, services, main) -> None:
        super().__init__()
        self.setObjectName("page")
        self.sv, self.main = services, main
        self.project: dict | None = None
        self.clips: list[dict] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.addWidget(label("Готовые клипы", "h1"))
        top = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._project_changed)
        self.fav_only = QCheckBox("Только избранные")
        self.fav_only.toggled.connect(self.reload_clips)
        b_open = QPushButton("📂 Открыть папку результатов")
        b_open.clicked.connect(self.open_folder)
        b_rep = QPushButton("Отчёт")
        b_rep.clicked.connect(self.open_report)
        b_keep = QPushButton("Оставить только избранные")
        b_keep.clicked.connect(self.keep_favorites)
        for w in (self.combo, self.fav_only, b_open, b_rep, b_keep):
            top.addWidget(w, 1 if w is self.combo else 0)
        root.addLayout(top)
        self.summary = label("", "muted")
        root.addWidget(self.summary)
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(150, 266))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSpacing(10)
        self.list.setWordWrap(True)
        self.list.setGridSize(QSize(180, 340))
        self.list.currentItemChanged.connect(self._select)
        self.list.itemDoubleClicked.connect(lambda _i: self.edit_clip())
        body.addWidget(self.list, 3)
        side = Card()
        self.title = label("Выберите клип", "h2")
        self.info = label("", "muted")
        self.reasons = QTextEdit(); self.reasons.setReadOnly(True); self.reasons.setMinimumHeight(120)
        self.meta = QTextEdit(); self.meta.setReadOnly(True); self.meta.setMaximumHeight(110)
        self.titles = QComboBox()
        self.titles.currentIndexChanged.connect(self._title_picked)
        for w in (self.title, self.info, QLabel("Почему выбран:"), self.reasons, QLabel("Варианты названия:"), self.titles,
                  QLabel("Описание и хештеги:"), self.meta):
            side.lay.addWidget(w)
        r1 = QHBoxLayout()
        self.b_play = QPushButton("▶ Просмотр / редактор")
        self.b_play.clicked.connect(self.edit_clip)
        self.b_fav = QPushButton("☆ В избранное")
        self.b_fav.clicked.connect(self.toggle_fav)
        r1.addWidget(self.b_play); r1.addWidget(self.b_fav)
        side.lay.addLayout(r1)
        r2 = QHBoxLayout()
        b_dir = QPushButton("Папка клипа"); b_dir.clicked.connect(self.open_clip_folder)
        self.b_re = QPushButton("↻ Повторный экспорт"); self.b_re.clicked.connect(self.reexport)
        b_del = QPushButton("Удалить"); b_del.setObjectName("danger"); b_del.clicked.connect(self.delete_clip)
        r2.addWidget(b_dir); r2.addWidget(self.b_re); r2.addWidget(b_del)
        side.lay.addLayout(r2)
        side.lay.addWidget(QLabel("Оцените клип — это улучшит следующие подборки:"))
        r3 = QHBoxLayout()
        for key, text in (("funny", "😂 Смешно"), ("not_funny", "😐 Не смешно"), ("interesting", "👍 Интересно"), ("boring", "😴 Скучно")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, k=key: self.rate(k))
            r3.addWidget(b)
        side.lay.addLayout(r3)
        self.status = label("", "muted")
        side.lay.addWidget(self.status)
        side.setMaximumWidth(460)
        body.addWidget(side, 2)
        root.addLayout(body, 1)

    # ------------------------------------------------------------ данные
    def refresh_projects(self, select_id: int | None = None) -> None:
        cur = select_id or (self.project["id"] if self.project else None)
        self.combo.blockSignals(True)
        self.combo.clear()
        for p in self.sv.db.list_projects():
            if p["status"] in ("done", "interrupted", "error", "cancelled", "no_clips") or self.sv.db.get_clips(p["id"]):
                self.combo.addItem(f"#{p['id']} · {p['title'][:60]} · {p['status']}", p["id"])
        i = self.combo.findData(cur) if cur else 0
        self.combo.setCurrentIndex(max(0, i))
        self.combo.blockSignals(False)
        self._project_changed()

    def show_project(self, pid: int) -> None:
        self.refresh_projects(pid)

    def _project_changed(self, *_a) -> None:
        pid = self.combo.currentData()
        self.project = self.sv.db.get_project(pid) if pid else None
        self.reload_clips()

    def reload_clips(self, *_a) -> None:
        self.list.clear()
        self.clips = self.sv.db.get_clips(self.project["id"]) if self.project else []
        shown = [c for c in self.clips if c.get("favorite") or not self.fav_only.isChecked()]
        for c in shown:
            q = (c.get("scores") or {}).get("quality", 0)
            star = "★ " if c.get("favorite") else ""
            mark = {"done": "", "error": " ⚠", "rendering": " …", "pending": " …"}.get(c["status"], "")
            it = QListWidgetItem(f"{star}{c['idx']:02d} · {c['title'][:40]}\n{c['end'] - c['start']:.0f} с · {q:.0f}/100{mark}")
            thumb = c.get("thumb_path")
            if thumb and Path(thumb).exists():
                it.setIcon(QIcon(QPixmap(thumb).scaled(150, 266, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            it.setData(Qt.UserRole, c["id"])
            self.list.addItem(it)
        if self.project:
            self.summary.setText(f"{self.project['title']} · режим: {self.project['mode']} · клипов: {len(self.clips)} · {self.project.get('message') or ''}")
        else:
            self.summary.setText("Пока нет готовых клипов. Запустите обработку на главной странице.")
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._select(None, None)

    def current(self) -> dict | None:
        it = self.list.currentItem()
        return self.sv.db.get_clip(it.data(Qt.UserRole)) if it else None

    def _select(self, it, _prev) -> None:
        c = self.current() if it else None
        for w in (self.b_play, self.b_fav, self.b_re):
            w.setEnabled(bool(c))
        if not c:
            self.title.setText("Выберите клип"); self.info.setText(""); self.reasons.clear(); self.meta.clear(); self.titles.clear()
            return
        sc = c.get("scores") or {}
        names = (("semantic", "смысл"), ("humor", "юмор"), ("reaction", "реакция"), ("audio", "звук"), ("completeness", "законч."), ("visual", "видео"))
        self.title.setText(c["title"])
        self.info.setText(f"{fmt_time(c['start'])}–{fmt_time(c['end'])} · {c['end'] - c['start']:.1f} с · итог {sc.get('quality', 0):.0f}/100\n"
                          + " · ".join(f"{n} {sc[k]:.0f}" for k, n in names if k in sc) + (f"\nОтметка: {LABEL_TITLES.get(c['label'], '')}" if c.get("label") else ""))
        self.reasons.setPlainText("\n".join("• " + r for r in (c.get("reasons") or [])) or "—")
        self.meta.setPlainText(f"{c.get('description') or ''}\n\n{c.get('hashtags') or ''}")
        self.titles.blockSignals(True)
        self.titles.clear()
        self.titles.addItems(c.get("titles") or [c["title"]])
        self.titles.setCurrentText(c["title"])
        self.titles.blockSignals(False)
        self.b_fav.setText("★ Убрать из избранного" if c.get("favorite") else "☆ В избранное")
        self.status.setText(c.get("error") or {"done": "Готов", "error": "Ошибка монтажа"}.get(c["status"], c["status"]))

    # ------------------------------------------------------------ действия
    def _title_picked(self, _i) -> None:
        c = self.current()
        t = self.titles.currentText()
        if c and t and t != c["title"]:
            self.sv.db.update_clip(c["id"], title=t)
            self.status.setText("Название изменено. Нажмите «Повторный экспорт», чтобы обновить имя файла.")

    def open_folder(self) -> None:
        if self.project and self.project.get("out_dir"):
            Path(self.project["out_dir"]).mkdir(parents=True, exist_ok=True)
            open_path(self.project["out_dir"])

    def open_report(self) -> None:
        if self.project and self.project.get("report_path") and Path(self.project["report_path"]).exists():
            open_path(self.project["report_path"])
        else:
            QMessageBox.information(self, "Отчёт", "Отчёт пока не создан.")

    def open_clip_folder(self) -> None:
        c = self.current()
        if c and c.get("file_path") and Path(c["file_path"]).exists():
            open_path(str(Path(c["file_path"]).parent))
        else:
            self.open_folder()

    def edit_clip(self) -> None:
        c = self.current()
        if c:
            self.main.open_editor(c["id"])

    def toggle_fav(self) -> None:
        c = self.current()
        if c:
            self.sv.db.update_clip(c["id"], favorite=0 if c.get("favorite") else 1)
            row = self.list.currentRow()
            self.reload_clips()
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def keep_favorites(self) -> None:
        favs = [c for c in self.clips if c.get("favorite")]
        if not favs:
            QMessageBox.information(self, "Избранное", "Сначала отметьте лучшие клипы звёздочкой.")
            return
        rest = [c for c in self.clips if not c.get("favorite")]
        if rest and QMessageBox.question(self, "Оставить избранные", f"Удалить {len(rest)} неизбранных клипов вместе с файлами?") == QMessageBox.Yes:
            for c in rest:
                self._remove(c)
            self.reload_clips()

    def _remove(self, c: dict) -> None:
        for k in ("file_path", "thumb_path", "srt_path"):
            if c.get(k):
                Path(c[k]).unlink(missing_ok=True)
        self.sv.db.update_clip(c["id"], status="deleted", file_path="", thumb_path="", srt_path="")

    def delete_clip(self) -> None:
        c = self.current()
        if c and QMessageBox.question(self, "Удаление", f"Удалить клип «{c['title']}» и его файлы?") == QMessageBox.Yes:
            self._remove(c)
            self.reload_clips()

    def rate(self, label_key: str) -> None:
        c = self.current()
        if not c:
            return
        self.sv.db.set_feedback(c, label_key)
        self.status.setText(f"Отметка «{LABEL_TITLES[label_key]}» сохранена локально и будет учтена при следующих подборках.")
        self._select(self.list.currentItem(), None)

    def reexport(self) -> None:
        c = self.current()
        if not c or not self.project:
            return
        pid, cid = self.project["id"], c["id"]
        self.status.setText("Повторный экспорт…")
        self.b_re.setEnabled(False)

        def work():
            proj = self.sv.db.get_project(pid)
            work_dir = Path(proj["work_dir"])
            tr, _ = load_assets(work_dir)
            s = Settings.from_dict(proj["settings"])
            return render_clip_row(self.sv.db, cid, Path(proj["video_path"]), probe(proj["video_path"]), s,
                                   Path(proj["out_dir"]), work_dir, tr, force=True)

        def done(res, err):
            self.b_re.setEnabled(True)
            self.status.setText("Экспорт завершён" if err is None else f"Ошибка: {getattr(err, 'message', err)}")
            self.reload_clips()
        self.sv.bridge.run_bg(work, done)
