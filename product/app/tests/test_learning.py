"""What the machine keeps from its own work, and what it must never keep.

Crawl memory: a page that does not exist is not asked for again, a site
that refused us is left alone, and an outage never wipes a live directory
page. Letter lessons: counts and categories only, outcomes kept up to date,
nothing that identifies anybody, and still there after an account goes."""

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


class Page:
    def __init__(self, status=200, text=""):
        self.status_code, self.text, self.headers = status, text, {}


class Site:
    """A fake company website: {path: (status, text)}, everything else 404."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def __call__(self, url, **kw):
        path = url.split(".co.uk", 1)[1]
        self.asked.append(path)
        status, text = self.pages.get(path, (404, ""))
        return Page(status, text)


class CrawlMemory(AppTestCase):
    def lookup(self, site, company="Kestrel Foods"):
        from app import company_lookup
        return company_lookup.lookup(company, get=site,
                                     find_domain=lambda n: "kestrel.co.uk",
                                     resolves=lambda d: True)

    def test_a_page_that_does_not_exist_is_not_asked_for_again(self):
        site = Site({"": (200, "Welcome"),
                     "/contact": (200, "Jobs: careers@kestrel.co.uk")})
        self.lookup(site)
        self.assertIn("/team", site.asked)
        site.asked.clear()
        self.lookup(site)
        self.assertNotIn("/team", site.asked)
        self.assertIn("/contact", site.asked)

    def test_a_site_that_refused_every_page_is_left_alone(self):
        from app import learning
        site = Site({p: (403, "") for p in ("", "/contact", "/contact-us",
                                            "/careers", "/jobs", "/about",
                                            "/team")})
        result = self.lookup(site)
        self.assertTrue(result["unreachable"])
        self.assertTrue(learning.resting("kestrel.co.uk"))
        site.asked.clear()
        self.lookup(site)
        self.assertEqual(site.asked, [])

    def test_an_outage_never_wipes_a_live_directory_page(self):
        from app import directory
        directory.record("Kestrel Foods", {
            "domain": "kestrel.co.uk", "emails": ["careers@kestrel.co.uk"],
            "found_on": {}})
        self.assertIsNotNone(directory.get("kestrel-foods"))
        down = {"domain": "kestrel.co.uk", "emails": [], "found_on": {},
                "unreachable": True}
        directory.record("Kestrel Foods", down)
        self.assertIsNotNone(directory.get("kestrel-foods"))
        # Reachable and the address has gone: then the page goes.
        directory.record("Kestrel Foods", dict(down, unreachable=False))
        self.assertIsNone(directory.get("kestrel-foods"))

    def test_the_sweep_remembers_too(self):
        from app import learning  # noqa: F401  (sets the sweep's memory)
        from app import runner  # noqa: F401
        from jobseeker.pipeline import discover

        class Session:
            def __init__(self):
                self.asked = []

            def get(self, url, **kw):
                self.asked.append(url)
                if url.endswith("/contact"):
                    return Page(200, "hr@pennine.co.uk")
                if url.endswith("pennine.co.uk"):
                    return Page(200, "home")
                return Page(404, "")
        s = Session()
        self.assertEqual(discover.scrape_site("pennine.co.uk", session=s,
                                              delay=0), ["hr@pennine.co.uk"])
        first = len(s.asked)
        s.asked.clear()
        discover.scrape_site("pennine.co.uk", session=s, delay=0)
        self.assertLess(len(s.asked), first)
        self.assertFalse([u for u in s.asked if u.endswith("/team")])

    def test_the_report_says_which_pages_carry_addresses(self):
        from app import learning
        self.lookup(Site({"": (200, "Welcome"),
                          "/contact": (200, "careers@kestrel.co.uk")}))
        report = learning.crawl_report()
        self.assertEqual(report["sites"], 1)
        contact = next(p for p in report["paths"] if p["path"] == "/contact")
        self.assertEqual(contact["address_pct"], 100)
        team = next(p for p in report["paths"] if p["path"] == "/team")
        self.assertEqual(team["dead_pct"], 100)


class LetterLessons(AppTestCase):
    def send(self, uid, outcome="", body=None, subject="Fitter"):
        from app import db
        did = db.add_draft(
            uid, job_title="Fitter", company="Pennine Foods",
            to_email="careers@pennine.co.uk", to_name="", contact_tier=2,
            subject=subject, status="sent", sent_at=int(time.time()),
            body=body or ("Hi, I saw your fitter role. Four years on packing "
                          "lines. Could I send my availability?"))
        if outcome:
            db.set_outcome(uid, did, outcome)
        return did

    def rows(self):
        from app.store import connect
        with connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM letter_lessons").fetchall()]

    def test_counts_and_categories_only(self):
        from app import db, learning
        uid = db.get_or_create_user("sam.lessons@example.com")["id"]
        self.send(uid, outcome="replied")
        self.send(uid, body="Dear Sir, please find my CV attached. Regards, Sam")
        self.assertEqual(learning.sync(), 2)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        stored = " ".join(str(v) for r in rows for v in r.values()).lower()
        for secret in ("pennine", "careers@", "sam", "example.com", "fitter role"):
            self.assertNotIn(secret, stored)
        asks = {r["asks_question"] for r in rows}
        self.assertEqual(asks, {0, 1})
        self.assertEqual({r["address_kind"] for r in rows}, {"hiring inbox"})
        self.assertEqual({r["subject_kind"] for r in rows}, {"the job title"})

    def test_an_outcome_reaches_the_lesson_later(self):
        from app import db, learning
        uid = db.get_or_create_user("sam.later@example.com")["id"]
        did = self.send(uid)
        learning.sync()
        self.assertEqual(self.rows()[0]["outcome"], "")
        db.set_outcome(uid, did, "interview")
        learning.sync()
        self.assertEqual(self.rows()[0]["outcome"], "interview")

    def test_the_lessons_outlive_the_account(self):
        from app import db, learning
        uid = db.get_or_create_user("sam.gone@example.com")["id"]
        self.send(uid, outcome="replied")
        learning.sync()
        db.delete_user(uid)
        learning.sync()
        self.assertEqual(len(self.rows()), 1)

    def test_the_report_says_when_there_are_too_few(self):
        from app import db, learning
        uid = db.get_or_create_user("sam.few@example.com")["id"]
        for i in range(3):
            self.send(uid, outcome="replied" if i == 0 else "")
        learning.sync()
        report = learning.letter_report()
        self.assertEqual((report["letters"], report["replied"]), (3, 1))
        question = next(f for f in report["features"]
                        if f["label"] == "Asks a question")
        self.assertTrue(all(line["too_few"] for line in question["lines"]))


if __name__ == "__main__":
    unittest.main()
