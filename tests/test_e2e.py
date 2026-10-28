import os
import tempfile
import unittest
from pathlib import Path

from helpers import make_media


class E2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["CLIPFORGE_HOME"] = str(Path(cls.tmp.name) / "home")
        cls.video = make_media(Path(cls.tmp.name) / "media")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, mode, **kw):
        from clipforge.config import Settings
        from clipforge.db import Database
        from clipforge.pipeline import Pipeline
        s = Settings()
        s.clip.mode = mode
        s.clip.min_score = kw.get("min_score", 30)
        s.clip.max_clips = 3
        s.export.output_dir = str(Path(self.tmp.name) / f"out_{mode}")
        s.export.short_side = 540
        s.export.preset = "ultrafast"
        s.analysis.render_workers = 2
        db = Database(":memory:")
        events = []
        res = Pipeline(db, s, str(self.video), on_progress=events.append, on_log=lambda m: None).run()
        return res, db, events

    def test_funny_finds_joke_with_laughter(self):
        res, db, events = self._run("funny")
        self.assertGreaterEqual(len(res.clips), 1)
        top = res.clips[0]
        # шутка: реплика 39.2–40.3 + смех до ~44 с; клип обязан содержать и подводку, и реакцию
        self.assertLess(top["start"], 39.2)
        self.assertGreater(top["end"], 40.3)
        self.assertTrue(Path(top["file_path"]).exists())
        self.assertTrue(any("смех" in r.lower() for r in top["reasons"]), top["reasons"])
        self.assertGreater(events[-1].overall, 0.99)

    def test_serious_complete_thought(self):
        res, db, _ = self._run("serious")
        self.assertGreaterEqual(len(res.clips), 1)
        c = res.clips[0]
        self.assertGreater(c["start"], 55)
        self.assertLess(c["start"], 61)
        self.assertGreaterEqual(c["end"], 75.0)          # мысль не обрезана на полуслове
        from clipforge.ffmpeg_utils import probe
        mi = probe(c["file_path"])
        self.assertEqual((mi.width, mi.height), (540, 960))

    def test_no_good_moments_is_reported(self):
        from clipforge.errors import NoGoodMomentsError
        with self.assertRaises(NoGoodMomentsError):
            self._run("funny", min_score=99)

    def test_resume_skips_rendered_clips(self):
        from clipforge.config import Settings
        from clipforge.db import Database
        from clipforge.pipeline import Pipeline
        res, db, _ = self._run("serious")
        mt = Path(res.clips[0]["file_path"]).stat().st_mtime
        s = Settings.from_dict(db.get_project(res.project_id)["settings"])
        res2 = Pipeline(db, s, str(self.video), project_id=res.project_id).run()
        self.assertEqual(Path(res2.clips[0]["file_path"]).stat().st_mtime, mt)


if __name__ == "__main__":
    unittest.main()
