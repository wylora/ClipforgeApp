"""Главная страница: импорт, очередь, режим, запуск."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QRadioButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..config import MODES
from ..downloader import fetch_info, is_url
from ..errors import ClipForgeError
from ..ffmpeg_utils import probe
from ..transcribe import find_sidecar, whisper_available, whisper_model_present
from ..util import fmt_size, fmt_time
from .widgets import Card, label

MODE_HINTS = {
    "funny": "Шутки, реакции, неожиданные повороты, смех",
    "serious": "Факты, выводы, советы, законченные мысли",
    "best": "Самое интересное и динамичное",
    "custom": "Опишите, что искать",
}


class HomePage(QWidget):
    def __init__(self, services, main) -> None:
        super().__init__()
        self.setObjectName("page")
        self.sv, self.main = services, main
        self.items: list[dict] = []
        s = services.settings
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        root.addWidget(label("Нарезка видео", "h1"))
        root.addWidget(label("Вставьте ссылку или выберите файл — программа найдёт лучшие моменты, смонтирует и сохранит клипы.", "muted"))

        c = Card("1. Источник")
        row = QHBoxLayout()
        self.url = QLineEdit(s.ui.last_url)
        self.url.setPlaceholderText("Ссылка на видео (YouTube и другие, поддерживаемые yt-dlp)")
        self.url.returnPressed.connect(self.add_url)
        b_url = QPushButton("Добавить ссылку")
        b_url.clicked.connect(self.add_url)
        b_file = QPushButton("Выбрать файлы…")
        b_file.clicked.connect(self.add_files)
        row.addWidget(self.url, 1); row.addWidget(b_url); row.addWidget(b_file)
        c.lay.addLayout(row)
        self.rights = QCheckBox("У меня есть право скачивать и обрабатывать это видео (требуется только для ссылок)")
        self.rights.setChecked(s.ui.rights_confirmed)
        self.rights.toggled.connect(self._rights)
        c.lay.addWidget(self.rights)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Видео", "Длительность", "Разрешение", "Размер", "Статус"])
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumHeight(130)
        self.tree.setColumnWidth(0, 360)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        c.lay.addWidget(self.tree)
        r2 = QHBoxLayout()
        b_rm = QPushButton("Убрать выбранное")
        b_rm.clicked.connect(self.remove_selected)
        b_clr = QPushButton("Очистить список")
        b_clr.clicked.connect(self.clear)
        r2.addWidget(b_rm); r2.addWidget(b_clr); r2.addStretch(1)
        self.out_lbl = QLabel()
        self.out_lbl.setObjectName("muted")
        b_out = QPushButton("Папка результатов…")
        b_out.clicked.connect(self.pick_out)
        r2.addWidget(self.out_lbl); r2.addWidget(b_out)
        c.lay.addLayout(r2)
        root.addWidget(c)

        m = Card("2. Что искать")
        self.group = QButtonGroup(self)
        mr = QHBoxLayout()
        for key, title in MODES.items():
            rb = QRadioButton(f"{title}\n{MODE_HINTS[key]}")
            rb.setProperty("mode", key)
            rb.setChecked(s.clip.mode == key)
            self.group.addButton(rb)
            mr.addWidget(rb)
        m.lay.addLayout(mr)
        self.prompt = QLineEdit(s.clip.custom_prompt)
        self.prompt.setPlaceholderText("Например: «Найди все смешные реакции ведущего» или «Выбери самые интересные истории»")
        self.prompt.textChanged.connect(self._prompt)
        m.lay.addWidget(self.prompt)
        self.group.buttonClicked.connect(self._mode)
        root.addWidget(m)

        a = QHBoxLayout()
        self.start_btn = QPushButton("▶  Начать анализ")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start)
        self.stage = label("Готов к работе.", "stage")
        a.addWidget(self.start_btn); a.addWidget(self.stage, 1)
        root.addLayout(a)
        root.addStretch(1)
        self._mode()
        self._refresh_out()
        services.bridge.url_info.connect(self._url_info)

    # ------------------------------------------------------------ настройки
    def _rights(self, v: bool) -> None:
        self.sv.settings.ui.rights_confirmed = v
        self.sv.save_settings()

    def _mode(self, *_a) -> None:
        for b in self.group.buttons():
            if b.isChecked():
                self.sv.settings.clip.mode = b.property("mode")
        self.prompt.setEnabled(self.sv.settings.clip.mode == "custom")
        self.sv.save_settings()

    def _prompt(self, t: str) -> None:
        self.sv.settings.clip.custom_prompt = t
        self.sv.save_settings()

    def _refresh_out(self) -> None:
        self.out_lbl.setText("Сохранение: " + self.sv.settings.export.output_dir)

    def pick_out(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Папка для готовых клипов", self.sv.settings.export.output_dir)
        if p:
            self.sv.settings.export.output_dir = p
            self.sv.save_settings()
            self._refresh_out()
            self.main.settings_page.binder.refresh()

    # ------------------------------------------------------------ очередь
    def _add_item(self, source: str, title: str, dur: float, res: str, size: str, status: str = "в списке") -> QTreeWidgetItem:
        it = QTreeWidgetItem([title, fmt_time(dur) if dur else "—", res, size, status])
        it.setData(0, Qt.UserRole, source)
        self.tree.addTopLevelItem(it)
        return it

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Выберите видео", "", "Видео (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.flv *.ts);;Все файлы (*)")
        for p in paths:
            if any(self.tree.topLevelItem(i).data(0, Qt.UserRole) == p for i in range(self.tree.topLevelItemCount())):
                continue
            try:
                mi = probe(p)
                if not mi.has_video:
                    raise ClipForgeError("В файле нет видеопотока.")
                self._add_item(p, Path(p).name, mi.duration, mi.resolution, fmt_size(mi.size_bytes))
            except ClipForgeError as e:
                QMessageBox.warning(self, "Файл не подходит", f"{Path(p).name}\n\n{e.user_text()}")

    def add_url(self) -> None:
        url = self.url.text().strip()
        if not url:
            return
        if not is_url(url):
            QMessageBox.warning(self, "Ссылка", "Введите корректную ссылку, начинающуюся с http:// или https://")
            return
        self.sv.settings.ui.last_url = url
        it = self._add_item(url, url, 0, "—", "—", "проверка ссылки…")
        self.sv.bridge.run_bg(lambda: fetch_info(url), lambda res, err, it=it: self._url_done(it, res, err))

    def _url_done(self, it: QTreeWidgetItem, info, err) -> None:
        if err is not None:
            it.setText(4, "недоступно")
            msg = err.user_text() if isinstance(err, ClipForgeError) else str(err)
            it.setToolTip(4, msg)
            QMessageBox.warning(self, "Ссылка недоступна", msg)
            return
        it.setText(0, info["title"] or it.text(0))
        it.setText(1, fmt_time(info["duration"]) if info["duration"] else "—")
        it.setText(2, f"{info['width']}×{info['height']}" if info.get("width") else "—")
        it.setText(3, fmt_size(info["filesize"]) if info.get("filesize") else "—")
        it.setText(4, "доступно")

    def _url_info(self, *_a) -> None:
        pass

    def remove_selected(self) -> None:
        for it in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))

    def clear(self) -> None:
        self.tree.clear()

    # ------------------------------------------------------------ запуск
    def _preflight(self, sources: list[str]) -> bool:
        s = self.sv.settings
        if s.clip.mode == "custom" and not s.clip.custom_prompt.strip():
            QMessageBox.warning(self, "Свой сценарий", "Опишите, какие моменты нужно найти.")
            return False
        if any(is_url(x) for x in sources) and not s.ui.rights_confirmed:
            QMessageBox.warning(self, "Права на видео", "Подтвердите, что у вас есть право скачивать и обрабатывать видео по ссылкам.")
            return False
        need_whisper = [x for x in sources if is_url(x) or find_sidecar(Path(x)) is None]
        if need_whisper:
            if not whisper_available():
                r = QMessageBox.question(self, "Нет распознавания речи",
                                         "Не установлен faster-whisper, а рядом с видео нет файла субтитров (.srt/.vtt).\n"
                                         "Без текста обработка остановится с ошибкой.\n\nУстановите: pip install faster-whisper.\nВсё равно продолжить?")
                return r == QMessageBox.Yes
            if not whisper_model_present(s.analysis.whisper_model) and not s.analysis.whisper_consent:
                r = QMessageBox.question(self, "Загрузка модели Whisper",
                                         f"Для распознавания речи нужно скачать модель «{s.analysis.whisper_model}» "
                                         "(от ~75 МБ до ~3 ГБ в зависимости от размера, требуется интернет).\n\nСкачать при первом запуске?")
                if r != QMessageBox.Yes:
                    return False
                s.analysis.whisper_consent = True
                self.sv.save_settings()
        return True

    def start(self) -> None:
        n = self.tree.topLevelItemCount()
        if n == 0:
            QMessageBox.information(self, "Нет видео", "Добавьте ссылку или выберите файл.")
            return
        sources = [self.tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(n)]
        if not self._preflight(sources):
            return
        started = 0
        for i, src in enumerate(sources):
            it = self.tree.topLevelItem(i)
            try:
                job = self.sv.submit(src)
                it.setText(4, f"в очереди (задача {job.id})")
                it.setData(1, Qt.UserRole, job.id)
                started += 1
                if started == 1:
                    self.main.progress_page.focus_job(job.id)
            except ClipForgeError as e:
                it.setText(4, "пропущено")
                QMessageBox.warning(self, "Очередь", e.user_text())
        if started:
            self.stage.setText(f"Запущено задач: {started}")
            self.main.show_page("progress")

    def update_item_status(self, job_id: int, text: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(1, Qt.UserRole) == job_id:
                it.setText(4, text)
        self.stage.setText(text)
