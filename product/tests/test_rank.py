"""Reading the likeliest listings first, and keeping scoring off the letter
model's allowance.

The free model reads sixty listings a run. Which sixty used to be whichever
the boards returned first. These hold:

  1. RANKING ONLY REORDERS. Nothing is dropped, because a ranker is a guess
     and only the model's score or the pay floor may say no.
  2. THE MOST RELEVANT ARE READ FIRST when there are more than the model
     can read, by embedding when the open-source model is there and by words
     when it is not.
  3. A BROKEN RANKER NEVER STOPS A RUN.
  4. SCORING WALKS DOWN ITS OWN MODELS - a spent or renamed one is skipped,
     and the letter model's allowance is only touched last.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from jobseeker import gemini  # noqa: E402
from jobseeker.pipeline import rank, scoring  # noqa: E402
from jobseeker.pipeline.harvest import Listing  # noqa: E402
from jobseeker.profile import Profile, Role  # noqa: E402


def profile():
    return Profile(
        name="Sam Doherty", location="Aberdeen", phone="07700 900123",
        situation="unemployed", min_salary_annual=25000,
        target_roles=["maintenance technician"],
        locations=["Aberdeen"],
        history=[Role(title="Electrical Maintenance Engineer", org="Acme",
                      detail="PLC fault finding on packaging lines")])


def listing(i, title, description=""):
    return Listing(external_id=str(i), source="adzuna", title=title,
                   company=f"Firm {i}", location="Aberdeen", url="",
                   description=description)


class RankingOnlyReorders(unittest.TestCase):
    def test_the_matching_job_comes_first_by_words(self):
        jobs = [listing(1, "Warehouse Operative"), listing(2, "Barista"),
                listing(3, "Maintenance Technician", "PLC fault finding")]
        self.assertEqual(rank.word_overlap(profile(), jobs)[2],
                         max(rank.word_overlap(profile(), jobs)))

    def test_nothing_is_dropped(self):
        jobs = [listing(i, t) for i, t in enumerate(
            ["Cleaner", "Maintenance Technician", "Driver", "Chef"])]
        ranked = rank.rank(jobs, profile(), embed=None)
        self.assertEqual(sorted(l.external_id for l in ranked),
                         sorted(l.external_id for l in jobs))

    def test_embeddings_are_used_when_there(self):
        """A synonym no word match would catch: the fake model says the
        'multi-skilled engineer' is closest."""
        jobs = [listing(1, "Maintenance Assistant"),
                listing(2, "Multi-skilled Engineer")]

        def fake(texts):
            return [[1.0, 0.0]] + [[0.1, 1.0] if "Assistant" in t else
                                   [1.0, 0.05] for t in texts[1:]]
        ranked = rank.rank(jobs, profile(), embed=fake)
        self.assertEqual(ranked[0].title, "Multi-skilled Engineer")

    def test_a_broken_model_falls_back_to_words(self):
        def broken(texts):
            raise RuntimeError("download failed")
        jobs = [listing(1, "Barista"), listing(2, "Maintenance Technician")]
        self.assertEqual(rank.rank(jobs, profile(), embed=broken)[0].title,
                         "Maintenance Technician")

    def test_a_wrong_sized_answer_falls_back_to_words(self):
        jobs = [listing(1, "Barista"), listing(2, "Maintenance Technician")]
        self.assertEqual(
            rank.rank(jobs, profile(), embed=lambda t: [[1.0]])[0].title,
            "Maintenance Technician")

    def test_no_embedding_library_is_not_an_error(self):
        with patch.dict(sys.modules, {"fastembed": None}), \
             patch.object(rank, "_embedder", None), \
             patch.object(rank, "_embedder_failed", False):
            jobs = [listing(1, "Barista"), listing(2, "Maintenance Technician")]
            self.assertEqual(rank.rank(jobs, profile())[0].title,
                             "Maintenance Technician")


class ScoringReadsTheLikeliestFirst(unittest.TestCase):
    def test_the_one_good_job_at_the_back_is_still_read(self):
        """The failure this exists for: the right job at number 200."""
        jobs = [listing(i, "Warehouse Operative") for i in range(199)]
        jobs.append(listing(199, "Maintenance Technician",
                            "PLC fault finding on packaging lines"))
        read = []

        def ai(prompt):
            read.append(prompt)
            return "[]"
        scoring.score(jobs, profile(), ai, embed=lambda t: 1 / 0)
        self.assertIn("Maintenance Technician", "".join(read))

    def test_a_small_queue_is_not_reordered(self):
        """Under the cap everything is read anyway; no model is loaded."""
        called = []
        scoring.score([listing(1, "Barista")], profile(), lambda p: "[]",
                      embed=lambda t: called.append(t))
        self.assertEqual(called, [])


class _Response:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text, self._payload = status, text, payload

    def json(self):
        return self._payload or {}


def _ok(text):
    return _Response(200, payload={"candidates": [
        {"content": {"parts": [{"text": text}]}}]})


_DAY_GONE = _Response(429, '{"quotaId":"GenerateRequestsPerDayPerProjectPerModel"}')


class ScoringWalksDownItsModels(unittest.TestCase):
    def setUp(self):
        gemini._spent_for_today.clear()
        gemini._last_call.clear()
        self.addCleanup(gemini._spent_for_today.clear)
        self.asked = []

    def post(self, answers):
        def fake(url, **kwargs):
            model = url.rsplit("/", 1)[1].split(":")[0]
            self.asked.append((model, kwargs["json"]))
            return answers[model]
        return patch.object(gemini.httpx, "post", side_effect=fake)

    def test_scoring_does_not_use_the_letter_model_first(self):
        self.assertNotEqual(gemini.SCORING_MODELS[0], gemini.MODEL)
        self.assertEqual(gemini.SCORING_MODELS[-1], gemini.MODEL)

    def test_a_spent_model_passes_to_the_next(self):
        first, second = gemini.SCORING_MODELS[:2]
        with self.post({first: _DAY_GONE, second: _ok("[]")}):
            self.assertEqual(gemini.score_call("p", sleep=lambda s: None), "[]")
            gemini.score_call("p", sleep=lambda s: None)
        # The spent one is not asked a second time today.
        self.assertEqual([m for m, _ in self.asked], [first, second, second])

    def test_a_renamed_model_passes_to_the_next(self):
        first, second = gemini.SCORING_MODELS[:2]
        with self.post({first: _Response(404, "not found"),
                        second: _ok("[]")}):
            self.assertEqual(gemini.score_call("p", sleep=lambda s: None), "[]")

    def test_all_spent_is_quota_exhausted(self):
        with self.post({m: _DAY_GONE for m in gemini.SCORING_MODELS}):
            with self.assertRaises(gemini.QuotaExhausted):
                gemini.score_call("p", sleep=lambda s: None)

    def test_the_open_model_gets_a_body_it_accepts(self):
        """Gemma refuses a thinking budget or a JSON response type, and
        answers in a fence instead."""
        gemma = next(m for m in gemini.SCORING_MODELS if m.startswith("gemma"))
        with self.post({gemma: _ok('```json\n[{"listing": 0}]\n```')}):
            out = gemini.call("p", model=gemma, sleep=lambda s: None)
        config = self.asked[0][1]["generationConfig"]
        self.assertNotIn("thinkingConfig", config)
        self.assertNotIn("responseMimeType", config)
        self.assertEqual(out, '[{"listing": 0}]')

    def test_a_spent_scoring_model_leaves_letters_alone(self):
        first = gemini.SCORING_MODELS[0]
        with self.post({first: _DAY_GONE, gemini.MODEL: _ok("letter")}):
            with self.assertRaises(gemini.QuotaExhausted):
                gemini.call("p", model=first, sleep=lambda s: None)
            self.assertEqual(gemini.call("p", sleep=lambda s: None), "letter")


if __name__ == "__main__":
    unittest.main()
