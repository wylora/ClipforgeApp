"""Конфигурация: пути, настройки (dataclass → JSON), профили монтажа."""
from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

APP_NAME = "ClipForge"

MODES = {
    "funny": "Смешные моменты",
    "serious": "Серьёзные моменты",
    "best": "Лучшие моменты",
    "custom": "Свой сценарий",
}

ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1)}


def app_home() -> Path:
    env = os.environ.get("CLIPFORGE_HOME")
    if env:
        p = Path(env)
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / APP_NAME
    else:
        p = Path.home() / ".clipforge"
    p.mkdir(parents=True, exist_ok=True)
    return p


def subdir(name: str) -> Path:
    p = app_home() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_output_dir() -> str:
    videos = Path.home() / "Videos"
    base = videos if videos.exists() else Path.home()
    return str(base / APP_NAME)


# Веса итоговой оценки для каждого режима (настраиваются в интерфейсе).
DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "funny": {"semantic": 0.18, "humor": 0.36, "reaction": 0.15, "audio": 0.09, "completeness": 0.14, "visual": 0.08},
    "serious": {"semantic": 0.42, "humor": 0.0, "reaction": 0.04, "audio": 0.08, "completeness": 0.34, "visual": 0.04},
    "best": {"semantic": 0.26, "humor": 0.14, "reaction": 0.10, "audio": 0.20, "completeness": 0.16, "visual": 0.14},
    "custom": {"semantic": 0.55, "humor": 0.05, "reaction": 0.07, "audio": 0.07, "completeness": 0.21, "visual": 0.05},
}


@dataclass
class ClipSettings:
    mode: str = "funny"
    custom_prompt: str = ""
    auto_duration: bool = True
    min_duration: float = 12.0
    max_duration: float = 60.0
    target_duration: float = 30.0
    max_clips: int = 5
    min_score: float = 45.0
    max_overlap: float = 0.0


@dataclass
class SubtitleSettings:
    enabled: bool = True
    style: str = "karaoke"          # classic | karaoke | bold_pop | minimal | boxed
    font: str = "Arial"
    size: int = 64                  # для высоты кадра 1920; масштабируется
    color: str = "#FFFFFF"
    highlight: str = "#FFD400"
    outline_color: str = "#000000"
    outline: int = 5
    position: str = "auto"          # auto | top | center | bottom
    words_per_line: int = 4
    uppercase: bool = False
    translate_to: str = ""          # "" | "en" | код языка (нужна LLM)


@dataclass
class AudioSettings:
    original_volume: int = 100      # 0..200 %
    music_mode: str = "off"         # off | auto (не для смешных) | on
    music_path: str = ""
    music_mood: str = "auto"        # auto | cheerful | energetic | calm | dramatic | motivational
    music_volume: int = 22          # 0..100 %
    ducking: int = 70               # 0..100
    normalize: bool = True
    denoise: bool = False
    mute_original: bool = False


@dataclass
class EffectSettings:
    profile: str = "minimal"
    zoom_punch: bool = False
    slow_zoom: bool = False
    slowmo_punch: bool = False
    speed: float = 1.0
    fade: float = 0.2
    color_grade: str = "none"       # none | light | cinematic
    vignette: bool = False
    punch_flash: bool = False
    sfx: bool = False
    remove_silence: bool = False
    intro_title: bool = False
    outro_text: str = ""
    crop_mode: str = "auto"         # auto (лицо) | center | manual | fit (размытый фон)
    manual_crop_x: float = 0.5


@dataclass
class ExportSettings:
    aspect: str = "9:16"
    short_side: int = 1080
    fps: int = 30
    crf: int = 21
    preset: str = "medium"
    bitrate_kbps: int = 0           # 0 = использовать CRF
    audio_bitrate: str = "160k"
    output_dir: str = field(default_factory=default_output_dir)
    name_template: str = "{index:02d}_{title}"
    export_srt: bool = True


@dataclass
class AnalysisSettings:
    whisper_model: str = "small"
    device: str = "auto"            # auto | cpu | cuda
    language: str = "auto"
    whisper_consent: bool = False   # согласие на загрузку модели
    use_faces: bool = True
    use_video_analysis: bool = True
    use_llm: bool = False
    llm_url: str = "http://localhost:11434/v1"
    llm_model: str = ""
    llm_api_key: str = ""
    ram_limit_mb: int = 0           # 0 = без ограничения
    render_workers: int = 2
    parallel_jobs: int = 1
    keep_source_download: bool = True
    weights: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_WEIGHTS))


@dataclass
class UISettings:
    theme: str = "dark"
    rights_confirmed: bool = False
    ffmpeg_dir: str = ""
    last_url: str = ""


@dataclass
class Settings:
    clip: ClipSettings = field(default_factory=ClipSettings)
    subtitles: SubtitleSettings = field(default_factory=SubtitleSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    effects: EffectSettings = field(default_factory=EffectSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    ui: UISettings = field(default_factory=UISettings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Settings":
        s = cls()
        for f in fields(s):
            sub = getattr(s, f.name)
            src = (data or {}).get(f.name)
            if is_dataclass(sub) and isinstance(src, dict):
                for k, v in src.items():
                    if hasattr(sub, k):
                        setattr(sub, k, v)
        w = s.analysis.weights
        for m, d in DEFAULT_WEIGHTS.items():       # дополняем недостающие веса
            w.setdefault(m, dict(d))
            for k, v in d.items():
                w[m].setdefault(k, v)
        return s

    def copy(self) -> "Settings":
        return Settings.from_dict(copy.deepcopy(self.to_dict()))

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls()


def settings_path() -> Path:
    return app_home() / "settings.json"


# ---------------------------------------------------------------- профили монтажа
PROFILES: dict[str, dict] = {
    "minimal": dict(
        effects=dict(zoom_punch=False, slow_zoom=False, slowmo_punch=False, speed=1.0, fade=0.15, color_grade="none",
                     vignette=False, punch_flash=False, sfx=False, remove_silence=False),
        subtitles=dict(style="classic", uppercase=False),
    ),
    "dynamic": dict(
        effects=dict(zoom_punch=True, slow_zoom=False, slowmo_punch=False, speed=1.03, fade=0.2, color_grade="light",
                     vignette=False, punch_flash=False, sfx=True, remove_silence=True),
        subtitles=dict(style="karaoke", uppercase=False),
    ),
    "meme": dict(
        effects=dict(zoom_punch=True, slow_zoom=False, slowmo_punch=True, speed=1.0, fade=0.1, color_grade="light",
                     vignette=False, punch_flash=True, sfx=True, remove_silence=False),
        subtitles=dict(style="bold_pop", uppercase=True),
    ),
    "cinematic": dict(
        effects=dict(zoom_punch=False, slow_zoom=True, slowmo_punch=False, speed=1.0, fade=0.5, color_grade="cinematic",
                     vignette=True, punch_flash=False, sfx=False, remove_silence=False),
        subtitles=dict(style="minimal", uppercase=False),
    ),
}
PROFILE_TITLES = {"minimal": "Минимализм", "dynamic": "Динамичный", "meme": "Мемный", "cinematic": "Кинематографичный"}


def apply_profile(settings: Settings, name: str) -> None:
    prof = PROFILES.get(name)
    if not prof:
        return
    settings.effects.profile = name
    for k, v in prof["effects"].items():
        setattr(settings.effects, k, v)
    for k, v in prof["subtitles"].items():
        setattr(settings.subtitles, k, v)


def output_size(aspect: str, short_side: int) -> tuple[int, int]:
    a, b = ASPECTS.get(aspect, (9, 16))
    if a == b:
        w = h = short_side
    elif a < b:
        w, h = short_side, round(short_side * b / a)
    else:
        w, h = round(short_side * a / b), short_side
    return w - w % 2, h - h % 2
