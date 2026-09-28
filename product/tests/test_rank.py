"""Reading the likeliest listings first.

The free model reads sixty listings a run. Which sixty used to be whichever
the boards returned first. These hold:

  1. RANKING ONLY REORDERS. Nothing is dropped, because a ranker is a guess
     and only the model's score or the pay floor may say no.
  2. THE MOST RELEVANT ARE READ FIRST when there are more than the model
     can read, by embedding when the open-source model is there and by words
     when it is not.
  3. A BROKEN RANKER NEVER STOPS A RUN.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


class TheSwitches(unittest.TestCase):
    def test_ranker_off_keeps_the_boards_order(self):
        jobs = [listing(1, "Barista"), listing(2, "Maintenance Technician")]
        with patch.dict(os.environ, {"RANKER_ENABLED": "0"}):
            self.assertEqual([l.title for l in rank.rank(jobs, profile())],
                             ["Barista", "Maintenance Technician"])

    def test_top_n_is_configurable(self):
        read = []
        jobs = [listing(i, "Technician") for i in range(100)]
        with patch.dict(os.environ, {"SCORING_TOP_N": "15"}):
            scoring.score(jobs, profile(),
                          lambda p: (read.append(p), "[]")[1])
        self.assertEqual(len(read), 1)

    def test_nonsense_top_n_is_the_default(self):
        with patch.dict(os.environ, {"SCORING_TOP_N": "lots"}):
            self.assertEqual(scoring.top_n(), scoring.MAX_SCORED_PER_RUN)


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


if __name__ == "__main__":
    unittest.main()
