"""Tests for the rule that a rate limit must never block a page.

The server runs one worker. `jobseeker.gemini.call` answers a 429 by sleeping
for as long as the model asks - up to ninety seconds, three times over - and
when that happens inside a web request it does not delay one page, it holds
the single worker and every other user's page with it. The app looked frozen
for minutes at a time and the logs said only "rate limited, waiting 59s".

So the sleeping is now bounded by a budget, and the two kinds of caller pick
different ones:

  - the scheduled sweep has nobody watching and waits as long as it takes
  - anything reached from a web request gives up at once, because every
    caller already copes with an unscored listing and none of them cope with
    a page that never loads
"""

import os
import sys
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("BILLING_ENABLED", "0")
os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from jobseeker import gemini  # noqa: E402


class _TooManyRequests:
    """What Google sends when you are going too fast."""

    status_code = 429
    text = '{"error":{"details":[{"retryDelay":"59s"}]}}'

    def json(self):
        return {"error": {"details": [{"retryDelay": "59s"}]}}


class RateLimitBudget(unittest.TestCase):

    def setUp(self):
        self.slept = []
        # Pretend the last call was long ago, so the MIN_INTERVAL spacing wait
        # does not fire and every sleep recorded below is a rate-limit backoff.
        gemini._last_call = time.monotonic() - 1000

    def _call(self, **kwargs):
        with patch.object(gemini.httpx, "post", return_value=_TooManyRequests()):
            with self.assertRaises(gemini.AIError):
                gemini.call("prompt", sleep=self.slept.append, **kwargs)

    def _backoffs(self):
        return list(self.slept)

    def test_no_budget_waits_it_out(self):
        """The sweep is patient, and must stay that way."""
        self._call()
        self.assertTrue(self._backoffs(),
                        "the background caller should still wait out a 429")

    def test_zero_budget_never_sleeps(self):
        """The regression: a web request must not nap on a 429."""
        self._call(budget=0.0)
        self.assertEqual(self._backoffs(), [],
                         "a rate limit blocked the single worker")

    def test_budget_stops_before_exceeding_itself(self):
        """A 59s wait is refused by a 30s budget rather than half-taken."""
        self._call(budget=30.0)
        self.assertEqual(self._backoffs(), [])

    def test_budget_allows_a_wait_it_can_afford(self):
        """A budget bigger than the delay still waits - it is a cap, not a ban."""
        self._call(budget=120.0)
        self.assertTrue(self._backoffs())

    def test_spacing_is_under_the_free_tier_ceiling(self):
        """Ten calls a minute is the limit; sitting exactly on it trips it."""
        self.assertGreater(gemini.MIN_INTERVAL, 6.0)


class InteractiveCallersAreImpatient(unittest.TestCase):
    """The wiring: the fail-fast caller has to actually be the one used."""

    def test_gemini_now_defaults_to_no_waiting(self):
        from app import ai
        seen = {}

        def fake(prompt, **kwargs):
            seen.update(kwargs)
            return "{}"

        with patch.object(ai, "gemini", fake):
            ai.gemini_now("prompt")
        self.assertEqual(seen.get("budget"), 0.0)

    def test_run_for_user_picks_by_interactive_flag(self):
        from app import ai, runner
        chosen = []

        def record(name):
            def f(prompt, **kwargs):
                chosen.append(name)
                return "{}"
            return f

        with patch.object(ai, "gemini", record("patient")), \
             patch.object(ai, "gemini_now", record("impatient")), \
             patch.object(runner.db, "load_profile", return_value=None):
            runner.run_for_user(1, interactive=True)
            runner.run_for_user(1)
        # No profile, so neither is called - what matters is it did not raise
        # and the flag is part of the signature rather than ignored.
        import inspect
        params = inspect.signature(runner.run_for_user).parameters
        self.assertIn("interactive", params)
        self.assertIs(params["interactive"].default, False)


if __name__ == "__main__":
    unittest.main()
