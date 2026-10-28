"""Оркестратор: загрузка → анализ → отбор → монтаж → отчёт. С кэшем этапов, отменой и возобновлением."""
from __future__ import annotations

import json
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .audio_analysis import AudioFeatures, analyze_audio
from .config import MODES, Settings, subdir
from .db import Database
from .downloader import download, is_url
from .editor import ClipSpec, render_clip
from .errors import (AnalysisError, ClipForgeError, DependencyError, FFmpegError, InvalidMediaError, NoGoodMomentsError)
from .feedback import PersonalModel
from .ffmpeg_utils import MediaInfo, extract_audio_wav, probe
from .llm import LLMClient
from .metadata import detect_lang, make_metadata
from .models import Candidate, Transcript
from .resources import check_disk_space
from .selector import Ctx, find_candidates
from .subtitles import SubLine, build_lines, lines_from_json, lines_to_json, retext_line, to_srt
from .transcribe import (assign_speakers, build_units, find_sidecar, parse_subtitle_file, pseudo_units_from_audio,
                         transcribe_audio, whisper_available)
from .util import CancelToken, fmt_time, safe_filename, stable_hash
from .video_analysis import HAVE_CV2, VideoFeatures, scan_video

log = logging.getLogger("clipforge.pipeline")

STAGES = [("download", "Загрузка", 8), ("prepare", "Подготовка", 4), ("transcribe", "Транскрибация", 30),
          ("audio", "Анализ звука", 8), ("video", "Анализ видео", 14), ("select", "Выбор моментов", 6),
          ("render", "Монтаж и экспорт", 30)]


@dataclass
class ProgressEvent:
    stage: str
    stage_label: str
    overall: float          # 0..1
    message: str
    eta: float | None = None


@dataclass
class PipelineResult:
    project_id: int
    clips: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    out_dir: str = ""
    message: str = ""


class Tracker:
    def __init__(self, cb: Callable[[ProgressEvent], None]):
        self.cb, self.t0 = cb, time.time()
        self.total = sum(w for *_, w in STAGES)
        self.done = 0.0
        self.cur = STAGES[0]

    def start(self, key: str) -> None:
        for i, (k, label, w) in enumerate(STAGES):
            if k == key:
                self.done = sum(x[2] for x in STAGES[:i])
                self.cur = (k, label, w)
        self.update(0.0, "")

    def update(self, frac: float, msg: str = "") -> None:
        k, label, w = self.cur
        overall = (self.done + w * max(0.0, min(1.0, frac))) / self.total
        el = time.time() - self.t0
        eta = el / overall * (1 - overall) if overall > 0.04 and el > 3 else None
        self.cb(ProgressEvent(k, label, overall, msg, eta))


class Pipeline:
    def __init__(self, db: Database, settings: Settings, source: str, token: CancelToken | None = None,
                 on_progress: Callable[[ProgressEvent], None] | None = None,
                 on_log: Callable[[str], None] | None = None, project_id: int | None = None):
        self.db, self.s, self.source = db, settings, source
        self.token = token or CancelToken()
        self._log = on_log or (lambda m: None)
        self.tr = Tracker(on_progress or (lambda e: None))
        self.project_id = project_id
        self.warnings: list[str] = []

    # ------------------------------------------------------------ helpers
    def log(self, msg: str) -> None:
        log.info(msg)
        self._log(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        log.warning(msg)
        self._log("⚠ " + msg)

    def _paths(self, pid: int) -> tuple[Path, Path]:
        root = subdir("projects") / f"p{pid}"
        work = root / "work"
        work.mkdir(parents=True, exist_ok=True)
        return root, work

    # ------------------------------------------------------------ run
    def run(self) -> PipelineResult:
        s, tk = self.s, self.token
        if self.project_id is None:
            self.project_id = self.db.add_project(self.source, Path(self.source).stem if not is_url(self.source) else self.source,
                                                  s.clip.mode, s.to_dict(), s.export.output_dir)
        pid = self.project_id
        root, work = self._paths(pid)
        self.db.update_project(pid, status="running", work_dir=str(work), settings=s.to_dict(), message="")
        try:
            return self._run(pid, root, work)
        except NoGoodMomentsError as e:
            self.db.update_project(pid, status="no_clips", message=e.user_text())
            raise
        except ClipForgeError as e:
            self.db.update_project(pid, status="cancelled" if type(e).__name__ == "CancelledError" else "error",
                                   message=e.user_text())
            raise
        except Exception as e:
            self.db.update_project(pid, status="error", message=str(e))
            raise

    def _run(self, pid: int, root: Path, work: Path) -> PipelineResult:
        s, tk, tr = self.s, self.token, self.tr
        # 1. источник
        tr.start("download")
        if is_url(self.source):
            if not s.ui.rights_confirmed:
                raise DependencyError("Не подтверждено право на загрузку и обработку видео.",
                                      hint="Поставьте галочку «У меня есть право скачивать и обрабатывать это видео».")
            existing = db_video = (self.db.get_project(pid) or {}).get("video_path")
            if db_video and Path(db_video).exists():
                video = Path(db_video)
                self.log("Видео уже загружено — пропускаю скачивание")
            else:
                self.log("Загрузка видео…")
                video, meta = download(self.source, root / "source", tk, lambda f, m: tr.update(f, m))
                self.db.update_project(pid, title=meta.get("title") or video.stem)
        else:
            video = Path(self.source)
            if not video.exists():
                raise InvalidMediaError(f"Файл не найден: {video}")
        # 2. подготовка
        tr.start("prepare")
        info = probe(video)
        if not info.has_video:
            raise InvalidMediaError("В файле нет видеопотока.")
        if info.duration < 8:
            raise InvalidMediaError(f"Видео слишком короткое ({info.duration:.1f} с) для поиска моментов.")
        if not info.has_audio:
            self.warn("В видео нет звуковой дорожки — анализ речи и звука невозможен.")
        self.log(f"Видео: {info.resolution}, {fmt_time(info.duration)}, {info.size_bytes / 1e6:.0f} МБ")
        title = (self.db.get_project(pid) or {}).get("title") or video.stem
        out_root = Path(s.export.output_dir) / f"{safe_filename(title, 60)}_{pid}"
        check_disk_space(out_root, int(info.size_bytes * 0.6 + 600e6))
        check_disk_space(work, int(info.duration * 16000 * 2 * 1.2 + 100e6))
        self.db.update_project(pid, video_path=str(video), duration=info.duration, out_dir=str(out_root))
        tk.raise_if_cancelled()

        # 3. транскрипт
        tr.start("transcribe")
        transcript = self._transcript(video, info, work)
        self.db.update_project(pid, language=transcript.language)
        self.log(f"Язык: {transcript.language}; реплик: {len(transcript.segments)}; источник: {transcript.source}")
        tk.raise_if_cancelled()

        # 4. аудио
        tr.start("audio")
        audio = None
        if info.has_audio:
            f = work / "audio.npz"
            if f.exists():
                audio = AudioFeatures.load(f)
                self.log("Анализ звука: взято из кэша")
            else:
                self.log("Анализ звука…")
                audio = analyze_audio(video, tk, lambda x: tr.update(x, "Анализ звука"), info.duration)
                audio.save(f)
        # 5. видео
        tr.start("video")
        vid = self._video_features(video, info, work)
        tk.raise_if_cancelled()

        # 6. выбор
        tr.start("select")
        units = build_units(transcript)
        if not units:
            self.warn("Речь не найдена — анализ только по звуку и видео, без текста.")
            units = pseudo_units_from_audio(audio)
        nspk = assign_speakers(units, audio)
        if nspk:
            self.log(f"Эвристика говорящих: ~{nspk} голос(а) (приблизительно)")
        llm = self._llm()
        ctx = Ctx(transcript, units, audio, vid, s, info.duration, llm, PersonalModel(self.db.feedback_samples()), self.log)
        key = stable_hash([s.clip.__dict__, s.analysis.weights, transcript.source, bool(vid), bool(llm)])
        proj = self.db.get_project(pid) or {}
        existing_clips = self.db.get_clips(pid)
        if existing_clips and proj.get("cand_key") == key:
            self.log("Параметры отбора не изменились — продолжаю с сохранённых клипов")
            clips = existing_clips
        else:
            cands = find_candidates(ctx, tk, lambda f, m: tr.update(f, m))
            self._save_candidates(work, cands)
            chosen = [c for c in cands if c.selected]
            if not chosen:
                best = cands[0].quality if cands else 0
                raise NoGoodMomentsError(
                    f"Достаточно хороших моментов не найдено (лучший кандидат: {best:.0f} баллов, порог {s.clip.min_score:.0f}).",
                    hint="Снизьте порог качества, смените режим или включите «Лучшие моменты». Слабые клипы не создаются намеренно.")
            for old in existing_clips:
                for k in ("file_path", "thumb_path", "srt_path"):
                    if old.get(k):
                        Path(old[k]).unlink(missing_ok=True)
            self.db.delete_clips(pid)
            clips = self._create_clip_rows(pid, chosen, transcript, audio, vid, llm)
            self.db.update_project(pid, cand_key=key)
            self.log(f"Выбрано клипов: {len(clips)} из {len(cands)} кандидатов")
        tk.raise_if_cancelled()

        # 7. монтаж
        tr.start("render")
        out_root.mkdir(parents=True, exist_ok=True)
        results = self._render_all(pid, video, info, clips, out_root, work, transcript)
        done = [c for c in self.db.get_clips(pid) if c.get("status") == "done"]
        if not done:
            raise FFmpegError("Ни один клип не удалось смонтировать.", hint="См. лог: возможно, проблема с FFmpeg или кодеками.")
        report = self._report(pid, title, info, transcript, out_root, work)
        self.db.update_project(pid, status="done", report_path=str(report), message="; ".join(self.warnings))
        self._cleanup(work)
        tr.update(1.0, "Готово")
        return PipelineResult(pid, done, self.warnings, str(out_root), f"Создано клипов: {len(done)}")

    # ------------------------------------------------------------ этапы
    def _transcript(self, video: Path, info: MediaInfo, work: Path) -> Transcript:
        cache, tr, tk = work / "transcript.json", self.tr, self.token
        sidecar = find_sidecar(video)
        src_key = f"sidecar:{sidecar.name}" if sidecar else f"whisper:{self.s.analysis.whisper_model}"
        if cache.exists():
            t = Transcript.load(cache)
            if t.source.startswith(src_key.split(":")[0]):
                self.log("Транскрипт взят из кэша")
                return t
        if sidecar:
            self.log(f"Использую готовые субтитры: {sidecar.name} (Whisper не нужен)")
            t = parse_subtitle_file(sidecar)
            t.language = detect_lang(" ".join(sg.text for sg in t.segments[:50]))
            t.duration = info.duration
        else:
            if not info.has_audio:
                raise AnalysisError("В видео нет звука, а файла субтитров рядом нет — анализировать нечего.")
            if not whisper_available():
                raise DependencyError("Нет ни faster-whisper, ни файла субтитров.",
                                      hint="pip install faster-whisper — или положите .srt/.vtt с тем же именем рядом с видео.")
            wav = work / "audio16k.wav"
            self.log("Извлечение звука…")
            extract_audio_wav(video, wav, tk, lambda x: tr.update(x * 0.1, "Извлечение звука"), info.duration)
            self.log(f"Распознавание речи (Whisper {self.s.analysis.whisper_model})…")
            t = transcribe_audio(wav, self.s.analysis, tk, lambda x: tr.update(0.1 + 0.9 * x, "Распознавание речи"), info.duration)
        t.save(cache)
        return t

    def _video_features(self, video: Path, info: MediaInfo, work: Path) -> VideoFeatures | None:
        a = self.s.analysis
        if not a.use_video_analysis:
            return None
        if not HAVE_CV2:
            self.warn("opencv-python не установлен — анализ видеоряда пропущен (оценка по звуку и тексту).")
            return None
        f = work / f"video_{int(a.use_faces)}.npz"
        if f.exists():
            self.log("Анализ видео: взято из кэша")
            return VideoFeatures.load(f)
        self.log("Анализ видеоряда (сцены, лица, динамика)…")
        try:
            vf = scan_video(video, info, fps=2.0, use_faces=a.use_faces, token=self.token,
                            progress_cb=lambda x: self.tr.update(x, "Анализ видеоряда"))
            vf.save(f)
            return vf
        except Exception as e:
            if type(e).__name__ == "CancelledError":
                raise
            self.warn(f"Анализ видеоряда не удался ({e}) — продолжаю без него.")
            return None

    def _llm(self) -> LLMClient | None:
        a = self.s.analysis
        if not a.use_llm:
            return None
        if not a.llm_model:
            self.warn("LLM включена, но модель не указана — используется локальный алгоритм.")
            return None
        c = LLMClient(a.llm_url, a.llm_model, a.llm_api_key)
        if not c.available():
            self.warn(f"LLM-сервер {a.llm_url} недоступен — используется локальный алгоритм.")
            return None
        self.log(f"LLM подключена: {a.llm_model}")
        return c

    def _save_candidates(self, work: Path, cands: list[Candidate]) -> None:
        (work / "candidates.json").write_text(json.dumps([c.to_dict() for c in cands], ensure_ascii=False), encoding="utf-8")

    def _create_clip_rows(self, pid: int, chosen: list[Candidate], transcript: Transcript, audio, vid, llm) -> list[dict]:
        words = transcript.all_words()
        rows = []
        for i, c in enumerate(sorted(chosen, key=lambda x: -x.quality), 1):
            meta = make_metadata(c.text, c.mode, llm=llm)
            llm_title = (c.details.get("llm") or {}).get("title")
            if llm_title:
                meta["titles"].insert(0, llm_title)
            lines = build_lines(words, c.start, c.end, self.s.subtitles.words_per_line,
                                max_chars=22 if self.s.export.aspect != "16:9" else 38)
            lines = self._translate(lines, c, llm)
            cid = self.db.add_clip(pid, i, start=c.start, end_=c.end, anchor=c.anchor_time, mode=c.mode,
                                   title=meta["titles"][0], titles=meta["titles"], description=meta["description"],
                                   hashtags=" ".join("#" + h for h in meta["hashtags"]), scores=c.scores,
                                   reasons=c.reasons, details=c.details, text=c.text, subtitles=lines_to_json(lines))
            rows.append(self.db.get_clip(cid))
        return rows

    def _translate(self, lines: list[SubLine], c: Candidate, llm) -> list[SubLine]:
        target = self.s.subtitles.translate_to
        if not target or not lines:
            return lines
        if llm is not None:
            res = llm.translate_lines([l.text for l in lines], target)
            if res:
                for l, t in zip(lines, res):
                    retext_line(l, t)
                return lines
            self.warn("Перевод субтитров через LLM не удался — оставлен оригинал.")
            return lines
        self.warn("Перевод субтитров требует LLM (Настройки → Анализ). Оставлен оригинал.")
        return lines

    def _render_all(self, pid, video, info, clips, out_root, work, transcript) -> None:
        tk, n = self.token, len(clips)
        counter = {"done": 0}
        workers = max(1, min(self.s.analysis.render_workers, 3, n))
        progress: dict[int, float] = {}

        def upd(cid: int, f: float) -> None:
            progress[cid] = f
            self.tr.update(sum(progress.values()) / n, f"Монтаж клипов: {counter['done']}/{n}")

        def job(row: dict) -> None:
            try:
                render_clip_row(self.db, row["id"], video, info, self.s, out_root, work, transcript, tk,
                                lambda f: upd(row["id"], f))
            except Exception as e:
                if type(e).__name__ == "CancelledError":
                    raise
                msg = e.user_text() if isinstance(e, ClipForgeError) else str(e)
                self.db.update_clip(row["id"], status="error", error=msg)
                self.warn(f"Клип {row['idx']}: ошибка монтажа — {msg} {getattr(e, 'details', '')[-300:]}")
            finally:
                counter["done"] += 1
                upd(row["id"], 1.0)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(job, r) for r in clips]
            try:
                for f in as_completed(futs):
                    f.result()
            except BaseException:
                tk.cancel()
                for f in futs:
                    f.cancel()
                raise

    def _report(self, pid: int, title: str, info: MediaInfo, transcript: Transcript, out_root: Path, work: Path) -> Path:
        clips = self.db.get_clips(pid)
        cands = []
        try:
            cands = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        lines = [f"# Отчёт ClipForge: {title}", "",
                 f"- Режим: {MODES.get(self.s.clip.mode)}", f"- Длительность: {fmt_time(info.duration)}, {info.resolution}",
                 f"- Язык: {transcript.language}; источник транскрипта: {transcript.source}",
                 f"- Рассмотрено кандидатов: {len(cands)}; создано клипов: {sum(c['status'] == 'done' for c in clips)}", ""]
        if self.warnings:
            lines += ["## Предупреждения", *[f"- {w}" for w in self.warnings], ""]
        lines.append("## Клипы")
        for c in clips:
            sc = c.get("scores") or {}
            lines += [f"### {c['idx']:02d}. {c['title']}", f"- Интервал: {fmt_time(c['start'])} – {fmt_time(c['end'])} ({c['end'] - c['start']:.1f} с)",
                      f"- Итог: **{sc.get('quality', 0):.0f}/100** — " + ", ".join(f"{k} {sc[k]:.0f}" for k in
                       ("semantic", "humor", "reaction", "audio", "completeness", "visual") if k in sc),
                      "- Почему выбран:", *[f"  - {r}" for r in (c.get("reasons") or [])],
                      f"- Файл: {c.get('file_path') or '—'} ({c['status']})", ""]
        lines.append("_Оценки — вероятностные эвристики, а не точное определение юмора; исправляйте их отметками в галерее._")
        p = out_root / "report.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        (out_root / "report.json").write_text(json.dumps({"clips": clips, "candidates": cands}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return p

    def _cleanup(self, work: Path) -> None:
        for name in ("audio16k.wav",):
            (work / name).unlink(missing_ok=True)
        for p in work.glob("*.ass"):
            p.unlink(missing_ok=True)
        for p in work.glob("*.part*"):
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------- рендер одного клипа (и для редактора)
def clip_settings(base: Settings, row: dict) -> Settings:
    s = base.copy()
    ov = row.get("overrides") or {}
    for section, vals in ov.items():
        sub = getattr(s, section, None)
        if sub is not None:
            for k, v in vals.items():
                if hasattr(sub, k):
                    setattr(sub, k, v)
    return s


def build_spec(row: dict, s: Settings, transcript: Transcript | None, audio: AudioFeatures | None) -> ClipSpec:
    start, end = row["start"], row["end"]
    if row.get("subtitles") is not None:
        lines = lines_from_json(row["subtitles"])
    elif transcript is not None:
        lines = build_lines(transcript.all_words(), start, end, s.subtitles.words_per_line)
    else:
        lines = []
    gaps = []
    if audio is not None and audio.n:
        gaps = [(a - start, b - start) for a, b in audio.silences(start, end, 0.6)]
    return ClipSpec(start, end, row.get("anchor") or start, row["mode"], row.get("title", ""), lines, gaps, seed=str(row["id"]))


def render_hash(row: dict, s: Settings) -> str:
    return stable_hash([row["start"], row["end"], row.get("subtitles"), row.get("title"), s.audio.__dict__,
                        s.effects.__dict__, s.subtitles.__dict__, s.export.__dict__])


def load_assets(work: Path) -> tuple[Transcript | None, AudioFeatures | None]:
    tr = Transcript.load(work / "transcript.json") if (work / "transcript.json").exists() else None
    au = AudioFeatures.load(work / "audio.npz") if (work / "audio.npz").exists() else None
    return tr, au


def render_clip_row(db: Database, cid: int, video: Path, info: MediaInfo, base: Settings, out_root: Path, work: Path,
                    transcript: Transcript | None, token: CancelToken | None = None, progress_cb=None,
                    force: bool = False, preview_to: Path | None = None) -> Path:
    row = db.get_clip(cid)
    s = clip_settings(base, row)
    _, audio = load_assets(work)
    spec = build_spec(row, s, transcript, audio)
    if preview_to is not None:
        r = render_clip(video, info, spec, s, preview_to, work, token, progress_cb, preview=True)
        return r.path
    rh = render_hash(row, s)
    if not force and row.get("status") == "done" and row.get("render_hash") == rh and row.get("file_path") and Path(row["file_path"]).exists():
        return Path(row["file_path"])
    name = s.export.name_template.format(index=row["idx"], title=safe_filename(row.get("title") or "clip", 50),
                                         mode=row["mode"], score=int((row.get("scores") or {}).get("quality", 0)))
    out = out_root / (safe_filename(name, 100) + ".mp4")
    if row.get("file_path") and Path(row["file_path"]) != out:
        Path(row["file_path"]).unlink(missing_ok=True)
    db.update_clip(cid, status="rendering", error="")
    r = render_clip(video, info, spec, s, out, work, token, progress_cb, make_thumb=True)
    srt = None
    if s.export.export_srt and spec.lines:
        srt = out.with_suffix(".srt")
        srt.write_text(to_srt(spec.lines, None if r.plan.is_identity else r.plan.map), encoding="utf-8")
    thumb = out.with_suffix(".jpg")
    db.update_clip(cid, status="done", file_path=str(out), thumb_path=str(thumb) if thumb.exists() else "",
                   srt_path=str(srt) if srt else "", render_hash=rh, error="")
    return out
