"""/find by company name: the step people abandoned was finding and pasting
the advert, so a name alone now works.

  1. A NAME FINDS the company's own site and the addresses it publishes,
     labelled as from their website.
  2. NOTHING IS GUESSED: no confident site, or a site with no address, says
     so plainly.
  3. THE SERVER GOES ONLY WHERE THE LOOKUP SENT IT: redirects off the
     company's domain are not followed, and a domain resolving to a private
     address is not read at all.
  4. RATIONED per visitor and for everybody, and cached for a week.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import AppTestCase  # noqa: E402


class Page:
    def __init__(self, status=200, text="", location=""):
        self.status_code, self.text = status, text
        self.headers = {"location": location} if location else {}


def site(pages):
    asked = []

    def get(url, **kw):
        asked.append(url)
        return pages.get(url, Page(404))
    get.asked = asked
    return get


class Unit(unittest.TestCase):
    def lookup(self, **kw):
        from app import company_lookup
        kw.setdefault("find_domain", lambda c: "kestrelfoods.co.uk")
        kw.setdefault("resolves", lambda d: True)
        return company_lookup.lookup("Kestrel Foods", **kw)

    def test_addresses_from_its_own_pages(self):
        get = site({"https://kestrelfoods.co.uk/contact":
                    Page(text="Email careers@kestrelfoods.co.uk")})
        out = self.lookup(get=get)
        self.assertEqual(out["domain"], "kestrelfoods.co.uk")
        self.assertEqual(out["emails"], ["careers@kestrelfoods.co.uk"])

    def test_no_confident_site_checks_nothing(self):
        get = site({})
        out = self.lookup(get=get, find_domain=lambda c: None)
        self.assertEqual(out, {**out, "domain": "", "emails": []})
        self.assertEqual(get.asked, [])

    def test_a_redirect_off_the_domain_is_not_followed(self):
        get = site({"https://kestrelfoods.co.uk/contact":
                    Page(302, location="http://169.254.169.254/latest")})
        self.lookup(get=get)
        self.assertFalse([u for u in get.asked if "169.254" in u])

    def test_a_redirect_within_the_domain_is(self):
        get = site({"https://kestrelfoods.co.uk/contact":
                    Page(301, location="https://www.kestrelfoods.co.uk/contact/"),
                    "https://www.kestrelfoods.co.uk/contact/":
                    Page(text="hr@kestrelfoods.co.uk")})
        self.assertIn("hr@kestrelfoods.co.uk", self.lookup(get=get)["emails"])

    def test_a_private_address_is_never_read(self):
        get = site({})
        out = self.lookup(get=get, resolves=lambda d: False)
        self.assertEqual(out["domain"], "")
        self.assertEqual(get.asked, [])

    def test_private_ranges_are_refused(self):
        from app import company_lookup
        with patch.object(company_lookup.socket, "getaddrinfo",
                          return_value=[(0, 0, 0, "", ("10.0.0.5", 443))]):
            self.assertFalse(company_lookup._public("evil.example"))

    def test_cached_for_a_week(self):
        kept = {}

        class Store:
            def get(self, k):
                return kept.get(k)

            def put(self, k, v):
                kept[k] = v
        get = site({"https://kestrelfoods.co.uk":
                    Page(text="jobs@kestrelfoods.co.uk")})
        self.lookup(get=get, store=Store())
        again = site({})
        out = self.lookup(get=again, store=Store())
        self.assertEqual(again.asked, [])
        self.assertIn("jobs@kestrelfoods.co.uk", out["emails"])


class ThePage(AppTestCase):
    def post(self, **data):
        return self.client.post("/find", data=data).text

    def test_company_name_comes_first(self):
        page = self.client.get("/find").text
        self.assertLess(page.index('id="company"'), page.index('id="advert"'))

    def test_a_name_alone_finds_and_labels_the_address(self):
        from app import company_lookup
        with patch.object(company_lookup, "lookup", return_value={
                "domain": "kestrelfoods.co.uk",
                "emails": ["fiona.menzies@kestrelfoods.co.uk"]}):
            page = self.post(company="Kestrel Foods")
        self.assertIn("fiona.menzies@kestrelfoods.co.uk", page)
        self.assertIn("from their website", page)

    def test_no_site_says_so(self):
        from app import company_lookup
        with patch.object(company_lookup, "lookup",
                          return_value={"domain": "", "emails": []}):
            page = self.post(company="Smith Ltd")
        self.assertIn("could not be sure which website", page)
        self.assertNotIn("Nothing in the advert itself", page)

    def test_a_site_with_nothing_says_so(self):
        from app import company_lookup
        with patch.object(company_lookup, "lookup", return_value={
                "domain": "smith.co.uk", "emails": []}):
            page = self.post(company="Smith Ltd")
        self.assertIn("smith.co.uk does not publish an address", page)

    def test_company_lookups_are_rationed_harder(self):
        from app import company_lookup
        limit, _ = self.main.COMPANY_PER_IP
        with patch.object(company_lookup, "lookup",
                          return_value={"domain": "", "emails": []}) as look:
            for _ in range(limit):
                self.post(company="Smith Ltd")
            page = self.post(company="Smith Ltd")
        self.assertIn("Company lookups are busy", page)
        self.assertEqual(look.call_count, limit)


if __name__ == "__main__":
    unittest.main()
