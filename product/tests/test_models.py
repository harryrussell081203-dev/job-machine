"""Which model does which job, and what happens when one runs out.

  1. SCORING NEVER STARTS ON THE LETTER MODEL. It walks down its own list; a
     model spent for the day or no longer served is skipped, and the letter
     model's allowance is touched last.
  2. THE NAMES AND CAPS ARE SETTINGS, read when used, so a renamed model is
     a repository variable away rather than a code change.
  3. "SPENT FOR TODAY" MEANS TODAY. The web server outlives a day, and a
     model spent on Monday must be asked again on Tuesday.
  4. EVERY ANSWER SAYS WHICH MODEL GAVE IT.
"""

import contextlib
import datetime as dt
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from jobseeker import gemini  # noqa: E402


class _Response:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text, self._payload = status, text, payload

    def json(self):
        return self._payload or {}


def _ok(text):
    return _Response(200, payload={"candidates": [
        {"content": {"parts": [{"text": text}]}}]})


_DAY_GONE = _Response(
    429, '{"quotaId":"GenerateRequestsPerDayPerProjectPerModel"}')


class Counter:
    def __init__(self, **used):
        self.counts = dict(used)

    def used(self, model):
        return self.counts.get(model, 0)

    def add(self, model):
        self.counts[model] = self.counts.get(model, 0) + 1


class Base(unittest.TestCase):
    def setUp(self):
        gemini._spent_for_today.clear()
        gemini._last_call.clear()
        self.addCleanup(gemini._spent_for_today.clear)
        self.addCleanup(setattr, gemini, "usage", None)
        self.asked = []

    def post(self, answers):
        def fake(url, **kwargs):
            model = url.rsplit("/", 1)[1].split(":")[0]
            self.asked.append((model, kwargs["json"]))
            return answers[model]
        return patch.object(gemini.httpx, "post", side_effect=fake)

    def quiet(self):
        return contextlib.redirect_stdout(io.StringIO())


class ScoringWalksDownItsModels(Base):
    def test_scoring_does_not_start_on_the_letter_model(self):
        models = gemini.scoring_models()
        self.assertNotEqual(models[0], gemini.letter_model())
        self.assertEqual(models[-1], gemini.letter_model())

    def test_a_spent_model_passes_to_the_next(self):
        first, second = gemini.scoring_models()[:2]
        with self.post({first: _DAY_GONE, second: _ok("[]")}), self.quiet():
            self.assertEqual(gemini.score_call("p", sleep=lambda s: None), "[]")
            gemini.score_call("p", sleep=lambda s: None)
        # The spent one is not asked a second time today.
        self.assertEqual([m for m, _ in self.asked], [first, second, second])

    def test_a_renamed_model_passes_to_the_next(self):
        first, second = gemini.scoring_models()[:2]
        with self.post({first: _Response(404, "not found"),
                        second: _ok("[]")}), self.quiet():
            self.assertEqual(gemini.score_call("p", sleep=lambda s: None), "[]")

    def test_all_spent_is_quota_exhausted(self):
        with self.post({m: _DAY_GONE for m in gemini.scoring_models()}), \
                self.quiet():
            with self.assertRaises(gemini.QuotaExhausted):
                gemini.score_call("p", sleep=lambda s: None)

    def test_the_open_model_gets_a_body_it_accepts(self):
        """Gemma refuses a thinking budget or a JSON response type, and
        answers in a fence instead."""
        gemma = next(m for m in gemini.scoring_models()
                     if m.startswith("gemma"))
        with self.post({gemma: _ok('```json\n[{"listing": 0}]\n```')}), \
                self.quiet():
            out = gemini.call("p", model=gemma, sleep=lambda s: None)
        config = self.asked[0][1]["generationConfig"]
        self.assertNotIn("thinkingConfig", config)
        self.assertNotIn("responseMimeType", config)
        self.assertEqual(out, '[{"listing": 0}]')

    def test_a_spent_scoring_model_leaves_letters_alone(self):
        first = gemini.scoring_models()[0]
        with self.post({first: _DAY_GONE,
                        gemini.letter_model(): _ok("letter")}), self.quiet():
            with self.assertRaises(gemini.QuotaExhausted):
                gemini.call("p", model=first, sleep=lambda s: None)
            self.assertEqual(gemini.call("p", sleep=lambda s: None), "letter")


class TheNamesAreSettings(Base):
    def test_models_come_from_the_environment(self):
        with patch.dict(os.environ, {"GEMINI_LETTER_MODEL": "letters-9",
                                     "GEMINI_SCORING_MODELS": "a, b"}):
            self.assertEqual(gemini.letter_model(), "letters-9")
            self.assertEqual(gemini.scoring_models(), ("a", "b", "letters-9"))
            with self.post({"letters-9": _ok("hi")}), self.quiet():
                gemini.call("p", sleep=lambda s: None)
        self.assertEqual(self.asked[0][0], "letters-9")

    def test_no_cap_unless_one_is_set(self):
        with patch.dict(os.environ, {"GEMINI_DAILY_CAPS": ""}):
            self.assertEqual(gemini.daily_caps(), {})

    def test_a_cap_stops_short_of_the_real_limit(self):
        gemini.usage = Counter(**{"m1": 5})
        with patch.dict(os.environ, {"GEMINI_DAILY_CAPS": "m1=5, junk, m2=x"}):
            with patch.object(gemini.httpx, "post") as post:
                with self.assertRaises(gemini.QuotaExhausted):
                    gemini.call("p", model="m1", sleep=lambda s: None)
                post.assert_not_called()

    def test_each_answer_is_counted(self):
        gemini.usage = Counter()
        with self.post({"m1": _ok("x")}), self.quiet():
            gemini.call("p", model="m1", sleep=lambda s: None)
        self.assertEqual(gemini.usage.used("m1"), 1)

    def test_a_broken_counter_caps_nothing(self):
        class Broken:
            def used(self, model):
                raise RuntimeError("database down")

            def add(self, model):
                raise RuntimeError("database down")
        gemini.usage = Broken()
        with patch.dict(os.environ, {"GEMINI_DAILY_CAPS": "m1=1"}), \
                self.post({"m1": _ok("x")}), self.quiet():
            self.assertEqual(gemini.call("p", model="m1",
                                         sleep=lambda s: None), "x")


class SpentMeansToday(Base):
    def test_yesterdays_spent_model_is_asked_again(self):
        gemini._spent_for_today["m1"] = "2000-01-01"
        with self.post({"m1": _ok("back")}), self.quiet():
            self.assertEqual(gemini.call("p", model="m1",
                                         sleep=lambda s: None), "back")

    def test_the_day_turns_at_midnight_in_california(self):
        """Google resets at midnight Pacific: 07:59 UTC is still yesterday
        there in summer, 08:01 is the new day."""
        before = dt.datetime(2026, 9, 28, 6, 59, tzinfo=dt.timezone.utc)
        after = dt.datetime(2026, 9, 28, 7, 1, tzinfo=dt.timezone.utc)
        self.assertEqual(gemini.quota_day(before), "2026-09-27")
        self.assertEqual(gemini.quota_day(after), "2026-09-28")


class EveryAnswerIsNamed(Base):
    def test_the_log_says_which_model_answered(self):
        out = io.StringIO()
        with self.post({"m1": _ok("x")}), contextlib.redirect_stdout(out):
            gemini.call("p", model="m1", sleep=lambda s: None)
        self.assertIn("answered by m1", out.getvalue())


if __name__ == "__main__":
    unittest.main()
