"""
Tests for merging two state files.

Several workflows write data/state.json and they can finish at the same
time. A plain git rebase hits a conflict in what is really a set of
independent records, so they are merged field by field instead.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import merge_state as ms  # noqa: E402


class TestMergingJobs(unittest.TestCase):
    def test_both_sides_jobs_survive(self):
        out = ms.merge({"jobs": {"a": {"status": "new"}}},
                       {"jobs": {"b": {"status": "new"}}})
        self.assertEqual(set(out["jobs"]), {"a", "b"})

    def test_the_record_further_along_the_pipeline_wins(self):
        out = ms.merge({"jobs": {"a": {"status": "scored"}}},
                       {"jobs": {"a": {"status": "sent"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "sent")

    def test_a_send_is_never_undone_by_a_slower_run(self):
        out = ms.merge({"jobs": {"a": {"status": "sent", "to": "x@y.com"}}},
                       {"jobs": {"a": {"status": "new"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "sent")


class TestMergingCounters(unittest.TestCase):
    def test_the_higher_count_per_day_is_kept(self):
        out = ms.merge({"send_counts": {"2026-08-02": 3}},
                       {"send_counts": {"2026-08-02": 5, "2026-08-01": 2}})
        self.assertEqual(out["send_counts"], {"2026-08-02": 5, "2026-08-01": 2})

    def test_the_earliest_contact_with_a_company_is_kept(self):
        out = ms.merge({"companies_contacted": {"acme": {"at": "2026-08-01"}}},
                       {"companies_contacted": {"acme": {"at": "2026-07-20"}}})
        self.assertEqual(out["companies_contacted"]["acme"]["at"], "2026-07-20")


class TestMergingTheBoardCache(unittest.TestCase):
    """Which ATS a company uses, remembered so it is not rediscovered on every
    run. Without a rule here, one run's findings are simply dropped."""
    def test_both_runs_discoveries_are_kept(self):
        out = ms.merge(
            {"ats_boards": {"acme": {"ats": "lever", "checked_at": "2026-08-01"}}},
            {"ats_boards": {"beta": {"ats": None, "checked_at": "2026-08-01"}}})
        self.assertEqual(set(out["ats_boards"]), {"acme", "beta"})

    def test_the_fresher_answer_wins(self):
        out = ms.merge(
            {"ats_boards": {"acme": {"ats": "lever", "checked_at": "2026-07-01"}}},
            {"ats_boards": {"acme": {"ats": None, "checked_at": "2026-08-01"}}})
        self.assertIsNone(out["ats_boards"]["acme"]["ats"])

    def test_a_found_board_beats_a_miss_of_the_same_age(self):
        out = ms.merge(
            {"ats_boards": {"acme": {"ats": None, "checked_at": "2026-08-01"}}},
            {"ats_boards": {"acme": {"ats": "lever", "slug": "acme",
                                     "checked_at": "2026-08-01"}}})
        self.assertEqual(out["ats_boards"]["acme"]["ats"], "lever")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestReopenedListings(unittest.TestCase):
    """A rescore sets a listing back to 'new' on purpose, and 'new' ranks below
    'skipped'. The ordinary more-advanced-wins rule therefore reverted every
    listing a re-judging run re-opened, and that run reported 'no state
    changes' - it had been structurally incapable of saving its work."""

    def test_a_deliberate_step_back_beats_an_accidental_step_forward(self):
        out = ms.merge(
            {"jobs": {"a": {"status": "skipped", "score": 65}}},
            {"jobs": {"a": {"status": "new", "rescored_at": "2026-08-03T15:00:00"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "new")

    def test_it_works_whichever_side_the_rescore_is_on(self):
        out = ms.merge(
            {"jobs": {"a": {"status": "new", "rescored_at": "2026-08-03T15:00:00"}}},
            {"jobs": {"a": {"status": "skipped", "score": 65}}})
        self.assertEqual(out["jobs"]["a"]["status"], "new")

    def test_the_newer_rescore_wins_when_both_were_reopened(self):
        out = ms.merge(
            {"jobs": {"a": {"status": "skipped", "rescored_at": "2026-08-01T09:00:00"}}},
            {"jobs": {"a": {"status": "new", "rescored_at": "2026-08-03T15:00:00"}}})
        self.assertEqual(out["jobs"]["a"]["rescored_at"], "2026-08-03T15:00:00")

    def test_ordinary_progress_is_unaffected(self):
        out = ms.merge({"jobs": {"a": {"status": "scored"}}},
                       {"jobs": {"a": {"status": "sent"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "sent")

    def test_a_send_is_still_never_undone(self):
        out = ms.merge({"jobs": {"a": {"status": "sent", "to": "x@y.com"}}},
                       {"jobs": {"a": {"status": "new"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "sent")
