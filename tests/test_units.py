import os
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

import numpy as np

from helpers import make_media  # noqa: F401  (добавляет корень проекта в sys.path)

_TMP = tempfile.TemporaryDirectory()
os.environ["CLIPFORGE_HOME"] = str(Path(_TMP.name) / "home")


class TimelineTest(unittest.TestCase):
    def test_silence_removal_and_mapping(self):
        from clipforge.timeline import build_plan
        p = build_plan(20, gaps=[(5, 7), (10, 10.4)], remove_silence=True)
        self.assertEqual(len(p.pieces), 2)                       # короткая пауза (<0.7 с) не режется
        self.assertAlmostEqual(p.duration, 20 - (2 - 0.3), places=3)
        self.assertAlmostEqual(p.map(15), 15 - 1.7, places=3)

    def test_protected_comic_pause_is_kept(self):
        from clipforge.timeline import build_plan
        p = build_plan(20, gaps=[(8, 10)], protect=[(7, 11)], remove_silence=True)
        self.assertTrue(p.is_identity)

    def test_slowmo_and_speed_limits(self):
        from clipforge.timeline import build_plan
        p = build_plan(10, punch_t=5, slowmo=True, speed=1.5)
        self.assertTrue(all(0.5 <= x.speed <= 2.0 for x in p.pieces))
        self.assertGreater(len(p.pieces), 1)


class SubtitleTest(unittest.TestCase):
    def setUp(self):
        from clipforge.models import Word
        self.words = [Word(1 + i * 0.4, 1.35 + i * 0.4, w) for i, w in enumerate("раз два три четыре пять шесть семь восемь".split())]

    def test_lines_and_srt_roundtrip(self):
        from clipforge.subtitles import build_lines, from_srt, to_srt
        lines = build_lines(self.words, 0.5, 6, words_per_line=3)
        self.assertEqual(len(lines), 3)
        self.assertAlmostEqual(lines[0].start, 0.5, places=2)           # время относительно начала клипа
        back = from_srt(to_srt(lines))
        self.assertEqual([l.text for l in back], [l.text for l in lines])
        self.assertAlmostEqual(back[1].start, lines[1].start, places=2)

    def test_edit_text_redistributes_words(self):
        from clipforge.subtitles import build_lines, retext_line
        l = build_lines(self.words, 0, 6, 4)[0]
        retext_line(l, "новый текст строки")
        self.assertEqual(len(l.words), 3)
        self.assertAlmostEqual(l.words[0].start, l.start)
        self.assertAlmostEqual(l.words[-1].end, l.end)

    def test_ass_highlight_events_and_mapping(self):
        from clipforge.config import SubtitleSettings
        from clipforge.subtitles import build_ass, build_lines
        from clipforge.timeline import build_plan
        lines = build_lines(self.words, 0, 6, 4)
        ass = build_ass(lines, SubtitleSettings(style="karaoke"), 1080, 1920)
        self.assertEqual(ass.count("Dialogue:"), 8)
        self.assertIn("PlayResX: 1080", ass)
        plan = build_plan(6, gaps=[(0, 1)], remove_silence=True, min_gap=0.5, keep=0.1)
        ass2 = build_ass(lines, SubtitleSettings(style="classic"), 1080, 1920, mapper=plan.map)
        self.assertTrue(ass2.count("Dialogue:") >= 2)


class SettingsDbTest(unittest.TestCase):
    def test_settings_roundtrip_and_unknown_keys(self):
        from clipforge.config import Settings, apply_profile
        s = Settings()
        apply_profile(s, "meme")
        s.audio.original_volume = 150
        p = Path(_TMP.name) / "s.json"
        s.save(p)
        s2 = Settings.load(p)
        self.assertEqual(s2.audio.original_volume, 150)
        self.assertTrue(s2.effects.punch_flash)
        d = s.to_dict(); d["clip"]["несуществующее"] = 1; d["analysis"]["weights"] = {"funny": {"humor": 0.9}}
        s3 = Settings.from_dict(d)
        self.assertEqual(s3.analysis.weights["funny"]["humor"], 0.9)
        self.assertIn("semantic", s3.analysis.weights["funny"])          # недостающее достроено
        self.assertEqual(Settings.load(Path(_TMP.name) / "нет.json").clip.mode, "funny")

    def test_db_projects_clips_feedback(self):
        from clipforge.db import Database
        db = Database(":memory:")
        pid = db.add_project("a.mp4", "t", "funny", {})
        cid = db.add_clip(pid, 1, start=1.0, end_=4.0, mode="funny", scores={"quality": 70, "humor": 80})
        db.update_clip(cid, status="done", subtitles=[{"a": 1}])
        c = db.get_clip(cid)
        self.assertEqual((c["start"], c["end"], c["status"], c["subtitles"]), (1.0, 4.0, "done", [{"a": 1}]))
        db.set_feedback(c, "funny"); db.set_feedback(c, "not_funny")        # повторная отметка заменяет прежнюю
        self.assertEqual(len(db.feedback_samples()["humor"]), 1)
        db.delete_project(pid)
        self.assertEqual(db.get_clips(pid), [])

    def test_personal_model_shifts_ranking(self):
        from clipforge.feedback import PersonalModel
        rows = [({"humor": 90, "audio": 20}, 1)] * 8 + [({"humor": 30, "audio": 80}, 0)] * 8
        m = PersonalModel({"humor": rows})
        hi = m.adjust(50, {"humor": 90, "audio": 20}, "funny")
        lo = m.adjust(50, {"humor": 30, "audio": 80}, "funny")
        self.assertGreater(hi, lo)
        self.assertEqual(PersonalModel({"humor": rows[:3]}).adjust(50, {"humor": 90}, "funny"), 50)   # мало данных


class FilenameTest(unittest.TestCase):
    def test_safe_filename(self):
        from clipforge.util import safe_filename
        self.assertEqual(safe_filename('a<b>:"c"/d\\e|f?*'), "a_b___c__d_e_f__")
        self.assertEqual(safe_filename("CON"), "_CON")
        self.assertEqual(safe_filename("   ...  "), "clip")
        self.assertEqual(safe_filename("Привет, мир 😂"), "Привет, мир 😂")
        self.assertLessEqual(len(safe_filename("x" * 500)), 80)


class DepsTest(unittest.TestCase):
    def test_missing_ffmpeg_gives_clear_error(self):
        from clipforge import ffmpeg_utils as fu
        from clipforge.errors import DependencyError
        old_path, old_env = os.environ.get("PATH"), os.environ.get("CLIPFORGE_FFMPEG_DIR")
        os.environ["PATH"] = ""
        try:
            with unittest.mock.patch.object(fu, "find_binary", return_value=None):
                with self.assertRaises(DependencyError) as cm:
                    fu.require_binary("ffmpeg")
                self.assertIn("FFmpeg", cm.exception.user_text())
        finally:
            os.environ["PATH"] = old_path or ""

    def test_dependency_report_and_missing_whisper(self):
        from clipforge.resources import check_dependencies
        names = {d.name: d for d in check_dependencies()}
        self.assertTrue(names["FFmpeg"].required)
        from clipforge.config import AnalysisSettings
        from clipforge.errors import DependencyError
        from clipforge.transcribe import transcribe_audio, whisper_available
        if not whisper_available():
            with self.assertRaises(DependencyError):
                transcribe_audio(Path("x.wav"), AnalysisSettings())


class QueueTest(unittest.TestCase):
    def _job(self, src, **kw):
        from clipforge.config import Settings
        from clipforge.tasks import Job
        return Job(src, Settings())

    def test_order_parallel_limit_duplicates_and_full(self):
        from clipforge.tasks import DuplicateJobError, JobManager, QueueFullError
        running, peak, lock, gate = [0], [0], threading.Lock(), threading.Event()

        def runner(job):
            with lock:
                running[0] += 1; peak[0] = max(peak[0], running[0])
            gate.wait(5)
            with lock:
                running[0] -= 1
            return job.source

        events = []
        m = JobManager(runner, max_parallel=2, max_queue=2, on_event=lambda e, j: events.append((e, j.source)))
        jobs = [m.submit(self._job(f"v{i}")) for i in range(2)]
        time.sleep(0.3)                                               # дать воркерам забрать задачи
        jobs += [m.submit(self._job(f"v{i}")) for i in range(2, 4)]  # 2 выполняются + 2 в очереди
        with self.assertRaises(DuplicateJobError):
            m.submit(self._job("v3"))
        with self.assertRaises(QueueFullError):
            m.submit(self._job("v9"))
        gate.set()
        for _ in range(50):
            if all(j.status == "done" for j in jobs):
                break
            time.sleep(0.1)
        self.assertTrue(all(j.status == "done" for j in jobs))
        self.assertLessEqual(peak[0], 2)
        m.shutdown()

    def test_cancel_running_and_queued(self):
        from clipforge.errors import CancelledError
        from clipforge.tasks import JobManager

        def runner(job):
            while True:
                job.token.raise_if_cancelled()
                time.sleep(0.02)

        m = JobManager(runner, max_parallel=1)
        a, b = m.submit(self._job("a")), m.submit(self._job("b"))
        time.sleep(0.2)
        m.cancel(b.id); m.cancel(a.id)
        time.sleep(0.5)
        self.assertEqual((a.status, b.status), ("cancelled", "cancelled"))
        m.shutdown()


class CancelFfmpegTest(unittest.TestCase):
    def test_cancel_kills_long_encode_quickly(self):
        from clipforge.errors import CancelledError
        from clipforge.ffmpeg_utils import run_ffmpeg
        from clipforge.util import CancelToken
        tok = CancelToken()
        threading.Timer(0.8, tok.cancel).start()
        t0 = time.time()
        with self.assertRaises(CancelledError):
            run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=3000", "-c:v", "libx264",
                        "-preset", "veryslow", "-f", "null", "-"], token=tok, duration=3000)
        self.assertLess(time.time() - t0, 6)

    def test_bad_filter_raises_ffmpeg_error_with_details(self):
        from clipforge.errors import FFmpegError
        from clipforge.ffmpeg_utils import run_ffmpeg
        with self.assertRaises(FFmpegError) as cm:
            run_ffmpeg(["-f", "lavfi", "-i", "testsrc=d=1", "-vf", "nonexistent_filter", "-f", "null", "-"])
        self.assertTrue(cm.exception.details)


class AudioMixTest(unittest.TestCase):
    """Сведение: музыка должна становиться тише, пока звучит речь (ducking), и громче в паузах."""

    def test_ducking(self):
        from clipforge.config import Settings
        from clipforge.editor import ClipSpec, render_clip
        from clipforge.ffmpeg_utils import decode_pcm_stream, probe
        d = Path(_TMP.name) / "mix"; d.mkdir(exist_ok=True)
        # речь: тон 1 кГц первые 4 с, затем тишина 4 с; музыка: 200 Гц постоянно
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=25:d=8",
                        "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*1000*t)*lt(t,4)':s=44100:d=8", "-c:v", "libx264", "-c:a", "aac",
                        str(d / "v.mp4")], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=200:d=10", str(d / "m.wav")], check=True)

        def measure(ducking):
            s = Settings()
            s.audio.music_mode, s.audio.music_path, s.audio.music_volume, s.audio.ducking = "on", str(d / "m.wav"), 60, ducking
            s.audio.normalize = False
            s.effects.fade = 0
            s.subtitles.enabled = False
            s.export.short_side, s.export.preset = 360, "ultrafast"
            out = d / f"o{ducking}.mp4"
            render_clip(d / "v.mp4", probe(d / "v.mp4"), ClipSpec(0, 8, 2, "serious"), s, out, d / "w")
            pcm = np.concatenate(list(decode_pcm_stream(out, 16000))).astype(np.float32) / 32768

            def tone(a, b, f):
                seg = pcm[int(a * 16000):int(b * 16000)]
                spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
                k = int(f * len(seg) / 16000)
                return spec[k - 3:k + 4].max()
            return tone(1, 3, 200), tone(5, 7, 200)
        sp, pause = measure(90)
        self.assertGreater(pause / sp, 2.0, (sp, pause))              # в паузе музыка заметно громче
        sp0, pause0 = measure(0)
        self.assertAlmostEqual(pause0 / sp0, 1.0, delta=0.35)         # без ducking уровень ровный

    def test_original_volume_over_100_does_not_clip(self):
        from clipforge.config import Settings
        from clipforge.editor import ClipSpec, render_clip
        from clipforge.ffmpeg_utils import decode_pcm_stream, probe
        d = Path(_TMP.name) / "mix"; d.mkdir(exist_ok=True)
        v = d / "v.mp4"
        if not v.exists():
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=25:d=4",
                            "-f", "lavfi", "-i", "sine=f=440:d=4", "-c:v", "libx264", "-c:a", "aac", str(v)], check=True)
        s = Settings(); s.audio.original_volume = 200; s.audio.normalize = False; s.subtitles.enabled = False
        s.export.short_side, s.export.preset = 360, "ultrafast"
        out = d / "loud.mp4"
        render_clip(v, probe(v), ClipSpec(0, 3, 1, "serious"), s, out, d / "w")
        pcm = np.concatenate(list(decode_pcm_stream(out, 16000))).astype(np.float32) / 32768
        self.assertLessEqual(np.abs(pcm).max(), 1.0)


class AudioAnalysisTest(unittest.TestCase):
    def test_laughter_vs_speech_and_silences(self):
        from clipforge.audio_analysis import analyze_audio
        d = Path(_TMP.name) / "aa"
        v = make_media(d, duration=60, name="aa")
        af = analyze_audio(v)
        lm, _ = af.laugh_in(41, 43.5)
        sp, _ = af.laugh_in(3, 5.5)
        self.assertGreater(lm, 0.5)
        self.assertGreater(lm, sp + 0.2)
        self.assertTrue(any(a < 20 < b for a, b in af.silences(0, 60, 1.0)))


class CutTest(unittest.TestCase):
    def test_cut_duration_and_unicode_name(self):
        from clipforge.config import Settings
        from clipforge.editor import ClipSpec, render_clip
        from clipforge.ffmpeg_utils import probe
        d = Path(_TMP.name) / "cut"
        v = make_media(d, duration=30, name="видео с пробелами & символами")
        s = Settings(); s.subtitles.enabled = False; s.export.short_side, s.export.preset = 360, "ultrafast"
        s.export.aspect = "1:1"
        out = d / "клип №1.mp4"
        r = render_clip(v, probe(v), ClipSpec(5.5, 17.25, 8, "best"), s, out, d / "w")
        mi = probe(out)
        self.assertAlmostEqual(mi.duration, 11.75, delta=0.3)
        self.assertEqual((mi.width, mi.height), (360, 360))


if __name__ == "__main__":
    import unittest.mock
    unittest.main()
