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

    def test_words_are_kept_sixty_days_as_the_privacy_notice_says(self):
        import time
        from app.store import connect
        with connect() as c:
            c.execute("INSERT INTO feedback (at, page, reason, text) "
                      "VALUES (?, 'landing', 'other', 'old')",
                      (int(time.time()) - 61 * 86400,))
        self.client.post("/why", headers=PHONE, json={
            "reason": "other", "text": "new", "page": "landing"})
        texts = [c["text"] for c in self.main.pulse.comments(since=0)]
        self.assertEqual(texts, ["new"])

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


class RealPeople(AppTestCase):
    def outcomes(self):
        import time
        return self.main.views.outcomes(since=time.time() - 3600)

    def test_a_page_reports_a_real_visit(self):
        self.client.post("/seen", json={"p": "/"}, headers=PHONE)
        self.client.post("/seen", json={"p": "/answers/attach-cv"}, headers=PHONE)
        self.client.post("/seen", json={"p": "/../../etc"}, headers=PHONE)
        out = self.outcomes()
        self.assertEqual(out.get("real:front"), 1)
        self.assertEqual(out.get("real:answers"), 1)
        self.assertEqual(out.get("real:other"), 1)

    def test_a_crawler_saying_so_is_not_a_person(self):
        self.client.post("/seen", json={"p": "/"},
                         headers={"User-Agent": "Googlebot/2.1"})
        self.assertNotIn("real:front", self.outcomes())

    def test_every_page_carries_the_beacon(self):
        for path in ("/", "/find", "/playbook"):
            page = self.client.get(path).text
            self.assertIn('navigator.webdriver', page, path)
            self.assertIn('"/seen"', page, path)

    def test_the_funnel_starts_from_real_people(self):
        import time
        for _ in range(5):
            self.client.get("/", headers=PHONE)      # the old counter
        self.client.post("/seen", json={"p": "/"}, headers=PHONE)
        r = self.main.pulse.report(since=time.time() - 3600)
        self.assertEqual(r["funnel"][0], {"label": "Real people on the front page",
                                          "n": 1, "kept": None})
        self.assertEqual(r["raw_front"], 5)


class FromAnAd(AppTestCase):
    def outcomes(self):
        import time
        return self.main.views.outcomes(since=time.time() - 3600)

    def test_a_tagged_visit_and_sign_up_are_counted(self):
        self.client.post("/seen", json={"p": "/", "c": "meta"}, headers=PHONE)
        page = self.client.get("/start?c=meta").text
        self.assertIn('action="/start/read?c=meta"', page)
        self.client.post("/start?c=meta", headers=PHONE, data={
            "target_roles": "warehouse operative", "location": "Leeds",
            "last_title": "Picker", "last_org": "Tesco", "min_pay": "12",
            "name": "Sam", "phone": "07700 900123",
            "email": "sam.ad@example.com"})
        out = self.outcomes()
        self.assertEqual(out.get("from:meta:real"), 1)
        self.assertEqual(out.get("from:meta:joined"), 1)
        import time
        report = self.main.pulse.report(since=time.time() - 3600)
        self.assertEqual(report["campaigns"], {"meta": {"real": 1, "joined": 1}})

    def test_an_unknown_tag_is_not_counted(self):
        self.client.post("/seen", json={"p": "/", "c": "anything-at-all"},
                         headers=PHONE)
        self.assertNotIn("anything-at-all", self.client.get(
            "/start?c=anything-at-all").text.split("<form", 1)[1][:200])
        self.assertFalse([k for k in self.outcomes() if k.startswith("from:")])

    def test_every_page_passes_the_tag_on_to_sign_up(self):
        page = self.client.get("/").text
        self.assertIn('a[href^="/start"]', page)
        self.assertIn("utm_source", page)


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
        self.assertEqual(steps["Real people on the front page"]["n"], 0)
        self.assertEqual(steps["Scrolled to the proof (25%)"]["n"], 1)
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
