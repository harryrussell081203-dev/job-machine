"""Telling somebody what "Look for work now" is doing while it does it.

A run is minutes long - the job boards, then a free-tier model reading every
listing a batch at a time. The button used to hold the request open for all of
it, which on a phone is a white screen and then very often a proxy timeout on
a run that actually worked.

Three things have to hold, and they are the three these tests are about:

  - the press comes back immediately; the work carries on without the request
  - what it says is true, specific, and changes as the run moves
  - it ends, always - including when the thing doing the work dies without
    getting the chance to say so
"""

import json
import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from cryptography.fernet import Fernet  # noqa: E402
from test_app import AppTestCase  # noqa: E402
from test_runner import PROFILE  # noqa: E402
from test_sending import Base as SendingBase  # noqa: E402


class Base(AppTestCase):
    # AppTestCase does not set one, so any test here that connects a mailbox
    # would fail inside the vault rather than on the thing it is testing.
    env = {"CREDENTIAL_KEY": Fernet.generate_key().decode()}

    def setUp(self):
        super().setUp()
        self.db = self.main.db
        self.autosend = sys.modules["app.autosend"]
        self.sent = []
        self.uid = self.db.get_or_create_user("sam@example.com")["id"]
        self.sign_in("sam@example.com")
        # The suite's one valid profile. Profile.from_dict validates far
        # more than save_profile does, so a hand-rolled dict here passes the
        # save and then fails inside the run.
        self.db.save_profile(self.uid, dict(PROFILE, email="sam@example.com"))

    def fake_send(self, **kw):
        self.sent.append(kw)

    # The same stub the sending suite uses, borrowed rather than
    # copied: it patches both the sys.modules entry AND the package
    # attribute, and a second copy of that subtlety is a second
    # copy to get wrong.
    no_drafting = SendingBase.no_drafting

    def fake_runner(self, work):
        """Replace the pipeline with `work(user_id, on_step)`."""
        import app
        real = self.main.runner.run_for_user

        class R:
            drafted = 0

            def summary(self):
                return "0 drafted from 0 listings"

        def run_for_user(user_id, *, on_step=None, **kw):
            work(user_id, on_step)
            return R()

        self.main.runner.run_for_user = run_for_user
        self.addCleanup(lambda: setattr(self.main.runner, "run_for_user", real))


class TestTheRecordOfARun(Base):
    def test_nothing_to_report_before_anything_has_run(self):
        self.assertIsNone(self.db.run_progress(self.uid))

    def test_it_reports_what_it_was_told(self):
        self.db.start_run(self.uid)
        self.db.set_run_step(self.uid, "Scoring 12 jobs", done=4, total=12)
        p = self.db.run_progress(self.uid)
        self.assertEqual(p["state"], "running")
        self.assertEqual(p["step"], "Scoring 12 jobs")
        self.assertEqual(p["percent"], 33)

    def test_an_unknown_total_has_no_percentage_rather_than_zero(self):
        """During the harvest nothing knows how many listings exist yet. A bar
        sitting at 0% reads as broken when it only means "counting", so the
        page is given None and shows a sweep instead of a number."""
        self.db.start_run(self.uid)
        self.db.set_run_step(self.uid, "Searching the job boards")
        self.assertIsNone(self.db.run_progress(self.uid)["percent"])

    def test_a_second_run_cannot_start_while_one_is_going(self):
        """Two taps half a second apart on a phone is the normal case."""
        self.assertTrue(self.db.start_run(self.uid))
        self.assertFalse(self.db.start_run(self.uid))

    def test_one_user_starting_does_not_block_another(self):
        other = self.db.get_or_create_user("someone@example.com")["id"]
        self.assertTrue(self.db.start_run(self.uid))
        self.assertTrue(self.db.start_run(other))

    def test_a_finished_run_lets_the_next_one_start(self):
        self.db.start_run(self.uid)
        self.db.finish_run(self.uid, result="2 drafted", drafted=2)
        self.assertTrue(self.db.start_run(self.uid))

    def test_a_run_that_died_without_saying_so_is_not_shown_for_ever(self):
        """The thread can be killed outright - the host restarts, the
        container is recycled. Nothing then writes 'failed', and without this
        the page animates a bar at somebody indefinitely, which is worse than
        an error because an error at least says to press the button again.
        """
        self.db.start_run(self.uid)
        with self.db.connect() as c:
            c.execute("UPDATE run_progress SET updated_at = ? "
                      "WHERE user_id = ?",
                      (self.db.now() - self.db.RUN_STALE_AFTER - 1, self.uid))
        p = self.db.run_progress(self.uid)
        self.assertEqual(p["state"], "failed")
        self.assertIn("again", p["result"])

    def test_a_stale_run_can_be_replaced(self):
        self.db.start_run(self.uid)
        with self.db.connect() as c:
            c.execute("UPDATE run_progress SET updated_at = ? "
                      "WHERE user_id = ?",
                      (self.db.now() - self.db.RUN_STALE_AFTER - 1, self.uid))
        self.assertTrue(self.db.start_run(self.uid),
                        "otherwise one lost thread locks the button for ever")

    def test_a_slow_run_is_not_mistaken_for_a_dead_one(self):
        """Every report resets the clock, so a run that is working stays
        alive however long it takes."""
        self.db.start_run(self.uid)
        with self.db.connect() as c:
            c.execute("UPDATE run_progress SET updated_at = ? "
                      "WHERE user_id = ?",
                      (self.db.now() - self.db.RUN_STALE_AFTER - 1, self.uid))
        self.db.set_run_step(self.uid, "still going")
        self.assertEqual(self.db.run_progress(self.uid)["state"], "running")

    def test_reporting_never_breaks_the_run_it_reports_on(self):
        """Progress is not the work. If the commentary fails the person loses
        the commentary and still gets their letters."""
        self.db.start_run(self.uid)
        real, self.db.connect = self.db.connect, self._explode
        try:
            self.db.set_run_step(self.uid, "anything")      # must not raise
            self.db.finish_run(self.uid, result="done")     # must not raise
        finally:
            self.db.connect = real

    def _explode(self, *a, **kw):
        raise RuntimeError("the database is gone")


class TestPressingTheButton(Base):
    def test_it_answers_immediately_rather_than_holding_the_request(self):
        """The whole point. The old version ran the pipeline inside the
        request and did not answer for minutes."""
        gate = threading.Event()
        self.fake_runner(lambda uid, on_step: gate.wait(5))

        started = time.time()
        r = self.client.post("/run", follow_redirects=False)
        elapsed = time.time() - started
        gate.set()

        self.assertEqual(r.status_code, 303)
        self.assertLess(elapsed, 2.0,
                        "the request must not wait for the run")

    def test_the_work_actually_happens(self):
        done = threading.Event()
        self.fake_runner(lambda uid, on_step: done.set())
        self.client.post("/run", follow_redirects=False)
        self.assertTrue(done.wait(5), "the thread never ran")

    def test_it_lands_somewhere_that_shows_the_progress(self):
        self.fake_runner(lambda uid, on_step: None)
        r = self.client.post("/run", follow_redirects=False)
        self.assertEqual(r.headers["location"], "/dashboard")

    def test_a_second_press_does_not_start_a_second_run(self):
        runs = []
        gate = threading.Event()

        def work(uid, on_step):
            runs.append(uid)
            gate.wait(5)
        self.fake_runner(work)

        self.client.post("/run", follow_redirects=False)
        self.client.post("/run", follow_redirects=False)
        time.sleep(0.3)
        gate.set()
        self.assertEqual(len(runs), 1)

    def test_a_crash_in_the_run_is_reported_rather_than_swallowed(self):
        """This thread is nobody's caller, so an exception would otherwise
        vanish into the log and leave the page waiting on a dead run."""
        def boom(uid, on_step):
            raise RuntimeError("Adzuna said no")
        self.fake_runner(boom)

        self.client.post("/run", follow_redirects=False)
        for _ in range(50):
            p = self.db.run_progress(self.uid)
            if p and p["state"] == "failed":
                break
            time.sleep(0.1)
        self.assertEqual(p["state"], "failed")
        self.assertIn("Adzuna said no", p["result"])

    def test_signing_in_is_required_to_start_one(self):
        self.client.cookies.clear()
        r = self.client.post("/run", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/login")


class TestWatchingIt(Base):
    def test_the_progress_endpoint_needs_an_account(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/run/progress").status_code, 401)

    def test_it_says_none_before_anything_has_run(self):
        self.assertEqual(self.client.get("/run/progress").json()["state"],
                         "none")

    def test_it_reports_the_live_step(self):
        self.db.start_run(self.uid)
        self.db.set_run_step(self.uid, "Finding a real address at Tendeka",
                             done=1, total=2)
        body = self.client.get("/run/progress").json()
        self.assertEqual(body["state"], "running")
        self.assertEqual(body["step"], "Finding a real address at Tendeka")
        self.assertEqual(body["percent"], 50)

    def test_one_user_cannot_watch_anothers_run(self):
        other = self.db.get_or_create_user("someone@example.com")["id"]
        self.db.start_run(other)
        self.db.set_run_step(other, "their private search")
        self.assertEqual(self.client.get("/run/progress").json()["state"],
                         "none")

    def test_the_panel_renders_from_the_server_too(self):
        """So it is right on first paint, survives a refresh, and still says
        something with JavaScript switched off."""
        self.db.start_run(self.uid)
        self.db.set_run_step(self.uid, "Searching the job boards")
        page = self.client.get("/dashboard").text
        self.assertIn("Searching the job boards", page)
        self.assertIn('data-state="running"', page)

    def test_the_bar_does_not_borrow_the_headers_class_name(self):
        """It did, and the bar rendered as an empty pill that never moved.

        The site header is .bar. Reusing the name gave the track the header's
        flex layout - 23px tall, with a fill 0px high - so nothing was ever
        visible however correct the width was. Nothing in the DOM looked
        wrong; only the rendered page did.
        """
        page = self.client.get("/dashboard").text
        self.assertIn('class="runbar"', page)
        self.assertNotIn('<div class="bar" id="runbar"', page)
        css = self.client.get("/static/style.css").text
        self.assertIn(".runbar > span", css)


class TestTheRunSaysWhatItIsDoing(Base):
    """The sentence is the feature; the bar is the supporting act. A
    percentage on its own tells somebody nothing about whether it is stuck.
    """

    def steps_of_a_real_run(self):
        import types
        from app import runner
        said = []

        listings = []
        for i in range(3):
            listing = types.SimpleNamespace(
                external_id=f"job-{i}", company=f"Firm {i}",
                title="Technician", location="Aberdeen", url="http://x",
                skipped="", score=0, score_reason="", salary_min=0,
                salary_max=0, description="d", posted_at=0)
            listings.append(listing)

        real_harvest = runner.harvest.harvest
        real_score = runner.scoring.score
        real_draft = runner._draft_one
        runner.harvest.harvest = lambda *a, **k: {"keep": listings,
                                                  "dropped": []}

        def score(items, profile, ai, **kw):
            on_batch = kw.get("on_batch")
            if on_batch:
                on_batch(0, len(items))
                on_batch(len(items), len(items))
            return {"passed": items, "rejected": []}
        runner.scoring.score = score
        runner._draft_one = lambda *a, **k: None

        def restore():
            runner.harvest.harvest = real_harvest
            runner.scoring.score = real_score
            runner._draft_one = real_draft
        self.addCleanup(restore)

        runner.run_for_user(self.uid, ai=lambda p: "", cap=5,
                            on_step=lambda text, **c: said.append(text))
        return said

    def test_it_names_each_stage_in_order(self):
        said = self.steps_of_a_real_run()
        self.assertTrue(said[0].startswith("Searching the job boards"), said)
        self.assertTrue(any("Setting aside employers" in s for s in said), said)
        self.assertTrue(any("Scoring" in s for s in said), said)
        self.assertEqual(said[-1], "Finishing up")

    def test_it_names_the_employer_it_is_working_on(self):
        """"Finding a real address at Kestrel Foods" is the moment somebody
        watching stops wondering whether it works."""
        said = self.steps_of_a_real_run()
        self.assertTrue(any("Firm 0" in s for s in said), said)

    def test_a_broken_reporter_does_not_stop_the_run(self):
        from app import runner
        real = runner.harvest.harvest
        runner.harvest.harvest = lambda *a, **k: {"keep": [], "dropped": []}
        self.addCleanup(lambda: setattr(runner.harvest, "harvest", real))

        def explode(text, **counts):
            raise RuntimeError("no")
        report = runner.run_for_user(self.uid, ai=lambda p: "",
                                     on_step=explode)
        self.assertEqual(report.errors, [])


class TestScoringReportsItsProgress(unittest.TestCase):
    """By far the slowest part of a run, so the part a watcher most needs to
    see moving."""

    def listings(self, n):
        import types
        return [types.SimpleNamespace(
            external_id=f"j{i}", company=f"C{i}", title="Technician",
            location="Aberdeen", url="http://x", skipped="", score=0,
            score_reason="", salary_min=0, salary_max=0, description="d",
            posted_at=0) for i in range(n)]

    def test_it_reports_before_each_batch_not_after(self):
        """The wait IS the call, so reporting after it would mean the screen
        went quiet for exactly as long as the slow part took."""
        from jobseeker.pipeline import scoring
        from jobseeker.profile import Profile

        seen = []
        calls = []

        def ai(prompt):
            calls.append(len(seen))
            return "\n".join(f"{i + 1}. 90 good" for i in range(2))

        profile = Profile.from_dict(PROFILE)
        scoring.score(self.listings(4), profile, ai, batch_size=2,
                      on_batch=lambda done, total: seen.append((done, total)))

        self.assertEqual(seen[0], (0, 4), "it says so before the first call")
        self.assertEqual(seen[-1], (4, 4), "and that it finished")
        self.assertTrue(calls, "the model was actually called")

    def test_the_sweep_passes_no_callback_and_gets_the_same_answer(self):
        """The scheduled sweep calls this with no callback at all, so the
        addition has to be invisible to it. Compared against itself rather
        than against a hand-written expectation, because what matters is that
        nothing CHANGED, not what the scores happen to be."""
        from jobseeker.pipeline import scoring
        from jobseeker.profile import Profile
        profile = Profile.from_dict(PROFILE)
        replies = ["1. 90 solid match\n2. 40 wrong trade"]

        def ai(prompt):
            return replies[0]

        without = scoring.score(self.listings(2), profile, ai, batch_size=2)
        with_cb = scoring.score(self.listings(2), profile, ai, batch_size=2,
                                on_batch=lambda done, total: None)
        self.assertEqual(
            [len(without["passed"]), len(without["rejected"])],
            [len(with_cb["passed"]), len(with_cb["rejected"])])


if __name__ == "__main__":
    unittest.main()


class TestTheDashboardSaysWhetherItIsOn(Base):
    """"Is this thing actually running?" is the question somebody has when
    they open the app, and until now nothing on the screen answered it.

    Every figure in this panel is something that HAPPENED. The obvious version
    says "next run at 11:10", and that would be a lie: the schedule is
    GitHub's, and GitHub delays scheduled workflows under load and drops the
    ones it cannot place - this repository's own sweep ran twice on a day it
    was set to run three times, and never at a listed minute.
    """

    def test_it_says_what_is_stopping_it_in_the_order_it_stops(self):
        status = self.db.machine_status(self.uid)
        self.assertFalse(status["mailbox"])
        self.assertIn("Connect a mailbox", self.client.get("/dashboard").text)

    def test_a_mailbox_without_automatic_sending_says_so(self):
        self.db.save_mail_account(self.uid, address="a@b.com",
                                  host="smtp.b.com", port=465, password="x")
        page = self.client.get("/dashboard").text
        self.assertIn("Writing, not sending", page)

    def test_when_it_is_on_it_says_what_it_will_spend(self):
        self.db.save_mail_account(self.uid, address="a@b.com",
                                  host="smtp.b.com", port=465, password="x")
        self.db.save_send_settings(self.uid, auto_send=1, daily_cap=12,
                                   hold_minutes=60)
        page = self.client.get("/dashboard").text
        self.assertIn("up to\n         12 a day", page.replace("\r", ""))
        self.assertIn("12 to spend", page)

    def test_it_never_promises_a_time_it_cannot_keep(self):
        """The rule this panel exists under. GitHub's scheduler is late and
        lossy, so a printed "next run" is how a product teaches somebody to
        stop believing it."""
        self.db.save_mail_account(self.uid, address="a@b.com",
                                  host="smtp.b.com", port=465, password="x")
        self.db.save_send_settings(self.uid, auto_send=1)
        page = self.client.get("/dashboard").text
        for promise in ("Next run", "next run", "will run at", "Next sweep"):
            self.assertNotIn(promise, page)

    def test_it_reports_the_last_thing_it_actually_did(self):
        self.db.record_sent(self.uid, draft_id=None, to_email="a@b.com",
                            company="Acme")
        status = self.db.machine_status(self.uid)
        self.assertGreater(status["last_sent_at"], 0)
        self.assertIn("Last letter went", self.client.get("/dashboard").text)

    def test_a_scheduled_sweep_counts_as_looking_for_work(self):
        """Before this the app could only see runs somebody pressed a button
        for, so a machine working overnight looked like a machine doing
        nothing - which is the opposite of the thing worth showing."""
        self.db.save_mail_account(self.uid, address="a@b.com",
                                  host="smtp.b.com", port=465, password="x")
        self.db.mark_mail_verified(self.uid)
        self.db.save_send_settings(self.uid, auto_send=1)
        drafted = self.no_drafting()

        self.autosend.sweep(sender=self.fake_send)

        self.assertEqual(drafted, [self.uid])
        self.assertGreater(self.db.machine_status(self.uid)["last_looked_at"],
                           0)

    def test_a_sweep_is_never_skipped_to_protect_a_progress_row(self):
        """start_run() refusing means a hand-started run is already in flight.
        The drafting must still happen; only the bookkeeping steps aside."""
        self.db.save_mail_account(self.uid, address="a@b.com",
                                  host="smtp.b.com", port=465, password="x")
        self.db.mark_mail_verified(self.uid)
        self.db.save_send_settings(self.uid, auto_send=1)
        self.db.start_run(self.uid)          # somebody is already running one
        drafted = self.no_drafting()

        self.autosend.sweep(sender=self.fake_send)

        self.assertEqual(drafted, [self.uid], "the sweep was dropped")


class TestHowLongAgo(Base):
    """"twenty minutes ago", not "2026-09-15 08:53". A timestamp makes the
    reader do arithmetic to answer the only question they have."""

    def ago(self, seconds):
        return self.main._ago(self.db.now() - seconds)

    def test_it_reads_like_a_person_would_say_it(self):
        self.assertEqual(self.ago(30), "just now")
        self.assertEqual(self.ago(600), "10 minutes ago")
        self.assertEqual(self.ago(3600), "about an hour ago")
        self.assertEqual(self.ago(4 * 3600), "4 hours ago")
        self.assertEqual(self.ago(26 * 3600), "yesterday")
        self.assertEqual(self.ago(3 * 86400), "3 days ago")

    def test_nothing_recorded_says_nothing(self):
        self.assertEqual(self.main._ago(0), "")
        self.assertEqual(self.main._ago(None), "")

    def test_a_clock_that_ran_backwards_says_nothing_rather_than_nonsense(self):
        self.assertEqual(self.main._ago(self.db.now() + 600), "")
