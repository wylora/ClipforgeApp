import unittest

from helpers import make_media  # noqa: F401


def _cand(start, end, q, text, sig=None):
    from clipforge.models import Candidate
    return Candidate(start, end, start, "funny", text, {"quality": q}, signature=sig or [])


class SelectionRulesTest(unittest.TestCase):
    def _ctx(self, **clip):
        from clipforge.config import Settings
        from clipforge.selector import Ctx
        s = Settings()
        for k, v in clip.items():
            setattr(s.clip, k, v)
        return Ctx(None, [], None, None, s, 100.0)

    def test_threshold_overlap_limit_and_duplicates(self):
        from clipforge.selector import select_best
        c = [_cand(10, 30, 80, "кот сидит на холодильнике и смотрит"), _cand(25, 45, 70, "совсем другой текст про погоду"),
             _cand(60, 80, 40, "слабый кандидат ниже порога"), _cand(90, 99, 75, "кот сидит на холодильнике и смотрит"),
             _cand(50, 58, 72, "третий уникальный момент про рыбалку и озеро", [1, 2, 3]),
             _cand(70, 78, 71, "четвёртый уникальный момент про горы и снег", [1, 2, 3])]
        chosen = select_best(c, self._ctx(min_score=45, max_clips=5, max_overlap=0))
        starts = sorted(x.start for x in chosen)
        self.assertEqual(starts, [10, 50])      # 25 — пересечение; 60 — слабый; 90 — повтор текста; 70 — визуальный дубль
        self.assertTrue(all(x.selected for x in chosen))
        self.assertEqual(select_best(c, self._ctx(min_score=45, max_clips=1))[0].start, 10)

    def test_overlap_allowed_when_configured(self):
        from clipforge.selector import select_best
        c = [_cand(10, 30, 80, "первый уникальный текст про кота"), _cand(28, 45, 70, "второй уникальный текст про рыбалку")]
        self.assertEqual(len(select_best(c, self._ctx(max_overlap=5.0))), 2)

    def test_custom_prompt_requires_text(self):
        from clipforge.errors import AnalysisError
        from clipforge.models import Unit
        from clipforge.selector import find_candidates
        ctx = self._ctx(mode="custom", custom_prompt="")
        ctx.units = [Unit(0, 0, 3, "привет", [])]
        with self.assertRaises(AnalysisError):
            find_candidates(ctx)

    def test_prompt_parsing(self):
        from clipforge.selector import parse_prompt
        p = parse_prompt("Найди все смешные реакции ведущего на неожиданную развязку")
        self.assertTrue(p.humor and p.reaction and p.surprise)
        self.assertTrue(p.keywords)


if __name__ == "__main__":
    unittest.main()
