"""The public employer directory: what may be published, the pages, removal,
and the daily builder."""

import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")

from test_app import AppTestCase  # noqa: E402


def site(*emails, domain="pennine.co.uk", page="https://pennine.co.uk/contact"):
    return {"domain": domain, "emails": list(emails),
            "found_on": {e: page for e in emails}}


class WhatMayBePublished(unittest.TestCase):
    def setUp(self):
        from app import directory
        self.d = directory

    def test_shared_inboxes_only(self):
        self.assertEqual(self.d.kind_of("careers@pennine.co.uk"), "hiring")
        self.assertEqual(self.d.kind_of("recruitment@pennine.co.uk"), "hiring")
        self.assertEqual(self.d.kind_of("hr@pennine.co.uk"), "hiring")
        self.assertEqual(self.d.kind_of("info@pennine.co.uk"), "general")
        self.assertEqual(self.d.kind_of("enquiries@pennine.co.uk"), "general")

    def test_never_a_person_and_never_the_irrelevant(self):
        for address in ("claire@pennine.co.uk", "jane.smith@pennine.co.uk",
                        "j.smith@pennine.co.uk", "sales@pennine.co.uk",
                        "accounts@pennine.co.uk", "complaints@pennine.co.uk",
                        "investors@pennine.co.uk"):
            self.assertEqual(self.d.kind_of(address), "", address)

    def test_only_their_own_domain_and_hiring_first(self):
        found = self.d.inboxes(site("info@pennine.co.uk", "jobs@pennine.co.uk",
                                    "careers@other.com", "claire@pennine.co.uk"))
        self.assertEqual([i["email"] for i in found],
                         ["jobs@pennine.co.uk", "info@pennine.co.uk"])
        self.assertEqual(found[0]["found_on"], "https://pennine.co.uk/contact")

    def test_slugs_and_names(self):
        self.assertEqual(self.d.slugify("Pennine Foods Ltd"), "pennine-foods")
        self.assertEqual(self.d.slugify("M&S (UK)"), "m-s")
        self.assertEqual(self.d.display_name("PENNINE FOODS"), "Pennine Foods")
        self.assertEqual(self.d.display_name("McAlpine"), "McAlpine")


class ThePages(AppTestCase):
    def setUp(self):
        super().setUp()
        from app import directory
        self.d = directory

    def test_a_company_with_an_inbox_gets_a_page(self):
        slug = self.d.record("Pennine Foods Ltd",
                             site("careers@pennine.co.uk", "claire@pennine.co.uk"),
                             roles=[{"title": "Maintenance Technician",
                                     "location": "Rotherham", "posted_at": ""}])
        self.assertEqual(slug, "pennine-foods")
        page = self.client.get("/employers/pennine-foods").text
        self.assertIn("<title>Pennine Foods Ltd careers email address", page)
        self.assertIn("careers@pennine.co.uk", page)
        self.assertIn("pennine.co.uk/contact", page)
        self.assertIn("Maintenance Technician", page)
        self.assertNotIn("claire@", page)
        self.assertIn("Pennine Foods Ltd", self.client.get("/employers").text)
        self.assertIn("/employers/pennine-foods",
                      self.client.get("/sitemap.xml").text)

    def test_nothing_to_publish_means_no_page(self):
        self.assertEqual(self.d.record("Solo Ltd",
                                       site("claire@solo.co.uk",
                                            domain="solo.co.uk")), "")
        r = self.client.get("/employers/solo")
        self.assertEqual(r.status_code, 404)

    def test_an_address_that_has_gone_takes_the_page_down(self):
        self.d.record("Pennine Foods", site("jobs@pennine.co.uk"))
        self.d.record("Pennine Foods", site())
        self.assertEqual(self.client.get("/employers/pennine-foods").status_code,
                         404)

    def test_removal_is_permanent(self):
        self.d.record("Pennine Foods", site("jobs@pennine.co.uk"))
        r = self.client.post("/employers/pennine-foods/remove",
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.client.get("/employers/pennine-foods").status_code,
                         404)
        self.assertEqual(self.d.record("Pennine Foods",
                                       site("jobs@pennine.co.uk")), "")
        self.assertEqual(self.d.due(["Pennine Foods"]), [])

    def test_a_find_lookup_feeds_the_directory(self):
        from app import company_lookup, db

        class Page:
            status_code = 200
            headers = {}
            text = "Jobs: careers@kestrel.co.uk. Or call Claire on claire@kestrel.co.uk"
        company_lookup.lookup(
            "Kestrel Foods", get=lambda url, **kw: Page(),
            find_domain=lambda name: "kestrel.co.uk",
            resolves=lambda d: True, store=db._PlaceCache())
        page = self.d.get("kestrel-foods")
        self.assertEqual([i["email"] for i in page["inboxes"]],
                         ["careers@kestrel.co.uk"])
        self.assertTrue(page["inboxes"][0]["found_on"].startswith("https://"))

    def test_the_builder_reads_due_companies_and_publishes(self):
        from app import db, directory_builder as b
        uid = db.get_or_create_user("sam@example.com")["id"]
        db.add_draft(uid, job_title="Fitter", company="Pennine Foods",
                     to_email="x@pennine.co.uk", subject="s", body="b")
        db.add_draft(uid, job_title="Fitter", company="Apex Recruitment",
                     to_email="x@apex.co.uk", subject="s", body="b")
        seen = []

        def look(name):
            seen.append(name)
            return site("jobs@pennine.co.uk")
        out = b.run(day=datetime.date(2026, 10, 2), lookup=look)
        self.assertEqual(seen, ["Pennine Foods"])        # not the agency
        self.assertEqual(out["published"], 1)
        # Read again only after a month.
        seen.clear()
        b.run(day=datetime.date(2026, 10, 3), lookup=look)
        self.assertEqual(seen, [])

    def test_never_a_company_anybody_blocked(self):
        from app import db
        uid = db.get_or_create_user("sam@example.com")["id"]
        db.block_company(uid, "Hydro Group")
        self.assertEqual(self.d.record("Hydro Group plc",
                                       site("careers@hydro.co.uk",
                                            domain="hydro.co.uk")), "")
        from unittest.mock import patch
        with patch.dict(os.environ, {"DIRECTORY_EXCLUDE": "Pennine Foods"}):
            self.assertEqual(self.d.record("Pennine Foods",
                                           site("jobs@pennine.co.uk")), "")

    def test_the_daily_searches_rotate(self):
        from app import directory_builder as b
        one = b.todays_searches(datetime.date(2026, 10, 2))
        two = b.todays_searches(datetime.date(2026, 10, 3))
        self.assertEqual(len(one), 6)
        self.assertFalse(set(one) & set(two))


if __name__ == "__main__":
    unittest.main()
