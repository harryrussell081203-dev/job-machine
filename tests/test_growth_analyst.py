"""Analyst agent tests.

Fetch is injected. The agent writes nothing to disk except the committed
state file that `growth/run.py` persists for it, so these tests only need
to check what the state carries back from `run()`.
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from growth import USER_AGENT  # noqa: E402
from growth.agents import analyst  # noqa: E402

BASE = "https://example.test"
SECRET = "s3cret"


class FakeResp:
    def __init__(self, data=None, status=200, text=None):
        self.status_code = status
        self._data = data
        self.text = text if text is not None else json.dumps(data or {})

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class Fake:
    def __init__(self, resp=None, *, boom=False):
        self.resp = resp if resp is not None else FakeResp({})
        self.boom = boom
        self.calls = []

    def __call__(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {}), timeout))
        if self.boom:
            raise ConnectionError("refused")
        return self.resp


def metrics(sc=None, views=None, pages=None):
    return FakeResp({
        "generated_at": 1_700_000_000,
        "window_days": 7,
        "search_console": sc,
        "views": views or {},
        "pages": pages or [],
    })


class IdentityTests(unittest.TestCase):
    def test_sends_user_agent_and_bearer(self):
        fake = Fake(metrics())
        analyst.run({}, base=BASE, get=fake, now=lambda: 1_700_000_000,
                    secret=SECRET)
        self.assertEqual(len(fake.calls), 1)
        _url, headers, _timeout = fake.calls[0]
        self.assertEqual(headers.get("User-Agent"), USER_AGENT)
        self.assertEqual(headers.get("Authorization"), f"Bearer {SECRET}")

    def test_fetches_the_metrics_endpoint_with_the_window(self):
        fake = Fake(metrics())
        analyst.run({}, base=BASE, get=fake, now=lambda: 1_700_000_000,
                    secret=SECRET, window_days=14)
        self.assertEqual(
            fake.calls[0][0],
            f"{BASE}/growth/metrics.json?window_days=14")


class FailClosedTests(unittest.TestCase):
    """A report that could not look is not the same shape as a quiet week.
    Every failure path preserves the previous state."""

    def test_missing_secret(self):
        before = {"report": {"ai_answer": {"total": 42, "pages": []}}}
        after, result = analyst.run(dict(before), base=BASE, get=Fake(),
                                    now=lambda: 1, secret="")
        self.assertFalse(result.ok)
        self.assertEqual(after, before)

    def test_connection_error(self):
        before = {"report": {"ai_answer": {"total": 42, "pages": []}}}
        after, result = analyst.run(dict(before), base=BASE,
                                    get=Fake(boom=True), now=lambda: 1,
                                    secret=SECRET)
        self.assertFalse(result.ok)
        self.assertEqual(after, before)

    def test_non_200(self):
        before = {"report": {"ai_answer": {"total": 42, "pages": []}}}
        after, result = analyst.run(dict(before), base=BASE,
                                    get=Fake(FakeResp({}, status=401)),
                                    now=lambda: 1, secret=SECRET)
        self.assertFalse(result.ok)
        self.assertEqual(after, before)

    def test_unparsable_json(self):
        before = {"report": {"ai_answer": {"total": 42, "pages": []}}}
        after, result = analyst.run(
            dict(before), base=BASE,
            get=Fake(FakeResp(None, text="not json")),
            now=lambda: 1, secret=SECRET)
        self.assertFalse(result.ok)
        self.assertEqual(after, before)


class AiAnswerSummaryTests(unittest.TestCase):
    """The agent's job: find the AI-answer crawler rows, group by page,
    rank by total, keep the per-operator breakdown."""

    def _views(self):
        return {
            "crawler_pages": {
                "ai-answer:openai": {"/": 10, "/playbook": 4},
                "ai-answer:anthropic": {"/": 6, "/answers/why-no-reply": 2},
                "ai-train:openai": {"/": 50},        # training, ignored
                "search:google": {"/": 100},         # search, ignored
                "other:ahrefs": {"/": 5},
            },
            "crawlers": {"ai-answer:openai": 14,
                         "ai-answer:anthropic": 8},
            "people": {},
            "robots": {},
        }

    def test_ranks_pages_by_ai_answer_hits(self):
        fake = Fake(metrics(views=self._views()))
        state, result = analyst.run({}, base=BASE, get=fake,
                                    now=lambda: 1_700_000_000,
                                    secret=SECRET)
        pages = state["report"]["ai_answer"]["pages"]
        self.assertEqual(pages[0]["path"], "/")
        self.assertEqual(pages[0]["total"], 16)
        self.assertEqual(pages[0]["operators"],
                         {"openai": 10, "anthropic": 6})
        self.assertEqual(result.counts["ai_answer_hits"], 22)

    def test_excludes_training_and_search_crawlers(self):
        fake = Fake(metrics(views=self._views()))
        state, _ = analyst.run({}, base=BASE, get=fake,
                               now=lambda: 1_700_000_000, secret=SECRET)
        totals = sum(p["total"]
                     for p in state["report"]["ai_answer"]["pages"])
        # Only the two ai-answer rows: 10+4+6+2 = 22. ai-train and search
        # must not have been counted.
        self.assertEqual(totals, 22)


class ZeroImpressionTests(unittest.TestCase):
    def test_lists_allowlist_pages_nothing_touched(self):
        views = {
            "people": {"/": 5},
            "robots": {"/answers/why-no-reply": 2},
            "crawlers": {},
            "crawler_pages": {},
        }
        pages = ["/", "/playbook", "/answers/why-no-reply", "/employers"]
        fake = Fake(metrics(views=views, pages=pages))
        state, _ = analyst.run({}, base=BASE, get=fake,
                               now=lambda: 1_700_000_000, secret=SECRET)
        self.assertEqual(
            state["report"]["zero_impression_pages"],
            ["/employers", "/playbook"])


class SearchConsoleSummaryTests(unittest.TestCase):
    def test_passes_search_console_summary_through(self):
        sc = {
            "from": "2026-09-01", "to": "2026-09-28",
            "nearly_there": [{"page": "/x", "position": 12.3}],
            "seen_not_clicked": [{"page": "/y"}],
            "rising": [], "falling": [],
            "two_pages_one_search": [],
            "questions_without_a_page": ["why do nobody reply"],
        }
        fake = Fake(metrics(sc=sc, views={}, pages=[]))
        state, _ = analyst.run({}, base=BASE, get=fake,
                               now=lambda: 1_700_000_000, secret=SECRET)
        summary = state["report"]["search_console"]
        self.assertEqual(summary["from"], "2026-09-01")
        self.assertEqual(summary["nearly_there"][0]["page"], "/x")
        self.assertEqual(summary["questions_without_a_page"],
                         ["why do nobody reply"])

    def test_missing_search_console_is_fine(self):
        fake = Fake(metrics(sc=None, views={}, pages=[]))
        state, result = analyst.run({}, base=BASE, get=fake,
                                    now=lambda: 1_700_000_000,
                                    secret=SECRET)
        self.assertTrue(result.ok)
        self.assertEqual(state["report"]["search_console"], {})


class NotesTests(unittest.TestCase):
    def test_zero_ai_answer_hits_is_flagged(self):
        fake = Fake(metrics(views={}, pages=["/"]))
        state, result = analyst.run({}, base=BASE, get=fake,
                                    now=lambda: 1_700_000_000,
                                    secret=SECRET)
        notes = state["report"]["notes"]
        self.assertTrue(any("no AI-answer crawlers" in n for n in notes))
        # for_review mirrors the notes so Actions-log readers see them.
        self.assertEqual(result.for_review, notes)

    def test_names_top_page_when_there_are_hits(self):
        views = {
            "crawler_pages": {"ai-answer:openai": {"/playbook": 10}},
        }
        fake = Fake(metrics(views=views, pages=["/playbook"]))
        state, _ = analyst.run({}, base=BASE, get=fake,
                               now=lambda: 1_700_000_000, secret=SECRET)
        notes = state["report"]["notes"]
        self.assertTrue(any("/playbook" in n for n in notes))


class HistoryTests(unittest.TestCase):
    def test_history_grows_and_is_bounded(self):
        state = {}
        fake = Fake(metrics(views={
            "crawler_pages": {"ai-answer:openai": {"/": 3}}}))
        for i in range(15):
            state, _ = analyst.run(state, base=BASE, get=fake,
                                   now=lambda i=i: 1_700_000_000 + i * 3600,
                                   secret=SECRET)
        self.assertLessEqual(len(state["history"]), 12)
        self.assertEqual(state["history"][-1]["ai_answer_total"], 3)


if __name__ == "__main__":
    unittest.main()
