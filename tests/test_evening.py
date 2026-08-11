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


if __name__ == "__main__":
    unittest.main(verbosity=2)
