"""The listener publishes nothing to the sites it reads, so most of these
tests are about the things it must refuse to become.

Nothing here touches the network, SMTP, or the real clock. The fetcher,
the mailer, and the clock are all injected.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from growth import USER_AGENT  # noqa: E402
from growth.agents import listener  # noqa: E402

UK = ZoneInfo("Europe/London")


def ts(y, m, d, h=12, mi=0, *, tz=UK):
    """Epoch seconds for a UK-local wall clock reading."""
    return int(datetime(y, m, d, h, mi, tzinfo=tz).astimezone(timezone.utc)
               .timestamp())


class FakeResp:
    def __init__(self, data=None, *, status=200):
        self.status_code = status
        self._data = data if data is not None else {"data": {"children": []}}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


class Fake:
    """requests.get stand-in. Scripted per URL."""

    def __init__(self, pages=None, *, boom=()):
        self.pages = pages or {}
        self.boom = set(boom)
        self.calls = []

    def __call__(self, url, headers=None, timeout=None):
        self.calls.append((url, (headers or {}).get("User-Agent", ""),
                           timeout))
        if url in self.boom:
            raise ConnectionError("refused")
        resp = self.pages.get(url, FakeResp())
        return resp


def listing(subreddit, posts):
    url = (f"https://www.reddit.com/r/{subreddit}/new.json?limit=25")
    return url, FakeResp({"data": {"children": [{"data": p} for p in posts]}})


def one_source():
    return [{"kind": "reddit", "subreddit": "UKJobs",
             "listing": "new", "limit": 25}]


def default_kw():
    return {
        "high": ["no replies", "cold email"],
        "medium": ["job hunt"],
        "exclude": ["visa"],
    }


class IdentityTests(unittest.TestCase):
    """A crawler that does not say who it is is a nuisance. This checks that
    every outbound request carries the engine's named UA."""

    def test_user_agent_on_every_fetch(self):
        url, resp = listing("UKJobs", [])
        fake = Fake({url: resp})
        state, result = listener.run(
            {}, get=fake, sleep=lambda s: None,
            now=lambda: ts(2026, 10, 5, 12),
            sources=one_source(), keywords=default_kw(),
            mailer=lambda **_: False)
        self.assertEqual(len(fake.calls), 1)
        for _url, ua, _timeout in fake.calls:
            self.assertEqual(ua, USER_AGENT)


class PolitenessTests(unittest.TestCase):
    """The two-second gap before each fetch is the one visible promise this
    agent makes to a server about its appetite. It must happen first, not
    after, and must happen on every source."""

    def test_sleep_before_each_fetch(self):
        urls = {}
        for sub in ("UKJobs", "jobs"):
            u, r = listing(sub, [])
            urls[u] = r
        fake = Fake(urls)
        order = []

        def recorded_sleep(s):
            order.append(("sleep", s))
        wrapped = Fake(urls)
        orig = wrapped.__call__

        def tracked_get(u, headers=None, timeout=None):
            order.append(("fetch", u))
            return orig(u, headers=headers, timeout=timeout)

        listener.run(
            {}, get=tracked_get, sleep=recorded_sleep,
            now=lambda: ts(2026, 10, 5, 12),
            sources=[
                {"kind": "reddit", "subreddit": "UKJobs",
                 "listing": "new", "limit": 25},
                {"kind": "reddit", "subreddit": "jobs",
                 "listing": "new", "limit": 25},
            ],
            keywords=default_kw(),
            mailer=lambda **_: False)

        self.assertEqual(order[0], ("sleep", listener.POLITENESS_DELAY))
        fetches = [i for i, step in enumerate(order) if step[0] == "fetch"]
        for i in fetches:
            self.assertEqual(order[i - 1][0], "sleep")


class FailClosedTests(unittest.TestCase):
    """One flaky sub is a Tuesday; every source dark at once is damage, and
    the shape of a run that could not look is not the shape of one that
    looked and found nothing."""

    def test_every_source_failing_fails_closed(self):
        url_a, _ = listing("UKJobs", [])
        url_b, _ = listing("jobs", [])
        fake = Fake({}, boom=[url_a, url_b])
        before = {"seen_urls": ["kept"], "pending_queue": [{"keep": "me"}],
                  "last_email_at": 0}
        after, result = listener.run(
            dict(before), get=fake, sleep=lambda s: None,
            now=lambda: ts(2026, 10, 5, 12),
            sources=[
                {"kind": "reddit", "subreddit": "UKJobs",
                 "listing": "new", "limit": 25},
                {"kind": "reddit", "subreddit": "jobs",
                 "listing": "new", "limit": 25},
            ],
            keywords=default_kw(),
            mailer=lambda **_: False)
        self.assertFalse(result.ok)
        # Pending queue and seen set are preserved rather than cleared.
        self.assertEqual(after["pending_queue"], [{"keep": "me"}])
        self.assertEqual(after["seen_urls"], ["kept"])

    def test_one_source_failing_is_recorded_not_fatal(self):
        good_url, good = listing("UKJobs", [
            {"title": "ats black hole, 200 applications, no replies",
             "selftext": "cold email anyone?",
             "permalink": "/r/UKJobs/comments/x/t/", "stickied": False,
             "over_18": False},
        ])
        bad_url, _ = listing("jobs", [])
        fake = Fake({good_url: good}, boom=[bad_url])
        state, result = listener.run(
            {}, get=fake, sleep=lambda s: None,
            now=lambda: ts(2026, 10, 5, 12),
            sources=[
                {"kind": "reddit", "subreddit": "UKJobs",
                 "listing": "new", "limit": 25},
                {"kind": "reddit", "subreddit": "jobs",
                 "listing": "new", "limit": 25},
            ],
            keywords=default_kw(),
            mailer=lambda **_: False)
        self.assertTrue(result.ok)
        self.assertIn("jobs", state["source_errors"])
        self.assertEqual(len(state["pending_queue"]), 1)
        self.assertTrue(any("r/jobs:" in line for line in result.for_review))


class ShapeTests(unittest.TestCase):
    """The one thing the agent must not grow into: a thing that posts. Any
    public callable named draft_reply, post, comment, submit, reply, send_to,
    or publish fails this test."""

    def test_no_posting_functions_in_the_public_surface(self):
        forbidden = {"draft_reply", "post", "comment", "submit", "reply",
                     "send_to", "publish", "autopost"}
        public = {name for name in dir(listener) if not name.startswith("_")}
        offending = public & forbidden
        self.assertFalse(offending,
                         f"listener grew a posting surface: {offending}")

    def test_run_signature_matches_the_engine_contract(self):
        """One entrypoint called run, returning (state, Result)."""
        self.assertTrue(callable(listener.run))
        url, resp = listing("UKJobs", [])
        state, result = listener.run(
            {}, get=Fake({url: resp}), sleep=lambda s: None,
            now=lambda: ts(2026, 10, 5, 12),
            sources=one_source(), keywords=default_kw(),
            mailer=lambda **_: False)
        self.assertIsInstance(state, dict)
        self.assertTrue(hasattr(result, "ok"))
        self.assertTrue(hasattr(result, "note"))


class ScoringTests(unittest.TestCase):
    def test_excluded_terms_veto(self):
        post = {"title": "cold email to US company, need visa sponsorship",
                "excerpt": "no replies at all"}
        score, reasoning = listener._score(post, default_kw())
        self.assertEqual(score, 0.0)
        self.assertIn("visa", reasoning)

    def test_high_signal_scores_higher_than_medium(self):
        high = listener._score({"title": "cold email, no replies",
                                "excerpt": ""}, default_kw())[0]
        med = listener._score({"title": "job hunt is hard",
                               "excerpt": ""}, default_kw())[0]
        self.assertGreater(high, med)

    def test_below_threshold_is_seen_not_queued(self):
        url, resp = listing("UKJobs", [{
            "title": "something unrelated",
            "selftext": "", "permalink": "/r/UKJobs/comments/a/t/",
            "stickied": False, "over_18": False}])
        state, _ = listener.run(
            {}, get=Fake({url: resp}), sleep=lambda s: None,
            now=lambda: ts(2026, 10, 5, 12),
            sources=one_source(), keywords=default_kw(),
            mailer=lambda **_: False)
        self.assertEqual(state["pending_queue"], [])
        self.assertEqual(
            state["seen_urls"],
            ["https://www.reddit.com/r/UKJobs/comments/a/t/"])


class EmailWindowTests(unittest.TestCase):
    def _run(self, when, queue, last_email=0):
        state = {"seen_urls": [], "pending_queue": list(queue),
                 "last_email_at": last_email}
        sent = []

        def mailer(subject, body):
            sent.append((subject, body))
            return True

        url, resp = listing("UKJobs", [])
        new_state, result = listener.run(
            state, get=Fake({url: resp}), sleep=lambda s: None,
            now=lambda: when, sources=one_source(),
            keywords=default_kw(), mailer=mailer)
        return new_state, result, sent

    def _queue(self):
        return [{"source": "r/UKJobs", "url": "u", "thread_title": "t",
                 "thread_excerpt": "x", "relevance": 0.5,
                 "relevance_reasoning": "", "discovered_at": ""}]

    def test_emails_on_friday_18_uk_bst(self):
        # BST runs Mar-Oct; 2026-10-02 18:00 UK = 17:00 UTC.
        state, _, sent = self._run(ts(2026, 10, 2, 18),
                                   self._queue(), last_email=0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(state["pending_queue"], [])
        self.assertGreater(state["last_email_at"], 0)

    def test_emails_on_friday_18_uk_gmt(self):
        # 2026-12-04 18:00 UK = 18:00 UTC in winter.
        state, _, sent = self._run(ts(2026, 12, 4, 18),
                                   self._queue(), last_email=0)
        self.assertEqual(len(sent), 1)

    def test_does_not_email_outside_window(self):
        _, _, sent = self._run(ts(2026, 10, 1, 18),  # Thursday
                               self._queue(), last_email=0)
        self.assertEqual(sent, [])

    def test_does_not_email_twice_in_the_same_week(self):
        last = ts(2026, 10, 2, 18)
        _, _, sent = self._run(ts(2026, 10, 2, 18, 30),
                               self._queue(), last_email=last)
        self.assertEqual(sent, [])

    def test_does_not_email_when_queue_is_empty(self):
        _, _, sent = self._run(ts(2026, 10, 2, 18), [], last_email=0)
        self.assertEqual(sent, [])

    def test_unconfigured_mailer_leaves_queue_intact(self):
        state = {"seen_urls": [], "pending_queue": self._queue(),
                 "last_email_at": 0}
        url, resp = listing("UKJobs", [])
        new_state, result = listener.run(
            state, get=Fake({url: resp}), sleep=lambda s: None,
            now=lambda: ts(2026, 10, 2, 18),
            sources=one_source(), keywords=default_kw(),
            mailer=lambda **_: False)
        self.assertEqual(len(new_state["pending_queue"]), 1)
        self.assertTrue(any("no mailer" in line
                            for line in result.for_review))


class IdempotenceTests(unittest.TestCase):
    def test_running_twice_adds_nothing_new(self):
        post = {"title": "no replies after 200 applications",
                "selftext": "cold email anyone?",
                "permalink": "/r/UKJobs/comments/x/t/",
                "stickied": False, "over_18": False}
        url, resp = listing("UKJobs", [post])
        fake = Fake({url: resp})
        now = lambda: ts(2026, 10, 1, 12)   # Thursday noon
        s1, _ = listener.run({}, get=fake, sleep=lambda s: None, now=now,
                             sources=one_source(), keywords=default_kw(),
                             mailer=lambda **_: False)
        self.assertEqual(len(s1["pending_queue"]), 1)
        s2, r2 = listener.run(s1, get=fake, sleep=lambda s: None, now=now,
                              sources=one_source(), keywords=default_kw(),
                              mailer=lambda **_: False)
        self.assertEqual(len(s2["pending_queue"]), 1)
        self.assertEqual(r2.counts["found"], 0)


if __name__ == "__main__":
    unittest.main()
