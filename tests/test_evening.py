"""
Tests for the evening digest.

Harry's words: "a lot of people just call me randomly as a result of the
system working and I have to figure out on the fly who's calling and what it's
about and what the role is."

That is the machine's own success arriving as an ambush. The test that matters
here is that a line names the company and the role before anything else,
because that is the question he cannot answer on the phone.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evening  # noqa: E402
import job_machine as jm  # noqa: E402


def replied(company="Hydrasun", title="Instrumentation Technician",
            category="interview", **over):
    job = {"external_id": company, "company": company, "title": title,
           "replied_at": jm.now(), "reply_category": category,
           "sent_at": jm.now(), "reply_subject": "Re: your application"}
    job.update(over)
    return job


def state(*jobs):
    return {"jobs": {j["external_id"]: j for j in jobs}}


class TestHeCanAnswerThePhone(unittest.TestCase):
    def test_the_line_names_the_company_and_the_role_first(self):
        line = evening.line_for(replied())
        self.assertTrue(line.startswith("Hydrasun"))
        self.assertIn("Instrumentation Technician", line)

    def test_an_interview_is_not_summarised_like_a_rejection(self):
        self.assertIn("INTERVIEW", evening.line_for(replied(category="interview")))
        self.assertIn("no thanks",
                      evening.line_for(replied(category="rejection")))

    def test_the_urgent_ones_come_first(self):
        st = state(replied("Zetland", category="rejection"),
                   replied("Kirkwall", category="interview"),
                   replied("Braemar", category="question"))
        text = evening.compose(st)
        self.assertLess(text.index("Kirkwall"), text.index("Braemar"))
        self.assertLess(text.index("Braemar"), text.index("Zetland"))

    def test_it_says_up_front_if_somebody_wants_to_interview_him(self):
        text = evening.compose(state(replied(category="interview")))
        self.assertIn("wants to interview you", text)

    def test_it_tells_him_why_he_is_reading_it(self):
        text = evening.compose(state(replied()))
        self.assertIn("rings tomorrow", text)


class TestWhatItLeavesOut(unittest.TestCase):
    def test_a_job_alert_is_not_a_reply(self):
        """A digest full of noreply mail is a digest he stops reading."""
        st = state(replied(reply_subject="Your daily job alert: 20 new jobs"))
        self.assertIsNone(evening.compose(st))

    def test_nothing_in_means_no_text(self):
        self.assertIsNone(evening.compose({"jobs": {}}))

    def test_yesterdays_replies_are_not_todays_news(self):
        old = replied()
        old["replied_at"] = "2026-07-01T09:00:00+00:00"
        self.assertIsNone(evening.compose(state(old)))

    def test_a_long_day_is_capped_and_says_so(self):
        st = state(*[replied(f"Firm{i}", category="question")
                     for i in range(14)])
        text = evening.compose(st)
        self.assertIn("more in your inbox", text)
        self.assertLessEqual(text.count("\n- "), evening.MAX_LINES + 1)


class TestWhenItGoes(unittest.TestCase):
    def test_it_waits_for_the_right_hour(self):
        with mock.patch.object(jm, "uk_now") as clock:
            clock.return_value = mock.Mock(hour=9)
            self.assertFalse(evening.is_time())
            clock.return_value = mock.Mock(hour=evening.EVENING_HOUR_UK)
            self.assertTrue(evening.is_time())

    def test_force_ignores_the_clock(self):
        with mock.patch.object(jm, "uk_now") as clock:
            clock.return_value = mock.Mock(hour=3)
            self.assertTrue(evening.is_time(force=True))

    def test_it_goes_once_a_day(self):
        st = state(replied())
        st[evening.SENT_ON] = evening.today_uk()
        with mock.patch.object(evening, "is_time", return_value=True), \
             mock.patch.object(evening.sms, "alert_harry") as text:
            self.assertFalse(evening.run(st, dry_run=False))
        text.assert_not_called()

    def test_a_dry_run_texts_nothing(self):
        with mock.patch.object(evening, "is_time", return_value=True), \
             mock.patch.object(evening.sms, "alert_harry") as text:
            evening.run(state(replied()), dry_run=True)
        text.assert_not_called()

    def test_it_only_ever_texts_harry(self):
        """alert_harry takes no recipient - it can only reach his own phone."""
        import inspect
        params = inspect.signature(evening.sms.alert_harry).parameters
        self.assertNotIn("to", params)
        self.assertNotIn("number", params)



class TestTheRestOfHisMail(unittest.TestCase):
    """His words: "text me every evening about the emails I've received that
    day and what actions need taken FOR ALL OF MY EMAILS".

    The first version of this covered replies only - mail from a company the
    machine had itself written to. In a live job hunt a good half of the useful
    mail is from somebody who found HIM: a consultant off a job board, the Job
    Centre, the Forces Employment Charity. None of it appeared here."""

    def state(self, **entry):
        base = {"who": "Cammy Keith", "address": "ckeith@tmm.com",
                "subject": "Re: your CV", "category": "question",
                "do": "Send your Thursday availability", "at": jm.now(),
                "tracked": False}
        base.update(entry)
        return {"jobs": {}, "inbox": {"k": base}}

    def test_a_stranger_who_wrote_in_gets_a_line(self):
        body = evening.compose(self.state())
        self.assertIn("Cammy Keith", body)
        self.assertIn("Thursday availability", body)

    def test_it_says_what_he_has_to_do_not_just_that_mail_arrived(self):
        body = evening.compose(self.state(do="Ring Graham before Friday",
                                          who="Graham Brown"))
        self.assertIn("Ring Graham before Friday", body)

    def test_a_company_the_reply_digest_already_names_is_not_listed_twice(self):
        """The reply line above already gives the company AND the role, which
        is more than the inbox alone can say."""
        self.assertIsNone(evening.compose(self.state(tracked=True)))

    def test_a_newsletter_that_slipped_through_is_still_left_out(self):
        self.assertIsNone(evening.compose(
            self.state(subject="Your weekly newsletter")))

    def test_mail_nobody_is_waiting_on_is_not_a_line(self):
        state = self.state()
        del state["inbox"]["k"]["category"]
        self.assertIsNone(evening.compose(state))

    def test_replies_and_other_mail_are_counted_separately(self):
        state = self.state()
        state["jobs"] = {"j": {"company": "Hydrasun", "title": "Tech",
                               "replied_at": jm.now(),
                               "reply_category": "rejection"}}
        body = evening.compose(state)
        self.assertIn("1 reply", body)
        self.assertIn("1 other", body)

    def test_a_broken_register_does_not_cost_him_the_reply_digest(self):
        state = {"jobs": {"j": {"company": "Hydrasun", "title": "Tech",
                                "replied_at": jm.now(),
                                "reply_category": "interview"}}}
        import inbox_watch
        with mock.patch.object(inbox_watch, "arrived_today",
                               side_effect=RuntimeError("no register")):
            body = evening.compose(state)
        self.assertIn("Hydrasun", body)


class TestNothingGoesSilent(unittest.TestCase):
    """A message asked about on Monday is not new enough for Tuesday's digest,
    and never beats an interview invitation to the two lines in the morning
    text. Without a count of what is still open it simply stops being
    mentioned, which is the same as losing it."""

    def old_entry(self, days=4):
        from datetime import datetime, timedelta, timezone
        when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return {"who": "Graham Brown", "address": "g@frs.co.uk",
                "subject": "Re: registering", "category": "document",
                "do": "Send Graham a Word copy of the CV", "at": when,
                "tracked": False}

    def test_an_older_unanswered_message_is_still_counted(self):
        state = {"jobs": {"j": {"company": "Hydrasun", "title": "Tech",
                                "replied_at": jm.now(),
                                "reply_category": "rejection"}},
                 "inbox": {"old": self.old_entry()}}
        body = evening.compose(state)
        self.assertIn("1 older one still waiting", body)

    def test_todays_mail_is_not_counted_twice(self):
        state = {"jobs": {}, "inbox": {"new": dict(self.old_entry(),
                                                   at=jm.now())}}
        body = evening.compose(state)
        self.assertIn("Graham Brown", body)
        self.assertNotIn("older", body)

    def test_nothing_outstanding_adds_no_line(self):
        state = {"jobs": {"j": {"company": "Hydrasun", "title": "Tech",
                                "replied_at": jm.now(),
                                "reply_category": "rejection"}}}
        self.assertNotIn("older", evening.compose(state))


if __name__ == "__main__":
    unittest.main(verbosity=2)
