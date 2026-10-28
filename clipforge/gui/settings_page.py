"""Настройки клипов, оформления, звука, эффектов и анализа."""
from __future__ import annotations

from PySide6.QtWidgets import (QDoubleSpinBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTabWidget,
                               QVBoxLayout, QWidget, QMessageBox)
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from ..audio_synth import MOOD_TITLES
from ..config import DEFAULT_WEIGHTS, MODES, PROFILE_TITLES, apply_profile
from ..music import catalog_dir, ensure_builtin_library
from ..resources import cuda_available
from .widgets import Binder, Card, form, label


def _tab(builder) -> QScrollArea:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(12)
    builder(lay)
    lay.addStretch(1)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    return sc


class SettingsPage(QWidget):
    def __init__(self, services, main) -> None:
        super().__init__()
        self.setObjectName("page")
        self.sv, self.main = services, main
        self.s = services.settings
        self.binder = Binder(services.save_settings)
        b = self.binder
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.addWidget(label("Настройки", "h1"))
        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        tabs.addTab(_tab(self._clips), "Клипы")
        tabs.addTab(_tab(self._format), "Формат и экспорт")
        tabs.addTab(_tab(self._subs), "Субтитры")
        tabs.addTab(_tab(self._audio), "Музыка и звук")
        tabs.addTab(_tab(self._effects), "Эффекты")
        tabs.addTab(_tab(self._analysis), "Анализ и система")

    # ------------------------------------------------------------ вкладки
    def _clips(self, lay) -> None:
        b, s = self.binder, self.s
        c = Card("Длительность и количество")
        form(c.lay, [
            ("", b.check("Автоматический выбор длительности по содержанию (короткие шутки не растягиваются)", s.clip, "auto_duration")),
            ("Минимальная длительность, с", b.spin(s.clip, "min_duration", 3, 180, 1, 0)),
            ("Максимальная длительность, с", b.spin(s.clip, "max_duration", 5, 300, 1, 0)),
            ("Желаемая длительность, с", b.spin(s.clip, "target_duration", 5, 180, 1, 0)),
            ("Количество клипов (максимум)", b.spin(s.clip, "max_clips", 1, 30)),
            ("Минимальный рейтинг качества", b.slider(s.clip, "min_score", 0, 100, "")),
            ("Допустимое перекрытие клипов, с", b.spin(s.clip, "max_overlap", 0, 30, 0.5, 1)),
        ])
        lay.addWidget(c)
        lay.addWidget(label("Если хороших моментов меньше, чем нужно, программа создаст меньше клипов и сообщит об этом — слабые клипы не делаются искусственно.", "muted"))
        c2 = Card("Профиль монтажа")
        self.profile = b.combo(s.effects, "profile", [(k, v) for k, v in PROFILE_TITLES.items()])
        self.profile.currentIndexChanged.connect(self._apply_profile)
        form(c2.lay, [("Профиль", self.profile)])
        c2.lay.addWidget(label("Профиль включает набор эффектов и стиль субтитров (их можно донастроить на вкладке «Эффекты»).", "muted"))
        lay.addWidget(c2)
        c3 = Card("Веса итогового рейтинга")
        self.wmode = QComboBox()
        for k, v in MODES.items():
            self.wmode.addItem(v, k)
        self.wmode.currentIndexChanged.connect(self._load_weights)
        c3.lay.addWidget(self.wmode)
        self.wspins: dict[str, QDoubleSpinBox] = {}
        rows = []
        for k, t in (("semantic", "Смысл (semantic)"), ("humor", "Юмор (humor)"), ("reaction", "Реакция (reaction)"),
                     ("audio", "Звук (audio)"), ("completeness", "Законченность (completeness)"), ("visual", "Видеоряд (visual)")):
            sp = QDoubleSpinBox(); sp.setRange(0, 1); sp.setSingleStep(0.05); sp.setDecimals(2)
            sp.valueChanged.connect(lambda v, key=k: self._set_weight(key, v))
            self.wspins[k] = sp
            rows.append((t, sp))
        form(c3.lay, rows)
        reset = QPushButton("Сбросить веса этого режима")
        reset.clicked.connect(self._reset_weights)
        c3.lay.addWidget(reset)
        lay.addWidget(c3)
        self._load_weights()

    def _apply_profile(self) -> None:
        apply_profile(self.s, self.profile.currentData())
        self.sv.save_settings()
        self.binder.refresh()

    def _load_weights(self) -> None:
        w = self.s.analysis.weights[self.wmode.currentData()]
        for k, sp in self.wspins.items():
            sp.blockSignals(True); sp.setValue(w.get(k, 0)); sp.blockSignals(False)

    def _set_weight(self, key: str, v: float) -> None:
        self.s.analysis.weights[self.wmode.currentData()][key] = v
        self.sv.save_settings()

    def _reset_weights(self) -> None:
        m = self.wmode.currentData()
        self.s.analysis.weights[m] = dict(DEFAULT_WEIGHTS[m])
        self._load_weights()
        self.sv.save_settings()

    def _format(self, lay) -> None:
        b, s = self.binder, self.s
        c = Card("Кадр и кодирование (MP4 · H.264 + AAC)")
        form(c.lay, [
            ("Формат кадра", b.combo(s.export, "aspect", [("9:16", "9:16 — Shorts / TikTok / Reels"), ("16:9", "16:9 — горизонтальный"), ("1:1", "1:1 — квадрат")])),
            ("Размер (короткая сторона)", b.combo(s.export, "short_side", [(720, "720 (HD)"), (1080, "1080 (Full HD)"), (1440, "1440 (2K)")])),
            ("FPS", b.combo(s.export, "fps", [(24, "24"), (25, "25"), (30, "30"), (50, "50"), (60, "60")])),
            ("Качество (CRF, меньше = лучше)", b.spin(s.export, "crf", 14, 35)),
            ("Битрейт, кбит/с (0 = по CRF)", b.spin(s.export, "bitrate_kbps", 0, 50000, 500)),
            ("Скорость кодирования", b.combo(s.export, "preset", [(p, p) for p in ("ultrafast", "veryfast", "fast", "medium", "slow")])),
            ("Аудио", b.combo(s.export, "audio_bitrate", [("128k", "128 кбит/с"), ("160k", "160 кбит/с"), ("192k", "192 кбит/с"), ("256k", "256 кбит/с")])),
            ("Шаблон имени файла", b.text(s.export, "name_template")),
            ("", b.check("Экспортировать субтитры в .srt рядом с клипом", s.export, "export_srt")),
        ])
        lay.addWidget(c)
        c2 = Card("Кадрирование для вертикального формата")
        form(c2.lay, [
            ("Режим", b.combo(s.effects, "crop_mode", [("auto", "Умный: следить за лицом/говорящим"), ("center", "По центру"),
                                                      ("manual", "Вручную (положение ниже)"), ("fit", "Без обрезки, размытый фон")])),
            ("Ручное положение (0 = лево, 100 = право)", self._crop_slider()),
        ])
        lay.addWidget(c2)
        lay.addWidget(label("Шаблон: {index} — номер, {title} — название, {mode} — режим, {score} — оценка. Пример: {index:02d}_{title}", "muted"))

    def _crop_slider(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QSlider
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, 100)
        sl.setValue(int(self.s.effects.manual_crop_x * 100))

        def ch(v: int) -> None:
            self.s.effects.manual_crop_x = v / 100.0
            self.sv.save_settings()
        sl.valueChanged.connect(ch)
        self.binder._refresh.append(lambda: sl.setValue(int(self.s.effects.manual_crop_x * 100)))
        return sl

    def _subs(self, lay) -> None:
        b, s = self.binder, self.s
        c = Card("Автоматические субтитры")
        form(c.lay, [
            ("", b.check("Накладывать субтитры на клипы", s.subtitles, "enabled")),
            ("Стиль", b.combo(s.subtitles, "style", [("classic", "Классический"), ("karaoke", "Караоке: подсветка слова"),
                                                    ("bold_pop", "Жирный с «поп»-акцентом"), ("minimal", "Минимальный"), ("boxed", "На плашке")])),
            ("Шрифт", b.text(s.subtitles, "font", "Arial")),
            ("Размер", b.spin(s.subtitles, "size", 24, 140)),
            ("Цвет текста", b.color(s.subtitles, "color")),
            ("Цвет текущего слова", b.color(s.subtitles, "highlight")),
            ("Цвет обводки", b.color(s.subtitles, "outline_color")),
            ("Толщина обводки", b.spin(s.subtitles, "outline", 0, 14)),
            ("Положение", b.combo(s.subtitles, "position", [("auto", "Авто (не закрывать лица)"), ("bottom", "Снизу"), ("center", "По центру"), ("top", "Сверху")])),
            ("Слов в строке", b.spin(s.subtitles, "words_per_line", 1, 8)),
            ("", b.check("ЗАГЛАВНЫЕ буквы", s.subtitles, "uppercase")),
            ("Перевод субтитров", b.combo(s.subtitles, "translate_to", [("", "Без перевода"), ("en", "English"), ("ru", "Русский"), ("es", "Español"), ("de", "Deutsch")])),
        ])
        lay.addWidget(c)
        lay.addWidget(label("Перевод требует LLM (вкладка «Анализ и система»): локальный сервер вроде Ollama или облачный API. Без неё остаётся оригинал. Текст и тайминги можно править в редакторе клипа.", "muted"))

    def _audio(self, lay) -> None:
        b, s = self.binder, self.s
        c = Card("Оригинальный звук")
        form(c.lay, [
            ("Громкость оригинала", b.slider(s.audio, "original_volume", 0, 200)),
            ("", b.check("Полностью отключить оригинальный звук", s.audio, "mute_original")),
            ("", b.check("Нормализовать итоговую громкость (−16 LUFS)", s.audio, "normalize")),
            ("", b.check("Уменьшить фоновый шум", s.audio, "denoise")),
        ])
        c.lay.addWidget(label("Значения выше 100% усиливают звук; ограничитель защищает от клиппинга, но сильно шумные записи лучше не усиливать.", "muted"))
        lay.addWidget(c)
        m = Card("Фоновая музыка")
        form(m.lay, [
            ("Режим", b.combo(s.audio, "music_mode", [("off", "Выключена"), ("auto", "Авто (кроме смешных роликов)"), ("on", "Всегда включена")])),
            ("Настроение", b.combo(s.audio, "music_mood", [("auto", "Подобрать под тип контента")] + [(k, v) for k, v in MOOD_TITLES.items()])),
            ("Свой файл музыки", b.file(s.audio, "music_path", "Музыка", "Аудио (*.mp3 *.wav *.flac *.m4a *.ogg *.aac)")),
            ("Громкость музыки", b.slider(s.audio, "music_volume", 0, 100)),
            ("Интенсивность приглушения при речи", b.slider(s.audio, "ducking", 0, 100)),
        ])
        row = QHBoxLayout()
        g = QPushButton("Создать встроенную подборку")
        g.clicked.connect(self._gen_music)
        o = QPushButton("Открыть каталог своей музыки")
        o.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(catalog_dir()))))
        row.addWidget(g); row.addWidget(o); row.addStretch(1)
        m.lay.addLayout(row)
        m.lay.addWidget(label(
            "Встроенные треки синтезируются самой программой (CC0, прав третьих лиц нет) — это простые подложки. "
            "Автоподбор берёт музыку из вашего каталога только если в catalog.json у трека указана лицензия: "
            '[{"file":"track.mp3","mood":"calm","license":"CC-BY 4.0","source":"URL"}]. '
            "Свой файл вы выбираете на свою ответственность. Во время речи музыка приглушается (ducking).", "muted"))
        lay.addWidget(m)

    def _gen_music(self) -> None:
        self.sv.bridge.run_bg(ensure_builtin_library, lambda res, err: QMessageBox.information(
            self, "Музыка", "Встроенная подборка готова." if err is None else f"Ошибка: {err}"))

    def _effects(self, lay) -> None:
        b, s = self.binder, self.s
        c = Card("Эффекты (включайте умеренно)")
        form(c.lay, [
            ("", b.check("Лёгкий зум на ключевой реплике", s.effects, "zoom_punch")),
            ("", b.check("Медленный наезд (кинематографичный)", s.effects, "slow_zoom")),
            ("", b.check("Замедление на панчлайне", s.effects, "slowmo_punch")),
            ("Скорость клипа", b.spin(s.effects, "speed", 0.7, 1.5, 0.05, 2, "×")),
            ("Плавное появление/затухание, с", b.spin(s.effects, "fade", 0, 1.5, 0.05, 2)),
            ("Цветокоррекция", b.combo(s.effects, "color_grade", [("none", "Нет"), ("light", "Лёгкая"), ("cinematic", "Кинематографичная")])),
            ("", b.check("Ненавязчивая виньетка", s.effects, "vignette")),
            ("", b.check("Визуальный акцент (вспышка) на панчлайне", s.effects, "punch_flash")),
            ("", b.check("Короткие звуковые эффекты", s.effects, "sfx")),
            ("", b.check("Удалять длинные паузы (паузу перед панчлайном сохраняет)", s.effects, "remove_silence")),
            ("", b.check("Заставка: название клипа в начале", s.effects, "intro_title")),
            ("Финальная надпись", b.text(s.effects, "outro_text", "например: Подпишись!")),
        ])
        lay.addWidget(c)

    def _analysis(self, lay) -> None:
        b, s = self.binder, self.s
        c = Card("Распознавание речи и анализ")
        gpu = "обнаружен" if cuda_available() else "не обнаружен"
        form(c.lay, [
            ("Модель Whisper", b.combo(s.analysis, "whisper_model", [("tiny", "tiny (~75 МБ, быстро)"), ("base", "base (~145 МБ)"), ("small", "small (~480 МБ, рекомендуется)"),
                                                                     ("medium", "medium (~1.5 ГБ)"), ("large-v3", "large-v3 (~3 ГБ, лучшее качество)")])),
            ("Устройство", b.combo(s.analysis, "device", [("auto", f"Авто (GPU CUDA: {gpu})"), ("cpu", "CPU"), ("cuda", "GPU (CUDA)")])),
            ("Язык речи", b.combo(s.analysis, "language", [("auto", "Определять автоматически"), ("ru", "Русский"), ("en", "English"), ("uk", "Українська"),
                                                          ("de", "Deutsch"), ("es", "Español"), ("fr", "Français")])),
            ("", b.check("Разрешаю скачивать модель Whisper при необходимости (нужен интернет)", s.analysis, "whisper_consent")),
            ("", b.check("Анализировать видеоряд (сцены, динамика, дубли)", s.analysis, "use_video_analysis")),
            ("", b.check("Искать лица и мимику (кроп, реакции)", s.analysis, "use_faces")),
        ])
        lay.addWidget(c)
        l = Card("LLM (необязательно)")
        form(l.lay, [
            ("", b.check("Использовать LLM для оценки лучших кандидатов, перевода и заголовков", s.analysis, "use_llm")),
            ("Адрес API (OpenAI-совместимый)", b.text(s.analysis, "llm_url")),
            ("Модель", b.text(s.analysis, "llm_model", "например: llama3.1:8b")),
            ("API-ключ (для облака)", b.text(s.analysis, "llm_api_key", "не нужен для локального сервера", password=True)),
        ])
        l.lay.addWidget(label("Требуется запущенный локальный сервер (Ollama, LM Studio) или облачный API с интернетом/ключом. Без LLM работает локальный алгоритм.", "muted"))
        lay.addWidget(l)
        r = Card("Ресурсы и интерфейс")
        form(r.lay, [
            ("Мягкий лимит памяти, МБ (0 = нет)", b.spin(s.analysis, "ram_limit_mb", 0, 65536, 512)),
            ("Параллельных видео (1–3)", b.spin(s.analysis, "parallel_jobs", 1, 3)),
            ("Параллельных рендеров клипов (1–3)", b.spin(s.analysis, "render_workers", 1, 3)),
            ("Папка с ffmpeg (если не в PATH)", b.file(s.ui, "ffmpeg_dir", "Папка с ffmpeg", "", folder=True)),
            ("Тема", b.combo(s.ui, "theme", [("dark", "Тёмная"), ("light", "Светлая")])),
        ])
        r.lay.addWidget(label("Лимит памяти — мягкий: уменьшает число потоков распознавания. Смена папки ffmpeg и числа параллельных задач применяется после перезапуска.", "muted"))
        lay.addWidget(r)
