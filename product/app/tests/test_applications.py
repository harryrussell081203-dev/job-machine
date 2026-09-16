"""Tests for the application tracker.

Every established product in this category has one. The gap here was not the
screen, it was that nothing recorded what came BACK: drafts.status says where
a letter is, never what the employer did about it.

The rules worth protecting:

  - a draft is not an application, and a discarded one never was. Counting
    them is how a tracker flatters somebody with a number that means nothing
  - a reply the inbox check noticed counts straight away, because a tracker
    that waits to be told shows "0 heard back" to somebody who has already
    had answers - the most discouraging possible lie to tell a job hunter.
    But it may only ever say a message ARRIVED, never what it said
  - what the user says wins wherever they have said anything. An explicit
    "rejected" is hearing back and is not a reply, and no detection may
    promote it into one
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

    def test_it_says_it_watches_without_claiming_to_read(self):
        """This screen used to say "You mark these yourself... replies never
        come to us", and that was true when it was written.

        It stopped being true when the inbox check shipped: with the user's
        mailbox connected the machine does look, and the tracker now counts
        what it finds. Leaving the old sentence up would have understated
        what the product does to the one person paying for it.

        The line it must not cross is the other one. It sees that a message
        arrived from an address it wrote to - never the subject, never the
        body - so it may say an employer has been in touch and must never say
        what they said.
        """
        self.application("Acme")
        body = self.client.get("/applications").text
        self.assertIn("This watches for answers", body)
        self.assertIn("never what it says", body)
        self.assertNotIn("You mark these yourself", body)

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


class TestADetectedReplyCountsWithoutBeingConfirmed(Base):
    """The screen said "14 letters sent, 0 heard back, 0% reply rate" on a
    search that had already had a reply - Octane Recruitment had answered the
    day before and the inbox check had flagged it.

    The flag was there. The stats simply did not look at it, because
    heard_back counted only outcomes somebody had tapped. So the one number a
    person job hunting most needs to be true - is this working? - read zero
    while the thing was working.

    Counting detections fixes that, and the cost is stated plainly rather
    than hidden: a FROM match cannot tell a real reply from "apply through
    our portal", so the screen says how many are unconfirmed and the landing
    page no longer claims autoresponders are excluded.
    """

    def detected(self, company="Acme"):
        did = self.application(company)
        self.db.mark_reply_seen(self.uid, did)
        return did

    def test_it_counts_before_anybody_taps(self):
        self.detected("Octane")
        stats = self.db.application_stats(self.uid)
        self.assertEqual(stats["heard_back"], 1)
        self.assertEqual(stats["reply_rate"], 100)
        self.assertEqual(stats["detected"], 1)

    def test_it_stops_counting_as_still_waiting(self):
        """Or the same letter is both answered and outstanding."""
        self.detected("Octane")
        self.assertEqual(self.db.application_stats(self.uid)["awaiting"], 0)

    def test_a_rejection_the_user_recorded_is_not_promoted_to_a_reply(self):
        """The whole point of keeping outcome and reply_seen_at separate.

        A rejection IS hearing back, and is deliberately not a reply, or the
        headline improves as things go worse. A message arriving is exactly
        how a rejection turns up, so without this the detection would quietly
        overturn the user's own answer.
        """
        did = self.application("Acme")
        self.db.mark_reply_seen(self.uid, did)
        self.db.set_outcome(self.uid, did, "rejected")
        stats = self.db.application_stats(self.uid)
        self.assertEqual(stats["heard_back"], 0)
        self.assertEqual(stats["reply_rate"], 0)
        self.assertEqual(stats["detected"], 0, "no longer unconfirmed")

    def test_confirming_it_does_not_count_it_twice(self):
        did = self.detected("Acme")
        self.db.set_outcome(self.uid, did, "replied")
        stats = self.db.application_stats(self.uid)
        self.assertEqual(stats["heard_back"], 1)
        self.assertEqual(stats["detected"], 0)

    def test_the_screen_says_how_many_nobody_has_checked(self):
        self.detected("Octane")
        body = self.client.get("/applications").text
        self.assertIn("spotted automatically", body)
        self.assertIn("apply through our portal", body)

    def test_the_public_rate_is_over_every_letter_now(self):
        """It used to be over letters somebody had come back and marked - a
        self-selected sample, because people record good news far more often
        than silence. Every letter is checked now, so every letter counts."""
        for i in range(4):
            self.application(f"Company {i}")
        self.detected("Answered")
        stats = self.db.public_stats()
        self.assertEqual(stats["tracked"], 5)
        self.assertEqual(stats["heard_back"], 1)
        self.assertEqual(stats["detected"], 1)

    def test_the_public_rate_still_needs_enough_people(self):
        """One person's diary is not a statistic, detections or not."""
        self.detected("Acme")
        stats = self.db.public_stats()
        self.assertIsNone(stats["reply_rate"])
        self.assertTrue(stats["rate_withheld"])


class TestWhatIsWorkingForYou(Base):
    """The product's whole argument is that WHO you write to decides
    everything, and that has been a claim on the landing page taken from one
    person's history. This asks the same question of the reader's own sending.

    On the founder's 135 imported letters it reads: a generic inbox 3%, a
    hiring inbox 28%, a named person 24%. That is the most useful screen the
    product can show somebody, because it is theirs and it is checkable.
    """

    def letters(self, tier, n, heard=0):
        for i in range(n):
            did = self.application(f"Firm {tier}-{i}")
            with self.db.connect() as c:
                c.execute("UPDATE drafts SET contact_tier = ? WHERE id = ?",
                          (tier, did))
            if i < heard:
                self.db.set_outcome(self.uid, did, "replied")

    def test_it_says_nothing_until_there_is_enough_to_say(self):
        self.letters(3, 4, heard=4)
        working = self.db.what_is_working(self.uid)
        self.assertFalse(working["enough"])
        self.assertNotIn("What is working for you",
                         self.client.get("/applications").text)

    def test_a_thin_row_is_counted_but_given_no_percentage(self):
        """Three letters and one reply is not 33%. A page that prints that
        teaches somebody to distrust everything else on it."""
        self.letters(3, 10, heard=3)
        self.letters(1, 3, heard=1)
        rows = {c["tier"]: c for c in
                self.db.what_is_working(self.uid)["contacts"]}
        self.assertEqual(rows[3]["rate"], 30)
        self.assertEqual(rows[1]["sent"], 3)
        self.assertIsNone(rows[1]["rate"], "too few to put a number on")

    def test_it_draws_the_lesson_when_the_gap_is_real(self):
        self.letters(3, 20, heard=8)      # 40%
        self.letters(1, 20, heard=1)      # 5%
        lesson = self.db.what_is_working(self.uid)["lesson"]
        self.assertIsNotNone(lesson)
        self.assertEqual(lesson["best"]["tier"], 3)
        self.assertEqual(lesson["worst"]["tier"], 1)
        self.assertEqual(lesson["gap"], 35)
        self.assertIn("is getting you", self.client.get("/applications").text)

    def test_it_draws_no_lesson_from_a_small_difference(self):
        """A five-point gap on twenty letters is noise, and telling somebody
        to change their search on it is worse than saying nothing."""
        self.letters(3, 20, heard=6)      # 30%
        self.letters(1, 20, heard=5)      # 25%
        self.assertIsNone(self.db.what_is_working(self.uid)["lesson"])

    def test_a_detected_reply_counts_here_too(self):
        """Same rule as the headline, or the two disagree on one screen."""
        self.letters(3, 10)
        did = self.application("Seen Co")
        with self.db.connect() as c:
            c.execute("UPDATE drafts SET contact_tier = 3 WHERE id = ?", (did,))
        self.db.mark_reply_seen(self.uid, did)
        rows = {c["tier"]: c for c in
                self.db.what_is_working(self.uid)["contacts"]}
        self.assertEqual(rows[3]["heard"], 1)

    def test_a_rejection_is_not_counted_as_working(self):
        self.letters(3, 10)
        did = self.application("No Thanks Ltd")
        with self.db.connect() as c:
            c.execute("UPDATE drafts SET contact_tier = 3 WHERE id = ?", (did,))
        self.db.set_outcome(self.uid, did, "rejected")
        rows = {c["tier"]: c for c in
                self.db.what_is_working(self.uid)["contacts"]}
        self.assertEqual(rows[3]["heard"], 0)

    def test_one_persons_results_are_not_anothers(self):
        self.letters(3, 10, heard=5)
        other = self.db.get_or_create_user("someone@example.com")["id"]
        self.assertFalse(self.db.what_is_working(other)["enough"])
