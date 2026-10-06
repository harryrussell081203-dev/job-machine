"""The letter on /find: no account, only what the person wrote, the house
rules, nothing kept."""

import json
import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")

from test_app import AppTestCase  # noqa: E402

from app import find_letter  # noqa: E402

GOOD = ("I'm writing about the warehouse operative role at your Leeds site. "
        "I spent three years picking and packing at Tesco, hitting rate on "
        "every shift, and I hold a counterbalance forklift licence. I'm "
        "reliable, I live twenty minutes away and I can start straight away "
        "on any shift pattern you need. Would it help if I came in for a "
        "short trial shift this week so you can see how I work?")


def model(*answers):
    replies = list(answers)

    def ai(prompt):
        ai.prompts.append(prompt)
        return json.dumps(replies.pop(0))
    ai.prompts = []
    return ai


class Writing(unittest.TestCase):
    def test_a_good_letter_gets_greeting_and_signoff(self):
        out = find_letter.write(
            job="warehouse operative", company="Pennine Foods",
            about="3 years at Tesco picking, forklift licence",
            greet="claire", name="Sam Doherty", phone="07700 900123",
            ai=model({"subject": "Warehouse operative", "body": GOOD}))
        self.assertTrue(out["body"].startswith("Hi Claire,\n\n"))
        self.assertTrue(out["body"].endswith("Sam Doherty\n07700 900123"))
        self.assertEqual(out["subject"], "Warehouse operative")

    def test_a_rule_broken_is_fed_back_and_retried(self):
        ai = model({"subject": "Job", "body": "Too short. Any chance?"},
                   {"subject": "Warehouse operative", "body": GOOD})
        out = find_letter.write(job="x", company="", about="3 years at Tesco",
                                ai=ai)
        self.assertIsNotNone(out)
        self.assertIn("must be 60 to 90", ai.prompts[1])

    def test_gives_up_rather_than_send_a_bad_one(self):
        bad = {"subject": "Job", "body": "I am passionate about synergy. " * 12}
        self.assertIsNone(find_letter.write(job="x", company="", about="y",
                                            ai=model(bad, bad)))

    def test_nothing_to_say_means_no_letter(self):
        self.assertIsNone(find_letter.write(job="x", company="", about="  ",
                                            ai=model()))

    def test_the_prompt_forbids_adding_claims(self):
        p = find_letter.prompt(job="chef", company="", about="cooked", advert="")
        self.assertIn("ONLY from what the person wrote", p)
        self.assertIn("Never add a qualification", p)


class ThePage(AppTestCase):
    env = {"BILLING_ENABLED": "1"}

    def test_found_addresses_offer_the_letter(self):
        page = self.client.post("/find", data={
            "advert": "Send your CV to claire.smith@pennine.co.uk"}).text
        self.assertIn('action="/find/letter"', page)
        self.assertIn('value="claire.smith@pennine.co.uk"', page)
        self.assertIn('value="Claire"', page)

    def test_no_account_needed_and_links_to_send(self):
        with patch("app.ai.gemini_now",
                   model({"subject": "Warehouse operative", "body": GOOD})):
            r = self.client.post("/find/letter", data={
                "to": "jobs@pennine.co.uk", "company": "Pennine Foods",
                "job": "warehouse operative", "about": "3 years at Tesco"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Open in Gmail", r.text)
        self.assertIn("mail.google.com/mail/?view=cm", r.text)
        self.assertIn("mailto:jobs%40pennine.co.uk", r.text)
        self.assertIn('<a class="btn" href="/start">', r.text)

    def test_nothing_typed_is_stored(self):
        with patch("app.ai.gemini_now",
                   model({"subject": "Warehouse operative", "body": GOOD})):
            self.client.post("/find/letter", data={
                "to": "jobs@pennine.co.uk", "about": "secret-detail-xyz"})
        from app import db
        with db.connect() as c:
            for table in ("drafts", "site_meta", "users"):
                rows = [dict(r) for r in c.execute(f"SELECT * FROM {table}")]
                self.assertNotIn("secret-detail-xyz", repr(rows), table)

    def test_without_anything_about_them_it_asks(self):
        r = self.client.post("/find/letter", data={"to": "jobs@pennine.co.uk",
                                                  "about": ""})
        self.assertIn("Write a line or two", r.text)

    def test_rationed_per_visitor(self):
        ai = model(*[{"subject": "Warehouse operative", "body": GOOD}] * 6)
        with patch("app.ai.gemini_now", ai):
            for _ in range(6):
                r = self.client.post("/find/letter", data={
                    "to": "jobs@pennine.co.uk", "about": "3 years at Tesco"})
        self.assertIn("a lot of letters", r.text)
        self.assertEqual(len(ai.prompts), 5)


if __name__ == "__main__":
    unittest.main()
