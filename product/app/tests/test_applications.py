"""Tests for the application tracker.

Every established product in this category has one. The gap here was not the
screen, it was that nothing recorded what came BACK: drafts.status says where
a letter is, never what the employer did about it.

The rules worth protecting:

  - a draft is not an application, and a discarded one never was. Counting
    them is how a tracker flatters somebody with a number that means nothing
  - the outcome is recorded by the person who saw the reply, because replies
    go to their inbox and never come to us. The screen has to say so rather
    than implying it is watching
  - it must be correctable. A tracker you cannot undo is one people stop
    trusting after the first mis-tap on a phone
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_app import AppTestCase  # noqa: E402


class Base(AppTestCase):
    def setUp(self):
        super().setUp()
        self.db = self.main.db
        self.uid = self.db.get_or_create_user("sam@example.com")["id"]
        self.sign_in("sam@example.com")

    def application(self, company="Acme", days_ago=0, outcome="",
                    status="sent"):
        did = self.db.add_draft(
            self.uid, job_title="Subsea Technician", company=company,
            to_email=f"a@{company.lower().replace(' ', '')}.com",
            subject="s", body="b", score=78, contact_tier=3)
        if status == "sent":
            self.db.mark_draft(self.uid, did, "sent")
            with self.db.connect() as c:
                c.execute("UPDATE drafts SET sent_at = ? WHERE id = ?",
                          (self.db.now() - days_ago * 86400, did))
        elif status != "draft":
            self.db.mark_draft(self.uid, did, status)
        if outcome:
            self.db.set_outcome(self.uid, did, outcome)
        return did


class TestWhatCounts(Base):
    def test_only_letters_that_actually_went(self):
        """A draft is not an application and a discarded one never was."""
        self.application("Sent Co")
        self.application("Still Drafted", status="draft")
        self.application("Binned", status="discarded")
        rows = self.db.applications(self.uid)
        self.assertEqual([r["company"] for r in rows], ["Sent Co"])
        self.assertEqual(self.db.application_stats(self.uid)["sent"], 1)

    def test_newest_first(self):
        self.application("Older", days_ago=10)
        self.application("Newer", days_ago=1)
        self.assertEqual([r["company"] for r in self.db.applications(self.uid)],
                         ["Newer", "Older"])

    def test_one_persons_applications_are_not_anothers(self):
        self.application("Mine")
        other = self.db.get_or_create_user("someone@example.com")["id"]
        self.assertEqual(self.db.applications(other), [])
        self.assertEqual(self.db.application_stats(other)["sent"], 0)


class TestTheNumbers(Base):
    def test_a_rejection_is_heard_back_but_not_a_reply_rate_win(self):
        """Being turned down is information and belongs in the tracker. It is
        not what the reply rate is measuring, and folding it in would make the
        headline number flatter as things go worse."""
        self.application("A", outcome="replied")
        self.application("B", outcome="rejected")
        self.application("C")
        s = self.db.application_stats(self.uid)
        self.assertEqual(s["sent"], 3)
        self.assertEqual(s["heard_back"], 1)
        self.assertEqual(s["reply_rate"], 33)

    def test_interviews_and_offers_count_as_hearing_back(self):
        self.application("A", outcome="interview")
        self.application("B", outcome="offer")
        s = self.db.application_stats(self.uid)
        self.assertEqual(s["heard_back"], 2)
        self.assertEqual(s["interviews"], 2)

    def test_sent_only_ever_goes_up(self):
        """The lesson from the personal machine: "awaiting a reply" DRAINS as
        answers arrive, so a headline built on it falls on the best days."""
        did = self.application("A")
        before = self.db.application_stats(self.uid)
        self.db.set_outcome(self.uid, did, "interview")
        after = self.db.application_stats(self.uid)
        self.assertEqual(before["sent"], after["sent"])
        self.assertGreater(before["awaiting"], after["awaiting"])

    def test_nothing_sent_is_not_a_division_by_zero(self):
        self.assertEqual(self.db.application_stats(self.uid)["reply_rate"], 0)

    def test_the_longest_wait_is_the_number_worth_acting_on(self):
        self.application("Old", days_ago=21)
        self.application("New", days_ago=1)
        self.application("Answered", days_ago=40, outcome="replied")
        s = self.db.application_stats(self.uid)
        self.assertEqual(s["longest_wait_days"], 21)
        self.assertEqual(s["awaiting"], 2)


class TestRecordingAnOutcome(Base):
    def test_it_can_be_recorded_and_undone(self):
        did = self.application("Acme")
        self.client.post(f"/applications/{did}/outcome",
                         data={"outcome": "interview"})
        self.assertEqual(self.db.get_draft(self.uid, did)["outcome"],
                         "interview")
        self.client.post(f"/applications/{did}/outcome", data={"outcome": ""})
        self.assertEqual(self.db.get_draft(self.uid, did)["outcome"], "")

    def test_an_invented_outcome_is_refused(self):
        did = self.application("Acme")
        self.assertFalse(self.db.set_outcome(self.uid, did, "hired maybe"))
        self.assertEqual(self.db.get_draft(self.uid, did)["outcome"], "")

    def test_you_cannot_mark_somebody_elses(self):
        did = self.application("Acme")
        other = self.db.get_or_create_user("someone@example.com")["id"]
        self.db.set_outcome(other, did, "offer")
        self.assertEqual(self.db.get_draft(self.uid, did)["outcome"], "")

    def test_an_unsent_draft_cannot_have_an_outcome(self):
        """It has not been anywhere, so nothing can have come back."""
        did = self.application("Acme", status="draft")
        self.db.set_outcome(self.uid, did, "replied")
        self.assertEqual(self.db.get_draft(self.uid, did)["outcome"], "")


class TestTheScreen(Base):
    def test_it_needs_an_account(self):
        self.client.cookies.clear()
        r = self.client.get("/applications", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_an_empty_tracker_says_so_and_points_somewhere(self):
        """A blank screen reads as broken. It should say nothing has gone yet
        and send them to the drafts waiting for them."""
        body = self.client.get("/applications").text
        self.assertIn("Nothing has gone out yet", body)
        self.assertIn('href="/drafts"', body)

    def test_it_shows_the_letters_and_how_long_they_have_waited(self):
        self.application("Acme Subsea", days_ago=12)
        body = self.client.get("/applications").text
        self.assertIn("Acme Subsea", body)
        self.assertIn("sent 12 days ago", body)

    def test_today_and_yesterday_are_not_zero_days_ago(self):
        self.application("Fresh", days_ago=0)
        self.assertIn("sent today", self.client.get("/applications").text)

    def test_it_says_the_user_marks_these_rather_than_implying_it_watches(self):
        """We cannot see replies - on an own mailbox they go to the user, and
        a Recruited address puts their address on Reply-To precisely so
        they still do. Implying otherwise would be a lie on the one screen
        whose whole job is telling the truth about what happened."""
        self.application("Acme")
        body = self.client.get("/applications").text
        self.assertIn("You mark these yourself", body)
        self.assertIn("never come to us", body)

    def test_a_long_wait_suggests_a_follow_up(self):
        self.application("Acme", days_ago=20)
        self.assertIn("follow-up", self.client.get("/applications").text)

    def test_a_short_wait_does_not_nag(self):
        self.application("Acme", days_ago=2)
        self.assertNotIn("follow-up", self.client.get("/applications").text)

    def test_it_is_reachable_from_every_page(self):
        self.assertIn('href="/applications"',
                      self.client.get("/dashboard").text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
