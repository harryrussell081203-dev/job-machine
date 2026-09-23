"""One nudge, once - and never to anybody who has already answered.

WHY THIS FILE EXISTS.

The personal machine this replaced sent up to three follow-ups and a fourth
letter to somebody else at the company. On 22 and 23 September that sequence
reached agencies that had already replied - Verelogic answered on 10 September
and was nudged twice more - and people noticed. These tests hold the line on
what is left: one short nudge, only on a letter this machine sent, only
while nobody at the organisation has been in touch, and only for somebody who
turned it on.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_sending import Base  # noqa: E402

DAY = 86400


class FollowupCase(Base):
    def setUp(self):
        super().setUp()
        self.followups = sys.modules.get("app.followups")
        if self.followups is None:
            import importlib
            self.followups = importlib.import_module("app.followups")
        self.connect_mail()
        self.db.mark_mail_verified(self.uid)
        self.db.save_send_settings(self.uid, auto_send=1, follow_up=1)
        self.db.save_profile(self.uid, {"name": "Harry Russell",
                                        "phone": "07700 900123"})
        self.replied = set()

    def finder(self, **kw):
        return {a for a in kw["addresses"] if a in self.replied}

    def letter(self, company="Acme Ltd", email="jobs@acme.co.uk",
               days_ago=6, delivered=True):
        did = self.db.add_draft(self.uid, job_title="Field Engineer",
                                company=company, to_email=email,
                                to_name="Sam", subject="Field Engineer",
                                body="b")
        if delivered:
            self.db.record_delivered(self.uid, draft_id=did, to_email=email,
                                     company=company)
        else:
            self.db.mark_draft(self.uid, did, "sent")
        when = self.db.now() - days_ago * DAY
        with self.db.connect() as c:
            c.execute("UPDATE drafts SET sent_at = ? WHERE id = ?", (when, did))
            c.execute("UPDATE sent_log SET sent_at = ? WHERE draft_id = ?",
                      (when, did))
        return did

    def run_it(self):
        return self.followups.send_for_user(self.uid, sender=self.fake_send,
                                            finder=self.finder)


class TestTheNudge(FollowupCase):
    def test_a_quiet_letter_gets_one_nudge_in_the_same_thread(self):
        self.letter()
        report = self.run_it()
        self.assertEqual(report.sent, 1)
        sent = self.sent[0]
        self.assertEqual(sent["to_email"], "jobs@acme.co.uk")
        self.assertEqual(sent["subject"], "Re: Field Engineer")
        self.assertIn("Hi Sam,", sent["body"])
        self.assertIn("Field Engineer role", sent["body"])
        self.assertIsNone(sent["attachment"])

    def test_never_a_second_one(self):
        self.letter()
        self.run_it()
        self.run_it()
        self.assertEqual(len(self.sent), 1)

    def test_it_counts_against_the_daily_cap_but_not_as_a_letter(self):
        """The mailbox sees every email; /numbers counts applications."""
        self.letter()
        before = self.db.public_stats()["letters"]
        self.run_it()
        self.assertEqual(self.db.public_stats()["letters"], before)
        self.assertEqual(self.db.sent_today(self.uid), 1)


class TestWhenItStaysQuiet(FollowupCase):
    def test_anybody_at_the_organisation_replying_stops_it(self):
        self.letter()
        self.replied.add("jobs@acme.co.uk")
        report = self.run_it()
        self.assertEqual(self.sent, [])
        self.assertEqual(report.answered, 1)

    def test_an_inbox_that_cannot_be_asked_sends_nothing(self):
        """Unreachable must not look like a quiet week."""
        self.letter()

        def broken(**kw):
            raise self.delivery.DeliveryError("imap down")
        report = self.followups.send_for_user(
            self.uid, sender=self.fake_send, finder=broken)
        self.assertEqual(self.sent, [])
        self.assertIn("could not check", report.reason)

    def test_too_soon(self):
        self.letter(days_ago=2)
        self.run_it()
        self.assertEqual(self.sent, [])

    def test_too_late(self):
        self.letter(days_ago=30)
        self.run_it()
        self.assertEqual(self.sent, [])

    def test_off_unless_turned_on(self):
        self.db.save_send_settings(self.uid, follow_up=0)
        self.letter()
        self.run_it()
        self.assertEqual(self.sent, [])

    def test_a_letter_sent_by_hand_is_theirs_to_chase(self):
        self.letter(delivered=False)
        self.run_it()
        self.assertEqual(self.sent, [])

    def test_an_answer_already_recorded_stops_it(self):
        did = self.letter()
        self.db.mark_reply_seen(self.uid, did)
        self.run_it()
        self.assertEqual(self.sent, [])

    def test_a_blocked_domain_is_never_nudged(self):
        self.letter(company="Some Name", email="hr@hydrogroup-uk.com")
        self.db.record_mail_contacted(self.uid, "hydrogroup-uk.com",
                                      reason="blocked: employer")
        self.run_it()
        self.assertEqual(self.sent, [])


class TestTheSettingsForm(FollowupCase):
    def test_the_tick_box_turns_it_on_and_off(self):
        from app.tests.test_app import AppTestCase
        case = AppTestCase()
        case.client, case.main = self.client, self.main
        case.sign_in("harry@example.com")
        self.client.post("/setup/sending",
                         data={"auto_send": "1", "follow_up": "1"})
        self.assertEqual(self.db.get_send_settings(self.uid)["follow_up"], 1)
        self.client.post("/setup/sending", data={"auto_send": "1"})
        self.assertEqual(self.db.get_send_settings(self.uid)["follow_up"], 0)


if __name__ == "__main__":
    unittest.main()
