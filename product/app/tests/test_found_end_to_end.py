"""Things found by walking the product as a real user, sign-up to sending.

Each test names what was seen, so the reason it exists survives the fix."""

import os
import sys
import unittest
from email import message_from_bytes
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")


class TheEmailThatGoesOut(unittest.TestCase):
    def sent(self):
        from app import delivery
        captured = {}

        class Conn:
            def send_message(self, msg):
                captured["msg"] = msg

        with patch.object(delivery, "_connect_and",
                          lambda h, p, u, pw, action, **k: action(Conn())):
            delivery.send_via_smtp(
                host="smtp.example.com", port=465, username="sam@example.com",
                password="x", to_email="jane@airevalley.example",
                subject="Forklift Driver", body="Hi Jane,\n\nShort letter.",
                display_name="Sam Example")
        return message_from_bytes(captured["msg"].as_bytes())

    def test_it_carries_a_date_and_a_message_id(self):
        """The received copy had neither. Gmail adds them on the way out;
        not every provider does, and their absence is a spam signal."""
        msg = self.sent()
        self.assertTrue(msg["Date"])
        self.assertTrue(msg["Message-ID"].endswith("@example.com>"))

    def test_it_is_from_the_person_by_name(self):
        self.assertEqual(self.sent()["From"], "Sam Example <sam@example.com>")


class TheRunSummary(unittest.TestCase):
    def test_every_listing_is_accounted_for(self):
        """'0 drafted from 3 listings (1 had no real address ...)' left two
        jobs unexplained; both had been set aside for a domain that takes
        no mail."""
        from app.runner import RunReport
        r = RunReport(harvested=3, no_address=1, undeliverable=2)
        self.assertIn("2: the email domain takes no mail", r.summary())
        self.assertNotIn("too far", r.summary())

    def test_the_pay_floor_is_named(self):
        """Live, a run read '0 drafted from 426 listings (12 had no real
        address, 137 scored too low, 55 already contacted)': 222 jobs with
        no reason, 243 of them under the pay floor."""
        from app.runner import RunReport
        r = RunReport(harvested=426, prefiltered=243, drafted=0)
        self.assertIn("243: below your pay floor", r.summary())


if __name__ == "__main__":
    unittest.main()
