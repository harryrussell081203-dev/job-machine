"""/start: the questions first, each answer said back, the account last.

What these hold:

  - ONLY WHAT IS MISSING IS ASKED. A sentence that answers four questions
    leaves one box, not five.
  - EVERY ANSWER COMES BACK IN WORDS. "£12.50 an hour or more", not a
    silent form.
  - THE SAME CHECKS AS EVERYWHERE ELSE. Nothing reaches an inbox until the
    answers would pass the Profile the machine runs on.
  - A LINK ALONE NEVER SAVES ANYTHING IN SOMEBODY'S NAME. Same browser that
    answered: it starts at once. Any other: one screen, one button.
"""

import html
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

ALL = {
    "target_roles": "warehouse operative",
    "location": "Leeds",
    "last_title": "Forklift driver",
    "last_org": "Tesco",
    "min_pay": "12.50",
    "name": "Sam Example",
    "phone": "07700 900123",
    "email": "sam@example.com",
}


def model(answer):
    return lambda prompt: json.dumps(answer)


class StartCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.started, self.mail = [], []
        real_start = self.main._start_run
        self.main._start_run = lambda uid: self.started.append(uid) or True
        self.addCleanup(setattr, self.main, "_start_run", real_start)
        real_send = self.main.auth.send_login_email
        self.main.auth.send_login_email = (
            lambda address, link, waiting="": self.mail.append(
                (address, link, waiting)))
        self.addCleanup(setattr, self.main.auth, "send_login_email", real_send)

    def read(self, answer, text="forklift at Tesco, want warehouse work in Leeds"):
        with patch("app.ai.gemini_now", model(answer)):
            return html.unescape(
                self.client.post("/start/read", data={"about": text}).text)

    def submit(self, **overrides):
        return html.unescape(
            self.client.post("/start", data=dict(ALL, **overrides)).text)

    def get(self, path):
        return html.unescape(self.client.get(path).text)

    def tap(self, client=None):
        token = self.mail[-1][1].split("token=", 1)[1]
        return (client or self.client).get(f"/auth/verify?token={token}",
                                           follow_redirects=False)

    def profile(self, email="sam@example.com"):
        user = self.main.db.get_user_by_email(email)
        return user and self.main.db.load_profile(user["id"])


class TheFirstScreen(StartCase):
    def test_one_box_to_start(self):
        page = self.client.get("/start").text
        self.assertIn('action="/start/read"', page)
        self.assertIn('name="about"', page)
        self.assertIn("Step 1 of 2", page)
        self.assertNotIn('name="phone"', page)

    def test_or_the_questions(self):
        page = self.client.get("/start?boxes=1").text
        for field in ALL:
            self.assertIn(f'name="{field}"', page, field)

    def test_the_landing_page_sends_people_here(self):
        self.assertIn('href="/start"', self.client.get("/").text)


class SayingItBack(StartCase):
    def test_what_was_understood_comes_back_in_words(self):
        page = self.read({"target_roles": "warehouse operative",
                          "location": "Leeds", "last_title": "Forklift driver",
                          "last_org": "Tesco", "min_pay": "12.50"})
        self.assertIn("Here's what I understood", page)
        self.assertIn("£12.50 an hour or more", page)
        self.assertIn("Leeds, and 25 miles around", page)
        self.assertIn("Forklift driver at Tesco", page)

    def test_only_what_is_missing_is_asked(self):
        page = self.read({"target_roles": "warehouse operative",
                          "location": "Leeds", "last_title": "Forklift driver",
                          "last_org": "Tesco"})
        self.assertIn("Just 1 more thing", page)
        before_fold = page.split("Something I understood wrong")[0]
        self.assertIn('name="min_pay"', before_fold)
        self.assertNotIn('name="location"', before_fold)

    def test_a_model_that_cannot_read_it_leaves_the_questions(self):
        with patch("app.ai.gemini_now", side_effect=RuntimeError("down")):
            page = html.unescape(self.client.post(
                "/start/read", data={"about": "x y z"}).text)
        self.assertIn("here are the questions", page)
        self.assertIn('name="target_roles"', page)


class TheLink(StartCase):
    def test_nothing_is_sent_until_the_answers_would_pass(self):
        page = self.submit(phone="")
        self.assertEqual(self.mail, [])
        self.assertIn("phone", page.lower())

    def test_a_bad_email_is_said_plainly(self):
        page = self.submit(email="sam")
        self.assertEqual(self.mail, [])
        self.assertIn("doesn't look right", page)

    def test_the_inbox_screen_shows_what_is_waiting(self):
        page = self.submit()
        self.assertIn("One last tap", page)
        self.assertIn("Saved and waiting for you", page)
        self.assertIn("£12.50 an hour or more", page)
        address, link, waiting = self.mail[0]
        self.assertEqual(address, "sam@example.com")
        self.assertEqual(waiting, "warehouse operative work around Leeds")

    def test_a_strangers_words_never_reach_the_email(self):
        """Anybody can type anybody's address. What they typed must not be
        a way to send a stranger a link or an offer."""
        self.submit(target_roles="claim prize at evil.example")
        self.assertEqual(self.mail[0][2], "")

    def test_nothing_personal_rides_in_the_link(self):
        self.submit()
        link = self.mail[0][1]
        self.assertNotIn("07700", link)
        import base64
        token = link.split("token=", 1)[1].split(".")[0]
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        self.assertNotIn(b"07700", decoded)


class TappingIt(StartCase):
    def test_same_browser_starts_at_once(self):
        self.submit()
        r = self.tap()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/dashboard?welcome=1")
        profile = self.profile()
        self.assertEqual(profile["target_roles"], ["warehouse operative"])
        self.assertEqual(profile["min_rate_hourly"], 13)
        self.assertEqual(len(self.started), 1)

    def test_the_welcome_says_what_it_is_doing(self):
        self.submit()
        self.tap()
        page = self.get("/dashboard?welcome=1")
        self.assertIn("You're in, Sam", page)
        self.assertIn("warehouse operative", page)
        self.assertIn("around Leeds", page)

    def test_another_browser_asks_once(self):
        """The mail app's own browser has no cookie from /start. A link only
        proves somebody can read the inbox, so nothing is saved until the
        answers are seen and the button pressed."""
        self.submit()
        self.client.cookies.clear()
        r = self.tap()
        self.assertEqual(r.headers["location"], "/start/confirm")
        self.assertIsNone(self.profile())
        page = self.get("/start/confirm")
        self.assertIn("Here's what you told us", page)
        self.assertIn("Start looking", page)
        draft = page.split('name="draft" value="', 1)[1].split('"', 1)[0]
        r = self.client.post("/start/confirm", data={"draft": draft},
                             follow_redirects=False)
        self.assertEqual(r.headers["location"], "/dashboard?welcome=1")
        self.assertTrue(self.profile())
        self.assertEqual(len(self.started), 1)

    def test_change_something_opens_the_answers_not_a_blank_form(self):
        self.submit()
        self.client.cookies.clear()
        self.tap()
        r = self.client.post("/start/confirm", data={"action": "edit"})
        self.assertIn('value="Forklift driver"', r.text)
        self.assertIsNone(self.profile())

    def test_answers_for_one_address_are_never_another_accounts(self):
        self.submit()
        self.client.cookies.clear()
        self.sign_in("someone.else@example.com")
        self.assertEqual(self.client.get("/start/confirm",
                                         follow_redirects=False).headers["location"],
                         "/setup")

    def test_an_expired_link_does_not_lose_the_answers(self):
        """Ask for a fresh link from the sign-in screen and the answers are
        still there to confirm."""
        self.submit()
        self.client.cookies.clear()
        self.sign_in("sam@example.com")
        self.assertIn("Here's what you told us", self.get("/start/confirm"))

    def test_used_once(self):
        self.submit()
        self.tap()
        self.assertFalse(self.main.db.signup_draft("sam@example.com"))


class TheWords(unittest.TestCase):
    def test_pay(self):
        from app.understood import pay_words, parse_pay
        self.assertEqual(pay_words("12.50"), "£12.50 an hour or more")
        self.assertEqual(pay_words("25k a year"), "£25,000 a year or more")
        self.assertEqual(parse_pay("25k a year"), (25000, 0))
        self.assertEqual(pay_words("decent"), "")

    def test_missing_counts_unreadable_pay(self):
        from app.understood import missing
        self.assertIn("min_pay", missing({"min_pay": "decent"}))


if __name__ == "__main__":
    unittest.main()
