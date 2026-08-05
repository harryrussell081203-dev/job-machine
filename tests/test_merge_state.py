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


class TestTheParkedJobsBeingReleased(unittest.TestCase):
    """The portal fallback moves a listing from 'portal_manual' (rank 7) back
    to 'scored' (rank 1) on purpose, because its application form could not be
    driven and an email is the way in instead.

    The ordinary more-advanced-wins rule read that as a step backwards and
    reverted it. Eighty-six listings were released three runs in a row and
    silently re-parked by this file each time - the stage worked perfectly and
    left no trace, which is the hardest kind of bug to see from the outside."""

    def test_a_released_job_is_not_re_parked_by_the_merge(self):
        out = ms.merge(
            {"jobs": {"a": {"status": "portal_manual", "score": 90}}},
            {"jobs": {"a": {"status": "scored", "score": 90,
                            "portal_fallback_at": "2026-08-04T15:30:00"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "scored")

    def test_it_works_whichever_side_the_release_is_on(self):
        out = ms.merge(
            {"jobs": {"a": {"status": "scored",
                            "portal_fallback_at": "2026-08-04T15:30:00"}}},
            {"jobs": {"a": {"status": "portal_manual", "score": 90}}})
        self.assertEqual(out["jobs"]["a"]["status"], "scored")

    def test_a_rescore_and_a_release_do_not_cancel_each_other(self):
        """Both are deliberate re-openings; the newer one is the current
        intent."""
        out = ms.merge(
            {"jobs": {"a": {"status": "new", "rescored_at": "2026-08-04T14:00:00"}}},
            {"jobs": {"a": {"status": "scored",
                            "portal_fallback_at": "2026-08-04T15:30:00"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "scored")

    def test_an_application_actually_sent_still_wins(self):
        """A release is a step back into the queue. Something already sent has
        left the queue, and must never be dragged back into it."""
        out = ms.merge(
            {"jobs": {"a": {"status": "sent", "to": "x@y.com",
                            "sent_at": "2026-08-04T16:00:00",
                            "portal_fallback_at": "2026-08-04T15:30:00"}}},
            {"jobs": {"a": {"status": "scored",
                            "portal_fallback_at": "2026-08-04T15:30:00"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "sent")


class TestTheSupportRegister(unittest.TestCase):
    """Which charities and training bodies have been written to. One approach
    each, ever - so an entry lost here is a second letter to somebody who has
    already had one. This is exactly what happened: four letters went out, the
    merge had no rule for the key, and main came back without it."""

    def test_a_run_that_writes_letters_does_not_lose_the_record(self):
        out = ms.merge({"jobs": {}},
                       {"jobs": {}, "support_asked": {
                           "poppyscotland": {"at": "2026-08-03T21:11:57",
                                             "email": "f.b@poppyscotland.org.uk"}}})
        self.assertIn("poppyscotland", out.get("support_asked", {}))

    def test_both_runs_letters_are_remembered(self):
        out = ms.merge({"support_asked": {"ssafa": {"at": "2026-08-03"}}},
                       {"support_asked": {"legion scotland": {"at": "2026-08-04"}}})
        self.assertEqual(set(out["support_asked"]), {"ssafa", "legion scotland"})

    def test_the_first_approach_is_the_one_remembered(self):
        out = ms.merge({"support_asked": {"ssafa": {"at": "2026-08-03"}}},
                       {"support_asked": {"ssafa": {"at": "2026-07-01"}}})
        self.assertEqual(out["support_asked"]["ssafa"]["at"], "2026-07-01")


class TestKeysNobodyHasWrittenARuleFor(unittest.TestCase):
    """Three separate keys have now been silently dropped by this file - the
    board cache, the rescore, the support register - each found only after a
    run had already lost work. The default has to stop being 'discard'."""

    def test_an_unrecognised_key_survives_the_merge(self):
        out = ms.merge({"jobs": {}}, {"jobs": {}, "invented_later": {"a": 1}})
        self.assertEqual(out["invented_later"], {"a": 1})

    def test_both_sides_records_under_an_unknown_key_are_kept(self):
        out = ms.merge({"invented_later": {"a": 1}},
                       {"invented_later": {"b": 2}})
        self.assertEqual(out["invented_later"], {"a": 1, "b": 2})

    def test_a_scalar_already_on_main_is_not_overwritten(self):
        out = ms.merge({"invented_later": "theirs"}, {"invented_later": "ours"})
        self.assertEqual(out["invented_later"], "theirs")

    def test_the_known_keys_keep_their_own_rules(self):
        """The catch-all must not reach a key that has a real rule, or it would
        quietly undo it - here, by letting a stale board answer win."""
        out = ms.merge(
            {"ats_boards": {"acme": {"ats": "lever", "checked_at": "2026-07-01"}}},
            {"ats_boards": {"acme": {"ats": None, "checked_at": "2026-08-01"}}})
        self.assertIsNone(out["ats_boards"]["acme"]["ats"])


class TestMergingTheAgencyRegister(unittest.TestCase):
    """The opposite rule to the charity register, for the opposite reason.

    A charity is written to once ever, so the earliest record is the true
    answer. An agency is written to again on a cooldown, so what matters is the
    LATEST letter - keep the earliest and the cooldown expires against a letter
    that has not been sent yet."""

    def test_the_most_recent_approach_wins(self):
        out = ms.merge(
            {"agency_registered": {"cammach": {"at": "2026-06-01", "count": 1}}},
            {"agency_registered": {"cammach": {"at": "2026-08-01", "count": 2}}})
        self.assertEqual(out["agency_registered"]["cammach"]["at"], "2026-08-01")

    def test_it_works_whichever_side_the_newer_letter_is_on(self):
        out = ms.merge(
            {"agency_registered": {"cammach": {"at": "2026-08-01", "count": 2}}},
            {"agency_registered": {"cammach": {"at": "2026-06-01", "count": 1}}})
        self.assertEqual(out["agency_registered"]["cammach"]["at"], "2026-08-01")

    def test_the_approach_count_can_never_go_down(self):
        """Two runs both wrote. The register must not claim fewer approaches
        than were actually made, or the cap stops capping."""
        out = ms.merge(
            {"agency_registered": {"cammach": {"at": "2026-08-02", "count": 1}}},
            {"agency_registered": {"cammach": {"at": "2026-08-01", "count": 4}}})
        self.assertEqual(out["agency_registered"]["cammach"]["count"], 4)

    def test_the_first_ever_approach_is_remembered(self):
        out = ms.merge(
            {"agency_registered": {"cammach": {"at": "2026-06-01", "count": 1}}},
            {"agency_registered": {"cammach": {"at": "2026-08-01", "count": 2,
                                               "first_at": "2026-06-01"}}})
        self.assertEqual(out["agency_registered"]["cammach"]["first_at"],
                         "2026-06-01")

    def test_both_sides_agencies_survive(self):
        out = ms.merge({"agency_registered": {"a": {"at": "2026-08-01"}}},
                       {"agency_registered": {"b": {"at": "2026-08-02"}}})
        self.assertEqual(set(out["agency_registered"]), {"a", "b"})

    def test_it_has_a_rule_rather_than_falling_through_to_the_catch_all(self):
        """The catch-all lets the remote side win a clash, which for this
        register is the stale answer."""
        self.assertIn("agency_registered", ms.KNOWN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
