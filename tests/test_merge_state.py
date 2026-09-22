"""
Tests for merging two state files.

Several workflows write data/state.json and they can finish at the same
time. A plain git rebase hits a conflict in what is really a set of
independent records, so they are merged field by field instead.
"""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import merge_state as ms  # noqa: E402
import personal  # noqa: E402


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

    def test_call_script_counts_merge_like_the_other_counters(self):
        out = ms.merge({"call_script_counts": {"2026-08-29": 1}},
                       {"call_script_counts": {"2026-08-29": 2, "2026-08-28": 1}})
        self.assertEqual(out["call_script_counts"],
                        {"2026-08-29": 2, "2026-08-28": 1})

    def test_it_no_longer_warns_about_call_script_counts(self):
        self.assertIn("call_script_counts", ms.KNOWN)


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
    """The portal agent has since been retired, but its records have not: the
    listings it released still carry portal_fallback_at and still have to beat
    the parked status they came from, so this rule stays under test.

    The portal fallback moves a listing from 'portal_manual' (rank 7) back
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
        quietly undo it.

        The catch-all resolves a clash in favour of `theirs`, because without
        knowing what a key means that is the choice that cannot lose a record.
        The support register's rule is the opposite - the EARLIEST approach is
        the true answer to 'has this ever been done' - so if the catch-all ever
        reached it, a second letter would go to somebody who has already had
        one."""
        out = ms.merge(
            {"support_asked": {"ssafa": {"at": "2026-08-03"}}},
            {"support_asked": {"ssafa": {"at": "2026-07-01"}}})
        self.assertEqual(out["support_asked"]["ssafa"]["at"], "2026-07-01")


class TestTheAgencyApproachCounter(unittest.TestCase):
    """Agencies, unlike everyone else, may be approached a few times - they
    are paid to place people and a second call about a different role is
    normal. That makes this a counter rather than a flag, and a counter that
    merges wrong is worse than one that is lost: take either side's count
    instead of the higher one and two concurrent runs each think an agency has
    been written to once, and keep writing."""

    def test_the_higher_count_wins(self):
        out = ms.merge(
            {"agency_registered": {"orion": {"count": 2, "at": "2026-08-04"}}},
            {"agency_registered": {"orion": {"count": 1, "at": "2026-08-03"}}})
        self.assertEqual(out["agency_registered"]["orion"]["count"], 2)

    def test_the_first_approach_and_the_latest_are_both_kept(self):
        out = ms.merge(
            {"agency_registered": {"orion": {"first_at": "2026-08-01",
                                             "at": "2026-08-04"}}},
            {"agency_registered": {"orion": {"first_at": "2026-07-20",
                                             "at": "2026-08-02"}}})
        entry = out["agency_registered"]["orion"]
        self.assertEqual(entry["first_at"], "2026-07-20")
        self.assertEqual(entry["at"], "2026-08-04")

    def test_every_address_used_is_remembered(self):
        out = ms.merge(
            {"agency_registered": {"orion": {"emails": ["a@orion.com"]}}},
            {"agency_registered": {"orion": {"emails": ["b@orion.com"]}}})
        self.assertEqual(sorted(out["agency_registered"]["orion"]["emails"]),
                         ["a@orion.com", "b@orion.com"])

    def test_both_sides_agencies_survive(self):
        out = ms.merge({"agency_registered": {"orion": {"count": 1}}},
                       {"agency_registered": {"cammach": {"count": 1}}})
        self.assertEqual(set(out["agency_registered"]), {"orion", "cammach"})

    def test_it_no_longer_warns_about_an_unknown_key(self):
        self.assertIn("agency_registered", ms.KNOWN)


class TestTheRediscoveredJobsSurvivingTheMerge(unittest.TestCase):
    """rediscover() moves a listing from 'no_email' (rank 3) back to 'scored'
    (rank 1), because the machine turned out to already hold its employer's
    domain.

    That is the third stage to move a listing backwards on purpose, and the
    first two both learned the same lesson the expensive way: the ordinary
    more-advanced-wins rule reads a deliberate step back as an accident and
    reverts it. Eighty-six portal listings were released three runs running
    and silently re-parked here every time.

    So this is under test from the same commit that added the stage, rather
    than after watching a hundred listings quietly go back in the bin."""

    def test_a_rediscovered_job_is_not_re_parked_by_the_merge(self):
        out = ms.merge(
            {"jobs": {"a": {"status": "no_email",
                            "skip_reason": "no domain found"}}},
            {"jobs": {"a": {"status": "scored",
                            "rediscovered_at": "2026-09-06T10:00:00"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "scored")

    def test_it_works_whichever_side_the_reopening_is_on(self):
        out = ms.merge(
            {"jobs": {"a": {"status": "scored",
                            "rediscovered_at": "2026-09-06T10:00:00"}}},
            {"jobs": {"a": {"status": "no_email",
                            "skip_reason": "no domain found"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "scored")

    def test_the_newer_reopening_wins_over_an_older_one(self):
        out = ms.merge(
            {"jobs": {"a": {"status": "new",
                            "rescored_at": "2026-09-01T09:00:00"}}},
            {"jobs": {"a": {"status": "scored",
                            "rediscovered_at": "2026-09-06T10:00:00"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "scored")

    def test_an_application_already_sent_is_never_dragged_back(self):
        out = ms.merge(
            {"jobs": {"a": {"status": "sent"}}},
            {"jobs": {"a": {"status": "scored",
                            "rediscovered_at": "2026-09-06T10:00:00"}}})
        self.assertEqual(out["jobs"]["a"]["status"], "sent")


class TestNoReopeningEverBeatsALetterAlreadySent(unittest.TestCase):
    """The re-opening rule was written to beat rank, and it beat rank
    absolutely - so 'sent' on one side lost to a re-opened 'scored' on the
    other, and the application would go out a second time.

    It stayed invisible because the one test covering it put the SAME
    portal_fallback_at on both sides, making them equal and falling through to
    rank before the re-opening rule could do any harm. The hole only opens
    when one side carries a stamp the other does not - the ordinary case for
    any stage that re-opens listings.

    An employer cannot be un-emailed, so this is tested for every status that
    means something left the building, and against every stamp that re-opens.
    """

    def test_every_terminal_status_survives_every_kind_of_reopening(self):
        for status in ("sent", "replied", "spec_sent", "test_sent",
                       "portal_submitted", "do_not_contact"):
            for field in ("rescored_at", "portal_fallback_at",
                          "rediscovered_at"):
                for order in (0, 1):
                    with self.subTest(status=status, field=field, order=order):
                        gone = {"jobs": {"a": {"status": status}}}
                        back = {"jobs": {"a": {"status": "scored",
                                               field: "2026-09-06T10:00:00"}}}
                        sides = (gone, back) if order == 0 else (back, gone)
                        out = ms.merge(*sides)
                        self.assertEqual(out["jobs"]["a"]["status"], status)

    def test_a_reopening_still_beats_a_status_that_is_not_terminal(self):
        """The rule this protects must not be broken in the fixing of it: a
        listing parked at 'skipped' or 'no_email' is still in the queue and a
        deliberate re-opening must still win."""
        for parked in ("skipped", "no_email", "compose_failed", "ready",
                       "portal_manual"):
            with self.subTest(parked=parked):
                out = ms.merge(
                    {"jobs": {"a": {"status": parked}}},
                    {"jobs": {"a": {"status": "scored",
                                    "rediscovered_at": "2026-09-06T10:00:00"}}})
                self.assertEqual(out["jobs"]["a"]["status"], "scored")


class TestTheHeartbeatSurvivesTheMerge(unittest.TestCase):
    """The alarm that says the machine has DIED was crying wolf daily.

    'heartbeat' had no merge rule, so carry_unknown() merged it with theirs
    winning - and theirs is the copy already on main, which had been frozen
    the same way. last_run could never advance: it sat at 2026-09-08T12:03
    while the machine ran a dozen more times, so every run measured a
    multi-day outage that was not happening and raised the alarm for it.

    An alarm that is always going off is one Harry learns to ignore, which is
    worse than no alarm - the real outage arrives looking exactly like the
    eleven false ones before it.
    """

    def test_the_newest_run_stamp_wins(self):
        out = ms.merge({"heartbeat": {"last_run": "2026-09-08T12:03:35+00:00"}},
                       {"heartbeat": {"last_run": "2026-09-10T12:17:01+00:00"}})
        self.assertEqual(out["heartbeat"]["last_run"],
                         "2026-09-10T12:17:01+00:00")

    def test_it_wins_from_either_side(self):
        """The run that just finished may be on either side of the merge."""
        out = ms.merge({"heartbeat": {"last_run": "2026-09-10T12:17:01+00:00"}},
                       {"heartbeat": {"last_run": "2026-09-08T12:03:35+00:00"}})
        self.assertEqual(out["heartbeat"]["last_run"],
                         "2026-09-10T12:17:01+00:00")

    def test_the_newest_alert_stamp_wins_too(self):
        """alerted_at is what stops a recovered outage being re-reported once
        an hour. Losing it turns one alarm into a stream of them."""
        out = ms.merge({"heartbeat": {"alerted_at": "2026-09-01T09:00:00+00:00"}},
                       {"heartbeat": {"alerted_at": "2026-09-09T12:20:06+00:00"}})
        self.assertEqual(out["heartbeat"]["alerted_at"],
                         "2026-09-09T12:20:06+00:00")

    def test_one_side_missing_the_heartbeat_keeps_the_other(self):
        out = ms.merge({}, {"heartbeat": {"last_run": "2026-09-10T12:17:01+00:00"}})
        self.assertEqual(out["heartbeat"]["last_run"],
                         "2026-09-10T12:17:01+00:00")
        out = ms.merge({"heartbeat": {"last_run": "2026-09-10T12:17:01+00:00"}}, {})
        self.assertEqual(out["heartbeat"]["last_run"],
                         "2026-09-10T12:17:01+00:00")

    def test_other_heartbeat_fields_are_not_dropped(self):
        out = ms.merge({"heartbeat": {"last_run": "2026-09-08T12:03:35+00:00",
                                      "note": "from main"}},
                       {"heartbeat": {"last_run": "2026-09-10T12:17:01+00:00"}})
        self.assertEqual(out["heartbeat"]["note"], "from main")

    def test_it_no_longer_warns_about_the_heartbeat(self):
        """A key showing up in carry_unknown() is asking for a real rule.
        This one now has one."""
        self.assertIn("heartbeat", ms.KNOWN)


class TestTheMergeDoesNotUnsealTheFile(unittest.TestCase):
    """The bug this class exists for, stated plainly.

    The machine seals state.json on the way out of every save. This script
    then merges that sealed file against the copy already on the branch - and
    every rule in it prefers the branch's copy when the two sides tie. A
    sealed record and its plaintext twin tie on all of them: same status, so
    pick() falls through to `len(a) >= len(b)` with theirs as `a`; same
    timestamp, so union_earliest() keeps theirs; and carry_unknown() does
    `merged.update(mine)` outright.

    Measured against the real 18MB file before the fix: of 579 addresses, 570
    came back in the clear and NOT ONE enc.v1: marker survived. The machine
    would have sealed the file every run and this would have unsealed it every
    commit, for ever, while every part looked like it was working.

    So the merge now unseals both sides, compares plaintext, and seals once at
    the end.
    """

    def setUp(self):
        self.key = personal.make_key()

    def sides(self):
        """The ordinary case: the same record on both sides, one sealed."""
        state = {
            "jobs": {"a": {"company": "Kestrel", "status": "sent",
                           "contact_email": "fiona@kestrel.example",
                           "sent_to": "fiona@kestrel.example"}},
            "companies_contacted": {"kestrel": {
                "email": "fiona@kestrel.example", "at": "2026-09-01T09:00:00"}},
            "contact_numbers": {"ex mil": {"name": "Jean Hedouin",
                                           "numbers": ["+443332026500"]}},
        }
        return state, personal.seal(copy.deepcopy(state), self.key)

    def test_a_sealed_side_does_not_lose_to_its_plaintext_twin(self):
        theirs, ours = self.sides()
        out = personal.seal(ms.merge(personal.unseal(copy.deepcopy(theirs)),
                                     personal.unseal(ours, self.key)),
                            self.key)
        blob = json.dumps(out)
        self.assertNotIn("fiona@kestrel.example", blob)
        self.assertNotIn("Jean Hedouin", blob)
        self.assertIn(personal.MARKER, blob)

    def test_the_whole_script_run_end_to_end_seals_what_it_writes(self):
        """Through main(), because the wiring is where this broke: load()
        unseals, main() seals, and either one missing brings the bug back."""
        theirs, ours = self.sides()
        work = tempfile.mkdtemp()
        paths = [os.path.join(work, n) for n in ("t.json", "o.json", "out.json")]
        for path, data in zip(paths[:2], (theirs, ours)):
            with open(path, "w") as f:
                json.dump(data, f)

        argv, env = sys.argv, os.environ.get(personal.KEY_ENV)
        os.environ[personal.KEY_ENV] = self.key
        try:
            sys.argv = ["merge_state.py"] + paths
            ms.main()
        finally:
            sys.argv = argv
            if env is None:
                os.environ.pop(personal.KEY_ENV, None)
            else:
                os.environ[personal.KEY_ENV] = env

        with open(paths[2]) as f:
            written = f.read()
        self.assertNotIn("fiona@kestrel.example", written)
        self.assertIn(personal.MARKER, written)
        # and it is still the same state underneath
        back = personal.unseal(json.loads(written), self.key)
        self.assertEqual(back["jobs"]["a"]["contact_email"],
                         "fiona@kestrel.example")
        self.assertEqual(back["companies_contacted"]["kestrel"]["at"],
                         "2026-09-01T09:00:00")

    def test_the_rules_still_see_real_values_rather_than_ciphertext(self):
        """The reason it unseals rather than teaching each rule about
        ciphertext. union_earliest keeps the EARLIEST contact, which is a
        string comparison on the timestamp - on sealed bytes it would compare
        base64 and pick at random."""
        theirs = {"companies_contacted": {"k": {"at": "2026-09-05T09:00:00",
                                                "email": "late@x.example"}}}
        ours = personal.seal(
            {"companies_contacted": {"k": {"at": "2026-01-02T09:00:00",
                                           "email": "early@x.example"}}},
            self.key)
        out = ms.merge(personal.unseal(theirs), personal.unseal(ours, self.key))
        self.assertEqual(out["companies_contacted"]["k"]["at"],
                         "2026-01-02T09:00:00")

    def test_without_a_key_it_behaves_exactly_as_it_always_did(self):
        """Local work and any checkout with no secret configured. Sealing is
        a no-op both ways, so this must not have changed the old behaviour."""
        old = os.environ.pop(personal.KEY_ENV, None)
        try:
            out = ms.merge({"jobs": {"a": {"status": "scored"}}},
                           {"jobs": {"a": {"status": "sent"}}})
            self.assertEqual(out["jobs"]["a"]["status"], "sent")
            self.assertEqual(personal.seal(out), out)
        finally:
            if old is not None:
                os.environ[personal.KEY_ENV] = old

    def test_a_sealed_file_with_no_key_raises_rather_than_merging_blind(self):
        """load() must not treat 'I cannot read this' as 'this is empty'. An
        unreadable side losing every comparison would drop the record of who
        has been written to, and the next run writes to them again."""
        work = tempfile.mkdtemp()
        path = os.path.join(work, "sealed.json")
        with open(path, "w") as f:
            json.dump(self.sides()[1], f)
        old = os.environ.pop(personal.KEY_ENV, None)
        try:
            with self.assertRaises(personal.KeyMissing):
                ms.load(path)
        finally:
            if old is not None:
                os.environ[personal.KEY_ENV] = old

    def test_a_missing_file_is_still_an_empty_state(self):
        """The distinction the above turns on: absent is a first run, and
        unreadable is damage."""
        self.assertEqual(ms.load("/nonexistent/state.json"), {})


if __name__ == "__main__":
    unittest.main()
