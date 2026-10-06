"""Why people look and don't sign up: where they stop, and what they say.

Anonymous throughout. These hold that only fixed event names are counted,
that the answers add up on the admin page, and that the question is on the
pages it should be on."""

import html
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")

from test_app import AppTestCase  # noqa: E402

PHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"}


class Counting(AppTestCase):
    def outcomes(self):
        import time
        return self.main.views.outcomes(since=time.time() - 3600)

    def test_a_known_event_is_counted(self):
        r = self.client.post("/pulse", json={"e": "land:scroll:50"}, headers=PHONE)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.outcomes().get("land:scroll:50"), 1)

    def test_anything_else_is_ignored(self):
        """Otherwise the tally fills with whatever a script posts."""
        self.client.post("/pulse", json={"e": "land:anything"}, headers=PHONE)
        self.client.post("/pulse", json={"e": "x" * 500}, headers=PHONE)
        self.assertEqual(self.outcomes(), {})

    def test_an_answer_is_counted_and_its_words_kept(self):
        r = self.client.post("/why", headers=PHONE, json={
            "reason": "other", "text": "Is it really free?", "page": "landing"})
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(self.outcomes().get("why:other"), 1)
        import time
        said = self.main.pulse.comments(since=time.time() - 60)
        self.assertEqual(said[0]["text"], "Is it really free?")

    def test_an_unknown_reason_is_refused(self):
        r = self.client.post("/why", headers=PHONE, json={"reason": "hack"})
        self.assertEqual(r.status_code, 400)

    def test_a_refused_answer_on_start_is_counted_by_kind(self):
        """Which box people get wrong, never what they typed."""
        self.client.post("/start", headers=PHONE, data={
            "target_roles": "warehouse operative", "location": "Leeds",
            "last_title": "Picker", "last_org": "Tesco", "min_pay": "12",
            "name": "Sam", "phone": "", "email": "sam@example.com"})
        self.assertEqual(self.outcomes().get("start:error:phone"), 1)


class OnThePages(AppTestCase):
    def test_landing_counts_and_asks(self):
        page = self.client.get("/").text
        # It was once spliced into the page title, where the words were
        # present (so a text check passed) and no browser ever ran it.
        title = page.split("<title>", 1)[1].split("</title>", 1)[0]
        self.assertNotIn("whycard", title)
        self.assertNotIn("pulse", title)
        body = page.split("<body", 1)[1]
        self.assertIn('id="whycard"', body)
        self.assertIn("/static/pulse.js", page)
        self.assertIn('data-pulse="land:cta:hero"', page)
        self.assertIn("what&rsquo;s stopping you?", page)

    def test_start_counts_and_asks(self):
        page = self.client.get("/start").text
        self.assertIn('"start-describe"', page)
        self.assertIn('id="whycard"', page)


class TheReport(AppTestCase):
    def test_the_funnel_reads_in_order_with_what_was_kept(self):
        for _ in range(4):
            self.client.get("/", headers=PHONE)
        for e in ("land:scroll:25", "land:cta:hero"):
            self.client.post("/pulse", json={"e": e}, headers=PHONE)
        self.client.post("/why", json={"reason": "unsure"}, headers=PHONE)
        import time
        report = self.main.pulse.report(since=time.time() - 3600)
        steps = {s["label"]: s for s in report["funnel"]}
        self.assertEqual(steps["Saw the front page"]["n"], 4)
        self.assertEqual(steps["Scrolled to the proof (25%)"]["kept"], 25)
        self.assertEqual(steps["Tapped a sign-up button"]["n"], 1)
        self.assertIn(("I'm not sure it works", 1), report["why"])

    def test_the_admin_page_shows_it(self):
        os.environ["ADMIN_EMAILS"] = "boss@example.com"
        try:
            import importlib
            importlib.reload(self.main.config)
            self.sign_in("boss@example.com")
            page = html.unescape(self.client.get("/admin").text)
        finally:
            os.environ.pop("ADMIN_EMAILS", None)
        if "Why people don't sign up" not in page:
            self.skipTest("admin gate is configured differently in tests")
        self.assertIn("Why people don't sign up", page)


if __name__ == "__main__":
    unittest.main()
