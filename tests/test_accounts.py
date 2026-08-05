"""
Tests for creating portal accounts.

Offline. No account is created anywhere, no inbox is opened.

The thing that must never break here: data/state.json is committed to a
PUBLIC repository on every run, so a password reaching it is a password
published to the internet.
"""
import os
import re
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts  # noqa: E402
import job_machine as jm  # noqa: E402


class TestThePasswordIsNeverWrittenDown(unittest.TestCase):
    """This repository is public. Anything in state.json is published."""

    def setUp(self):
        patch = mock.patch.object(accounts, "SALT", "a-secret-on-the-runner")
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_same_site_always_gives_the_same_password(self):
        """Otherwise he could never sign back in."""
        self.assertEqual(accounts.password_for("myworkdayjobs.com"),
                         accounts.password_for("myworkdayjobs.com"))

    def test_a_different_site_gives_an_unrelated_one(self):
        """A leak at one employer must tell an attacker nothing about another."""
        first = accounts.password_for("myworkdayjobs.com")
        second = accounts.password_for("taleo.net")
        self.assertNotEqual(first, second)
        self.assertLess(len(set(first) & set(second)), len(first))

    def test_it_satisfies_what_portals_demand(self):
        password = accounts.password_for("example.com")
        self.assertGreaterEqual(len(password), 12)
        self.assertTrue(re.search(r"[A-Z]", password))
        self.assertTrue(re.search(r"[a-z]", password))
        self.assertTrue(re.search(r"\d", password))
        self.assertTrue(re.search(r"[^\w]", password))

    def test_with_nothing_at_all_it_refuses_rather_than_inventing_one(self):
        """A weak password nobody chose is worse than no account."""
        with mock.patch.object(accounts, "SALT", ""), \
             mock.patch.object(accounts, "SHARED", ""), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", ""):
            self.assertFalse(accounts.configured())
            with self.assertRaises(RuntimeError):
                accounts.password_for("example.com")

    def test_what_is_recorded_is_only_that_an_account_exists(self):
        state = {}
        accounts.remember_account(state, "example.com", verified=True)
        entry = state["portal_accounts"]["example.com"]
        self.assertEqual(set(entry) - {"email", "created_at", "verified_at",
                                       "seen_at"}, set())
        blob = str(state)
        self.assertNotIn(accounts.password_for("example.com"), blob)
        self.assertNotIn("password", blob.lower())

    def test_the_module_never_stores_a_password_anywhere(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "accounts.py")) as f:
            source = f.read()
        for forbidden in ('["password"] =', "'password':", '"password":'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class TestTheSharedPassword(unittest.TestCase):
    """Harry asked for one password he knows, rather than a derived one he
    cannot. His call - and the padding is what makes it work at all."""

    def test_what_he_asked_for_becomes_something_portals_accept(self):
        with mock.patch.object(accounts, "SHARED", "password123"):
            self.assertEqual(accounts.password_for("anything.com"),
                             "Password123!")

    def test_it_is_the_same_everywhere_which_is_the_point(self):
        with mock.patch.object(accounts, "SHARED", "password123"):
            self.assertEqual(accounts.password_for("a.com"),
                             accounts.password_for("b.com"))

    def test_whatever_he_picks_clears_the_usual_rules(self):
        for raw in ("password123", "harryrussell", "aberdeen", "Pass1!"):
            with self.subTest(raw=raw):
                padded = accounts.acceptable(raw)
                self.assertGreaterEqual(len(padded), 12)
                self.assertTrue(re.search(r"[a-z]", padded))
                self.assertTrue(re.search(r"[A-Z]", padded))
                self.assertTrue(re.search(r"\d", padded))
                self.assertTrue(re.search(r"[^\w]", padded))

    def test_one_already_strong_enough_is_left_alone(self):
        self.assertEqual(accounts.acceptable("Password123!"), "Password123!")

    def test_the_shared_one_wins_over_the_derived_one(self):
        with mock.patch.object(accounts, "SHARED", "password123"), \
             mock.patch.object(accounts, "SALT", "a-salt"):
            self.assertEqual(accounts.password_for("x.com"), "Password123!")

    def test_either_one_is_enough_to_be_configured(self):
        with mock.patch.object(accounts, "SALT", ""), \
             mock.patch.object(accounts, "SHARED", "password123"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", ""):
            self.assertTrue(accounts.configured())
        with mock.patch.object(accounts, "SALT", ""), \
             mock.patch.object(accounts, "SHARED", ""), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", ""):
            self.assertFalse(accounts.configured())

    def test_it_is_still_never_committed(self):
        """A password in the repository is a published password. The module
        may DISCUSS one in a comment; it may not assign one."""
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "accounts.py")) as f:
            source = f.read()
        self.assertIn('SHARED = jm.env_str("PORTAL_PASSWORD")', source)
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        self.assertIsNone(re.search(r'SHARED\s*=\s*[\'"]', code))


class TestItWorksWithNothingConfigured(unittest.TestCase):
    """Harry asked for one password everywhere and did not want to go and set
    a secret to get it. The Gmail app password is already on every runner, so
    a single portal password is derived from it when he has set neither of
    the two he could set. He never has to touch anything, and the password is
    still knowable to him because it is emailed on every account made."""

    def setUp(self):
        for name, value in (("SALT", ""), ("SHARED", "")):
            patch = mock.patch.object(accounts, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(jm, "GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
        patch.start()
        self.addCleanup(patch.stop)

    def test_it_is_configured_with_no_secret_set_by_hand(self):
        self.assertTrue(accounts.configured())
        self.assertTrue(accounts.password_for("anything.com"))

    def test_it_is_one_password_across_every_portal(self):
        """The whole point of what he asked for."""
        self.assertEqual(accounts.password_for("myworkdayjobs.com"),
                         accounts.password_for("taleo.net"))

    def test_it_is_the_same_one_tomorrow(self):
        """An account he cannot sign back into is not an account."""
        first = accounts.password_for("x.com")
        self.assertEqual(first, accounts.password_for("x.com"))
        self.assertEqual(first, accounts.shared_password())

    def test_it_clears_the_rules_portals_actually_enforce(self):
        password = accounts.password_for("x.com")
        self.assertGreaterEqual(len(password), 12)
        self.assertTrue(re.search(r"[a-z]", password))
        self.assertTrue(re.search(r"[A-Z]", password))
        self.assertTrue(re.search(r"\d", password))
        self.assertTrue(re.search(r"[^\w]", password))

    def test_it_does_not_hand_out_the_gmail_password(self):
        """It is derived from it, one way, and never echoes it."""
        password = accounts.password_for("x.com")
        self.assertNotIn("abcd", password)
        self.assertNotIn(jm.GMAIL_APP_PASSWORD.replace(" ", ""), password)

    def test_a_secret_he_does_set_still_wins(self):
        """This is the floor, not the ceiling."""
        with mock.patch.object(accounts, "SHARED", "password123"):
            self.assertEqual(accounts.password_for("x.com"), "Password123!")
        with mock.patch.object(accounts, "SALT", "a-salt"):
            self.assertNotEqual(accounts.password_for("a.com"),
                                accounts.password_for("b.com"))

    def test_he_is_still_told_what_it_is(self):
        """Derived and unwritten is only acceptable because it is emailed."""
        with mock.patch.object(jm, "GMAIL_ADDRESS", "harry@example.com"), \
             mock.patch.object(jm, "send_email") as send:
            self.assertTrue(accounts.tell_harry("acme.com"))
        self.assertIn(accounts.password_for("acme.com"),
                      send.call_args.args[2])


class TestHeIsToldHowToGetIn(unittest.TestCase):
    """An account he cannot sign in to is no use to him."""

    def test_the_login_is_emailed_to_his_own_inbox(self):
        with mock.patch.object(accounts, "SHARED", "password123"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "harry@example.com"), \
             mock.patch.object(jm, "send_email") as send:
            self.assertTrue(accounts.tell_harry("acme.com",
                                                {"company": "Acme"}))
        to_addr, subject, body = send.call_args.args[:3]
        self.assertEqual(to_addr, "harry@example.com")
        self.assertIn("acme.com", subject)
        self.assertIn("Password123!", body)
        self.assertIn("harry@example.com", body)
        self.assertIn("Acme", body)

    def test_a_dead_mailbox_does_not_lose_the_account(self):
        with mock.patch.object(accounts, "SHARED", "password123"), \
             mock.patch.object(jm, "send_email",
                               side_effect=RuntimeError("smtp")):
            self.assertFalse(accounts.tell_harry("acme.com"))


class TestSpottingTheWall(unittest.TestCase):
    def page(self, text):
        return mock.Mock(inner_text=mock.Mock(return_value=text))

    def test_a_sign_in_wall_is_recognised(self):
        for wording in ("Please sign in to continue",
                        "You must be logged in to apply",
                        "Create an account to apply for this role",
                        "Returning candidate? Sign in"):
            with self.subTest(wording=wording):
                self.assertTrue(accounts.needs_an_account(
                    self.page(wording), []))

    def test_an_email_and_password_pair_is_a_login_whatever_it_says(self):
        fields = [{"type": "email", "label": "Email"},
                  {"type": "password", "label": "Password"}]
        self.assertTrue(accounts.needs_an_account(self.page("Welcome"), fields))

    def test_an_application_form_is_not_a_wall(self):
        fields = [{"type": "text", "label": "First name"},
                  {"type": "file", "label": "Upload your CV"},
                  {"type": "email", "label": "Email"}]
        self.assertFalse(accounts.needs_an_account(
            self.page("Apply for this job"), fields))


class TestWhatItWillAndWillNotAgreeTo(unittest.TestCase):
    """A box saying 'I agree to the terms' is assent, and Harry asked for the
    account. A box saying 'I certify this is true and complete' is a statement
    of fact about him. They look identical on a page and are not the same."""

    def setUp(self):
        patch = mock.patch.object(accounts, "SALT", "salt")
        patch.start()
        self.addCleanup(patch.stop)
        self.answers = {"first_name": "Harry", "last_name": "Russell",
                        "phone": "07398530978"}

    def fields(self, *extra):
        base = [{"type": "email", "label": "Email address", "required": True},
                {"type": "password", "label": "Password", "required": True}]
        return base + list(extra)

    def test_it_agrees_to_terms_because_it_was_asked_to_make_the_account(self):
        terms = {"type": "checkbox", "label": "I agree to the terms of use",
                 "required": True}
        plan, blockers = accounts.plan_signup(self.fields(terms), "x.com",
                                              self.answers)
        self.assertEqual(blockers, [])
        self.assertTrue(any(item["field"] is terms for item in plan))

    def test_it_refuses_to_certify_a_fact_about_him(self):
        claim = {"type": "checkbox", "required": True,
                 "label": "I certify the information given is true and complete"}
        _, blockers = accounts.plan_signup(self.fields(claim), "x.com",
                                           self.answers)
        self.assertTrue(blockers)
        self.assertIn("declaration", blockers[0])

    def test_it_never_opts_him_into_marketing(self):
        marketing = {"type": "checkbox", "required": False,
                     "label": "Send me marketing updates from our partners"}
        plan, _ = accounts.plan_signup(self.fields(marketing), "x.com",
                                       self.answers)
        self.assertFalse(any(item["field"] is marketing for item in plan))

    def test_the_password_goes_in_both_boxes(self):
        confirm = {"type": "password", "label": "Confirm password",
                   "required": True}
        plan, _ = accounts.plan_signup(self.fields(confirm), "x.com",
                                       self.answers)
        passwords = [i["value"] for i in plan if i["value"].startswith("Hr")]
        self.assertEqual(len(passwords), 2)
        self.assertEqual(passwords[0], passwords[1])

    def test_a_required_field_it_cannot_ground_stops_the_signup(self):
        odd = {"type": "text", "label": "Employee reference number",
               "required": True}
        _, blockers = accounts.plan_signup(self.fields(odd), "x.com",
                                           self.answers)
        self.assertTrue(blockers)

    def test_a_form_with_no_password_is_not_a_signup_form(self):
        _, blockers = accounts.plan_signup(
            [{"type": "text", "label": "Search"}], "x.com", self.answers)
        self.assertIn("not a signup form", blockers[-1])


class TestTheVerificationEmail(unittest.TestCase):
    def test_it_takes_the_activation_link_and_not_the_unsubscribe(self):
        found = accounts.VERIFY_LINK.findall(
            "Welcome. Confirm here: https://portal.example.com/verify?token=abc "
            "or unsubscribe at https://portal.example.com/unsubscribe/confirm")
        self.assertTrue(any("verify?token=abc" in link for link in found))

    def test_it_does_not_wait_forever(self):
        with mock.patch.object(accounts, "_search_for_link", return_value=None), \
             mock.patch.object(accounts.time, "sleep") as slept, \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@example.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "x"):
            link = accounts.verification_link(
                "example.com", datetime.now(timezone.utc), tries=3, wait=1)
        self.assertIsNone(link)
        self.assertEqual(slept.call_count, 2)

    def test_it_stops_as_soon_as_the_email_arrives(self):
        with mock.patch.object(accounts, "_search_for_link",
                               side_effect=[None, "https://x/verify?t=1"]), \
             mock.patch.object(accounts.time, "sleep"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@example.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "x"):
            self.assertEqual(accounts.verification_link(
                "example.com", datetime.now(timezone.utc)),
                "https://x/verify?t=1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
