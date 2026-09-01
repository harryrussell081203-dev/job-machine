"""Tests for the free launch places.

FREE_ACCESS_EMAILS names people you already know. These are places rather
than invitations: the first N accounts to exist take them, whoever they turn
out to be, and then the paywall applies to everybody after.

Everything here is about money, so the tests are about the ways it could be
given away by accident: more places than exist, a place taken twice by the
same person, a place taken by somebody signing in for the second time, and a
place quietly withdrawn from somebody who already has one.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_app import AppTestCase, build_app  # noqa: E402

PAID = {"BILLING_ENABLED": "1", "DEV_MODE": "1",
        "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_PRICE_ID": "price_x",
        "STRIPE_WEBHOOK_SECRET": "whsec_test"}


class ThreePlaces(AppTestCase):
    env = dict(PAID, FREE_SPOTS="3")

    def test_the_first_three_get_in_free(self):
        for who in ("a@example.com", "b@example.com", "c@example.com"):
            self.client.cookies.clear()
            self.sign_in(who)
            self.assertNotIn("Subscribe to start",
                             self.client.get("/dashboard").text, who)

    def test_the_fourth_has_to_pay(self):
        for who in ("a@example.com", "b@example.com", "c@example.com"):
            self.client.cookies.clear()
            self.sign_in(who)
        self.client.cookies.clear()
        self.sign_in("d@example.com")
        self.assertIn("Subscribe to start", self.client.get("/dashboard").text)

    def test_signing_in_again_does_not_take_a_second_place(self):
        for _ in range(4):
            self.client.cookies.clear()
            self.sign_in("a@example.com")
        self.assertEqual(self.main.db.free_spots_taken(), 1)

    def test_an_existing_account_cannot_take_a_place_later(self):
        """Somebody who already decided not to pay must not be handed one."""
        self.sign_in("early@example.com")
        self.main.db.claim_free_spot(1)          # pretend they had one
        with_spot = self.main.db.free_spots_taken()
        self.client.cookies.clear()
        self.sign_in("early@example.com")        # signing in again
        self.assertEqual(self.main.db.free_spots_taken(), with_spot)

    def test_the_count_left_falls_as_they_go(self):
        self.assertEqual(self.main.db.free_spots_left(), 3)
        self.sign_in("a@example.com")
        self.assertEqual(self.main.db.free_spots_left(), 2)

    def test_it_never_goes_negative(self):
        for who in "abcde":
            self.client.cookies.clear()
            self.sign_in(f"{who}@example.com")
        self.assertEqual(self.main.db.free_spots_left(), 0)
        self.assertEqual(self.main.db.free_spots_taken(), 3)

    def test_a_place_survives_the_offer_being_withdrawn(self):
        """Turning FREE_SPOTS down must not evict somebody who has a place.

        Taking back what was already given is not something a config change
        should be able to do quietly.
        """
        self.sign_in("a@example.com")
        user = self.main.db.get_or_create_user("a@example.com")
        self.assertTrue(self.main.db.is_paid(user))
        self.main.config.FREE_SPOTS = 0
        user = self.main.db.get_user(user["id"])
        self.assertTrue(self.main.db.is_paid(user),
                        "somebody was evicted from a place they already had")


class NoPlacesOffered(AppTestCase):
    """The default. Nothing is given away unless somebody said to."""
    env = dict(PAID)

    def test_the_first_signup_hits_the_paywall(self):
        self.sign_in("a@example.com")
        self.assertIn("Subscribe to start", self.client.get("/dashboard").text)

    def test_claiming_returns_false_rather_than_granting(self):
        self.sign_in("a@example.com")
        self.assertFalse(self.main.db.claim_free_spot(1))
        self.assertEqual(self.main.db.free_spots_taken(), 0)

    def test_the_landing_page_offers_nothing(self):
        self.assertNotIn("free place", self.client.get("/").text)


class OnePlaceLeftReadsProperly(AppTestCase):
    env = dict(PAID, FREE_SPOTS="1")

    def test_the_landing_page_says_one_place_not_one_places(self):
        page = self.client.get("/").text
        self.assertIn("1 free place left", page)
        self.assertNotIn("1 free places", page)

    def test_the_offer_disappears_once_it_is_gone(self):
        self.sign_in("a@example.com")
        self.client.cookies.clear()
        self.client.post("/logout")
        page = self.client.get("/").text
        self.assertNotIn("free place", page)
        self.assertIn("Start", page)


class ThePlacesAreSeparateFromTheNamedList(AppTestCase):
    """Being on FREE_ACCESS_EMAILS must not consume a place."""
    env = dict(PAID, FREE_SPOTS="2",
               FREE_ACCESS_EMAILS="friend@example.com")

    def test_a_named_friend_does_not_use_up_a_place(self):
        self.sign_in("friend@example.com")
        # They get in either way, but the places are for strangers.
        self.assertNotIn("Subscribe to start",
                         self.client.get("/dashboard").text)
        self.assertEqual(self.main.db.free_spots_left(), 1,
                         "a named friend consumed a launch place")


class TheColumnOnADatabaseThatAlreadyExists(unittest.TestCase):
    """users predates free_spot, so the migration has to add it."""

    OLD_SCHEMA = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
        created_at BIGINT NOT NULL, last_seen_at BIGINT,
        stripe_customer_id TEXT, stripe_subscription_id TEXT,
        subscription_status TEXT NOT NULL DEFAULT 'none', paid_until BIGINT);
    INSERT INTO users (email, created_at, subscription_status)
        VALUES ('paying@example.com', 1, 'active');
    """

    def setUp(self):
        import importlib
        import sqlite3
        import tempfile
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        conn = sqlite3.connect(self.path)
        conn.executescript(self.OLD_SCHEMA)
        conn.commit()
        conn.close()
        _main, throwaway = build_app(**dict(PAID, FREE_SPOTS="3"))
        os.path.exists(throwaway) and os.unlink(throwaway)
        os.environ["DB_PATH"] = self.path
        for mod in ("app.config", "app.store", "app.db"):
            importlib.reload(sys.modules[mod])
        self.db = sys.modules["app.db"]
        self.db.init()

    def test_the_column_is_added(self):
        import sqlite3
        columns = [r[1] for r in sqlite3.connect(self.path)
                   .execute("PRAGMA table_info(users)")]
        self.assertIn("free_spot", columns)

    def test_an_existing_paying_customer_is_untouched(self):
        user = self.db.get_user(1)
        self.assertEqual(user["subscription_status"], "active")
        self.assertTrue(self.db.is_paid(user))

    def test_nobody_arrives_holding_a_place(self):
        self.assertEqual(self.db.free_spots_taken(), 0)


if __name__ == "__main__":
    unittest.main()
