"""Counting the people who never sign up.

The whole point of this table is to answer one question honestly - did anybody
land - so the tests are mostly about the ways a view counter lies:

  - by counting robots as an audience
  - by counting one person twice
  - by breaking the page it is supposed to be measuring
"""

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_app import AppTestCase  # noqa: E402

BROWSER = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
           "Mobile/15E148 Safari/604.1")


class Base(AppTestCase):
    def setUp(self):
        super().setUp()
        import app.views as views
        self.views = views

    def see(self, path="/", **kw):
        kw.setdefault("user_agent", BROWSER)
        self.views.record(path, **kw)

    def counts(self, hours=24):
        return self.views.totals(since=time.time() - hours * 3600)


class TestTellingPeopleFromRobots(Base):
    def test_a_phone_browser_is_a_person(self):
        self.see("/")
        self.assertEqual(self.counts()["people"].get("/"), 1)
        self.assertEqual(self.counts()["robots"], {})

    def test_a_crawler_is_not_an_audience(self):
        """The failure this exists to prevent: forty views, all of them
        robots, reading on the page exactly like forty readers."""
        self.see("/", user_agent="Mozilla/5.0 (compatible; Googlebot/2.1)")
        got = self.counts()
        self.assertEqual(got["people"], {})
        self.assertEqual(got["robots"].get("/"), 1)
        self.assertEqual(got["total"], 0, "a robot must not reach the headline")

    def test_the_link_previews_that_run_on_every_share(self):
        """Post the link anywhere and these fetch it before a human sees it.
        On a launch day they are the majority of all traffic."""
        for agent in ("facebookexternalhit/1.1",
                      "WhatsApp/2.23.20.0",
                      "Slackbot-LinkExpanding 1.0",
                      "Twitterbot/1.0",
                      "TelegramBot (like TwitterBot)",
                      "Snapchat/12.0 (link preview)"):
            with self.subTest(agent=agent):
                self.assertTrue(self.views.looks_like_a_robot(agent), agent)

    def test_our_own_uptime_pinger_is_not_a_visitor(self):
        """It calls every ten minutes, all day. Counted as people it would
        invent about 100 views a day out of nothing."""
        self.assertTrue(self.views.looks_like_a_robot("pg_net/0.20.4"))

    def test_no_user_agent_at_all_is_a_robot(self):
        """Every real browser sends one. A scraper that sets none is the
        commonest sort there is, so the absence is the signal."""
        self.assertTrue(self.views.looks_like_a_robot(""))
        self.assertTrue(self.views.looks_like_a_robot(None))

    def test_ordinary_browsers_are_not_swept_up(self):
        """The substring list is crude on purpose, which makes a false
        positive the thing to guard: marking real people as robots would
        report an empty site on a busy day."""
        for agent in (
            BROWSER,
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.6 Safari/605.1.15",
        ):
            with self.subTest(agent=agent[:40]):
                self.assertFalse(self.views.looks_like_a_robot(agent))


class TestWhereTheyCameFrom(Base):
    def test_an_app_tap_has_no_referrer_and_says_so(self):
        """Snapchat and Instagram strip it. This is the commonest arrival
        there is for this product and it must not look like an error."""
        self.see("/", referer="", host="recruited.org.uk")
        self.assertEqual(self.counts()["sources"], {"direct or an app": 1})

    def test_moving_around_the_site_is_not_a_new_arrival(self):
        """Somebody reading four pages is one visit. Counting the internal
        hops as sources would report the site as its own best marketing
        channel."""
        self.see("/find", referer="https://recruited.org.uk/",
                 host="recruited.org.uk")
        self.assertNotIn("recruited.org.uk", self.counts()["sources"])

    def test_www_is_the_same_site(self):
        self.assertEqual(
            self.views.source_of("https://www.recruited.org.uk/x",
                                 "recruited.org.uk"), "self")

    def test_a_real_link_elsewhere_is_named(self):
        self.assertEqual(
            self.views.source_of("https://l.instagram.com/?u=x",
                                 "recruited.org.uk"), "l.instagram.com")

    def test_only_the_host_is_kept(self):
        """A referring URL can carry a search query or a private page title.
        None of that is this product's business, and storing it would turn a
        tally into a record of what people were reading."""
        source = self.views.source_of(
            "https://www.google.com/search?q=how+to+get+a+job+after+prison",
            "recruited.org.uk")
        self.assertEqual(source, "google.com")

    def test_rubbish_in_the_header_does_not_raise(self):
        for junk in ("", "not a url", "://", "javascript:alert(1)", None):
            with self.subTest(junk=junk):
                self.views.source_of(junk, "recruited.org.uk")


class TestCountingHonestly(Base):
    def test_views_add_up_across_an_hour(self):
        for _ in range(5):
            self.see("/")
        self.assertEqual(self.counts()["people"]["/"], 5)

    def test_pages_are_counted_apart(self):
        self.see("/")
        self.see("/find")
        self.see("/find")
        people = self.counts()["people"]
        self.assertEqual(people["/"], 1)
        self.assertEqual(people["/find"], 2)

    def test_an_old_view_falls_outside_the_window(self):
        self.see("/", when=time.time() - 40 * 3600)
        self.assertEqual(self.counts(hours=24)["total"], 0)
        self.assertEqual(self.counts(hours=72)["total"], 1)

    def test_every_hour_appears_even_the_empty_ones(self):
        """A chart that omits quiet hours compresses a dead day into
        something that looks busy."""
        self.see("/")
        hours = self.views.by_hour(hours=24)
        self.assertEqual(len(hours), 24)
        self.assertEqual(sum(h["count"] for h in hours), 1)
        self.assertEqual(hours[-1]["count"], 1, "newest hour is last")

    def test_the_hourly_chart_is_people_only(self):
        self.see("/", user_agent="Googlebot/2.1")
        self.assertEqual(sum(h["count"] for h in self.views.by_hour()), 0)

    def test_a_long_path_cannot_blow_up_the_key(self):
        self.see("/" + "x" * 500)
        self.assertTrue(all(len(p) <= self.views.MAX_PATH
                            for p in self.counts()["people"]))


class TestItNeverBreaksThePage(Base):
    def test_a_broken_database_does_not_stop_a_view_being_served(self):
        """Same rule as the progress bar: this describes the work, so it may
        never break it. A page that cannot be counted still renders."""
        def explode(*a, **kw):
            raise RuntimeError("database is gone")

        real, self.views.connect = self.views.connect, explode
        try:
            self.views.record("/")            # must not raise
            self.assertEqual(self.views.totals(since=0)["total"], 0)
            self.assertEqual(len(self.views.by_hour(hours=6)), 6)
        finally:
            self.views.connect = real

    def test_the_landing_page_still_renders_when_counting_fails(self):
        def explode(*a, **kw):
            raise RuntimeError("no")

        real, self.views.record = self.views.record, explode
        try:
            self.assertEqual(self.client.get("/").status_code, 200)
        finally:
            self.views.record = real


class TestThroughTheApp(Base):
    """What actually gets counted when somebody uses the site."""

    def test_opening_the_front_page_is_counted(self):
        self.client.get("/", headers={"user-agent": BROWSER})
        self.assertEqual(self.counts()["people"].get("/"), 1)

    def test_pasting_an_advert_is_not_a_second_arrival(self):
        """/find answers a POST through the same render(), so counting those
        would make one person who used the tool look like two visitors -
        inflating the exact number this exists to measure honestly."""
        self.client.get("/find", headers={"user-agent": BROWSER})
        self.client.post("/find", data={"advert": "email jobs@acme.com"},
                         headers={"user-agent": BROWSER})
        self.assertEqual(self.counts()["people"].get("/find"), 1)

    def test_the_health_check_is_not_traffic(self):
        """It is hit every ten minutes forever and renders no template, so it
        must never reach the tally - by construction, not by filtering."""
        self.client.get("/healthz", headers={"user-agent": BROWSER})
        self.assertEqual(self.counts()["total"], 0)

    def test_static_files_are_not_traffic(self):
        self.client.get("/static/style.css", headers={"user-agent": BROWSER})
        self.assertNotIn("/static/style.css", self.counts()["people"])

    def test_nothing_identifying_is_stored(self):
        """The basis for having no consent banner and nothing worth leaking.

        Asserted against the table itself rather than against the code that
        writes to it, so an extra column added later fails here.
        """
        self.client.get("/", headers={"user-agent": BROWSER,
                                      "referer": "https://example.com/p"})
        with self.views.connect() as c:
            row = c.execute("SELECT * FROM page_views").fetchone()
        self.assertEqual(set(dict(row)),
                         {"path", "source", "kind", "hour_at", "views"})


if __name__ == "__main__":
    unittest.main()
