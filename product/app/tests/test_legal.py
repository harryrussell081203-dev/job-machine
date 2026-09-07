"""Tests for the terms and privacy pages.

Not box-ticking. Charging a UK consumer for a service that holds their CV and
their mailbox password without publishing either page is a straightforward
compliance failure, and Stripe expects both to exist before it lets you take
money.

The tests worth having are not "does the page render". They are the ways
these two pages go quietly wrong:

  - they end up behind the sign-in wall, so the person deciding whether to
    hand over a mailbox password cannot read what happens to it
  - they lose the link from the footer and become unreachable in practice
  - they describe a product that no longer matches the code
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_app import AppTestCase  # noqa: E402


def prose(html: str) -> str:
    """The page with its line breaks flattened.

    These templates wrap at 78 columns like everything else here, so
    "Information Commissioner" arrives with a newline in the middle of it and
    a plain assertIn misses a phrase that is plainly on the page. Asserting
    against the wrapped form instead would be worse: reflowing a paragraph is
    not a change to what it says, and a test that breaks on it teaches people
    to stop reflowing paragraphs.
    """
    return re.sub(r"\s+", " ", html)


class TestReachableWithoutAnAccount(AppTestCase):
    """A privacy notice behind a sign-in wall is not a notice. Somebody has
    to be able to read what happens to their CV before they upload one."""

    def test_both_pages_are_public(self):
        for path in ("/privacy", "/terms"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertNotIn("/login", str(r.url))

    def test_the_footer_links_to_both_from_a_public_page(self):
        body = prose(self.client.get("/").text)
        self.assertIn('href="/privacy"', body)
        self.assertIn('href="/terms"', body)

    def test_the_footer_links_to_both_from_behind_the_wall_too(self):
        self.sign_in()
        body = prose(self.client.get("/dashboard").text)
        self.assertIn('href="/privacy"', body)
        self.assertIn('href="/terms"', body)


class TestThePrivacyNoticeSaysTheThingsItMust(AppTestCase):
    def page(self):
        return prose(self.client.get("/privacy").text)

    def test_it_names_the_categories_actually_stored(self):
        """Every one of these is a real column. A notice that omits one is
        describing a different product from the one running."""
        body = self.page().lower()
        for held in ("email address", "cv", "password", "stripe",
                     "ip address"):
            self.assertIn(held, body, held)

    def test_it_says_where_the_data_physically_is(self):
        body = self.page()
        self.assertIn("Supabase", body)
        self.assertIn("Render", body)
        self.assertIn("Ireland", body)

    def test_it_declares_the_transfer_to_google(self):
        """The letters are written by Gemini, which means the CV and the
        advert leave the UK. That is the disclosure most easily left out and
        the one a reader most needs."""
        body = self.page()
        self.assertIn("Google", body)
        self.assertIn("United States", body)

    def test_it_does_not_claim_cards_are_stored(self):
        self.assertIn("Card details are never seen", self.page())

    def test_it_names_the_regulator_and_how_to_complain(self):
        body = self.page()
        self.assertIn("Information Commissioner", body)
        self.assertIn("ico.org.uk", body)

    def test_it_is_honest_about_what_a_mail_password_grants(self):
        """The comfortable version of this paragraph says "encrypted" and
        stops. An app password can read the mailbox as well as send from it,
        and a reader deciding whether to hand one over needs that sentence."""
        body = self.page()
        self.assertIn("app password", body.lower())
        self.assertIn("read that", body.lower())

    def test_it_points_at_a_delete_that_actually_exists(self):
        self.assertIn('href="/account"', self.page())
        self.sign_in()
        self.assertEqual(self.client.get("/account/delete").status_code, 200)


class TestTheTermsSayTheThingsTheyMust(AppTestCase):
    def page(self):
        return prose(self.client.get("/terms").text)

    def test_it_promises_no_outcome(self):
        """A service that implies it gets you a job is lying, and the claim
        would be the first thing a regulator or an angry customer picked."""
        self.assertIn("does not get you one", self.page())

    def test_it_repeats_the_four_promises_the_code_enforces(self):
        body = self.page()
        for promise in ("Guess an email address",
                        "Claim a qualification you do not hold",
                        "Write to the same employer twice",
                        "Name your current employer"):
            self.assertIn(promise, body, promise)

    def test_it_states_the_price_from_config_rather_than_a_hardcoded_one(self):
        """Two places naming a price is one place to forget."""
        self.assertIn(self.main.config.PRICE_LABEL, self.page())

    def test_it_covers_cancelling_and_the_statutory_right(self):
        body = self.page()
        self.assertIn("Cancel any time", body)
        self.assertIn("Consumer Contracts Regulations", body)
        self.assertIn("Consumer Rights Act 2015", body)

    def test_it_does_not_purport_to_exclude_what_cannot_be_excluded(self):
        self.assertIn("death or personal injury", self.page())

    def test_it_says_letters_are_written_by_a_model(self):
        """Somebody switching on automatic sending is agreeing to let an
        unread letter go out with their name on it. Burying that would be
        the single most misleading omission available here."""
        body = self.page().lower()
        self.assertIn("language model", body)
        self.assertIn("holding window", body)


class TestTheControllerIsIdentifiable(AppTestCase):
    """UK GDPR requires the controller to be named and contactable. These are
    configuration, so the failure mode is shipping with them unset - which
    renders a notice naming nobody."""

    env = {"CONTROLLER_NAME": "Harry Russell",
           "CONTACT_EMAIL": "harry@example.com"}

    def test_the_name_and_contact_appear(self):
        body = prose(self.client.get("/privacy").text)
        self.assertIn("Harry Russell", body)
        self.assertIn("harry@example.com", body)

    def test_the_contact_reaches_the_footer(self):
        self.assertIn("mailto:harry@example.com", prose(self.client.get("/").text))


class TestUnsetContactDoesNotRenderAnEmptyMailto(AppTestCase):
    env = {"CONTROLLER_NAME": "", "CONTACT_EMAIL": ""}

    def test_no_broken_contact_link_is_shown(self):
        """Better to show nothing than a mailto: that goes nowhere - a dead
        contact link on a privacy notice is worse than an absent one."""
        for body in (prose(self.client.get("/privacy").text),
                     prose(self.client.get("/terms").text),
                     prose(self.client.get("/").text)):
            self.assertNotIn('mailto:"', body)
            self.assertNotIn("mailto:>", body)


class TestTheIssuedAddressIsDisclosed(AppTestCase):
    """A Job Machine address changes what is held and who processes the
    letter, so both pages have to say so. A privacy notice describing only
    the mailbox route would be describing half the product."""

    def test_the_privacy_notice_covers_it(self):
        body = prose(self.client.get("/privacy").text)
        self.assertIn("Job Machine sending address", body)
        self.assertIn("no password", body.lower())
        self.assertIn("Reply-To", body)

    def test_it_says_replies_do_not_come_to_us(self):
        """The reassurance only means something if it is stated plainly."""
        body = prose(self.client.get("/privacy").text).lower()
        self.assertIn("does not come to us", body)

    def test_the_password_section_says_which_route_it_applies_to(self):
        """It used to open 'automatic sending is off unless you turn it on',
        which now reads as though every user hands over a password."""
        body = prose(self.client.get("/privacy").text)
        self.assertIn("only if you connect", body.lower())

    def test_the_terms_state_the_shared_allowance(self):
        """Somebody whose letters go tomorrow instead of today should have
        been told that before they chose it, not after."""
        body = prose(self.client.get("/terms").text)
        self.assertIn("one daily allowance", body)
        self.assertIn("wait until tomorrow", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
