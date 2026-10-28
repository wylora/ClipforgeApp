"""Монтаж одного клипа: кроп, эффекты, таймлайн, субтитры, сведение звука, кодирование и проверка результата."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Settings, output_size
from .errors import FFmpegError
from .ffmpeg_utils import amix_supports_normalize, ffmpeg_has_filter, make_thumbnail, probe, MediaInfo, run_ffmpeg
from .music import ensure_sfx, pick_music
from .smart_crop import CropPlan, crop_filter, plan_crop
from .subtitles import SubLine, build_ass
from .timeline import TimelinePlan, build_plan
from .util import CancelToken

log = logging.getLogger("clipforge.editor")


@dataclass
class ClipSpec:
    start: float
    end: float
    anchor_time: float
    mode: str
    title: str = ""
    lines: list[SubLine] = field(default_factory=list)           # время относительно начала клипа
    gaps: list[tuple[float, float]] = field(default_factory=list)  # паузы (относительно начала клипа), для удаления тишины
    seed: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class RenderResult:
    path: Path
    duration: float
    plan: TimelinePlan
    crop: CropPlan
    notes: list[str] = field(default_factory=list)
    music: str = ""


def _f(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def _zoom_expr(punches: list[float], slow_total: float | None, dur: float) -> str | None:
    terms = [f"0.07*max(0,1-abs(t-{_f(p)})/0.6)" for p in punches]
    if slow_total:
        terms.append(f"0.05*t/{_f(max(dur, 1))}")
    return "+".join(terms) if terms else None


def render_clip(src: str | Path, info: MediaInfo, spec: ClipSpec, settings: Settings, out_path: Path, work_dir: Path,
                token: CancelToken | None = None, progress_cb: Callable[[float], None] | None = None,
                preview: bool = False, make_thumb: bool = False) -> RenderResult:
    ex, fx, au, sb = settings.export, settings.effects, settings.audio, settings.subtitles
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    W, H = output_size(ex.aspect, 540 if preview else ex.short_side)
    D = spec.duration
    punch = max(0.0, spec.anchor_time - spec.start)

    crop = plan_crop(src, info, spec.start, spec.end, ex.aspect, fx.crop_mode, fx.manual_crop_x,
                     settings.analysis.use_faces, token)
    if crop.note:
        notes.append(f"Кадрирование: {crop.note}")

    protect = [(max(0.0, punch - 2.5), punch)] if spec.mode == "funny" else []
    plan = build_plan(D, gaps=spec.gaps, protect=protect, punch_t=punch, remove_silence=fx.remove_silence,
                      slowmo=fx.slowmo_punch, speed=fx.speed)
    out_dur = plan.duration

    # ---------- субтитры (ASS)
    ass_name = None
    intro = (spec.title, min(2.2, out_dur / 2)) if fx.intro_title and spec.title else None
    outro = (fx.outro_text, max(0.0, out_dur - 2.2), min(2.0, out_dur)) if fx.outro_text else None
    lines = spec.lines if sb.enabled else []
    if lines or intro or outro:
        if ffmpeg_has_filter("ass"):
            align = sb.position
            if align == "auto":
                align = "top" if (crop.face_bottom is not None and crop.face_bottom > 0.62) else "bottom"
            mapper = None if plan.is_identity else plan.map
            ass_name = f"sub_{abs(hash((str(out_path), spec.start))) % 10**8}.ass"
            (work_dir / ass_name).write_text(build_ass(lines, sb, W, H, align, mapper, intro, outro), encoding="utf-8")
        else:
            notes.append("⚠ Ваша сборка FFmpeg без libass — субтитры не наложены (установите сборку gyan.dev).")

    # ---------- музыка и SFX
    music_path, music_label = None, ""
    use_music = au.music_mode == "on" or (au.music_mode == "auto" and spec.mode != "funny")
    if au.music_mode == "auto" and spec.mode == "funny":
        notes.append("Музыка не добавлена автоматически: в смешных роликах она ухудшает комический эффект.")
    if use_music and au.music_volume > 0:
        if au.music_path and Path(au.music_path).exists():
            music_path, music_label = Path(au.music_path), "файл пользователя"
        elif au.music_path:
            notes.append(f"⚠ Файл музыки не найден: {au.music_path}")
        else:
            music_path, music_label = pick_music(au.music_mood, spec.mode, spec.seed or spec.title)
    sfx_events: list[tuple[Path, float, float]] = []
    if fx.sfx:
        sfx = ensure_sfx()
        if intro:
            sfx_events.append((sfx["whoosh"], 0.05, 0.5))
        if spec.mode == "funny" or fx.punch_flash or fx.zoom_punch:
            sfx_events.append((sfx["pop" if spec.mode == "funny" else "impact"], plan.map(punch), 0.55))

    # ---------- граф
    graph: list[str] = []
    inputs: list[str] = ["-ss", _f(spec.start), "-t", _f(D), "-i", str(src)]
    n_in = 1
    cur = "v0"
    if crop.mode == "fit":
        graph.append(f"[0:v]split=2[bg][fg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                     f"boxblur=24:2[bgb];[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];"
                     f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={ex.fps}[{cur}]")
    else:
        pre = (crop_filter(crop) + ",") if crop.mode == "crop" else ""
        graph.append(f"[0:v]{pre}scale={W}:{H}:flags=lanczos,setsar=1,fps={ex.fps}[{cur}]")

    def vadd(chain: str) -> None:
        nonlocal cur
        nxt = f"v{len(graph)}"
        graph.append(f"[{cur}]{chain}[{nxt}]")
        cur = nxt

    punches = [punch + 0.3] if (fx.zoom_punch and spec.mode in ("funny", "best", "custom", "serious")) else []
    z = _zoom_expr(punches, fx.slow_zoom or None, D)
    if z:
        vadd(f"scale=w='trunc({W}*(1+{z})/2)*2':h='trunc({H}*(1+{z})/2)*2':eval=frame,crop={W}:{H}")
    if fx.color_grade == "light":
        vadd("eq=contrast=1.04:saturation=1.12:brightness=0.01")
    elif fx.color_grade == "cinematic":
        vadd("eq=contrast=1.1:saturation=0.92:gamma=0.97,colorbalance=rs=0.05:bs=-0.05:rh=0.04:bh=-0.03")
    if fx.vignette:
        vadd("vignette=angle=0.5")
    if fx.punch_flash and spec.mode in ("funny", "best", "custom"):
        vadd(f"eq=brightness='0.2*max(0,1-abs(t-{_f(punch + 0.2)})/0.1)':eval=frame")

    # ---------- аудио база
    has_a = info.has_audio and not au.mute_original
    if has_a:
        graph.append("[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0]")
    else:
        inputs += ["-f", "lavfi", "-t", _f(D), "-i", "anullsrc=r=48000:cl=stereo"]
        graph.append(f"[{n_in}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0]")
        n_in += 1
    acur = "a0"

    # ---------- таймлайн (вырезка тишины / слоумо / скорость)
    if not plan.is_identity:
        n = len(plan.pieces)
        vl = "".join(f"[vs{i}]" for i in range(n))
        al = "".join(f"[as{i}]" for i in range(n))
        graph.append(f"[{cur}]split={n}{vl}")
        graph.append(f"[{acur}]asplit={n}{al}")
        concat_in = ""
        for i, p in enumerate(plan.pieces):
            graph.append(f"[vs{i}]trim=start={_f(p.s)}:end={_f(p.e)},setpts=(PTS-STARTPTS)/{p.speed:.4f}[pv{i}]")
            at = f"atrim=start={_f(p.s)}:end={_f(p.e)},asetpts=PTS-STARTPTS"
            if abs(p.speed - 1) > 1e-4:
                at += f",atempo={p.speed:.4f}"
            graph.append(f"[as{i}]{at}[pa{i}]")
            concat_in += f"[pv{i}][pa{i}]"
        graph.append(f"{concat_in}concat=n={n}:v=1:a=1[vcat][acat]")
        cur, acur = "vcat", "acat"

    fade = min(fx.fade, out_dur / 4) if fx.fade > 0 else 0.0
    if fade > 0.01:
        vadd(f"fade=t=in:st=0:d={_f(fade)},fade=t=out:st={_f(out_dur - fade)}:d={_f(fade)}")
    if ass_name:
        vadd(f"ass={ass_name}")
    vadd("format=yuv420p")
    vout = cur

    # ---------- речь/оригинальный звук
    chain = ["highpass=f=70"]
    if au.denoise:
        chain.append("afftdn=nf=-28")
    vol = max(0.0, min(2.0, au.original_volume / 100.0))
    chain.append(f"volume={vol:.3f}")
    if vol > 1.0:
        chain.append("alimiter=limit=0.95:level=0")         # защита от клиппинга при усилении выше 100 %
    af = max(0.04, fade)
    chain.append(f"afade=t=in:st=0:d={_f(af)},afade=t=out:st={_f(max(0, out_dur - af))}:d={_f(af)}")
    graph.append(f"[{acur}]{','.join(chain)}[sp]")
    mix_labels = ["sp"]

    if music_path:
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]
        mi = n_in
        n_in += 1
        mvol = max(0.0, min(1.0, au.music_volume / 100.0))
        graph.append(f"[{mi}:a]atrim=0:{_f(out_dur)},asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=48000:"
                     f"channel_layouts=stereo,volume={mvol:.3f},afade=t=in:st=0:d=0.8,"
                     f"afade=t=out:st={_f(max(0, out_dur - 1.5))}:d=1.5[mus]")
        if au.ducking > 0 and has_a:
            k = au.ducking / 100.0
            thr = 0.12 - 0.105 * k                      # чувствительнее при сильном приглушении
            ratio = 2.0 + 12.0 * k
            graph.append("[sp]asplit=2[sp1][sp2]")
            graph.append(f"[mus][sp1]sidechaincompress=threshold={thr:.4f}:ratio={ratio:.1f}:attack=15:release=450:makeup=1[md]")
            mix_labels = ["sp2", "md"]
        else:
            mix_labels.append("mus")
        notes.append(f"Музыка: {music_label}, приглушение {au.ducking}%")

    for j, (spath, at, gain) in enumerate(sfx_events):
        inputs += ["-i", str(spath)]
        si = n_in
        n_in += 1
        ms = int(max(0, at) * 1000)
        graph.append(f"[{si}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                     f"adelay={ms}|{ms},volume={gain:.2f}[sfx{j}]")
        mix_labels.append(f"sfx{j}")

    if len(mix_labels) > 1:
        n = len(mix_labels)
        ins = "".join(f"[{l}]" for l in mix_labels)
        if amix_supports_normalize():
            graph.append(f"{ins}amix=inputs={n}:duration=first:normalize=0:dropout_transition=0[mx]")
        else:
            graph.append(f"{ins}amix=inputs={n}:duration=first:dropout_transition=0,volume={n}[mx]")
        last = "mx"
    else:
        last = mix_labels[0]
    tail = []
    if au.normalize:
        tail.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        tail.append("aresample=48000")
    tail.append("alimiter=limit=0.95:level=0")
    graph.append(f"[{last}]{','.join(tail)}[aout]")

    enc = ["-c:v", "libx264", "-preset", "ultrafast" if preview else ex.preset, "-profile:v", "high"]
    if preview:
        enc += ["-crf", "30"]
    elif ex.bitrate_kbps > 0:
        enc += ["-b:v", f"{ex.bitrate_kbps}k", "-maxrate", f"{int(ex.bitrate_kbps * 1.5)}k", "-bufsize", f"{ex.bitrate_kbps * 2}k"]
    else:
        enc += ["-crf", str(ex.crf)]
    args = [*inputs, "-filter_complex", ";".join(graph), "-map", f"[{vout}]", "-map", "[aout]", *enc,
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", ex.audio_bitrate, "-movflags", "+faststart",
            "-t", _f(out_dur), str(out_path)]
    tmp = out_path.with_name(out_path.stem + ".part" + out_path.suffix)
    args[-1] = str(tmp)
    try:
        run_ffmpeg(args, token=token, duration=out_dur, on_progress=progress_cb, cwd=work_dir)
        verify_output(tmp, out_dur)
        tmp.replace(out_path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        if ass_name:
            try:
                (work_dir / ass_name).unlink()
            except OSError:
                pass
    if make_thumb:
        make_thumbnail(out_path, min(out_dur * 0.35, max(0.2, out_dur - 0.3)), out_path.with_suffix(".jpg"))
    return RenderResult(out_path, out_dur, plan, crop, notes, music_label)


def verify_output(path: Path, expected: float) -> None:
    if not path.exists() or path.stat().st_size < 1000:
        raise FFmpegError("Итоговый файл не создан или пуст.")
    mi = probe(path)
    if not mi.has_video:
        raise FFmpegError("В итоговом файле нет видеопотока.")
    if not mi.has_audio:
        raise FFmpegError("В итоговом файле нет звука.")
    if abs(mi.duration - expected) > max(0.8, expected * 0.06):
        raise FFmpegError(f"Длительность результата ({mi.duration:.1f} с) не совпадает с ожидаемой ({expected:.1f} с).")
