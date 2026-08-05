"""
Tests for what the machine has learned.

Offline.

The failure this guards against is not a crash - it is a confident wrong
conclusion. Twenty-eight sends and two replies can produce any story you like
if you let it, and a machine that acts on the story keeps the template that
got lucky and bins the one that did not, forever, without ever finding out.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_machine as jm  # noqa: E402
import learn  # noqa: E402


def sent(n, replies=0, **extra):
    jobs = {}
    for i in range(n):
        job = {"sent_at": "2026-08-05T10:00:00+00:00", "company": f"F{i}",
               "template_family": "general", "email_tier": 3, "score": 75}
        job.update(extra)
        if i < replies:
            job["replied_at"] = "2026-08-05T12:00:00+00:00"
            job["reply_category"] = "question"
        jobs[str(i) + str(extra)] = job
    return jobs


class TestTheIntervalNotThePointEstimate(unittest.TestCase):
    def test_one_reply_from_three_is_not_a_thirty_three_percent_rate(self):
        low, high = learn.wilson(1, 3)
        self.assertLess(low, 0.1)
        self.assertGreater(high, 0.7)

    def test_the_interval_narrows_as_the_evidence_grows(self):
        narrow = learn.wilson(20, 100)
        wide = learn.wilson(2, 10)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_no_data_is_total_ignorance_not_zero(self):
        self.assertEqual(learn.wilson(0, 0), (0.0, 1.0))


class TestAnAutoresponderIsNotAReply(unittest.TestCase):
    def test_a_human_counts(self):
        self.assertTrue(learn.is_reply({"replied_at": "x",
                                        "reply_category": "question"}))

    def test_an_acknowledgement_does_not(self):
        """Counting these would make every large firm look responsive."""
        self.assertFalse(learn.is_reply(
            {"replied_at": "x", "reply_category": "auto_acknowledgement"}))

    def test_silence_does_not(self):
        self.assertFalse(learn.is_reply({}))


class TestItRefusesToConcludeTooEarly(unittest.TestCase):
    def test_a_landslide_on_three_sends_each_decides_nothing(self):
        state = {"jobs": {**sent(3, 3, template_family="lucky"),
                          **sent(3, 0, template_family="unlucky")}}
        self.assertEqual(learn.verdicts(state)["verdicts"], [])

    def test_it_says_what_it_is_waiting_for(self):
        state = {"jobs": sent(3, 1)}
        pending = learn.verdicts(state)["not_yet"]
        self.assertTrue(pending)
        self.assertTrue(all(p["sends_needed"] > 0 for p in pending))

    def test_a_real_difference_with_real_numbers_is_called(self):
        state = {"jobs": {**sent(20, 12, template_family="good"),
                          **sent(20, 0, template_family="bad")}}
        found = [v for v in learn.verdicts(state)["verdicts"]
                 if v["dimension"] == "template"]
        self.assertTrue(found)
        self.assertEqual(found[0]["prefer"], "good")
        self.assertEqual(found[0]["avoid"], "bad")

    def test_overlapping_intervals_are_not_a_difference(self):
        state = {"jobs": {**sent(10, 3, template_family="a"),
                          **sent(10, 2, template_family="b")}}
        found = [v for v in learn.verdicts(state)["verdicts"]
                 if v["dimension"] == "template"]
        self.assertEqual(found, [])


class TestTheQuestionsThatStopApplications(unittest.TestCase):
    """The highest-value output in the file: each one is a line in
    answers.json and ten seconds of Harry's time."""

    def state(self):
        return {"jobs": {
            "a": {"portal_flags": ["'' has no option matching '35000'"],
                  "company": "Staffline"},
            "b": {"portal_flags": ["'' has no option matching '35000'",
                                   "First name: TimeoutError"],
                  "company": "Acme"},
            "c": {"portal_flags": ["unrecognised required field 'Organisation'"],
                  "company": "Council"}}}

    def test_they_come_back_in_frequency_order(self):
        gaps = learn.unanswered_questions(self.state())
        self.assertEqual(gaps[0]["times"], 2)
        self.assertIn("35000", gaps[0]["question"])

    def test_a_bug_in_the_agent_is_not_a_question_for_him(self):
        gaps = learn.unanswered_questions(self.state())
        self.assertFalse(any("TimeoutError" in g["question"] for g in gaps))

    def test_it_says_where_it_first_happened(self):
        gaps = learn.unanswered_questions(self.state())
        self.assertEqual(gaps[0]["first_seen_at"], "Staffline")


class TestLearningWhichPlatformsFinish(unittest.TestCase):
    def state(self):
        jobs = {}
        for i in range(4):
            jobs[f"g{i}"] = {"portal_attempted_at": jm.now(),
                             "ats": "greenhouse", "portal_filled": ["a"],
                             "status": "portal_submitted"}
        for i in range(4):
            jobs[f"t{i}"] = {"portal_attempted_at": jm.now(), "ats": "taleo",
                             "status": "portal_manual"}
        return {"jobs": jobs}

    def test_it_measures_reaching_and_finishing_separately(self):
        stats = learn.platform_success(self.state())
        self.assertEqual(stats["greenhouse"]["finish_rate"], 1.0)
        self.assertEqual(stats["taleo"]["finish_rate"], 0.0)
        self.assertEqual(stats["taleo"]["reached"], 0)

    def test_the_one_that_finishes_is_weighted_higher(self):
        weights = learn.platform_order(self.state())
        self.assertGreater(weights["greenhouse"], weights["taleo"])

    def test_an_untried_platform_is_unproven_not_condemned(self):
        """Otherwise a new platform never gets the evidence to be judged."""
        state = {"jobs": {"a": {"portal_attempted_at": jm.now(),
                                "ats": "newthing", "status": "portal_manual"}}}
        self.assertEqual(learn.platform_order(state)["newthing"], 0.5)


class TestWhatGetsWritten(unittest.TestCase):
    def test_the_file_records_its_own_uncertainty(self):
        import tempfile
        state = {"jobs": sent(4, 1)}
        with mock.patch.object(learn, "LEARNED_PATH",
                               os.path.join(tempfile.mkdtemp(), "learned.json")):
            learned = learn.write(state)
        self.assertEqual(learned["verdicts"], [])
        self.assertTrue(learned["not_yet"])
        self.assertEqual(learned["emails"]["sent"], 4)
        self.assertEqual(learned["emails"]["human_replies"], 1)

    def test_a_missing_file_is_not_an_error(self):
        with mock.patch.object(learn, "LEARNED_PATH", "/nonexistent/x.json"):
            self.assertEqual(learn.load_learned(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
