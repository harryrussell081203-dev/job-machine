"""Role and town pages, and the builder changes that feed them.

Held here: a page exists only with enough employers; every employer on it
has a live directory page (so removing one removes it everywhere); only
shared inboxes appear; the pages are in the sitemap; and the builder no
longer spends every day on the same companies it already failed on."""

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


def site(name, local="careers"):
    domain = name.lower().replace(" ", "") + ".co.uk"
    address = f"{local}@{domain}"
    return {"domain": domain, "emails": [address, f"claire@{domain}"],
            "found_on": {address: f"https://{domain}/contact"}}


def job(title, town="Leeds", search="warehouse operative", posted="2026-10-01"):
    return {"title": title, "location": f"{town}, West Yorkshire",
            "posted_at": posted, "search": search, "town": town}


class Matching(unittest.TestCase):
    def test_by_the_search_that_found_it(self):
        from app import hubs
        self.assertEqual(hubs.role_of(job("Picker")), "warehouse operative")
        self.assertEqual(hubs.town_of(job("Picker")), "Leeds")

    def test_older_entries_by_their_own_words(self):
        from app import hubs
        old = {"title": "Night Warehouse Operatives", "location": "Hull, East Riding"}
        self.assertEqual(hubs.role_of(old), "warehouse operative")
        self.assertEqual(hubs.town_of(old), "Hull")
        self.assertEqual(hubs.role_of({"title": "Solicitor"}), "")
        # Whole words: "Hullbridge" is not Hull.
        self.assertEqual(hubs.town_of({"location": "Hullbridge, Essex"}), "")


class Pages(AppTestCase):
    def publish(self, *names, roles=None):
        from app import directory
        for n in names:
            directory.record(n, site(n), roles=roles or [job(f"{n} picker")])

    def test_no_page_below_three_employers(self):
        self.publish("Pennine Foods", "Kestrel Logistics")
        self.assertEqual(self.client.get("/jobs/warehouse-operative/leeds")
                         .status_code, 404)
        self.assertNotIn("/jobs/", self.client.get("/sitemap.xml").text)

    def test_three_employers_make_a_page(self):
        self.publish("Pennine Foods", "Kestrel Logistics", "Aire Valley Stores")
        r = self.client.get("/jobs/warehouse-operative/leeds")
        self.assertEqual(r.status_code, 200)
        page = r.text
        self.assertIn("Warehouse operative jobs in Leeds: who to email", page)
        self.assertIn("careers@penninefoods.co.uk", page)
        self.assertIn('href="/employers/pennine-foods"', page)
        self.assertNotIn("claire@", page)          # never a person
        self.assertIn("Adzuna", page)
        self.assertIn('href="/start"', page)
        self.assertEqual(self.client.get("/jobs/warehouse-operative").status_code, 200)
        self.assertEqual(self.client.get("/jobs").status_code, 200)
        sitemap = self.client.get("/sitemap.xml").text
        for path in ("/jobs</loc>", "/jobs/warehouse-operative</loc>",
                     "/jobs/warehouse-operative/leeds</loc>"):
            self.assertIn(path, sitemap)

    def test_removing_an_employer_removes_it_everywhere(self):
        from app import directory
        self.publish("Pennine Foods", "Kestrel Logistics", "Aire Valley Stores")
        directory.remove("pennine-foods")
        self.assertEqual(self.client.get("/jobs/warehouse-operative/leeds")
                         .status_code, 404)

    def test_unknown_roles_and_towns_are_not_pages(self):
        self.publish("Pennine Foods", "Kestrel Logistics", "Aire Valley Stores")
        for path in ("/jobs/astronaut", "/jobs/warehouse-operative/narnia",
                     "/jobs/warehouse-operative/hull"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_links_to_the_same_role_elsewhere(self):
        self.publish("Pennine Foods", "Kestrel Logistics", "Aire Valley Stores")
        self.publish("Humber Freight", "Hull Docks Supply", "East Coast Pick",
                     roles=[job("Picker", town="Hull")])
        page = self.client.get("/jobs/warehouse-operative/leeds").text
        self.assertIn('href="/jobs/warehouse-operative/hull"', page)


class TheBuilder(AppTestCase):
    def test_a_miss_waits_a_month_and_is_never_published(self):
        from app import directory, directory_builder as b, db
        uid = db.get_or_create_user("sam@example.com")["id"]
        db.add_draft(uid, job_title="Fitter", company="Nowhere Fabrication",
                     to_email="x@n.co.uk", subject="s", body="b")
        seen = []

        def look(name):
            seen.append(name)
            return {"domain": "", "emails": [], "found_on": {}}
        b.run(day=datetime.date(2026, 10, 2), lookup=look)
        self.assertEqual(seen, ["Nowhere Fabrication"])
        seen.clear()
        b.run(day=datetime.date(2026, 10, 3), lookup=look)
        self.assertEqual(seen, [])
        self.assertEqual(directory.listed(), [])
        self.assertIsNone(directory.get("nowhere-fabrication"))
        self.assertEqual(self.client.get("/employers/nowhere-fabrication")
                         .status_code, 404)

    def test_a_find_lookup_is_never_remembered(self):
        """A name typed into /find could be anything, a person's included."""
        from app import directory
        from app.store import connect
        directory.record("Somebody Typed This", {"domain": "", "emails": []})
        with connect() as c:
            self.assertIsNone(c.execute(
                "SELECT 1 FROM employer_pages WHERE slug = 'somebody-typed-this'"
            ).fetchone())

    def test_todays_advertisers_come_first(self):
        from unittest.mock import patch
        from app import directory_builder as b, db
        uid = db.get_or_create_user("sam@example.com")["id"]
        db.add_draft(uid, job_title="Fitter", company="Old Draft Co",
                     to_email="x@o.co.uk", subject="s", body="b")
        seen = []
        with patch.object(b, "from_boards",
                          lambda day, session=None: {"Fresh Advert Co": [job("x")]}):
            b.run(day=datetime.date(2026, 10, 2), per_run=1,
                  lookup=lambda n: seen.append(n) or {"domain": "", "emails": []})
        self.assertEqual(seen, ["Fresh Advert Co"])

    def test_a_kept_miss_is_looked_at_again_when_asked(self):
        import json
        import time
        from app import company_lookup, db
        store = db._PlaceCache()
        store.put("find:pennine foods", json.dumps(
            {"domain": "", "emails": [], "found_on": {}, "at": int(time.time())}))
        asked = []
        company_lookup.lookup("Pennine Foods", store=store, retry_unknown=True,
                              find_domain=lambda n: asked.append(n) or None)
        self.assertEqual(asked, ["Pennine Foods"])
        asked.clear()
        company_lookup.lookup("Pennine Foods", store=store,
                              find_domain=lambda n: asked.append(n) or None)
        self.assertEqual(asked, [])

    def test_announced_under_its_own_fingerprint(self):
        from unittest.mock import patch
        from app import directory_builder as b, indexnow
        seen = {}
        with patch.object(indexnow, "submit_if_changed",
                          lambda urls, **kw: seen.update(kw, urls=urls) or "off"):
            b.announce()
        self.assertEqual(seen["memory_key"], "indexnow_directory")
        self.assertNotEqual(seen["memory_key"], indexnow.MEMORY_KEY)


if __name__ == "__main__":
    unittest.main()
