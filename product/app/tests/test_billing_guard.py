"""Who has to have a Stripe account, and who does not.

The sweep failed its first real run on this, and the error was honest but the
rule behind it was wrong:

    RuntimeError: billing is on but STRIPE_SECRET_KEY, STRIPE_PRICE_ID,
    STRIPE_WEBHOOK_SECRET not set.

The guard ran at the bottom of config.py, so it fired for ANYTHING that
imported config. The sweep imports autosend, autosend imports config, and the
scheduled run died before it read a single user - on hardware whose whole job
is sending letters for people who have already paid.

The tempting fix was BILLING_ENABLED=0 on the sweep, and it would have been a
quiet disaster: is_paid() returns True for every account with the paywall off,
and paid_user_ids() returns every user, so the sweep would have written to
employers on behalf of people the website was still asking to pay.

So the split is by JOB, not by convenience: the website takes money and must
not start if it cannot honour a payment; the sweep spends none and needs no
payment credential. BILLING_ENABLED itself is not relaxed for either.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# Every variable these tests care about, set explicitly every time. The shared
# build_app() helper only ever SETS variables and never clears them, which has
# already produced one test that passed alone and failed in the suite. Anything
# left set by an earlier module would decide these tests instead of the test
# body doing it.
BASE = {
    "DEV_MODE": "0",
    "BILLING_ENABLED": "1",
    "SECRET_KEY": "test-secret-key-not-for-production",
    "STRIPE_SECRET_KEY": "",
    "STRIPE_PRICE_ID": "",
    "STRIPE_WEBHOOK_SECRET": "",
    "STRIPE_PAYMENT_LINK": "",
    "BASE_URL": "http://testserver",
}


class GuardTestCase(unittest.TestCase):
    def env(self, **overrides):
        """Apply BASE plus overrides, and put the environment back after."""
        before = {k: os.environ.get(k) for k in set(BASE) | set(overrides)}

        def restore():
            for key, value in before.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)

        values = dict(BASE)
        values.update(overrides)
        for key, value in values.items():
            os.environ[key] = value

    def config(self):
        import app.config
        return importlib.reload(app.config)


class TestTheWebsiteStillRefusesToStartMisconfigured(GuardTestCase):
    """The protection that matters, unchanged. A website that can take a
    payment it cannot honour must die at boot, not at the till."""

    def test_missing_stripe_keys_stop_the_app_importing(self):
        self.env()
        self.config()
        for mod in ("app.main",):
            sys.modules.pop(mod, None)
        with self.assertRaises(RuntimeError) as caught:
            importlib.import_module("app.main")
        self.assertIn("STRIPE_SECRET_KEY", str(caught.exception))
        sys.modules.pop("app.main", None)

    def test_the_message_still_names_all_three_ways_out(self):
        self.env()
        config = self.config()
        with self.assertRaises(RuntimeError) as caught:
            config.check_billing_config()
        message = str(caught.exception)
        self.assertIn("STRIPE_PAYMENT_LINK", message)
        self.assertIn("BILLING_ENABLED=0", message)

    def test_payment_link_mode_needs_only_the_webhook_secret(self):
        # Link mode makes no API calls, so no secret key. The webhook is still
        # the only thing that opens the app, so it stays mandatory.
        self.env(STRIPE_PAYMENT_LINK="https://buy.stripe.com/test")
        config = self.config()
        with self.assertRaises(RuntimeError) as caught:
            config.check_billing_config()
        self.assertIn("STRIPE_WEBHOOK_SECRET", str(caught.exception))

        self.env(STRIPE_PAYMENT_LINK="https://buy.stripe.com/test",
                 STRIPE_WEBHOOK_SECRET="whsec_test")
        self.config().check_billing_config()          # must not raise

    def test_dev_mode_is_exempt(self):
        self.env(DEV_MODE="1")
        self.config().check_billing_config()          # must not raise


class TestTheSweepDoesNotNeedAStripeAccount(GuardTestCase):
    """The actual bug. The sweep sends letters for people who have already
    paid; it never calls Stripe, so it must not need Stripe's credentials."""

    def test_importing_config_alone_does_not_raise(self):
        self.env()
        self.config()               # the reload IS the assertion

    def test_the_sweep_module_imports_with_billing_on_and_no_stripe(self):
        self.env()
        self.config()
        for mod in ("app.autosend", "app.sweep"):
            sys.modules.pop(mod, None)
        importlib.import_module("app.sweep")

    def test_billing_enabled_is_not_relaxed_by_any_of_this(self):
        # The failure this guards against: if the sweep ran with the paywall
        # off while the website had it on, every account would count as paid.
        self.env()
        self.assertTrue(self.config().BILLING_ENABLED)


class TestTheSweepSaysWhichSideOfThePaywallItIsOn(GuardTestCase):
    """Nothing makes GitHub's secrets and the host's dashboard agree, and a
    disagreement is silent in both directions. So it gets printed."""

    def _run_say(self):
        self.config()
        import app.sweep
        sweep = importlib.reload(app.sweep)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sweep._say_billing_mode()
        return out.getvalue()

    def test_paywall_on_is_reported(self):
        self.env()
        self.assertIn("paywall ON", self._run_say())

    def test_paywall_off_says_what_that_means_not_just_off(self):
        # "paywall OFF" beside a user count is only alarming if you know what
        # it does. The line has to say it.
        self.env(BILLING_ENABLED="0")
        printed = self._run_say()
        self.assertIn("OFF", printed)
        self.assertIn("counts as paid", printed)


if __name__ == "__main__":
    unittest.main()
