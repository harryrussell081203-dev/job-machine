"""The invite card, and the month it promises, end to end.

The reward logic existed and nobody could see a link to use it. These hold:

  1. THE CARD SHOWS a working link when billing is on, and not when
     everything is free anyway (a free month of a free thing is no offer).
  2. "YOU BOTH GET A MONTH" IS TRUE: a friend arriving on the link, signing
     in and uploading a CV through the real page moves BOTH paid_until dates
     by a month.
  3. A SIGN-UP ALONE PAYS NOBODY, and nothing pays twice.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import AppTestCase  # noqa: E402


class Paid(AppTestCase):
    """Billing on, with both people let in through the free list so the
    pages open without Stripe."""
    env = {"BILLING_ENABLED": "1",
           "FREE_ACCESS_EMAILS": "hal@example.com,ivy@example.com"}

    def setUp(self):
        super().setUp()
        self.db = self.main.db
        self.referrals = self.main.referrals

    def referrer(self):
        self.sign_in("hal@example.com")
        return self.db.get_user_by_email("hal@example.com")

    def friend_arrives(self, referrer):
        link = self.referrals.link_for(referrer["id"])
        self.client.cookies.clear()
        self.client.get("/?" + link.split("?", 1)[1])
        self.sign_in("ivy@example.com")
        return self.db.get_user_by_email("ivy@example.com")

    def paid_until(self, user):
        return self.db.get_user(user["id"])["paid_until"] or 0

    def upload_cv(self):
        self.client.post("/setup/cv", files={"cv": (
            "cv.txt", b"Ivy Ross, electrician", "text/plain")},
            follow_redirects=False)


class TheCard(Paid):
    def test_it_shows_a_link_that_carries_the_code(self):
        hal = self.referrer()
        self.db.save_profile(hal["id"], {"name": "Hal", "location": "Leeds"})
        page = self.client.get("/dashboard").text
        self.assertIn("you both get a month free", page)
        self.assertIn(self.referrals.link_for(hal["id"]), page)
        self.assertIn('id="invitecopy"', page)

    def test_it_can_be_switched_off(self):
        hal = self.referrer()
        self.db.save_profile(hal["id"], {"name": "Hal", "location": "Leeds"})
        with patch.dict(os.environ, {"REFERRALS_ENABLED": "0"}):
            self.assertNotIn("invitelink", self.client.get("/dashboard").text)


class Free(AppTestCase):
    env = {"BILLING_ENABLED": "0"}

    def test_no_card_when_everything_is_free(self):
        self.sign_in("hal@example.com")
        hal = self.main.db.get_user_by_email("hal@example.com")
        self.main.db.save_profile(hal["id"], {"name": "Hal", "location": "Leeds"})
        self.assertNotIn("invitelink", self.client.get("/dashboard").text)


class EndToEnd(Paid):
    def test_a_cv_upload_through_the_page_pays_both(self):
        hal = self.referrer()
        ivy = self.friend_arrives(hal)
        self.assertEqual(ivy["referred_by"], hal["id"])
        hal_before, ivy_before = self.paid_until(hal), self.paid_until(ivy)

        self.upload_cv()

        now = self.db.now()
        month = self.referrals.MONTH
        self.assertGreaterEqual(self.paid_until(hal) - max(hal_before, now),
                                month - 5)
        self.assertGreaterEqual(self.paid_until(ivy) - max(ivy_before, now),
                                month - 5)

    def test_a_sign_up_alone_pays_nobody(self):
        hal = self.referrer()
        ivy = self.friend_arrives(hal)
        self.assertEqual(self.paid_until(hal), 0)
        self.assertEqual(self.paid_until(ivy), 0)

    def test_nothing_pays_twice(self):
        hal = self.referrer()
        self.friend_arrives(hal)
        self.upload_cv()
        once = (self.paid_until(hal),
                self.paid_until(self.db.get_user_by_email("ivy@example.com")))
        self.upload_cv()
        self.assertEqual(once, (
            self.paid_until(hal),
            self.paid_until(self.db.get_user_by_email("ivy@example.com"))))

    def test_nobody_arriving_without_a_link_gets_a_welcome(self):
        self.sign_in("ivy@example.com")
        self.upload_cv()
        ivy = self.db.get_user_by_email("ivy@example.com")
        self.assertEqual(self.paid_until(ivy), 0)


if __name__ == "__main__":
    unittest.main()
