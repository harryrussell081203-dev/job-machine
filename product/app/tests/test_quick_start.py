"""The six-question start, and the path that needs no mailbox.

WHY THIS FILE EXISTS.

Nine people signed up and not one saved a profile. The setup screen called it
"two minutes" and "the only thing it needs", then sent them to an eighteen-
field form. Nobody ever reached the mailbox step - the thing everyone,
including me, had assumed was the wall.

So these tests guard three things:

  1. SIX ANSWERS ARE ENOUGH. The quick start saves a profile the machine can
     actually use, through the same validation as every other route, and
     starts the first search so the next screen is letters being written.

  2. A BAD ANSWER SAYS WHAT TO FIX, IN THE WORDS ON THE SCREEN. The Profile's
     own messages name JSON fields ("history is empty") that this form never
     shows. Re-rendering those to somebody on a phone is a second wall.

  3. SENDING IT YOURSELF COUNTS. Until now only automatic sends reached the
     funnel, so the path that needs no mailbox counted as nobody activating.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import AppTestCase  # noqa: E402

SIX = {
    "target_roles": "warehouse operative",
    "location": "Aberdeen",
    "name": "Sam Example",
    "phone": "07700 900123",
    "last_title": "Forklift driver",
    "last_org": "Tesco Distribution",
    "min_pay": "24000",
}


class QuickStartCase(AppTestCase):
    def setUp(self):
        super().setUp()
        # A real run hits the job boards and a model. What these tests care
        # about is that one is STARTED, so the start is recorded instead.
        self.started = []
        self._real_start = self.main._start_run
        self.main._start_run = lambda uid: self.started.append(uid) or True
        self.addCleanup(setattr, self.main, "_start_run", self._real_start)

    def me(self, email="sam@example.com"):
        return self.main.db.get_user_by_email(email)

    def post(self, **overrides):
        data = dict(SIX, **overrides)
        return self.client.post("/setup/quick", data=data,
                                follow_redirects=False)


class TestSixAnswersAreEnough(QuickStartCase):
    def test_the_setup_screen_asks_them_itself(self):
        """Not a link to eighteen fields - the questions, on the page."""
        self.sign_in()
        page = self.client.get("/setup").text
        self.assertIn('action="/setup/quick', page)
        for field in SIX:
            with self.subTest(field=field):
                self.assertIn(f'name="{field}"', page)

    def test_six_answers_save_a_profile_the_machine_can_use(self):
        """Validated by the same Profile the engine uses, so a quick profile
        is never a weaker one - just a shorter way to the same thing."""
        from jobseeker.profile import Profile
        self.sign_in()
        r = self.post()
        self.assertEqual(r.status_code, 303)
        data = self.main.db.load_profile(self.me()["id"])
        self.assertIsNotNone(data)
        Profile.from_dict(data)              # raises if it is not usable
        self.assertEqual(data["target_roles"], ["warehouse operative"])
        self.assertEqual(data["locations"], ["Aberdeen"])
        self.assertEqual(data["history"][0]["org"], "Tesco Distribution")

    def test_the_first_search_starts_straight_away(self):
        """Finishing setup and landing on an empty dashboard until a sweep
        hours later is the next place people would stop."""
        self.sign_in()
        self.post()
        self.assertEqual(self.started, [self.me()["id"]])

    def test_it_lands_on_the_dashboard(self):
        self.sign_in()
        r = self.post()
        self.assertTrue(r.headers["location"].startswith("/dashboard"))

    def test_it_counts_as_onboarded(self):
        """A saved profile is what starts the machine, so it is what
        onboarded means - not a CV, which is optional."""
        self.sign_in()
        self.post()
        self.assertIsNotNone(
            self.main.db.first_event_at(self.me()["id"], "onboarded"))

    def test_it_never_writes_over_a_fuller_profile(self):
        """Six answers over eighteen would silently throw away twelve."""
        self.sign_in()
        self.post()
        uid = self.me()["id"]
        full = self.main.db.load_profile(uid)
        full["qualifications"] = ["NVQ Level 2"]
        self.main.db.save_profile(uid, full)
        r = self.post(target_roles="something else entirely")
        self.assertEqual(r.headers["location"], "/profile")
        after = self.main.db.load_profile(uid)
        self.assertEqual(after["qualifications"], ["NVQ Level 2"])
        self.assertEqual(after["target_roles"], ["warehouse operative"])


class TestThePayBox(QuickStartCase):
    """One box, typed on a phone, in whatever form people actually type."""

    def floor(self, typed):
        return self.main._pay(typed)

    def test_a_plain_salary(self):
        self.assertEqual(self.floor("24000"), (24000, 0))

    def test_pounds_and_commas(self):
        self.assertEqual(self.floor("£25,000"), (25000, 0))

    def test_k_shorthand(self):
        self.assertEqual(self.floor("25k"), (25000, 0))

    def test_an_hourly_rate(self):
        self.assertEqual(self.floor("12"), (0, 12))

    def test_a_floor_is_rounded_up_never_down(self):
        """12.50 stored as 12 would let through a £12.00 job the person said
        they would not take."""
        self.assertEqual(self.floor("12.50"), (0, 13))
        self.assertEqual(self.floor("£11.44 an hour"), (0, 12))

    def test_nonsense_is_nothing_rather_than_a_guess(self):
        """(0, 0) lets the Profile refuse it with its own rule, rather than
        this inventing a number somebody did not give."""
        for typed in ("", "loads", "-5", "0"):
            with self.subTest(typed=typed):
                self.assertEqual(self.floor(typed), (0, 0))


class TestABadAnswerSaysWhatToFix(QuickStartCase):
    def test_a_missing_last_job_is_asked_for_in_plain_words(self):
        """The Profile says "history is empty". Nobody on this screen has
        seen a field called history."""
        self.sign_in()
        r = self.post(last_title="", last_org="")
        self.assertEqual(r.status_code, 200)
        self.assertIn("last job", r.text)
        self.assertNotIn("history is empty", r.text)

    def test_an_unreadable_pay_floor_says_what_is_wanted(self):
        self.sign_in()
        r = self.post(min_pay="decent")
        self.assertIn("the least you would work for", r.text.lower())
        self.assertNotIn("min_salary_annual", r.text)

    def test_what_they_typed_is_still_there(self):
        """Six fields re-typed on a phone because one was wrong is exactly
        the kind of thing people give up over."""
        self.sign_in()
        r = self.post(min_pay="decent")
        self.assertIn('value="Tesco Distribution"', r.text)
        self.assertIn('value="warehouse operative"', r.text)

    def test_nothing_is_saved_and_no_search_starts(self):
        self.sign_in()
        self.post(last_title="", last_org="")
        self.assertIsNone(self.main.db.load_profile(self.me()["id"]))
        self.assertEqual(self.started, [])


class TestSendingItYourselfCounts(QuickStartCase):
    def draft(self, uid):
        return self.main.db.add_draft(
            uid, job_title="Forklift driver", company="Tesco Distribution",
            to_email="jo@tesco.example", subject="Forklift driver",
            body="Hello", status="draft")

    def test_marking_a_draft_sent_records_a_first_send(self):
        """The path that needs no mailbox. Before this, it counted as
        nobody ever activating."""
        self.sign_in()
        uid = self.me()["id"]
        draft_id = self.draft(uid)
        self.client.post(f"/drafts/{draft_id}/sent", follow_redirects=False)
        self.assertIsNotNone(
            self.main.db.first_event_at(uid, "first_email_sent"))

    def test_but_it_is_not_the_event_that_pays_referrals(self):
        """A tap is not evidence a letter went. first_auto_send is."""
        self.sign_in()
        uid = self.me()["id"]
        self.client.post(f"/drafts/{self.draft(uid)}/sent",
                         follow_redirects=False)
        self.assertIsNone(self.main.db.first_event_at(uid, "first_auto_send"))

    def test_the_drafts_screen_offers_to_open_it_in_their_own_email(self):
        """Already built and never the problem - asserted so it stays."""
        self.sign_in()
        self.draft(self.me()["id"])
        page = self.client.get("/drafts").text
        self.assertIn("mailto:", page)


if __name__ == "__main__":
    unittest.main()
