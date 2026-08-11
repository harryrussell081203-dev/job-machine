"""
Tests for the whole-inbox watcher.

Offline. No mailbox is opened, no text is sent, no model is called.

The failure modes this guards against are not crashes:

  A TEXT ABOUT SOMETHING THAT WAS NEVER SAID. The model is reading real mail
  from real people and its output goes straight to Harry's phone, so an action
  attributed to a message that does not exist, or to a sender who did not write
  it, has to be dropped rather than shortened.

  THE SAME EMAIL, EVERY DAY, FOREVER. What was here before this module recorded
  nothing, so the same message was re-read and re-judged every morning and could
  never be marked as dealt with. A daily text that nags about something done is
  a daily text he stops opening.

  THE SAME EMAIL, TWICE, IN ONE HOUR. He gets one text about a message, ever.
  The mark that says so is written by a runner and merged into main by
  tools/merge_state.py, which is where four separate bugs in this project have
  already silently deleted exactly that kind of mark.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inbox_watch  # noqa: E402
import job_machine as jm  # noqa: E402
import sms  # noqa: E402


def message(key="<1@x>", who="Cammy Keith", address="ckeith@tmm.com",
            subject="Re: your CV", body="Can you send your availability?",
            at=None):
    return {"key": key, "who": who, "address": address, "subject": subject,
            "body": body, "at": at or jm.now()}


class TestWhatItRefusesToRead(unittest.TestCase):
    def test_automated_mail_is_dropped_before_anything_reads_it(self):
        for sender in ("no-reply@x.com", "notifications@y.com",
                       "info@jobs.totaljobsmail.com", "mailer-daemon@z.com",
                       "newsletter@a.com", "alerts@indeed.com"):
            with self.subTest(sender=sender):
                self.assertTrue(inbox_watch.NOT_A_PERSON.search(sender))

    def test_a_real_consultant_is_not_dropped(self):
        for sender in ("Cammy Keith <CKeith@tmmrecruitment.com>",
                       "annie.thompson@forcesemployment.org.uk",
                       "j.smith@enermech.com"):
            with self.subTest(sender=sender):
                self.assertIsNone(inbox_watch.NOT_A_PERSON.search(sender))

    def test_it_spots_somebody_waiting_on_him(self):
        self.assertTrue(inbox_watch.asks_something(
            "Re: your CV", "Can you send your availability for Thursday?"))
        self.assertTrue(inbox_watch.asks_something(
            "Registering", "Please send a word copy to Will."))

    def test_it_does_not_treat_a_statement_as_an_ask(self):
        self.assertFalse(inbox_watch.asks_something(
            "Thanks", "Thanks for your email. We have received it."))


class TestGrounding(unittest.TestCase):
    """Everything the model says ends up on his phone, so everything it says
    has to be traceable to a message that really arrived."""

    MESSAGES = [message(), message(key="<2@x>", who="Graham Brown",
                                   address="g@frs.co.uk",
                                   subject="Re: registering",
                                   body="Please send a word copy to Will.")]

    def triage(self, answer):
        with mock.patch.object(jm, "GEMINI_API_KEY", "k"), \
             mock.patch.object(jm, "gemini_exhausted", return_value=False), \
             mock.patch.object(jm, "gemini_json", return_value=answer):
            return inbox_watch.triage(self.MESSAGES)

    def test_an_action_about_a_message_that_does_not_exist_is_dropped(self):
        read = self.triage({"items": [
            {"n": 7, "who": "Cammy Keith", "category": "question",
             "do": "Send Thursday availability"}]})
        self.assertEqual(read, {})

    def test_an_action_attributed_to_the_wrong_sender_is_dropped(self):
        """It answered about message 1, which is from Cammy, and named
        somebody else. One of the two is wrong and neither is textable."""
        read = self.triage({"items": [
            {"n": 1, "who": "Someone Who Never Wrote", "category": "call",
             "do": "Ring them back"}]})
        self.assertEqual(read, {})

    def test_a_category_it_invented_is_dropped(self):
        read = self.triage({"items": [
            {"n": 1, "who": "Cammy Keith", "category": "extremely urgent",
             "do": "Ring them back"}]})
        self.assertEqual(read, {})

    def test_a_real_answer_gets_through(self):
        read = self.triage({"items": [
            {"n": 2, "who": "Graham Brown", "category": "document",
             "do": "Send Graham a Word copy of the CV"}]})
        self.assertEqual(read["<2@x>"]["category"], "document")
        self.assertIn("Word copy", read["<2@x>"]["do"])

    def test_an_empty_answer_produces_nothing(self):
        self.assertEqual(self.triage({"items": []}), {})

    def test_a_refusal_to_answer_produces_nothing(self):
        self.assertEqual(self.triage(None), {})

    def test_without_a_key_it_still_says_who_is_waiting(self):
        """No key is not a reason to drop real mail on the floor. The regex
        already decided somebody is waiting; say so plainly."""
        with mock.patch.object(jm, "GEMINI_API_KEY", ""):
            read = inbox_watch.triage(self.MESSAGES)
        self.assertEqual(len(read), 2)
        self.assertEqual(read["<1@x>"]["category"], "question")

    def test_a_spent_budget_behaves_like_no_key(self):
        with mock.patch.object(jm, "GEMINI_API_KEY", "k"), \
             mock.patch.object(jm, "gemini_exhausted", return_value=True), \
             mock.patch.object(jm, "gemini_json") as called:
            read = inbox_watch.triage(self.MESSAGES)
        called.assert_not_called()
        self.assertEqual(len(read), 2)


class TestTheRegister(unittest.TestCase):
    def scan(self, state, messages, send=False):
        with mock.patch.object(jm, "GEMINI_API_KEY", ""):
            return inbox_watch.scan(state, messages=messages, send=send)

    def test_a_message_is_only_ever_read_once(self):
        state = {}
        self.assertEqual(len(self.scan(state, [message()])), 1)
        with mock.patch.object(inbox_watch, "triage") as triage:
            self.assertEqual(self.scan(state, [message()]), [])
        triage.assert_not_called()

    def test_mail_nobody_is_waiting_on_is_recorded_but_never_listed(self):
        """Recorded so it is not looked at again; unlisted because a to-do
        list with nothing to do on it is not read."""
        state = {}
        self.scan(state, [message(body="Thanks, received.")])
        self.assertEqual(len(state["inbox"]), 1)
        self.assertEqual(inbox_watch.outstanding(state), [])

    def test_marking_it_done_takes_it_off_the_list(self):
        state = {}
        self.scan(state, [message()])
        key = next(iter(state["inbox"]))
        self.assertTrue(inbox_watch.mark_done(state, key))
        self.assertEqual(inbox_watch.outstanding(state), [])

    def test_it_stops_nagging_after_a_fortnight(self):
        """Nothing here can tell whether he answered - he answers from his own
        inbox and the machine never sees it."""
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        state = {}
        self.scan(state, [message(at=old)])
        self.assertEqual(inbox_watch.outstanding(state), [])

    def test_the_worst_thing_is_first(self):
        state = {"inbox": {
            "a": {"who": "A", "category": "information", "do": "read it",
                  "at": jm.now()},
            "b": {"who": "B", "category": "interview", "do": "reply today",
                  "at": jm.now()},
            "c": {"who": "C", "category": "question", "do": "answer",
                  "at": jm.now()}}}
        order = [e["who"] for _, e in
                 inbox_watch.outstanding(state, include_information=True)]
        self.assertEqual(order[0], "B")
        self.assertEqual(order[-1], "A")

    def test_a_contact_the_reply_watcher_covers_is_flagged(self):
        """Not ignored - flagged. The reply watcher already texts about that
        one, and the evening digest already names the company and the role,
        so a second line about the same email is noise."""
        state = {"jobs": {"j": {"contact_email": "CKeith@tmm.com"}}}
        self.scan(state, [message(address="ckeith@tmm.com")])
        entry = next(iter(state["inbox"].values()))
        self.assertTrue(entry["tracked"])

    def test_long_dead_entries_are_forgotten(self):
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        state = {"inbox": {"old": {"who": "A", "at": old, "seen_at": old}}}
        inbox_watch.prune(state)
        self.assertEqual(state["inbox"], {})

    def test_an_entry_inside_the_window_survives_the_prune(self):
        """It has to outlive the outstanding window by a clear margin,
        otherwise it drops off the list one day and comes back as brand new
        the next, because it is still inside the IMAP lookback."""
        recent = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        state = {"inbox": {"k": {"who": "A", "at": recent, "seen_at": recent}}}
        inbox_watch.prune(state)
        self.assertIn("k", state["inbox"])


class TestWhatGoesOnHisPhone(unittest.TestCase):
    def entry(self, **kw):
        base = {"who": "Cammy Keith", "category": "interview",
                "do": "Reply with Thursday availability", "tracked": False}
        base.update(kw)
        return base

    def texts(self, state, added, send=True):
        with mock.patch.object(sms, "alert_harry", return_value=True) as sent:
            inbox_watch.shout(state, added, send=send)
        return [c.args[0] for c in sent.call_args_list]

    def test_an_interview_from_a_stranger_goes_straight_to_the_phone(self):
        state = {"inbox": {"k": self.entry()}}
        texts = self.texts(state, [("k", state["inbox"]["k"])])
        self.assertEqual(len(texts), 1)
        self.assertIn("Cammy Keith", texts[0])
        self.assertIn("Thursday", texts[0])

    def test_a_question_waits_for_the_evening(self):
        state = {"inbox": {"k": self.entry(category="question")}}
        self.assertEqual(self.texts(state, [("k", state["inbox"]["k"])]), [])

    def test_a_contact_the_reply_watcher_covers_is_not_texted_twice(self):
        state = {"inbox": {"k": self.entry(tracked=True)}}
        self.assertEqual(self.texts(state, [("k", state["inbox"]["k"])]), [])

    def test_he_is_told_once_and_the_mark_is_written(self):
        state = {"inbox": {"k": self.entry()}}
        self.texts(state, [("k", state["inbox"]["k"])])
        self.assertTrue(state["inbox"]["k"]["texted_at"])
        self.assertEqual(self.texts(state, [("k", state["inbox"]["k"])]), [])

    def test_a_dry_run_writes_no_mark(self):
        state = {"inbox": {"k": self.entry()}}
        self.assertEqual(self.texts(state, [("k", state["inbox"]["k"])],
                                    send=False), [])
        self.assertNotIn("texted_at", state["inbox"]["k"])

    def test_one_bad_hour_cannot_become_an_alarm_clock(self):
        state = {"inbox": {str(i): self.entry() for i in range(6)}}
        texts = self.texts(state, [(k, v) for k, v in state["inbox"].items()])
        self.assertLessEqual(len(texts), inbox_watch.MAX_TEXTS)

    def test_the_only_number_it_can_text_is_his_own(self):
        """This is what makes reading his whole inbox safe: there is no
        recipient argument to get wrong."""
        import inspect
        self.assertNotIn("to", inspect.signature(sms.alert_harry).parameters)

    def test_it_never_writes_back_to_anybody(self):
        """The reply watcher answers a confirmed interview invitation on a job
        the machine itself applied for, and that is a narrow, chosen
        exception. Mail from a stranger is Harry's to answer."""
        with open(inbox_watch.__file__) as f:
            source = f.read()
        for outbound in ("send_email", "smtplib", "sms.send("):
            with self.subTest(outbound=outbound):
                self.assertNotIn(outbound, source)


class TestReadingTheHeaders(unittest.TestCase):
    def test_a_message_with_no_id_still_gets_a_stable_key(self):
        """Without this the handful of mailers that omit Message-ID would be
        re-classified, re-texted and re-listed on every single run."""
        msg = {"Message-ID": "", "Date": "Tue, 11 Aug 2026 09:00:00 +0100"}
        first = inbox_watch.key_for(msg, "a@b.com", "Hello")
        second = inbox_watch.key_for(msg, "a@b.com", "Hello")
        self.assertEqual(first, second)
        self.assertNotEqual(first, inbox_watch.key_for(msg, "a@b.com", "Other"))

    def test_the_address_is_pulled_out_of_a_display_name(self):
        self.assertEqual(
            inbox_watch.address_of('"Keith, Cammy" <CKeith@TMM.com>'),
            "ckeith@tmm.com")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestWhatTheFirstLiveRunFound(unittest.TestCase):
    """Two defects the tests did not catch and thirty-two real emails did.

    Worth keeping as their own class, because both are the same mistake in
    different clothes: assuming the state file's shape rather than checking it,
    and assuming a cap on work is a cap on cost."""

    def test_a_firm_written_to_through_the_agency_register_counts_as_tracked(self):
        """It reported nought of thirty-two as tracked, in an inbox holding
        Cammach answering the offshore-tickets letter. The agency letters, the
        speculative letters and the charities each live in a register of their
        own, and none of them is a job."""
        state = {"jobs": {},
                 "agency_registered": {"cammach": {
                     "email": "recruitment@wearecammach.com"}}}
        self.assertIn("recruitment@wearecammach.com",
                      inbox_watch.tracked_addresses(state))

    def test_a_consultant_replying_from_her_own_address_is_the_same_firm(self):
        """The machine wrote to recruitment@wearecammach.com; Louise Young
        answered from l.young@wearecammach.com. A consultant almost never
        replies from the address on the contact-us page."""
        state = {"agency_registered": {"cammach": {
            "email": "recruitment@wearecammach.com"}}}
        self.assertTrue(inbox_watch.is_tracked(
            "l.young@wearecammach.com",
            inbox_watch.tracked_addresses(state),
            inbox_watch.tracked_domains(state)))

    def test_two_strangers_on_gmail_are_not_the_same_firm(self):
        state = {"jobs": {"a": {"contact_email": "someone@gmail.com"}}}
        self.assertFalse(inbox_watch.is_tracked(
            "anybody.else@gmail.com",
            inbox_watch.tracked_addresses(state),
            inbox_watch.tracked_domains(state)))

    def test_a_register_that_stores_a_reason_instead_of_a_record(self):
        """spec_done stores a string. Reading .email off it would take the
        whole watcher down on a live state file."""
        state = {"spec_done": {"acteon": "no domain or MX"}}
        self.assertEqual(inbox_watch.tracked_addresses(state), set())

    def test_the_cap_paces_the_work_and_never_discards_it(self):
        """The first version recorded every fresh message and triaged the first
        ten, so on a busy morning the eleventh was filed as seen, never read,
        and never looked at again."""
        messages = [message(key=f"<{i}@x>") for i in range(20)]
        state = {}
        with mock.patch.object(jm, "GEMINI_API_KEY", ""):
            inbox_watch.scan(state, messages=messages)
        self.assertEqual(len(state["inbox"]), inbox_watch.MAX_TRIAGE)
        with mock.patch.object(jm, "GEMINI_API_KEY", ""):
            inbox_watch.scan(state, messages=messages)
        self.assertEqual(len(state["inbox"]), inbox_watch.MAX_TRIAGE * 2)

    def test_mail_nobody_is_waiting_on_still_gets_a_look(self):
        """'RE: Fire & Security Engineer - available now' from a real
        consultant carries no question mark and is still worth reading."""
        state = {}
        seen = []
        with mock.patch.object(inbox_watch, "triage",
                               side_effect=lambda b: seen.extend(b) or {}):
            inbox_watch.scan(state, messages=[message(body="Thanks, noted.")])
        self.assertEqual(len(seen), 1)

    def test_the_asks_are_read_first_when_the_budget_is_short(self):
        state = {}
        seen = []
        messages = ([message(key=f"<n{i}@x>", body="Thanks, noted.")
                     for i in range(inbox_watch.MAX_TRIAGE)]
                    + [message(key="<ask@x>", body="Can you send your CV?")])
        with mock.patch.object(inbox_watch, "triage",
                               side_effect=lambda b: seen.extend(b) or {}):
            inbox_watch.scan(state, messages=messages)
        self.assertEqual(seen[0]["key"], "<ask@x>")

    def test_an_out_of_office_is_not_a_thing_to_do(self):
        for subject in ("Automatic reply: Could I come in and see you?",
                        "Out of office", "Undeliverable: your message"):
            with self.subTest(subject=subject):
                self.assertTrue(inbox_watch.NOT_WORTH_READING.search(subject))

    def test_a_real_reply_is_not_mistaken_for_an_auto_reply(self):
        self.assertIsNone(inbox_watch.NOT_WORTH_READING.search(
            "RE: Offshore tickets - would you ever sponsor them?"))

    def test_marketing_from_a_bulk_subdomain_is_dropped(self):
        for sender in ("specialoffers@email.currys.co.uk",
                       "hello@news.example.com"):
            with self.subTest(sender=sender):
                self.assertTrue(inbox_watch.NOT_A_PERSON.search(sender))

    def test_a_consultant_on_a_bare_company_domain_is_not(self):
        for sender in ("l.young@wearecammach.com", "skazmi@hrcrecruitment.co.uk",
                       "annie.thompson@forcesemployment.org.uk"):
            with self.subTest(sender=sender):
                self.assertIsNone(inbox_watch.NOT_A_PERSON.search(sender))

    def test_without_a_key_only_the_asks_get_a_line(self):
        """The batch carries mail nobody is waiting on now. Labelling that a
        question would fill the list with nothing."""
        with mock.patch.object(jm, "GEMINI_API_KEY", ""):
            read = inbox_watch.triage([message(body="Thanks, received."),
                                       message(key="<2@x>",
                                               body="Can you send your CV?")])
        self.assertEqual(list(read), ["<2@x>"])
