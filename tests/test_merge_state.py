"""
Tests for merging two state files.

Several workflows write data/state.json and they can finish at the same
time. A plain git rebase hits a conflict in what is really a set of
independent records, so they are merged field by field instead.
"""
import os
import re
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


class TestAStatusNobodyToldTheMergeAbout(unittest.TestCase):
    """The bug this class exists to prevent has already happened twice.

    A status missing from PROGRESS used to rank 0 - below 'new' - so it lost
    every merge and the work behind it was deleted on the way back to main.
    'portal_awaiting_captcha' is the status of an application the agent has
    FILLED IN COMPLETELY, with every answer banked for the handoff page, held
    up only by a bot check. Five were built and binned in one run."""

    def test_a_completely_filled_application_is_not_binned_by_a_stale_record(self):
        theirs = {"status": "no_email", "portal_attempted_at": "2026-08-01"}
        ours = {"status": "portal_awaiting_captcha",
                "captcha_answers": [{"name": "first_name"}],
                "portal_screenshot": "shot.png", "portal_filled": ["a"]}
        self.assertEqual(ms.pick(theirs, ours)["status"],
                         "portal_awaiting_captcha")

    def test_it_works_whichever_side_the_filled_one_is_on(self):
        ours = {"status": "skipped"}
        theirs = {"status": "portal_awaiting_captcha", "captcha_answers": [1],
                  "portal_screenshot": "s.png", "portal_filled": ["a"]}
        self.assertEqual(ms.pick(theirs, ours)["status"],
                         "portal_awaiting_captcha")

    def test_an_unknown_status_does_not_beat_a_real_send(self):
        """The fix must not overshoot: a sent application is still the truth."""
        sent = {"status": "sent", "sent_at": "2026-08-01", "contact_email": "a@b",
                "sent_subject": "x", "message_id": "1", "score": 80}
        odd = {"status": "invented_later"}
        self.assertEqual(ms.pick(sent, odd)["status"], "sent")

    def test_every_status_the_code_can_set_is_ranked(self):
        """The guard. Add a status anywhere, and this fails until the merge
        knows where it sits."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pattern = re.compile(r'"status":\s*"([a-z_]+)"|\["status"\]\s*=\s*"([a-z_]+)"')
        found = set()
        for name in os.listdir(root):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name)) as f:
                for a, b in pattern.findall(f.read()):
                    found.add(a or b)
        missing = sorted(s for s in found if s not in ms.PROGRESS)
        self.assertEqual(missing, [], f"statuses the merge would drop: {missing}")

    def test_the_filled_ones_outrank_the_ones_that_never_got_there(self):
        order = {s: i for i, s in enumerate(ms.PROGRESS)}
        self.assertLess(order["portal_manual"], order["portal_review"])
        self.assertLess(order["portal_review"], order["portal_awaiting_captcha"])
        self.assertLess(order["portal_awaiting_captcha"], order["portal_ready"])
        self.assertLess(order["portal_ready"], order["portal_submitted"])


class TestABurnRunKeepsItsWork(unittest.TestCase):
    """The same merge bug as the statuses, pointing the other way, and worse.

    reopen_fallbacks() REMOVES portal_fallback_at and writes
    portal_reopened_at in its place. So after a burn run:

        ours   (the runner)  no portal_fallback_at, status portal_manual
        theirs (main)        portal_fallback_at from days ago, no_email

    reopened(ours) was '' and reopened(theirs) was a real timestamp, so
    THEIRS won - and the side that had opened a browser, filled a form and
    recorded the outcome was thrown away. A whole burn run landed on main
    having recorded nothing: seven pages captured, nine screenshots taken,
    zero attempts saved."""

    def theirs(self):
        return {"status": "no_email", "score": 85,
                "portal_fallback_at": "2026-08-04T15:00:00+00:00",
                "portal_reason": "only 0 form fields found"}

    def ours(self, **over):
        job = {"status": "portal_manual", "score": 85,
               "portal_reopened_at": "2026-08-06T00:25:00+00:00",
               "portal_attempted_at": "2026-08-06T00:25:30+00:00",
               "portal_screenshot": "shot.png",
               "portal_reason": "needs a login"}
        job.update(over)
        return job

    def test_the_run_that_did_the_work_keeps_it(self):
        won = ms.pick(self.theirs(), self.ours())
        self.assertEqual(won["status"], "portal_manual")
        self.assertTrue(won.get("portal_attempted_at"))

    def test_a_filled_application_is_not_reverted_either(self):
        """The one that matters most: every answer worked out, banked, and
        deleted by the merge on the way home."""
        won = ms.pick(self.theirs(),
                      self.ours(status="portal_awaiting_captcha",
                                captcha_answers=[{"label": "Name"}]))
        self.assertEqual(won["status"], "portal_awaiting_captcha")
        self.assertEqual(len(won["captcha_answers"]), 1)

    def test_a_submitted_one_certainly_is_not(self):
        won = ms.pick(self.theirs(), self.ours(status="portal_submitted"))
        self.assertEqual(won["status"], "portal_submitted")

    def test_a_real_fallback_still_beats_a_stale_reopening(self):
        """The fix must not overshoot. A run that has just RELEASED a job to
        the email route is the newest word on it."""
        stale = self.ours(portal_reopened_at="2026-08-01T00:00:00+00:00")
        fresh = {"status": "scored", "score": 85,
                 "portal_fallback_at": "2026-08-06T09:00:00+00:00"}
        self.assertEqual(ms.pick(stale, fresh)["status"], "scored")

    def test_every_field_that_steps_a_job_back_is_known_to_the_merge(self):
        """The guard, matching the one on statuses. Add a field anywhere that
        deliberately re-opens a record, and this fails until the merge knows
        about it - because a step backwards the merge cannot see is a step
        backwards that silently undoes the run which took it."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # 'pruned' is on this list because the guard MISSED it. Five
        # Australian applications were pruned on the runner and every one came
        # back, because prune_overseas sets 'skipped' - a deliberate step
        # backwards - and the merge could not see the field that marked it as
        # deliberate. Fourth time this exact shape has cost a run's work.
        pattern = re.compile(
            r'\["(\w*(?:reopened|fallback|rescored|pruned)\w*_at)"\]\s*=')
        found = set()
        for name in os.listdir(root):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name)) as f:
                found.update(pattern.findall(f.read()))
        missing = sorted(f for f in found if f not in ms.REOPENING_FIELDS)
        self.assertEqual(missing, [],
                         f"re-opening fields the merge cannot see: {missing}")


class TestARerunCanActuallyUpdateARecord(unittest.TestCase):
    """T-Tech was filled to 13 of 15 fields at 09:32, and attempted again at
    09:44 with two bugs fixed. The merge kept the 09:32 version.

    Both sides said portal_awaiting_captcha, both had the same number of
    keys, and the tie-break was '>=' - which always favours whatever is
    already on main. Three of the five applications that run worked on were
    discarded that way, and the numbers came back as though it had barely
    run. A stage that cannot record a second attempt cannot be improved,
    because no improvement can ever be observed."""

    def attempt(self, when, **over):
        job = {"status": "portal_awaiting_captcha",
               "portal_attempted_at": when,
               "portal_filled": ["a"], "captcha_answers": [1]}
        job.update(over)
        return job

    def test_the_newer_attempt_wins_at_the_same_status(self):
        old = self.attempt("2026-08-06T09:32:15")
        new = self.attempt("2026-08-06T09:44:02")
        self.assertEqual(ms.pick(old, new)["portal_attempted_at"],
                         "2026-08-06T09:44:02")
        self.assertEqual(ms.pick(new, old)["portal_attempted_at"],
                         "2026-08-06T09:44:02")

    def test_it_is_not_fooled_by_the_older_record_being_fatter(self):
        old = self.attempt("2026-08-06T09:32:15", portal_screenshot="a.png",
                           portal_flags=["x"], portal_pages=2, ats="workable")
        new = self.attempt("2026-08-06T09:44:02")
        self.assertEqual(ms.pick(old, new)["portal_attempted_at"],
                         "2026-08-06T09:44:02")

    def test_any_stamp_counts_not_just_the_attempt(self):
        """Every stage stamps what it did; the newest of them is the truth."""
        old = {"status": "sent", "sent_at": "2026-08-01T10:00:00"}
        new = {"status": "sent", "sent_at": "2026-08-01T10:00:00",
               "replied_at": "2026-08-05T11:00:00"}
        self.assertEqual(ms.pick(old, new), new)

    def test_a_further_status_still_beats_a_newer_one(self):
        """Recency is the tie-break, not the rule. A sent application does
        not lose to a fresher 'skipped'."""
        sent = {"status": "sent", "sent_at": "2026-08-01T10:00:00"}
        later = {"status": "skipped", "scored_at": "2026-08-06T10:00:00"}
        self.assertEqual(ms.pick(sent, later)["status"], "sent")

    def test_two_records_with_no_stamps_at_all_still_pick_one(self):
        a, b = {"status": "new"}, {"status": "new", "score": 70}
        self.assertIn(ms.pick(a, b), (a, b))
        self.assertEqual(ms.last_touched({"status": "new"}), "")

    def test_a_non_string_stamp_does_not_crash_the_merge(self):
        self.assertEqual(ms.last_touched({"scored_at": None, "x_at": 12}), "")


class TestTheInboxRegister(unittest.TestCase):
    """Every email that has been read, what it wants, and whether it has been
    texted about or dealt with.

    This needed a rule of its own, and the reason is the class of bug that has
    now bitten this file four separate times: without one, carry_unknown()
    unions the two dicts and lets THEIRS win a clash, which throws away every
    mark the runner just wrote. For this register that means Harry gets texted
    about the same email again on the next run, and the one after that."""

    def test_both_sides_messages_survive(self):
        out = ms.merge({"inbox": {"a": {"who": "A"}}},
                       {"inbox": {"b": {"who": "B"}}})
        self.assertEqual(set(out["inbox"]), {"a", "b"})

    def test_the_runners_texted_mark_is_not_thrown_away(self):
        """The bug this whole rule exists for. Main has the message from an
        earlier run with no mark; the runner just texted him about it."""
        out = ms.merge({"inbox": {"a": {"who": "A", "category": "interview"}}},
                       {"inbox": {"a": {"who": "A", "category": "interview",
                                        "texted_at": "2026-08-11T10:00:00"}}})
        self.assertEqual(out["inbox"]["a"]["texted_at"], "2026-08-11T10:00:00")

    def test_him_marking_it_done_survives_a_later_run(self):
        out = ms.merge({"inbox": {"a": {"who": "A", "category": "question"}}},
                       {"inbox": {"a": {"who": "A", "category": "question",
                                        "done_at": "2026-08-11T10:00:00"}}})
        self.assertEqual(out["inbox"]["a"]["done_at"], "2026-08-11T10:00:00")

    def test_the_first_time_he_was_told_is_the_true_one(self):
        out = ms.merge({"inbox": {"a": {"who": "A", "texted_at": "2026-08-11T12:00:00"}}},
                       {"inbox": {"a": {"who": "A", "texted_at": "2026-08-11T09:00:00"}}})
        self.assertEqual(out["inbox"]["a"]["texted_at"], "2026-08-11T09:00:00")

    def test_the_side_that_actually_read_the_message_wins(self):
        """Triage costs a model call. Losing it means paying for it twice and
        the message sitting unlisted in between."""
        out = ms.merge({"inbox": {"a": {"who": "A"}}},
                       {"inbox": {"a": {"who": "A", "category": "call",
                                        "do": "Ring Cammy back"}}})
        self.assertEqual(out["inbox"]["a"]["category"], "call")
        self.assertEqual(out["inbox"]["a"]["do"], "Ring Cammy back")

    def test_a_read_message_is_not_overwritten_by_an_unread_copy(self):
        out = ms.merge({"inbox": {"a": {"who": "A", "category": "call",
                                        "do": "Ring Cammy back"}}},
                       {"inbox": {"a": {"who": "A"}}})
        self.assertEqual(out["inbox"]["a"]["category"], "call")

    def test_the_register_has_a_rule_so_it_never_falls_to_carry_unknown(self):
        self.assertIn("inbox", ms.KNOWN)
