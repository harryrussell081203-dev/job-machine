"""The cookie question and what accepting it unlocks.

Held here: nothing is recorded about a visitor until they say yes; no is as
easy as yes and is remembered; a yes gives a random code that is never the
account; and the journey report reads sensibly - a move from the front page
to sign-up is not counted as leaving."""

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")

from test_app import AppTestCase  # noqa: E402

PHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"}


class Case(AppTestCase):
    def events(self):
        from app.store import connect
        with connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM journey_events ORDER BY at").fetchall()]

    def accept(self):
        r = self.client.post("/consent", headers=PHONE,
                             json={"choice": "yes", "path": "/",
                                   "referrer": "https://www.reddit.com/r/UKJobs"})
        self.assertEqual(r.json(), {"ok": True})
        return self.client.cookies.get("vid")


class TheQuestion(Case):
    def test_asked_until_answered(self):
        self.assertIn('id="consentbar"', self.client.get("/").text)
        self.client.post("/consent", json={"choice": "no"}, headers=PHONE)
        self.assertNotIn('id="consentbar"', self.client.get("/").text)

    def test_no_is_as_easy_as_yes(self):
        page = self.client.get("/").text
        bar = page.split('id="consentbar"', 1)[1].split("</div>\n</div>", 1)[0]
        self.assertIn('class="btn small" data-consent="yes"', bar)
        self.assertIn('class="btn small" data-consent="no"', bar)

    def test_nothing_is_recorded_before_an_answer(self):
        self.client.get("/", headers=PHONE)
        self.client.get("/start", headers=PHONE)
        self.assertEqual(self.events(), [])

    def test_nothing_is_recorded_after_a_no(self):
        self.client.post("/consent", json={"choice": "no"}, headers=PHONE)
        self.assertIsNone(self.client.cookies.get("vid"))
        self.client.get("/", headers=PHONE)
        self.client.post("/j", json={"k": "left", "s": "hero", "t": 9, "p": "/"})
        self.assertEqual(self.events(), [])

    def test_a_yes_starts_a_journey_with_where_they_came_from(self):
        vid = self.accept()
        self.assertTrue(vid)
        self.client.get("/start", headers=PHONE)
        ev = self.events()
        self.assertEqual(ev[0]["source"], "reddit.com")
        self.assertEqual([e["path"] for e in ev], ["/", "/start"])
        self.assertTrue(all(e["vid"] == vid for e in ev))


class WhatItRecords(Case):
    def test_only_fixed_kinds_and_sections(self):
        self.accept()
        self.client.post("/j", json={"k": "anything", "s": "hero"})
        self.client.post("/j", json={"k": "left", "s": "<script>", "t": 5, "p": "/"})
        left = [e for e in self.events() if e["kind"] == "left"]
        self.assertEqual([e["detail"] for e in left], ["other|5"])

    def test_a_sign_up_is_an_event_not_a_link_to_the_person(self):
        self.accept()
        self.client.post("/start", headers=PHONE, data={
            "target_roles": "warehouse operative", "location": "Leeds",
            "last_title": "Picker", "last_org": "Tesco", "min_pay": "12",
            "name": "Sam Example", "phone": "07700 900123",
            "email": "sam.journey@example.com"})
        joined = [e for e in self.events() if e["kind"] == "signed_up"]
        self.assertEqual(len(joined), 1)
        for e in self.events():
            self.assertNotIn("sam", (e["detail"] + e["path"]).lower())

    def test_the_headline_stays_the_same_for_one_visitor(self):
        from app import journey
        vid = self.accept()
        first = self.client.get("/").text
        again = self.client.get("/").text
        title = journey.HEADLINES[journey.variant(vid)][0]
        self.assertIn(title, first)
        self.assertIn(title, again)

    def test_everybody_without_a_code_sees_a(self):
        from app import journey
        self.assertIn(journey.HEADLINES["A"][0], self.client.get("/").text)


class TheFirstScreen(Case):
    def test_no_install_bar_before_an_account(self):
        """It sat above the headline on a stranger's first visit."""
        self.assertNotIn('id="installbar"', self.client.get("/").text)
        self.sign_in()
        self.assertIn('id="installbar"', self.client.get("/dashboard").text)


class TheReport(Case):
    def test_moving_on_to_sign_up_is_not_leaving(self):
        from app import journey
        now = time.time()
        journey.record("v" * 20, "view", "/", "", "reddit.com", "A", when=now)
        journey.record("v" * 20, "left", "/", "how|40", "", "A", when=now + 40)
        journey.record("v" * 20, "view", "/start", "", "", "A", when=now + 41)
        journey.record("v" * 20, "left", "/start", "start|20", "", "A", when=now + 61)
        r = journey.report(since=now - 60)
        self.assertEqual(r["left_at"], [("/start - start", 1)])
        self.assertEqual(r["sources"], [("reddit.com", 1, 0)])
        self.assertEqual(r["headline_test"]["A"]["opened_start"], 1)

    def test_kept_ninety_days(self):
        from app import journey
        now = time.time()
        journey.record("o" * 20, "view", "/", when=now - 91 * 86400)
        journey.record("n" * 20, "view", "/", when=now)
        self.assertEqual({e["vid"][0] for e in self.events()}, {"n"})


if __name__ == "__main__":
    unittest.main()
