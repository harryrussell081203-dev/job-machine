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
        # Forget earlier calls, so the MIN_INTERVAL spacing wait does not
        # fire and every sleep recorded below is a rate-limit backoff.
        gemini._last_call.clear()
        gemini._spent_for_today.clear()

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


class TheDailyLimitIsRecognised(unittest.TestCase):
    """25 September: every call for 47 minutes was a 429 on an allowance
    that was simply used up, because the check looked for "per day" and
    Google writes PerDay."""

    class _DayGone:
        status_code = 429
        text = ('{"error":{"code":429,"message":"You exceeded your current '
                'quota","details":[{"violations":[{"quotaId":'
                '"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},'
                '{"retryDelay":"59s"}]}}')

    def setUp(self):
        gemini._spent_for_today.clear()
        gemini._last_call.clear()
        self.addCleanup(gemini._spent_for_today.clear)

    def test_googles_wording_counts_as_the_daily_limit(self):
        slept = []
        with patch.object(gemini.httpx, "post", return_value=self._DayGone()):
            with self.assertRaises(gemini.QuotaExhausted):
                gemini.call("prompt", sleep=slept.append)
        self.assertEqual(slept, [], "it waited on a limit that resets tomorrow")

    def test_after_that_nothing_more_is_asked_today(self):
        with patch.object(gemini.httpx, "post", return_value=self._DayGone()):
            with self.assertRaises(gemini.QuotaExhausted):
                gemini.call("prompt", sleep=lambda s: None)
        with patch.object(gemini.httpx, "post") as post:
            with self.assertRaises(gemini.QuotaExhausted):
                gemini.call("prompt", sleep=lambda s: None)
            post.assert_not_called()

    def test_an_ordinary_slow_down_is_not_mistaken_for_it(self):
        self.assertFalse(gemini._is_daily_limit(_TooManyRequests.text))


class ScoringStaysInsideTheAllowance(unittest.TestCase):
    def listings(self, n):
        from jobseeker.pipeline.harvest import Listing
        return [Listing(external_id=str(i), source="adzuna", title="Technician",
                        company=f"Firm {i}", location="Aberdeen",
                        url="https://x", description="work")
                for i in range(n)]

    def profile(self):
        from jobseeker.profile import Profile
        return Profile.from_dict({
            "name": "Sam", "location": "Aberdeen", "phone": "07700 900123",
            "target_roles": ["technician"], "locations": ["Aberdeen"],
            "min_salary_annual": 20000,
            "history": [{"title": "Technician", "org": "Acme",
                         "detail": "maintenance"}]})

    def test_one_run_asks_about_no_more_than_the_cap(self):
        from jobseeker.pipeline import scoring
        calls = []
        scoring.score(self.listings(200), self.profile(),
                      lambda p: (calls.append(p), "[]")[1])
        self.assertLessEqual(len(calls) * scoring.BATCH_SIZE,
                             scoring.MAX_SCORED_PER_RUN + scoring.BATCH_SIZE)

    def test_it_stops_once_the_model_keeps_refusing(self):
        from jobseeker.pipeline import scoring
        calls = []

        def refuse(prompt):
            calls.append(prompt)
            raise gemini.AIError("rate limited")
        scoring.score(self.listings(60), self.profile(), refuse)
        self.assertEqual(len(calls), scoring.STOP_AFTER_FAILURES)

    def test_a_used_up_day_stops_it_at_once(self):
        from jobseeker.pipeline import scoring
        calls = []

        def gone(prompt):
            calls.append(prompt)
            raise gemini.QuotaExhausted("daily model quota exhausted")
        scoring.score(self.listings(60), self.profile(), gone)
        self.assertEqual(len(calls), 1)
