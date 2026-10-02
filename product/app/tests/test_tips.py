"""The weekly tips: nobody is emailed who did not ask, and confirm.

  1. SIGNING UP SENDS ONE EMAIL, the confirmation, and nothing else until
     its link is clicked. An unconfirmed address is forgotten after a week.
  2. CONFIRMING sends the first tip; after that, one a week, 8 in all, then
     it stops.
  3. EVERY TIP carries a one-click unsubscribe link and the List-Unsubscribe
     headers, and unsubscribing deletes the address.
  4. THE FORM does not reveal who is subscribed, and ignores bots.
  5. EVERY FIGURE in the tips is one the site already publishes.
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import AppTestCase  # noqa: E402


class Case(AppTestCase):
    def setUp(self):
        super().setUp()
        from app import tips
        self.tips = tips
        self.outbox = []

    def send(self, to, subject, body, headers=None):
        self.outbox.append({"to": to, "subject": subject, "body": body,
                            "headers": headers or {}})

    def token(self, email="ivy@example.com"):
        with self.main.db.connect() as c:
            return c.execute("SELECT token FROM tip_subscribers WHERE email = ?",
                             (email,)).fetchone()["token"]


class ConsentFirst(Case):
    def test_signing_up_sends_only_the_confirmation(self):
        self.tips.subscribe("Ivy@Example.com", "/find", send=self.send)
        self.assertEqual(len(self.outbox), 1)
        self.assertIn("/tips/confirm/", self.outbox[0]["body"])
        self.assertEqual(self.tips.send_due(send=self.send)["sent"], 0)
        self.assertEqual(len(self.outbox), 1)

    def test_unconfirmed_is_forgotten_after_a_week(self):
        self.tips.subscribe("ivy@example.com", send=self.send)
        later = self.main.db.now() + 8 * 86400
        self.assertEqual(self.tips.send_due(now=later, send=self.send)
                         ["forgotten"], 1)

    def test_confirming_sends_the_first_tip(self):
        self.tips.subscribe("ivy@example.com", send=self.send)
        self.assertTrue(self.tips.confirm(self.token(), send=self.send))
        self.assertEqual(self.outbox[-1]["subject"],
                         self.tips.TIPS[0]["subject"])

    def test_a_confirmed_address_is_not_asked_again(self):
        self.tips.subscribe("ivy@example.com", send=self.send)
        self.tips.confirm(self.token(), send=self.send)
        count = len(self.outbox)
        self.tips.subscribe("ivy@example.com", send=self.send)
        self.assertEqual(len(self.outbox), count)


class OneAWeekThenStop(Case):
    def test_eight_then_nothing(self):
        self.tips.subscribe("ivy@example.com", send=self.send)
        self.tips.confirm(self.token(), send=self.send)
        now = self.main.db.now()
        self.assertEqual(self.tips.send_due(now=now + 3600, send=self.send)
                         ["sent"], 0)
        for week in range(1, 12):
            self.tips.send_due(now=now + week * 7 * 86400 + 60, send=self.send)
        tips_sent = [m for m in self.outbox if "confirm" not in m["subject"].lower()]
        self.assertEqual(len(tips_sent), len(self.tips.TIPS))
        self.assertEqual(len(self.tips.TIPS), 8)


class LeavingIsEasy(Case):
    def test_every_tip_can_unsubscribe_in_one_click(self):
        self.tips.subscribe("ivy@example.com", send=self.send)
        self.tips.confirm(self.token(), send=self.send)
        tip = self.outbox[-1]
        self.assertIn("/tips/unsubscribe/", tip["body"])
        self.assertIn("List-Unsubscribe", tip["headers"])
        self.assertEqual(tip["headers"]["List-Unsubscribe-Post"],
                         "List-Unsubscribe=One-Click")

    def test_unsubscribing_deletes_the_address(self):
        self.tips.subscribe("ivy@example.com", send=self.send)
        token = self.token()
        page = self.client.post(f"/tips/unsubscribe/{token}").text
        self.assertIn("deleted", page)
        with self.main.db.connect() as c:
            self.assertIsNone(c.execute(
                "SELECT 1 FROM tip_subscribers").fetchone())

    def test_opening_the_link_alone_does_not_unsubscribe(self):
        """Mail scanners open every link in an email."""
        self.tips.subscribe("ivy@example.com", send=self.send)
        self.client.get(f"/tips/unsubscribe/{self.token()}")
        self.assertTrue(self.token())


class TheForm(Case):
    def test_it_is_on_the_free_tools(self):
        for path in ("/find", "/tools/cover-letter-ai-check",
                     "/tools/follow-up", "/answers/why-no-reply"):
            self.assertIn('action="/tips"', self.client.get(path).text, path)

    def test_same_answer_whether_or_not_already_subscribed(self):
        from unittest.mock import patch
        from app import auth
        with patch.object(auth, "send_app_email", self.send):
            first = self.client.post("/tips",
                                     data={"email": "a@b.example"}).text
            again = self.client.post("/tips",
                                     data={"email": "a@b.example"}).text
        self.assertIn("Check your email", first)
        self.assertIn("Check your email", again)

    def test_a_bot_filling_the_hidden_field_is_ignored(self):
        self.client.post("/tips", data={"email": "bot@b.example",
                                        "website": "http://spam"})
        with self.main.db.connect() as c:
            self.assertIsNone(c.execute(
                "SELECT 1 FROM tip_subscribers").fetchone())


class TheWords(unittest.TestCase):
    PUBLISHED = {"171", "137", "32", "19", "86", "38", "50", "10", "9", "6",
                 "60", "90", "1", "5", "8", "2", "3", "4"}

    def test_every_figure_is_one_already_published(self):
        os.environ.setdefault("DEV_MODE", "1")
        os.environ.setdefault("SECRET_KEY", "test")
        from app import tips
        for tip in tips.TIPS:
            for n in re.findall(r"\d+", tip["subject"] + tip["body"]):
                self.assertIn(n, self.PUBLISHED, f"{n} in {tip['subject']!r}")

    def test_no_age_and_no_employer(self):
        from app import tips
        text = " ".join(t["subject"] + t["body"] for t in tips.TIPS).lower()
        for word in ("hydro", "22", "year-old", "years old"):
            self.assertNotIn(word, text)


class ThePostgresCursor(unittest.TestCase):
    """The tips job's clean-up counts deleted rows. SQLite's cursor has
    rowcount; the Postgres wrapper did not, so the first real run failed."""

    def test_the_wrapper_reports_rowcount(self):
        from app import store

        class Raw:
            rowcount = 3
        self.assertEqual(store._PgCursor(Raw()).rowcount, 3)


if __name__ == "__main__":
    unittest.main()
