"""
Tests for the second letter.

Offline. Nothing is sent.

Two things matter more than the rest. It must go out ONCE, because Harry has
to work in this city afterwards. And it must not look automated, because a
chase is asking a stranger for a favour.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import followup  # noqa: E402
import job_machine as jm  # noqa: E402


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def letter(**over):
    job = {"external_id": "a", "company": "Hydrasun", "title": "Technician",
           "contact_email": "sarah@hydrasun.com", "contact_name": "Sarah",
           "sent_at": ago(9), "sent_subject": "Harry Russell, Technician",
           "message_id": "<abc@gmail.com>", "status": "sent",
           "description": "Aberdeen based."}
    job.update(over)
    return job


class TestWhoIsDue(unittest.TestCase):
    def test_a_letter_nobody_answered_is_due(self):
        self.assertTrue(followup.due(letter()))

    def test_it_is_chased_once_and_only_once(self):
        """A second chase is worth sending to the sender, not the reader."""
        job = letter(chased_at=jm.now())
        self.assertFalse(followup.due(job))

    def test_a_human_reply_stops_it(self):
        self.assertFalse(followup.due(
            letter(replied_at=jm.now(), reply_category="interest")))

    def test_an_autoresponder_does_not_stop_it(self):
        """It proves the address works and that nobody has read it yet."""
        self.assertTrue(followup.due(
            letter(replied_at=jm.now(),
                   reply_category="auto_acknowledgement")))

    def test_too_soon_is_not_due(self):
        self.assertFalse(followup.due(letter(sent_at=ago(1))))

    def test_too_late_is_not_due(self):
        """A chase about a vacancy filled six weeks ago makes the sender look
        like he is not paying attention."""
        self.assertFalse(followup.due(letter(sent_at=ago(60))))

    def test_no_address_means_nothing_to_chase(self):
        self.assertFalse(followup.due(letter(contact_email=None)))

    def test_an_application_already_in_is_not_chased(self):
        self.assertFalse(followup.due(letter(status="portal_submitted")))


class TestWeekendsAreNotChances(unittest.TestCase):
    """Nobody reads a recruitment email on a Sunday. A letter sent on the
    Friday is four calendar days old on the Tuesday and has had exactly one
    working day of attention."""

    def test_a_weekend_does_not_count_as_two_days(self):
        friday = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)
        tuesday = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
        with mock.patch.object(followup, "datetime") as clock:
            clock.now.return_value = tuesday
            self.assertEqual(
                followup.working_days_since(friday.isoformat()), 2)

    def test_a_full_working_week_counts_as_five(self):
        friday = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)
        next_friday = datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc)
        with mock.patch.object(followup, "datetime") as clock:
            clock.now.return_value = next_friday
            self.assertEqual(
                followup.working_days_since(friday.isoformat()), 5)

    def test_the_same_day_is_no_days(self):
        self.assertEqual(followup.working_days_since(jm.now()), 0)


class TestItMustNotLookAutomated(unittest.TestCase):
    """The first draft produced 'Hello Mysupporthr,' to Baker Hughes, because
    contact_name had been filled in from the local part of
    mysupporthr@bakerhughes.com. It would have gone out looking like a mail
    merge that had failed, which is worse than no name at all."""

    def test_a_mailbox_handle_is_never_greeted_as_a_person(self):
        for name, mail in (("Mysupporthr", "mysupporthr@bakerhughes.com"),
                           ("Giadung", "giadung@hmh.com.vn"),
                           ("Careers", "careers@acme.com"),
                           ("Recruitment", "recruitment@acme.com"),
                           ("Info", "info@acme.com")):
            with self.subTest(name=name):
                self.assertEqual(
                    followup.greeting({"contact_name": name,
                                       "contact_email": mail}), "Hello,")

    def test_a_real_first_name_is_used(self):
        self.assertEqual(
            followup.greeting({"contact_name": "Sarah",
                               "contact_email": "sarah.mcleod@hydrasun.com"}),
            "Hello Sarah,")

    def test_no_name_is_simply_hello(self):
        self.assertEqual(followup.greeting({}), "Hello,")


class TestTheLetterItself(unittest.TestCase):
    def test_it_threads_onto_the_original(self):
        """So it lands under the first letter rather than arriving as a
        second cold approach from somebody who forgot they already wrote."""
        headers = followup.threading_headers(letter())
        self.assertEqual(headers["In-Reply-To"], "<abc@gmail.com>")
        self.assertEqual(headers["References"], "<abc@gmail.com>")

    def test_the_subject_becomes_a_reply(self):
        subject, _ = followup.compose(letter())
        self.assertTrue(subject.startswith("Re: "))
        self.assertNotIn("Re: Re:", subject)

    def test_an_agency_is_asked_to_be_put_on_the_list(self):
        """They have fifty roles. Asking about one wastes the approach."""
        _, body = followup.compose(letter(company="Orion Group Recruitment"))
        self.assertTrue(any(w in body.lower() for w in
                            ("database", "registering", "list")))

    def test_an_employer_is_asked_about_the_vacancy(self):
        _, body = followup.compose(letter(company="Hydrasun"))
        self.assertTrue(any(w in body.lower() for w in
                            ("still open", "still live", "applying formally")))

    def test_the_cv_is_not_sent_again(self):
        """It is in the thread directly below. Sending it twice says he has
        not noticed that he already did."""
        job = letter()
        with mock.patch.object(jm, "send_email") as send:
            followup.send_one({}, job, dry_run=False)
        self.assertIs(send.call_args.kwargs["attach_cv"], False)

    def test_a_send_that_fails_is_not_recorded_as_chased(self):
        job = letter()
        with mock.patch.object(jm, "send_email",
                               side_effect=RuntimeError("smtp")):
            self.assertFalse(followup.send_one({}, job, dry_run=False))
        self.assertNotIn(followup.CHASED, job)

    def test_a_dry_run_sends_nothing_and_records_nothing(self):
        job = letter()
        with mock.patch.object(jm, "send_email") as send:
            followup.run({"jobs": {"a": job}}, dry_run=True)
        send.assert_not_called()
        self.assertNotIn(followup.CHASED, job)

    def test_the_run_is_capped(self):
        jobs = {str(i): letter(external_id=str(i)) for i in range(20)}
        with mock.patch.object(jm, "send_email", return_value="<x@y>"):
            sent = followup.run({"jobs": jobs}, dry_run=False, limit=3)
        self.assertEqual(sent, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
