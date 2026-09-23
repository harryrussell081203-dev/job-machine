"""The end-of-day email to the user themselves.

It replaces the personal machine's nightly summary, and it goes to one
place only: the user's own inbox. These tests hold that, and hold the two
ways a digest becomes noise - more than one a day, and one about nothing.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_sending import Base  # noqa: E402

EVENING = int(datetime(2026, 9, 24, 16, 45, tzinfo=timezone.utc).timestamp())
MORNING = int(datetime(2026, 9, 24, 9, 45, tzinfo=timezone.utc).timestamp())


class DigestCase(Base):
    def setUp(self):
        super().setUp()
        import importlib
        self.digest = importlib.import_module("app.digest")
        self.connect_mail()
        self.db.mark_mail_verified(self.uid)
        self.db.save_send_settings(self.uid, digest=1)

    def deliver(self, company="Acme Ltd", email="jobs@acme.co.uk", at=None):
        did = self.db.add_draft(self.uid, job_title="Engineer", company=company,
                                to_email=email, subject="s", body="b")
        self.db.record_delivered(self.uid, draft_id=did, to_email=email,
                                 company=company)
        with self.db.connect() as c:
            c.execute("UPDATE sent_log SET sent_at = ? WHERE draft_id = ?",
                      ((at or EVENING) - 3600, did))
        return did

    def run_it(self, now=EVENING):
        return self.digest.send_for_user(self.uid, now=now,
                                         sender=self.fake_send)


class TestTheDigest(DigestCase):
    def test_it_goes_to_the_user_and_nobody_else(self):
        self.deliver()
        self.assertTrue(self.run_it())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["to_email"], "harry@gmail.com")
        self.assertIn("Acme Ltd", self.sent[0]["body"])

    def test_once_a_day(self):
        self.deliver()
        self.run_it()
        self.run_it(now=EVENING + 1800)
        self.assertEqual(len(self.sent), 1)

    def test_not_before_the_end_of_the_day(self):
        self.deliver(at=MORNING)
        self.assertFalse(self.run_it(now=MORNING))
        self.assertEqual(self.sent, [])

    def test_nothing_to_say_sends_nothing(self):
        self.assertFalse(self.run_it())
        self.assertEqual(self.sent, [])

    def test_a_reply_leads(self):
        did = self.deliver()
        self.db.mark_reply_seen(self.uid, did)
        with self.db.connect() as c:
            c.execute("UPDATE drafts SET reply_seen_at = ?", (EVENING - 60,))
        self.run_it()
        self.assertTrue(self.sent[0]["subject"].startswith(
            "Recruited today: 1 reply"))

    def test_off_unless_turned_on(self):
        self.db.save_send_settings(self.uid, digest=0)
        self.deliver()
        self.assertFalse(self.run_it())


if __name__ == "__main__":
    unittest.main()
