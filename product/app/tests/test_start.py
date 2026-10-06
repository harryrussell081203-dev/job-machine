"""/start: the questions first, each answer said back, the account last.

What these hold:

  - ONLY WHAT IS MISSING IS ASKED. A sentence that answers four questions
    leaves one box, not five.
  - EVERY ANSWER COMES BACK IN WORDS. "£12.50 an hour or more", not a
    silent form.
  - THE SAME CHECKS AS EVERYWHERE ELSE. Nothing reaches an inbox until the
    answers would pass the Profile the machine runs on.
  - A NEW ADDRESS IS STRAIGHT IN. No link to tap before the search starts;
    the link that follows confirms the address and is only required where
    that proof protects something.
  - AN EXISTING ACCOUNT STILL NEEDS ITS LINK, and a link alone never saves
    anything in somebody's name. Same browser that answered: it starts at
    once. Any other: one screen, one button.
  - TYPING SOMEBODY ELSE'S ADDRESS FIRST GETS YOU NOTHING LASTING. Their
    confirming it signs out every session made before.
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
        self.confirms = []
        real_confirm = self.main.auth.send_confirm_email
        self.main.auth.send_confirm_email = (
            lambda address, link, waiting="": self.confirms.append(
                (address, link, waiting)))
        self.addCleanup(setattr, self.main.auth, "send_confirm_email",
                        real_confirm)

    def existing(self, email="sam@example.com"):
        """An account that already exists, made elsewhere: these tests are
        about what /start does for it, not about how it was made."""
        self.main.db.get_or_create_user(email)

    def read(self, answer, text="forklift at Tesco, want warehouse work in Leeds"):
        with patch("app.ai.gemini_now", model(answer)):
            return html.unescape(
                self.client.post("/start/read", data={"about": text}).text)

    def submit(self, **overrides):
        return html.unescape(
            self.client.post("/start", data=dict(ALL, **overrides)).text)

    def join(self, **overrides):
        return self.client.post("/start", data=dict(ALL, **overrides),
                                follow_redirects=False)

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


class ANewAddressIsStraightIn(StartCase):
    def test_no_link_before_the_search_starts(self):
        r = self.join()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/dashboard?welcome=1")
        self.assertEqual(self.mail, [])
        profile = self.profile()
        self.assertEqual(profile["target_roles"], ["warehouse operative"])
        self.assertEqual(profile["min_rate_hourly"], 13)
        self.assertEqual(len(self.started), 1)

    def test_signed_in_and_welcomed(self):
        self.join()
        page = self.get("/dashboard?welcome=1")
        self.assertIn("You're in, Sam", page)
        self.assertIn("around Leeds", page)

    def test_takes_a_free_place(self):
        with patch.object(self.main.config, "FREE_SPOTS", 3):
            self.join()
        user = self.main.db.get_user_by_email("sam@example.com")
        self.assertTrue(user["free_spot"])

    def test_one_email_follows_to_confirm_it(self):
        self.join()
        self.assertEqual(len(self.confirms), 1)
        address, link, waiting = self.confirms[0]
        self.assertEqual(address, "sam@example.com")
        self.assertEqual(waiting, "warehouse operative work around Leeds")

    def test_the_confirm_email_is_safe_to_receive_by_mistake(self):
        from app import auth
        body = auth._confirm_body("https://x/y", "chef work around York")
        self.assertIn("If you did not sign up, ignore this", body)
        self.assertIn("Nothing else will be sent", body)

    def test_nothing_is_made_until_the_answers_would_pass(self):
        page = self.submit(phone="")
        self.assertIn("phone", page.lower())
        self.assertIsNone(self.main.db.get_user_by_email("sam@example.com"))

    def test_a_bad_email_is_said_plainly(self):
        page = self.submit(email="sam")
        self.assertIn("doesn't look right", page)

    def test_one_machine_cannot_take_every_free_place(self):
        for i in range(5):
            self.client.cookies.clear()
            self.assertEqual(self.join(email=f"p{i}@example.com").status_code,
                             303)
        self.client.cookies.clear()
        page = self.submit(email="p6@example.com")
        self.assertIn("One last tap", page)
        self.assertIsNone(self.main.db.get_user_by_email("p6@example.com"))


class ConfirmingTheAddress(StartCase):
    def tap_confirm(self, client=None):
        token = self.confirms[-1][1].split("token=", 1)[1]
        return (client or self.client).get(f"/auth/verify?token={token}",
                                           follow_redirects=False)

    def test_tapping_it_confirms_and_keeps_them_signed_in(self):
        self.join()
        r = self.tap_confirm()
        self.assertEqual(r.headers["location"], "/dashboard?confirmed=1")
        user = self.main.db.get_user_by_email("sam@example.com")
        self.assertFalse(self.main.db.email_unconfirmed(user))
        self.assertIn("Email confirmed", self.get("/dashboard?confirmed=1"))

    def test_whoever_typed_it_first_is_signed_out(self):
        """Somebody types another person's address and keeps the session.
        The moment the real owner taps the link, that session is dead."""
        from fastapi.testclient import TestClient
        self.join()
        squatter = dict(self.client.cookies)
        owner = TestClient(self.main.app)
        self.tap_confirm(owner)
        self.client.cookies.clear()
        for k, v in squatter.items():
            self.client.cookies.set(k, v)
        r = self.client.get("/dashboard", follow_redirects=False)
        self.assertNotEqual(r.status_code, 200)

    def test_the_confirm_link_outlasts_a_sign_in_link(self):
        import time as _t
        self.join()
        token = self.confirms[-1][1].split("token=", 1)[1]
        later = _t.time() + 3600
        with patch("app.auth.time.time", return_value=later):
            email, _ = self.main.auth.consume_login(token)
        self.assertEqual(email, "sam@example.com")

    def test_an_ordinary_link_still_dies_in_fifteen_minutes(self):
        import time as _t
        token = self.main.auth.make_login_link("x@example.com").split("token=")[1]
        with patch("app.auth.time.time", return_value=_t.time() + 3600):
            self.assertEqual(self.main.auth.consume_login(token), (None, ""))

    def test_a_recruited_address_waits_for_it(self):
        """Its letters carry this address on Reply-To."""
        self.join()
        with patch.object(self.main.config, "managed_mail_available",
                          lambda: True), \
                patch.object(self.main.vault, "available", lambda: True):
            r = self.client.post("/setup/mail/managed", follow_redirects=False)
        self.assertEqual(r.headers["location"], "/setup/mail?e=confirm")
        user = self.main.db.get_user_by_email("sam@example.com")
        self.assertIsNone(self.main.db.get_mail_account(user["id"]))

    def test_send_it_again(self):
        self.join()
        self.client.post("/account/confirm", data={"next": "/dashboard"})
        self.assertEqual(len(self.confirms), 2)


class AnExistingAccountStillGetsALink(StartCase):
    def setUp(self):
        super().setUp()
        self.existing()
    def test_typing_its_address_does_not_sign_anybody_in(self):
        r = self.join()
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.client.cookies.get("jm_session"))
        self.assertIsNone(self.profile())

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
    def setUp(self):
        super().setUp()
        self.existing()

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
        self.existing("someone.else@example.com")
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
