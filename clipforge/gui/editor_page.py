"""Предпросмотр и редактор клипа: границы, субтитры, звук/музыка, сравнение с оригиналом, пересборка."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QSlider, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAVE_MM = True
except Exception:  # pragma: no cover
    HAVE_MM = False

from ..config import Settings
from ..ffmpeg_utils import probe
from ..pipeline import clip_settings, load_assets, render_clip_row
from ..subtitles import SubLine, build_lines, lines_from_json, lines_to_json, retext_line, to_srt
from ..util import fmt_time
from .gallery_page import open_path
from .widgets import Binder, Card, RangeBar, form, label


class EditorPage(QWidget):
    def __init__(self, services, main) -> None:
        super().__init__()
        self.setObjectName("page")
        self.sv, self.main = services, main
        self.clip: dict | None = None
        self.project: dict | None = None
        self.base: Settings | None = None
        self.es: Settings | None = None
        self.transcript = None
        self.lines: list[SubLine] = []
        self.lines_edited = False
        self.src_dur = 0.0
        self.binder = Binder(lambda: None)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.addWidget(label("Предпросмотр и редактор", "h1"))
        self.head = label("Откройте клип из галереи (двойной клик).", "muted")
        root.addWidget(self.head)
        body = QHBoxLayout()
        left = QVBoxLayout()
        vids = QHBoxLayout()
        self.orig_player = self.res_player = None
        if HAVE_MM:
            self.orig_view, self.orig_player, self.orig_audio = self._mk_player()
            self.res_view, self.res_player, self.res_audio = self._mk_player()
            self.orig_audio.setVolume(0.0)
            for cap, v in (("Оригинал", self.orig_view), ("Результат", self.res_view)):
                col = QVBoxLayout(); col.addWidget(label(cap, "h2")); col.addWidget(v, 1)
                vids.addLayout(col, 1)
            self.orig_player.positionChanged.connect(self._orig_pos)
            self.res_player.positionChanged.connect(self._res_pos)
        else:
            vids.addWidget(label("Модуль QtMultimedia недоступен — используйте «Открыть во внешнем плеере».", "muted"))
        left.addLayout(vids, 1)
        ctl = QHBoxLayout()
        self.b_play = QPushButton("▶ Играть оба")
        self.b_play.clicked.connect(self.play_both)
        self.b_stop = QPushButton("■")
        self.b_stop.clicked.connect(self.stop_both)
        self.hear_orig = QCheckBox("Слышать оригинал")
        self.hear_orig.toggled.connect(lambda v: self.orig_audio.setVolume(1.0 if v else 0.0) if HAVE_MM else None)
        b_ext = QPushButton("Открыть во внешнем плеере")
        b_ext.clicked.connect(self.open_external)
        for w in (self.b_play, self.b_stop, self.hear_orig, b_ext):
            ctl.addWidget(w)
        ctl.addStretch(1)
        left.addLayout(ctl)
        self.bar = RangeBar()
        self.bar.changed.connect(self._bar_changed)
        left.addWidget(self.bar)
        tr = QHBoxLayout()
        self.sp_a, self.sp_b = QDoubleSpinBox(), QDoubleSpinBox()
        for sp in (self.sp_a, self.sp_b):
            sp.setDecimals(2); sp.setRange(0, 100000); sp.setSingleStep(0.1); sp.setSuffix(" с")
            sp.valueChanged.connect(self._spin_changed)
        self.dur_lbl = QLabel()
        tr.addWidget(QLabel("Начало:")); tr.addWidget(self.sp_a); tr.addWidget(QLabel("Конец:")); tr.addWidget(self.sp_b)
        tr.addWidget(self.dur_lbl); tr.addStretch(1)
        left.addLayout(tr)
        body.addLayout(left, 3)

        tabs = QTabWidget()
        tabs.setMaximumWidth(470)
        sub = QWidget(); sl = QVBoxLayout(sub)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Начало", "Конец", "Текст"])
        self.table.setColumnWidth(0, 70); self.table.setColumnWidth(1, 70)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._sub_edited)
        sl.addWidget(self.table)
        sr = QHBoxLayout()
        b_srt = QPushButton("Экспорт SRT…"); b_srt.clicked.connect(self.export_srt)
        b_rs = QPushButton("Сбросить по транскрипту"); b_rs.clicked.connect(self.reset_subs)
        sr.addWidget(b_srt); sr.addWidget(b_rs)
        sl.addLayout(sr)
        sl.addWidget(label("Время — в секундах от начала клипа. Правка текста равномерно перераспределяет подсветку слов.", "muted"))
        tabs.addTab(sub, "Субтитры")
        self.audio_tab = QWidget(); self.audio_lay = QVBoxLayout(self.audio_tab)
        tabs.addTab(self.audio_tab, "Звук и кадр")
        body.addWidget(tabs, 2)
        root.addLayout(body, 1)
        act = QHBoxLayout()
        self.b_prev = QPushButton("👁 Быстрый предпросмотр"); self.b_prev.clicked.connect(self.preview)
        self.b_build = QPushButton("⚙ Пересобрать клип"); self.b_build.setObjectName("primary"); self.b_build.clicked.connect(self.rebuild)
        self.status = label("", "muted")
        act.addWidget(self.b_prev); act.addWidget(self.b_build); act.addWidget(self.status, 1)
        root.addLayout(act)

    def _mk_player(self):
        v = QVideoWidget(); v.setMinimumSize(200, 240)
        p = QMediaPlayer(); a = QAudioOutput()
        p.setAudioOutput(a); p.setVideoOutput(v)
        return v, p, a

    # ------------------------------------------------------------ загрузка
    def load(self, clip_id: int) -> None:
        db = self.sv.db
        self.clip = db.get_clip(clip_id)
        self.project = db.get_project(self.clip["project_id"])
        self.base = Settings.from_dict(self.project["settings"])
        self.es = clip_settings(self.base, self.clip)
        work = Path(self.project["work_dir"])
        self.transcript, _ = load_assets(work)
        try:
            self.src_dur = probe(self.project["video_path"]).duration
        except Exception:
            self.src_dur = self.clip["end"] + 60
        self.lines = lines_from_json(self.clip["subtitles"]) if self.clip.get("subtitles") is not None else []
        self.lines_edited = False
        self.head.setText(f"{self.clip['idx']:02d} · {self.clip['title']}  —  {self.project['title']}")
        self._build_audio_tab()
        self.sp_a.blockSignals(True); self.sp_b.blockSignals(True)
        self.sp_a.setValue(self.clip["start"]); self.sp_b.setValue(self.clip["end"])
        self.sp_a.blockSignals(False); self.sp_b.blockSignals(False)
        self._sync_bar()
        self._fill_table()
        if HAVE_MM:
            self.orig_player.setSource(QUrl.fromLocalFile(self.project["video_path"]))
            self.orig_player.setPosition(int(self.clip["start"] * 1000))
            fp = self.clip.get("file_path")
            self.res_player.setSource(QUrl.fromLocalFile(fp) if fp and Path(fp).exists() else QUrl())
        self.status.setText("")

    def _build_audio_tab(self) -> None:
        while self.audio_lay.count():
            it = self.audio_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.binder = Binder(lambda: None)
        b, es = self.binder, self.es
        c = Card("Звук клипа")
        form(c.lay, [
            ("Громкость оригинала", b.slider(es.audio, "original_volume", 0, 200)),
            ("", b.check("Отключить оригинальный звук", es.audio, "mute_original")),
            ("Музыка", b.combo(es.audio, "music_mode", [("off", "Выключена"), ("auto", "Авто (кроме смешных)"), ("on", "Включена")])),
            ("Громкость музыки", b.slider(es.audio, "music_volume", 0, 100)),
            ("Приглушение при речи", b.slider(es.audio, "ducking", 0, 100)),
            ("Свой файл музыки", b.file(es.audio, "music_path", "Музыка", "Аудио (*.mp3 *.wav *.flac *.m4a *.ogg *.aac)")),
            ("", b.check("Нормализовать громкость", es.audio, "normalize")),
        ])
        self.audio_lay.addWidget(c)
        c2 = Card("Кадр и субтитры")
        form(c2.lay, [
            ("Кадрирование", b.combo(es.effects, "crop_mode", [("auto", "Следить за лицом"), ("center", "По центру"), ("manual", "Вручную"), ("fit", "С размытым фоном")])),
            ("Положение (ручное)", self._crop_slider()),
            ("", b.check("Субтитры включены", es.subtitles, "enabled")),
        ])
        self.audio_lay.addWidget(c2)
        self.audio_lay.addStretch(1)

    def _crop_slider(self) -> QSlider:
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, 100)
        sl.setValue(int(self.es.effects.manual_crop_x * 100))
        sl.valueChanged.connect(lambda v: setattr(self.es.effects, "manual_crop_x", v / 100.0))
        return sl

    def _sync_bar(self) -> None:
        a, b = self.sp_a.value(), self.sp_b.value()
        self.bar.set_range(max(0, a - 30), min(self.src_dur or b + 30, b + 30), a, b)
        self.dur_lbl.setText(f"длительность {b - a:.1f} с")

    # ------------------------------------------------------------ границы
    def _bar_changed(self, a: float, b: float) -> None:
        for sp, v in ((self.sp_a, a), (self.sp_b, b)):
            sp.blockSignals(True); sp.setValue(v); sp.blockSignals(False)
        self.dur_lbl.setText(f"длительность {b - a:.1f} с")

    def _spin_changed(self, _v) -> None:
        if self.sp_b.value() - self.sp_a.value() < 1.0:
            self.sp_b.blockSignals(True); self.sp_b.setValue(self.sp_a.value() + 1.0); self.sp_b.blockSignals(False)
        self._sync_bar()

    # ------------------------------------------------------------ субтитры
    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.lines))
        for i, l in enumerate(self.lines):
            for c, v in enumerate((f"{l.start:.2f}", f"{l.end:.2f}", l.text)):
                self.table.setItem(i, c, QTableWidgetItem(v))
        self.table.blockSignals(False)

    def _sub_edited(self, item: QTableWidgetItem) -> None:
        l = self.lines[item.row()]
        try:
            if item.column() == 0:
                l.start = max(0.0, float(item.text().replace(",", ".")))
                retext_line(l, l.text)
            elif item.column() == 1:
                l.end = max(l.start + 0.1, float(item.text().replace(",", ".")))
                retext_line(l, l.text)
            else:
                retext_line(l, item.text())
            self.lines_edited = True
        except ValueError:
            self.status.setText("Время должно быть числом (секунды).")
            self._fill_table()

    def reset_subs(self) -> None:
        self._rebuild_lines()
        self._fill_table()
        self.lines_edited = False

    def _rebuild_lines(self) -> None:
        if self.transcript is None:
            self.status.setText("Транскрипт недоступен — субтитры не пересобраны.")
            return
        self.lines = build_lines(self.transcript.all_words(), self.sp_a.value(), self.sp_b.value(), self.es.subtitles.words_per_line)

    def export_srt(self) -> None:
        if not self.lines:
            QMessageBox.information(self, "Субтитры", "Нет строк субтитров.")
            return
        p, _ = QFileDialog.getSaveFileName(self, "Сохранить SRT", f"{self.clip['title'][:40]}.srt", "SRT (*.srt)")
        if p:
            Path(p).write_text(to_srt(self.lines), encoding="utf-8")
            self.status.setText(f"Сохранено: {p}")

    # ------------------------------------------------------------ сохранение и рендер
    def _save(self) -> None:
        a, b = self.sp_a.value(), self.sp_b.value()
        old_a, old_b = self.clip["start"], self.clip["end"]
        if abs(a - old_a) > 0.01 or abs(b - old_b) > 0.01:
            if not self.lines_edited and self.transcript is not None:
                self._rebuild_lines()
            else:
                d = old_a - a
                for l in self.lines:
                    l.start += d; l.end += d
                    for w in l.words:
                        w.start += d; w.end += d
                self.lines = [l for l in self.lines if l.end > 0 and l.start < b - a]
            self._fill_table()
        e = self.es
        overrides = {"audio": e.audio.__dict__.copy(), "effects": {"crop_mode": e.effects.crop_mode, "manual_crop_x": e.effects.manual_crop_x},
                     "subtitles": {"enabled": e.subtitles.enabled}}
        self.sv.db.update_clip(self.clip["id"], start=a, end_=b, subtitles=lines_to_json(self.lines), overrides=overrides)
        self.clip = self.sv.db.get_clip(self.clip["id"])

    def _job_args(self):
        p = self.project
        return (self.sv.db, self.clip["id"], Path(p["video_path"]), probe(p["video_path"]), self.base, Path(p["out_dir"]),
                Path(p["work_dir"]), self.transcript)

    def _busy(self, v: bool, text: str = "") -> None:
        self.b_prev.setEnabled(not v); self.b_build.setEnabled(not v)
        self.status.setText(text)

    def preview(self) -> None:
        if not self.clip:
            return
        self._save()
        out = Path(self.project["work_dir"]) / f"preview_{self.clip['id']}.mp4"
        args = self._job_args()
        self._busy(True, "Собираю быстрый предпросмотр…")
        self.sv.bridge.run_bg(lambda: render_clip_row(*args, preview_to=out), lambda r, err: self._after(r, err, False))

    def rebuild(self) -> None:
        if not self.clip:
            return
        self._save()
        args = self._job_args()
        self._busy(True, "Пересборка клипа…")
        self.sv.bridge.run_bg(lambda: render_clip_row(*args, force=True), lambda r, err: self._after(r, err, True))

    def _after(self, res, err, final: bool) -> None:
        self._busy(False)
        if err is not None:
            self.status.setText("Ошибка: " + getattr(err, "message", str(err)))
            QMessageBox.warning(self, "Ошибка монтажа", getattr(err, "user_text", lambda: str(err))())
            return
        self.status.setText("Клип пересобран и сохранён." if final else "Предпросмотр готов.")
        if HAVE_MM:
            self.res_player.setSource(QUrl.fromLocalFile(str(res)))
        if final:
            self.clip = self.sv.db.get_clip(self.clip["id"])
            self.main.gallery_page.reload_clips()
        self.play_both()

    # ------------------------------------------------------------ плееры
    def play_both(self) -> None:
        if not HAVE_MM or not self.clip:
            return
        self.orig_player.setPosition(int(self.sp_a.value() * 1000))
        self.res_player.setPosition(0)
        self.orig_player.play(); self.res_player.play()

    def stop_both(self) -> None:
        if HAVE_MM:
            self.orig_player.pause(); self.res_player.pause()

    def _orig_pos(self, ms: int) -> None:
        t = ms / 1000
        self.bar.set_pos(t)
        if t >= self.sp_b.value():
            self.orig_player.pause()

    def _res_pos(self, _ms: int) -> None:
        pass

    def open_external(self) -> None:
        if self.clip and self.clip.get("file_path") and Path(self.clip["file_path"]).exists():
            open_path(self.clip["file_path"])
        else:
            QMessageBox.information(self, "Файл", "Готового файла пока нет — нажмите «Пересобрать клип».")

    def leave(self) -> None:
        self.stop_both()
