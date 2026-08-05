"""
Tests for texting.

Offline. No message is sent, and httpSMS is never called.

The risk here is not a bug, it is a text landing on a stranger's phone at
eleven at night with Harry's name on it. So most of these tests are about the
messages that must NOT go: cold numbers, landlines, second attempts, numbers
lifted out of a quoted trail rather than a signature.
"""
import os
import re
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_machine as jm  # noqa: E402
import sms  # noqa: E402


def uk(year, month, day, hour):
    return datetime(year, month, day, hour, tzinfo=ZoneInfo("Europe/London"))


class TestReadingNumbers(unittest.TestCase):
    def test_the_shapes_people_actually_write(self):
        for raw, expected in [
                ("01224 327 030", "+441224327030"),
                ("07398530978", "+447398530978"),
                ("0333 202 6500", "+443332026500"),
                ("+44 7891 169509", "+447891169509"),
                ("01353-645004", "+441353645004"),
                ("(01224) 446600", "+441224446600")]:
            with self.subTest(raw=raw):
                self.assertEqual(sms.normalise(raw), expected)

    def test_things_that_are_not_phone_numbers(self):
        for raw in ("2026", "IPC-A-610", "35000", "", None, "0", "123",
                    "0123456789012345"):
            with self.subTest(raw=raw):
                self.assertIsNone(sms.normalise(raw))

    def test_numbers_that_cost_the_caller_money_are_dropped(self):
        for raw in ("09011 234567", "0871 234 5678", "0844 800 1234"):
            with self.subTest(raw=raw):
                self.assertIsNone(sms.normalise(raw))

    def test_only_a_mobile_can_be_texted(self):
        self.assertTrue(sms.is_mobile("+447891169509"))
        self.assertFalse(sms.is_mobile("+441224327030"))
        self.assertFalse(sms.is_mobile(None))

    def test_his_own_number_is_never_harvested_as_a_contact(self):
        found = sms.numbers_in("call me on 07398 530978 or 01224 327030")
        self.assertEqual(found, ["+441224327030"])

    def test_the_direct_line_comes_before_the_switchboard(self):
        """Signatures put the direct line first, so document order is right."""
        signature = "DL: 01224 327 030\nSwitchboard: 0333 202 6500"
        self.assertEqual(sms.numbers_in(signature),
                         ["+441224327030", "+443332026500"])


class TestNotHarvestingTheQuotedTrail(unittest.TestCase):
    """The trail under a reply carries Harry's own number, and on a forwarded
    thread everyone else's. A number taken from there gets filed against the
    wrong person, and the first thing that does is text a stranger."""

    REPLY = ("Hi Harry,\n"
             "Thanks for your email, I look after the instrumentation desk.\n"
             "Cammy Keith\nDL: 01224 327 030\n"
             "\n"
             "From: harryrussell081203@gmail.com\n"
             "Harry Russell\n07398 530978\n")

    def test_only_the_signature_is_read(self):
        self.assertEqual(sms.numbers_from_signature(self.REPLY),
                         ["+441224327030"])

    def test_a_gmail_style_trail_is_cut(self):
        text = ("Give me a ring on 07891 169509\n"
                "On Wed, 5 Aug 2026, 11:04 Someone Else, <a@b.com> wrote:\n"
                "> my number is 01224 999999\n")
        self.assertEqual(sms.numbers_from_signature(text), ["+447891169509"])

    def test_an_outlook_style_trail_is_cut(self):
        text = ("Call 0131 555 0000\n-----Original Message-----\n"
                "phone 0141 555 1111\n")
        self.assertEqual(sms.numbers_from_signature(text), ["+441315550000"])


class TestWhoMayBeTexted(unittest.TestCase):
    def contact(self, **kw):
        base = {"number": "+447891169509", "name": "Annie Thompson",
                "replied": True, "wants_a_word": True}
        base.update(kw)
        return base

    def setUp(self):
        self.state = {"sms_sent": {}, "contact_numbers": {}}
        patches = [mock.patch.object(sms, "API_KEY", "test-key"),
                   mock.patch.object(sms, "OUTBOUND", True),
                   mock.patch.object(sms, "SMS_FROM", "+447398530978"),
                   mock.patch.object(sms, "in_texting_hours",
                                     return_value=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_a_consultant_who_wrote_back_may_be_texted(self):
        self.assertTrue(sms.may_text(self.state, self.contact())[0])

    def test_somebody_who_has_not_written_back_may_not(self):
        """This is the whole difference between a follow-up and a cold text."""
        allowed, reason = sms.may_text(self.state, self.contact(replied=False))
        self.assertFalse(allowed)
        self.assertIn("cold", reason)

    def test_a_landline_is_a_call_not_a_text(self):
        allowed, reason = sms.may_text(
            self.state, self.contact(number="+441224327030"))
        self.assertFalse(allowed)
        self.assertIn("landline", reason)

    def test_nobody_is_texted_twice(self):
        self.state["sms_sent"]["+447891169509"] = {"at": jm.now()}
        allowed, reason = sms.may_text(self.state, self.contact())
        self.assertFalse(allowed)
        self.assertIn("once", reason)

    def test_the_daily_cap_holds(self):
        for i in range(sms.DAILY_SMS_CAP):
            self.state["sms_sent"][f"+44789116950{i}"] = {"at": jm.now()}
        self.assertFalse(sms.may_text(self.state, self.contact())[0])

    def test_no_key_means_no_text(self):
        with mock.patch.object(sms, "API_KEY", ""):
            self.assertFalse(sms.may_text(self.state, self.contact())[0])

    def test_the_outbound_half_can_be_turned_off_on_its_own(self):
        with mock.patch.object(sms, "OUTBOUND", False):
            allowed, reason = sms.may_text(self.state, self.contact())
        self.assertFalse(allowed)
        self.assertIn("off", reason)


class TestWhenTextsMayGo(unittest.TestCase):
    def test_a_weekday_afternoon_is_fine(self):
        self.assertTrue(sms.in_texting_hours(uk(2026, 8, 5, 14)))

    def test_nothing_goes_at_eleven_at_night(self):
        self.assertFalse(sms.in_texting_hours(uk(2026, 8, 5, 23)))

    def test_nothing_goes_before_nine(self):
        self.assertFalse(sms.in_texting_hours(uk(2026, 8, 5, 7)))

    def test_nothing_goes_at_the_weekend(self):
        self.assertFalse(sms.in_texting_hours(uk(2026, 8, 8, 11)))
        self.assertFalse(sms.in_texting_hours(uk(2026, 8, 9, 11)))


class TestSendingOne(unittest.TestCase):
    def setUp(self):
        self.state = {"sms_sent": {}, "contact_numbers": {}}
        for p in [mock.patch.object(sms, "API_KEY", "test-key"),
                  mock.patch.object(sms, "OUTBOUND", True),
                  mock.patch.object(sms, "in_texting_hours", return_value=True)]:
            p.start()
            self.addCleanup(p.stop)

    def contact(self):
        return {"number": "+447891169509", "name": "Annie Thompson",
                "replied": True, "wants_a_word": True,
                "company": "Forces Employment Charity"}

    def test_it_goes_and_is_recorded(self):
        with mock.patch.object(sms, "send") as send:
            self.assertTrue(sms.text_contact(self.state, self.contact()))
        send.assert_called_once()
        self.assertIn("+447891169509", self.state["sms_sent"])

    def test_the_message_says_who_it_is_and_why(self):
        body = sms.follow_up_text(dict(self.contact(), wants_a_word=False))
        self.assertTrue(body.startswith("Hi Annie,"))
        self.assertIn("Harry Russell", body)
        self.assertIn("07398 530978", body)
        self.assertIn("replied to my email", body)
        self.assertLessEqual(len(body), 320)

    def test_it_still_reads_properly_with_no_name(self):
        body = sms.follow_up_text({"number": "+447891169509"})
        self.assertTrue(body.startswith("Hi, "))

    def test_test_mode_sends_it_to_harry_instead(self):
        with mock.patch.object(jm, "TEST_MODE", True), \
             mock.patch.object(sms, "send") as send:
            sms.text_contact(self.state, self.contact())
        to_number, body = send.call_args.args
        self.assertEqual(to_number, sms.SMS_FROM)
        self.assertIn("[TEST -> +447891169509]", body)

    def test_a_failure_is_not_recorded_as_a_send(self):
        """Otherwise the once-ever rule would eat the one attempt allowed."""
        with mock.patch.object(sms, "send", side_effect=RuntimeError("502")):
            self.assertFalse(sms.text_contact(self.state, self.contact()))
        self.assertEqual(self.state["sms_sent"], {})


class TestTellingHarry(unittest.TestCase):
    def test_the_interview_text_says_what_happened_and_what_was_done(self):
        body = sms.interview_alert({"company": "Hydro Group",
                                    "title": "Test Technician"})
        self.assertIn("INTERVIEW", body)
        self.assertIn("Hydro Group", body)
        self.assertIn("Test Technician", body)
        self.assertIn("availability reply", body)

    def test_it_goes_to_his_own_phone(self):
        with mock.patch.object(sms, "API_KEY", "test-key"), \
             mock.patch.object(sms, "send") as send:
            self.assertTrue(sms.alert_harry("test"))
        self.assertEqual(send.call_args.args[0], sms.SMS_FROM)

    def test_nothing_happens_without_a_key(self):
        with mock.patch.object(sms, "API_KEY", ""), \
             mock.patch.object(sms, "send") as send:
            self.assertFalse(sms.alert_harry("test"))
        send.assert_not_called()

    def test_a_failed_alert_never_takes_the_run_down(self):
        with mock.patch.object(sms, "API_KEY", "test-key"), \
             mock.patch.object(sms, "send", side_effect=RuntimeError("boom")):
            self.assertFalse(sms.alert_harry("test"))


class TestTheKeyIsNeverInTheRepository(unittest.TestCase):
    """This repository is public."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # What an httpSMS key looks like, without one being written down here -
    # a test that guards against a committed secret must not commit one.
    KEY_SHAPED = re.compile(r"\buk_[A-Za-z0-9_-]{30,}")

    def read(self, name):
        with open(os.path.join(self.ROOT, name)) as f:
            return f.read()

    def test_the_key_comes_from_the_environment_only(self):
        self.assertIn('env_str("HTTPSMS_API_KEY")', self.read("sms.py"))

    def test_nothing_key_shaped_is_committed(self):
        for name in ("sms.py", "job_machine.py", "README.md",
                     ".github/workflows/run.yml", ".github/workflows/reply.yml"):
            with self.subTest(name=name):
                self.assertIsNone(self.KEY_SHAPED.search(self.read(name)))


class TestTheCallList(unittest.TestCase):
    def test_landlines_come_first_because_they_are_the_ones_to_ring(self):
        state = {"contact_numbers": {
            "tmm": {"name": "Cammy Keith", "numbers": ["+441224327030"]},
            "fec": {"name": "Annie Thompson", "numbers": ["+447891169509"]}}}
        rows = sms.call_list(state)
        self.assertEqual([r["name"] for r in rows],
                         ["Cammy Keith", "Annie Thompson"])
        self.assertFalse(rows[0]["mobile"])

    def test_a_number_is_filed_against_whoever_it_belongs_to(self):
        state = {}
        sms.remember(state, "tmm", "+441224327030", "Cammy Keith", "signature")
        sms.remember(state, "tmm", "+443332026500")
        self.assertEqual(state["contact_numbers"]["tmm"]["numbers"],
                         ["+441224327030", "+443332026500"])
        self.assertEqual(state["contact_numbers"]["tmm"]["name"], "Cammy Keith")

    def test_the_same_number_is_not_filed_twice(self):
        state = {}
        for _ in range(3):
            sms.remember(state, "tmm", "+441224327030", "Cammy Keith")
        self.assertEqual(len(state["contact_numbers"]["tmm"]["numbers"]), 1)


class TestTheReplyWatcherWiring(unittest.TestCase):
    def test_a_number_in_a_reply_is_filed_and_no_text_is_sent_to_them(self):
        state = {"jobs": {}}
        job = {"company": "TMM Recruitment", "title": "Technician",
               "contact_email": "ckeith@tmmrecruitment.com",
               "contact_name": "Cammy"}
        reply = {"text": "Hi Harry, I look after that desk.\nDL: 01224 327 030"}
        with mock.patch.object(sms, "send") as send:
            jm.harvest_contact_number(state, job, reply, "question")
        self.assertIn("+441224327030",
                      state["contact_numbers"]["tmm"]["numbers"])
        send.assert_not_called()

    def test_an_interview_invitation_reaches_his_phone(self):
        state = {"jobs": {}}
        job = {"company": "Hydro Group", "title": "Test Technician"}
        with mock.patch.object(sms, "API_KEY", "test-key"), \
             mock.patch.object(sms, "send") as send:
            jm.harvest_contact_number(state, job, {"text": "can you come in?"},
                                      "interview_invite")
        send.assert_called_once()
        self.assertIn("INTERVIEW", send.call_args.args[1])

    def test_a_broken_sms_stage_never_takes_the_reply_watcher_down(self):
        with mock.patch.object(sms, "numbers_from_signature",
                               side_effect=RuntimeError("boom")):
            jm.harvest_contact_number({}, {"company": "X"}, {"text": ""}, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSpottingSomebodyWhoWantsToTalk(unittest.TestCase):
    """The strongest signal in this inbox, and the easiest to miss - it
    arrives looking like every other email. Every phrase below came off a real
    reply Harry received."""

    def test_the_real_ones(self):
        for text in [
                "I have tried to reach you by telephone today, but I was "
                "unsuccessful and I have left you a voicemail.",
                "can you please give me a call when you are able to please",
                "Let me know when you are free for a quick chat",
                "What is the best time to call you?",
                "Happy to have a chat this week",
                "Give me a ring when you get this",
                "I missed you earlier, try me back"]:
            with self.subTest(text=text[:40]):
                self.assertTrue(sms.wants_a_word(text))

    def test_an_ordinary_reply_is_not_mistaken_for_one(self):
        for text in [
                "Thanks for your email, I have passed it to my colleague.",
                "Unfortunately this position requires an 18th edition.",
                "We have received your application and will be in touch.",
                "Please apply through our recruitment portal.",
                "Thank you for your interest in working with us."]:
            with self.subTest(text=text[:40]):
                self.assertFalse(sms.wants_a_word(text))

    def test_it_does_not_read_harrys_own_words_in_the_trail(self):
        """His own emails say 'free to call any time'. Matching on that would
        have him texting people who never asked for anything."""
        text = ("Thanks, noted.\n"
                "On Wed, 5 Aug 2026, Harry Russell wrote:\n"
                "> give me a call any time, I am free all day\n")
        self.assertFalse(sms.wants_a_word(text))


class TestTheOutboundTriggerIsNarrow(unittest.TestCase):
    def setUp(self):
        self.state = {"sms_sent": {}, "contact_numbers": {}}
        for p in [mock.patch.object(sms, "API_KEY", "test-key"),
                  mock.patch.object(sms, "OUTBOUND", True),
                  mock.patch.object(sms, "in_texting_hours", return_value=True)]:
            p.start()
            self.addCleanup(p.stop)

    def contact(self, **kw):
        base = {"number": "+447891169509", "name": "Annie Thompson",
                "replied": True, "wants_a_word": True}
        base.update(kw)
        return base

    def test_somebody_who_asked_to_speak_gets_a_text(self):
        self.assertTrue(sms.may_text(self.state, self.contact())[0])

    def test_somebody_who_merely_replied_does_not(self):
        """A reply is not an invitation. Answering a request to talk is."""
        allowed, reason = sms.may_text(self.state,
                                       self.contact(wants_a_word=False))
        self.assertFalse(allowed)
        self.assertIn("uninvited", reason)

    def test_that_rule_can_be_relaxed_deliberately(self):
        with mock.patch.object(sms, "REPLY_TO_A_REQUEST_ONLY", False):
            self.assertTrue(sms.may_text(
                self.state, self.contact(wants_a_word=False))[0])

    def test_the_text_answers_the_request_it_is_replying_to(self):
        body = sms.follow_up_text(self.contact())
        self.assertIn("you asked for a call", body)
        self.assertIn("Sorry if you have tried me", body)
        self.assertIn(jm.PHONE, body)

    def test_only_the_people_who_asked_are_queued(self):
        state = {"jobs": {
            "a": {"company": "TMM", "wants_a_word": True,
                  "contact_mobile": "+447700900001"},
            "b": {"company": "Other", "contact_mobile": "+447700900002"},
            "c": {"company": "Done", "wants_a_word": True,
                  "contact_mobile": "+447700900003",
                  "sms_sent_at": "2026-08-05T10:00:00+00:00"},
            "d": {"company": "Landline only", "wants_a_word": True}}}
        pending = sms.pending_conversations(state)
        self.assertEqual([c["company"] for c in pending], ["TMM"])

    def test_a_run_texts_them_once_and_marks_the_job(self):
        job = {"company": "TMM", "wants_a_word": True,
               "contact_mobile": "+447700900001", "contact_name": "Cammy"}
        state = {"jobs": {"a": job}, "sms_sent": {}}
        with mock.patch.object(sms, "send") as send, \
             mock.patch.object(jm, "save"):
            self.assertEqual(sms.run_followups(state), 1)
        send.assert_called_once()
        self.assertIn("sms_sent_at", job)
        with mock.patch.object(sms, "send") as send, \
             mock.patch.object(jm, "save"):
            self.assertEqual(sms.run_followups(state), 0)
        send.assert_not_called()

    def test_an_out_of_hours_reply_waits_rather_than_being_lost(self):
        """It stays queued, so the next run in working hours picks it up."""
        job = {"company": "TMM", "wants_a_word": True,
               "contact_mobile": "+447700900001"}
        state = {"jobs": {"a": job}, "sms_sent": {}}
        with mock.patch.object(sms, "in_texting_hours", return_value=False), \
             mock.patch.object(sms, "send") as send, \
             mock.patch.object(jm, "save"):
            self.assertEqual(sms.run_followups(state), 0)
        send.assert_not_called()
        self.assertNotIn("sms_sent_at", job)
        self.assertEqual(len(sms.pending_conversations(state)), 1)


class TestAlertingHimAtSensibleHours(unittest.TestCase):
    def test_an_interview_goes_whenever_it_lands(self):
        """A small firm sends these on a Sunday evening, and he would rather
        know than be protected from knowing."""
        with mock.patch.object(sms, "API_KEY", "test-key"), \
             mock.patch.object(sms, "waking_hours", return_value=False), \
             mock.patch.object(sms, "send") as send:
            self.assertTrue(sms.alert_harry("INTERVIEW: x", urgent=True))
        send.assert_called_once()

    def test_everything_else_waits_for_the_morning(self):
        with mock.patch.object(sms, "API_KEY", "test-key"), \
             mock.patch.object(sms, "waking_hours", return_value=False), \
             mock.patch.object(sms, "send") as send:
            self.assertFalse(sms.alert_harry("someone wants a call"))
        send.assert_not_called()

    def test_the_call_back_alert_says_who_and_what_number(self):
        body = sms.call_back_alert("Annie Thompson", "+447891169509",
                                   "Forces Employment Charity")
        self.assertIn("Annie Thompson", body)
        self.assertIn("+447891169509", body)
        self.assertIn("ring them", body)

    def test_a_request_to_speak_is_recorded_and_he_is_told(self):
        state = {"jobs": {}}
        job = {"company": "Forces Employment Charity", "title": "n/a",
               "contact_email": "annie.thompson@forcesemployment.org.uk",
               "contact_name": "Annie"}
        reply = {"text": "I have tried to reach you by telephone today.\n"
                         "Annie Thompson\nT: 07891169509"}
        with mock.patch.object(sms, "API_KEY", "test-key"), \
             mock.patch.object(sms, "waking_hours", return_value=True), \
             mock.patch.object(sms, "send") as send:
            jm.harvest_contact_number(state, job, reply, "question")
        self.assertTrue(job["wants_a_word"])
        self.assertEqual(job["contact_mobile"], "+447891169509")
        send.assert_called_once()
        self.assertIn("wants a call", send.call_args.args[1])
