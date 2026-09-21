"""What a machine gets, and the difference between checking and finding.

The agent publishes nothing, so there is only one way it can do damage: by
reporting that all is well when it has not looked. A run that fetched nothing
and a run that found nothing wrong produce the same reassuring number, and
most of these tests are about keeping those two apart.

Nothing here touches the network. The fetcher is injected.
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from growth.agents import crawl_health  # noqa: E402

BASE = "https://example.test"

SITEMAP = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'<url><loc>{BASE}/</loc></url>'
           f'<url><loc>{BASE}/answers/why-no-reply</loc></url>'
           '</urlset>')


def page(canonical, *, ld='{"@type":"FAQPage"}', body="x" * 800):
    return (f'<html><head><link rel="canonical" href="{canonical}">'
            f'<script type="application/ld+json">{ld}</script>'
            f'</head><body>{body}</body></html>')


class Fake:
    """Stands in for requests.get. Slow pages and failures are scripted."""

    def __init__(self, pages, *, seconds=None, boom=()):
        self.pages, self.seconds, self.boom = pages, seconds or {}, set(boom)
        self.asked = []
        self.clock = [0.0]

    def __call__(self, url, headers=None, timeout=None):
        self.asked.append((url, (headers or {}).get("User-Agent", "")))
        self.clock[0] += self.seconds.get(url, 0.1)
        if url in self.boom:
            raise ConnectionError("refused")
        body = self.pages.get(url)
        code = 200 if body is not None else 404
        return type("R", (), {"status_code": code, "text": body or ""})()


def clock_of(fake):
    return lambda: fake.clock[0]


def good_site(**over):
    pages = {f"{BASE}/sitemap.xml": SITEMAP,
             f"{BASE}/": page(f"{BASE}/"),
             f"{BASE}/answers/why-no-reply": page(f"{BASE}/answers/why-no-reply")}
    pages.update(over)
    return pages


class TestItLooksLikeAMachine(unittest.TestCase):
    def test_it_identifies_itself_honestly_with_somewhere_to_complain(self):
        """A crawler that will not say who it is, is a nuisance."""
        get = Fake(good_site())
        crawl_health.run({}, base=BASE, get=get, now=lambda: 0)
        agents = {ua for _, ua in get.asked}
        self.assertTrue(all("RecruitedGrowthBot" in ua for ua in agents), agents)
        self.assertTrue(all("http" in ua for ua in agents), agents)

    def test_a_healthy_site_reports_no_problems(self):
        state, result = crawl_health.run({}, base=BASE, get=Fake(good_site()),
                                         now=lambda: 1000)
        self.assertTrue(result.ok)
        self.assertEqual(result.counts["broken"], 0)
        self.assertEqual(result.counts["urls"], 2)
        self.assertEqual(result.for_review, [])
        self.assertEqual(state["checked_at"], 1000)


class TestCheckingIsNotTheSameAsFindingNothing(unittest.TestCase):
    def test_an_unreachable_sitemap_fails_closed(self):
        """The failure this agent exists not to commit. Zero problems found
        and zero pages checked are the same number on a dashboard."""
        get = Fake({}, boom=[f"{BASE}/sitemap.xml"])
        state, result = crawl_health.run({"pages": {"old": 1}}, base=BASE,
                                         get=get, now=lambda: 0)
        self.assertFalse(result.ok)
        self.assertIn("sitemap", result.note)
        self.assertEqual(state, {"pages": {"old": 1}}, "state was modified")

    def test_a_sitemap_that_does_not_parse_fails_closed(self):
        get = Fake({f"{BASE}/sitemap.xml": "<urlset><broken"})
        _, result = crawl_health.run({}, base=BASE, get=get, now=lambda: 0)
        self.assertFalse(result.ok)

    def test_an_empty_sitemap_fails_closed(self):
        empty = ('<?xml version="1.0"?><urlset '
                 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>')
        get = Fake({f"{BASE}/sitemap.xml": empty})
        _, result = crawl_health.run({}, base=BASE, get=get, now=lambda: 0)
        self.assertFalse(result.ok)


class TestWhatItCatches(unittest.TestCase):
    def problems(self, pages):
        _, result = crawl_health.run({}, base=BASE, get=Fake(pages),
                                     now=lambda: 0)
        return " ".join(result.for_review)

    def test_a_url_in_the_sitemap_that_404s(self):
        pages = good_site()
        del pages[f"{BASE}/answers/why-no-reply"]
        self.assertIn("status 404", self.problems(pages))

    def test_structured_data_that_does_not_parse(self):
        """Markup nothing can read is markup nothing acts on, and a
        substring check would never notice."""
        pages = good_site(**{f"{BASE}/": page(f"{BASE}/", ld='{"@type":}')})
        self.assertIn("does not parse", self.problems(pages))

    def test_a_canonical_pointing_somewhere_else(self):
        """A page asking not to be indexed."""
        pages = good_site(**{f"{BASE}/": page("https://elsewhere.test/")})
        self.assertIn("canonical points at", self.problems(pages))

    def test_a_missing_canonical(self):
        pages = good_site(**{f"{BASE}/":
                             '<html><body>' + "x" * 800 + '</body></html>'})
        self.assertIn("no canonical", self.problems(pages))

    def test_a_page_that_is_nearly_empty(self):
        """The shape of a page that assembles itself in a browser. An
        assistant fetching it gets nothing to quote."""
        pages = good_site(**{f"{BASE}/": page(f"{BASE}/", body="loading")})
        self.assertIn("is this rendering", self.problems(pages))

    def test_a_query_string_is_not_a_canonical_mismatch(self):
        """Dropping the query is the entire job of the tag, so a sitemap URL
        carrying one must not be reported as disagreeing with itself."""
        sitemap = SITEMAP.replace(f"{BASE}/answers/why-no-reply",
                                  f"{BASE}/answers/why-no-reply?utm_source=x")
        pages = good_site(**{f"{BASE}/sitemap.xml": sitemap,
                             f"{BASE}/answers/why-no-reply?utm_source=x":
                             page(f"{BASE}/answers/why-no-reply")})
        self.assertNotIn("canonical", self.problems(pages))


class TestTheColdStart(unittest.TestCase):
    def test_a_slow_page_is_reported_even_though_it_worked(self):
        """The free instance sleeps after fifteen minutes and takes most of a
        minute to wake. A crawler that times out records that against the
        domain, so a page can succeed and still be a problem."""
        get = Fake(good_site(), seconds={f"{BASE}/": 42.0})
        _, result = crawl_health.run({}, base=BASE, get=get, now=lambda: 0,
                                     clock=clock_of(get))
        self.assertIn("slow", " ".join(result.for_review))
        self.assertGreater(result.counts["slowest"], 40)


class TestHistory(unittest.TestCase):
    def test_it_keeps_a_trail_so_new_breakage_is_distinguishable(self):
        """A page broken for a month and one broken this morning need
        different responses, and a single snapshot cannot tell them apart."""
        state = {}
        for stamp in (1, 2, 3):
            state, _ = crawl_health.run(state, base=BASE, get=Fake(good_site()),
                                        now=lambda s=stamp: s)
        self.assertEqual([h["at"] for h in state["history"]], [1, 2, 3])

    def test_the_trail_does_not_grow_without_end(self):
        state = {}
        for stamp in range(40):
            state, _ = crawl_health.run(state, base=BASE, get=Fake(good_site()),
                                        now=lambda s=stamp: s)
        self.assertLessEqual(len(state["history"]), 30)


class TestTheStateIsCommittable(unittest.TestCase):
    def test_it_is_json_and_sorts_stably(self):
        """These files are read as diffs by a person. An unordered dump is a
        diff where every line moved and none of them changed."""
        state, _ = crawl_health.run({}, base=BASE, get=Fake(good_site()),
                                    now=lambda: 0)
        first = json.dumps(state, indent=1, sort_keys=True)
        second = json.dumps(json.loads(first), indent=1, sort_keys=True)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
