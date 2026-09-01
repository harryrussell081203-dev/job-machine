"""Tests for the two dials and the friction removal.

The dials are the user's, but their ceilings are not. The form's `max`
attribute is advice to a browser; anything at all can POST to this route, so
the clamp on the server is the only thing that actually holds.

The friction removal is that connecting a mailbox now turns automatic sending
on. That is the whole product working without anybody opening it, and it is
also the one change here that sends mail somebody did not tick a box for, so
it gets the most tests.
"""

import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_app import AppTestCase  # noqa: E402

from cryptography.fernet import Fernet  # noqa: E402

# Storing a mail password needs a real key. Set here rather than relied on
# from whatever another test module happened to leave in os.environ, so these
# tests do not quietly depend on the order they run in.
VAULT_ENV = {"CREDENTIAL_KEY": Fernet.generate_key().decode()}

# A profile the machine will actually accept. It refuses one with no history,
# because a letter with no proof points is not worth sending.
PROFILE = {
    "name": "Sam", "email": "sam@example.com", "phone": "07000000000",
    "location": "Aberdeen", "target_roles": ["technician"],
    "locations": ["Aberdeen"], "min_salary_annual": 30000,
    "history": [{"title": "Technician", "org": "Northern Plant",
                 "detail": "Serviced 40 hydraulic rigs to LOLER standard."}],
}


class SendingSettings(AppTestCase):
    env = dict(VAULT_ENV)

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.user = self.main.db.get_or_create_user("sam@example.com")
        # Automatic sending needs a connected mailbox, and connecting one for
        # real would want an SMTP server.
        self.main.db.save_mail_account(self.user["id"], address="sam@example.com",
                                       host="smtp.example.com", port=465,
                                       password="app-password")

    def post(self, **fields):
        data = {"auto_send": "1"}
        data.update({k: str(v) for k, v in fields.items()})
        self.client.post("/setup/sending", data=data)
        return self.main.db.get_send_settings(self.user["id"])

    # -- the daily cap -------------------------------------------------
    def test_a_number_inside_the_range_is_kept(self):
        self.assertEqual(self.post(daily_cap=7)["daily_cap"], 7)

    def test_the_cap_is_enforced_on_the_server_not_the_form(self):
        # The form says max=25. This POST does not come from the form.
        cap = self.main.config.MAX_DAILY_CAP
        self.assertEqual(self.post(daily_cap=500)["daily_cap"], cap)
        self.assertEqual(self.post(daily_cap=26)["daily_cap"], cap)

    def test_zero_and_negative_become_one(self):
        # Zero would mean "on, but never sends", which looks like a bug to
        # whoever set it. Off is the checkbox.
        self.assertEqual(self.post(daily_cap=0)["daily_cap"], 1)
        self.assertEqual(self.post(daily_cap=-40)["daily_cap"], 1)

    def test_nonsense_falls_back_to_the_default(self):
        self.assertEqual(self.post(daily_cap="lots")["daily_cap"], 12)

    # -- how far back --------------------------------------------------
    def test_the_search_window_is_saved(self):
        self.assertEqual(self.post(search_days=7)["search_days"], 7)

    def test_the_window_cannot_reach_past_a_month(self):
        top = self.main.config.MAX_SEARCH_DAYS
        self.assertEqual(self.post(search_days=3650)["search_days"], top)

    def test_every_offered_window_survives_the_clamp(self):
        """Anything the form can offer must be a value the route accepts."""
        for days, _label in self.main.config.SEARCH_WINDOWS:
            self.assertEqual(self.post(search_days=days)["search_days"], days)

    def test_a_window_of_zero_days_would_find_nothing_so_is_refused(self):
        self.assertEqual(self.post(search_days=0)["search_days"], 1)


class TheSearchWindowReachesTheSearch(AppTestCase):
    """A setting that does not change what the machine does is decoration."""

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.user = self.main.db.get_or_create_user("sam@example.com")
        self.main.db.save_profile(self.user["id"], PROFILE)

    def hours_used(self, search_days=None):
        from app import runner
        if search_days is not None:
            self.main.db.save_send_settings(self.user["id"],
                                            search_days=search_days)
        seen = {}

        def fake_harvest(profile, creds, **kwargs):
            seen.update(kwargs)
            return {"keep": [], "dropped": []}

        with patch.object(runner.harvest, "harvest", fake_harvest):
            runner.run_for_user(self.user["id"])
        return seen.get("max_age_hours")

    def test_the_default_is_still_forty_eight_hours(self):
        self.assertEqual(self.hours_used(), 48)

    def test_a_week_asks_the_job_boards_for_a_week(self):
        self.assertEqual(self.hours_used(7), 168)

    def test_a_month_asks_for_a_month(self):
        self.assertEqual(self.hours_used(30), 720)


class ConnectingAMailboxTurnsSendingOn(AppTestCase):
    """The friction removal, and the ways it must not misfire."""
    env = dict(VAULT_ENV)

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.user = self.main.db.get_or_create_user("sam@example.com")

    def connect(self, address="sam@example.com"):
        with patch.object(self.main.delivery, "verify", lambda **kw: None):
            return self.client.post("/setup/mail", data={
                "address": address, "password": "app-password",
                "host": "smtp.example.com", "port": "465"})

    def settings(self):
        return self.main.db.get_send_settings(self.user["id"])

    def test_it_is_off_before_a_mailbox_is_connected(self):
        self.assertEqual(self.settings()["auto_send"], 0)

    def test_connecting_a_mailbox_turns_it_on(self):
        self.connect()
        self.assertEqual(self.settings()["auto_send"], 1)

    def test_reconnecting_does_not_override_a_deliberate_off(self):
        """The regression that matters.

        Somebody who turned sending off and later fixed their password must
        not have it silently turned back on. Only the first connection is
        read as consent; after that it is their setting.
        """
        self.connect()
        self.main.db.save_send_settings(self.user["id"], auto_send=0)
        self.connect()
        self.assertEqual(self.settings()["auto_send"], 0)

    def test_disconnecting_turns_it_off_again(self):
        self.connect()
        self.client.post("/setup/mail/forget")
        self.assertEqual(self.settings()["auto_send"], 0)

    def test_a_refused_password_connects_nothing_and_sends_nothing(self):
        def refuse(**kwargs):
            raise self.main.delivery.DeliveryError("the password was rejected")
        with patch.object(self.main.delivery, "verify", refuse):
            self.client.post("/setup/mail", data={
                "address": "sam@example.com", "password": "wrong",
                "host": "smtp.example.com", "port": "465"})
        self.assertEqual(self.settings()["auto_send"], 0)
        self.assertIsNone(self.main.db.get_mail_account(self.user["id"]))

    def test_the_holding_window_is_what_makes_this_safe(self):
        # On by default is only defensible because nothing goes out
        # immediately. If the default hold were ever zero, this test should
        # fail and somebody should have to think about it again.
        self.connect()
        self.assertGreaterEqual(self.settings()["hold_minutes"], 30)


if __name__ == "__main__":
    unittest.main()


class AddingTheColumnToADatabaseThatAlreadyExists(unittest.TestCase):
    """The migration, against the shape production is actually in.

    CREATE TABLE IF NOT EXISTS does nothing to a table that is already there,
    so without the ALTER this change reads fine in tests on a fresh database
    and breaks every existing customer's settings on deploy.
    """

    OLD_SCHEMA = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
        created_at BIGINT NOT NULL, last_seen_at BIGINT,
        stripe_customer_id TEXT, stripe_subscription_id TEXT,
        subscription_status TEXT NOT NULL DEFAULT 'none', paid_until BIGINT);
    CREATE TABLE send_settings (
        user_id INTEGER PRIMARY KEY, auto_send INTEGER NOT NULL DEFAULT 0,
        hold_minutes INTEGER NOT NULL DEFAULT 60,
        daily_cap INTEGER NOT NULL DEFAULT 20,
        paused_until BIGINT, updated_at BIGINT NOT NULL);
    INSERT INTO users (email, created_at) VALUES ('old@example.com', 1);
    INSERT INTO send_settings
        (user_id, auto_send, hold_minutes, daily_cap, updated_at)
        VALUES (1, 1, 180, 9, 1);
    """

    def setUp(self):
        import sqlite3
        import tempfile
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        conn = sqlite3.connect(self.path)
        conn.executescript(self.OLD_SCHEMA)
        conn.commit()
        conn.close()
        # Point the app at that database rather than a fresh one.
        from test_app import build_app
        self.main, throwaway = build_app()
        os.path.exists(throwaway) and os.unlink(throwaway)
        os.environ["DB_PATH"] = self.path
        import importlib
        for mod in ("app.config", "app.store", "app.db"):
            importlib.reload(sys.modules[mod])
        self.db = sys.modules["app.db"]
        self.db.init()

    def test_the_column_is_added(self):
        import sqlite3
        columns = [r[1] for r in sqlite3.connect(self.path)
                   .execute("PRAGMA table_info(send_settings)")]
        self.assertIn("search_days", columns)

    def test_settings_somebody_already_chose_are_not_touched(self):
        settings = self.db.get_send_settings(1)
        self.assertEqual(settings["auto_send"], 1)
        self.assertEqual(settings["hold_minutes"], 180)
        self.assertEqual(settings["daily_cap"], 9)

    def test_the_new_field_arrives_at_its_default(self):
        self.assertEqual(self.db.get_send_settings(1)["search_days"], 2)

    def test_the_new_field_can_then_be_written(self):
        self.db.save_send_settings(1, search_days=7)
        self.assertEqual(self.db.get_send_settings(1)["search_days"], 7)

    def test_running_it_twice_changes_nothing(self):
        """Every boot calls init(). The second one must be a no-op, not an
        error that takes the app down."""
        self.db.save_send_settings(1, search_days=14)
        self.db.init()
        self.db.init()
        self.assertEqual(self.db.get_send_settings(1)["search_days"], 14)
