"""What a machine sees when it reads this site.

Most of the traffic that will ever quote these pages is not a person. The
answer pages, the JSON endpoint and every rate carrying its count are all
aimed at being retrieved and repeated accurately, and that only works if the
plumbing underneath is right.

Three failures matter here and none of them is visible to a human reading the
site, which is why they need tests rather than a look:

  - the same page reachable at several addresses, so whatever standing it
    earns is split between the copies;
  - structured data claiming something the page does not, which is how a
    site's markup stops being read at all;
  - a crawler being told to go away by a rule meant for somebody else.
"""

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_sending import Base  # noqa: E402

PUBLIC = ("/", "/find", "/playbook", "/answers", "/numbers", "/terms",
          "/privacy", "/login")


class TestOneAddressPerPage(Base):
    def canonical(self, page):
        found = re.search(r'<link rel="canonical" href="([^"]+)"', page)
        self.assertIsNotNone(found, "no canonical link")
        return found.group(1)

    def test_every_public_page_says_which_address_is_the_real_one(self):
        for path in PUBLIC:
            page = self.client.get(path).text
            self.assertTrue(self.canonical(page).endswith(path), path)

    def test_a_tracking_parameter_points_back_at_the_clean_address(self):
        """Every post that has gone out carried one. The Snapchat story used
        ?utm_source=snapchat, and to a crawler that is a second page with
        identical content competing against the first."""
        page = self.client.get("/find?utm_source=tiktok&fbclid=abc123").text
        canonical = self.canonical(page)
        self.assertNotIn("utm_source", canonical)
        self.assertNotIn("fbclid", canonical)
        self.assertTrue(canonical.endswith("/find"), canonical)

    def test_answer_pages_have_their_own(self):
        from app import answers
        for a in answers.ANSWERS:
            page = self.client.get(f"/answers/{a.slug}").text
            self.assertTrue(self.canonical(page).endswith(a.slug), a.slug)


class TestWhoThisSiteSaysItIs(Base):
    def graph(self):
        page = self.client.get("/").text
        for block in re.findall(
                r'<script type="application/ld\+json">(.*?)</script>',
                page, re.S):
            data = json.loads(block)
            if "@graph" in data:
                return {item["@type"]: item for item in data["@graph"]}
        self.fail("no @graph on the landing page")

    def test_it_parses(self):
        """Markup that does not parse is markup nothing acts on, and a
        substring check would never notice."""
        self.assertTrue(self.graph())

    def test_it_names_itself_and_binds_that_name_to_this_url(self):
        """There is an unrelated UK recruitment agency with this name. An
        assistant asked about "Recruited" has to pick one of us."""
        org = self.graph()["Organization"]
        self.assertEqual(org["name"], "Recruited")
        self.assertIn("http", org["url"])

    def test_the_free_tool_is_declared_free_rather_than_implied(self):
        app_ld = self.graph()["SoftwareApplication"]
        self.assertEqual(app_ld["offers"]["price"], "0")
        self.assertTrue(app_ld["url"].endswith("/find"))

    def test_nothing_is_claimed_that_nobody_can_check(self):
        """A rating or review count with nobody behind it is the single
        fastest way to have a site's structured data disregarded, and this
        site's whole argument is that its claims can be checked."""
        page = self.client.get("/").text
        for invented in ("aggregateRating", "reviewCount", "ratingValue",
                         "foundingDate"):
            self.assertNotIn(invented, page)


class TestTheFileWrittenForModels(Base):
    def body(self):
        r = self.client.get("/llms.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/plain", r.headers["content-type"])
        return r.text

    def test_it_exists_and_names_the_product(self):
        self.assertIn("Recruited", self.body())

    def test_it_states_the_thing_most_likely_to_be_misquoted(self):
        """Two reply rates are published and they measure different events.
        A model that takes one for the other quotes a number this site never
        claimed, so it is spelled out before it can happen."""
        body = self.body()
        self.assertIn("26%", body)
        self.assertIn("rejections", body)
        self.assertIn("/numbers.json", body)

    def test_it_says_what_the_product_refuses_to_do(self):
        body = self.body()
        self.assertIn("516", body)
        self.assertIn("firstname.lastname", body)

    def test_it_is_not_disallowed_by_robots(self):
        robots = self.client.get("/robots.txt").text
        for line in robots.splitlines():
            if line.startswith("Disallow:"):
                rule = line.split(":", 1)[1].strip()
                self.assertFalse(rule and "/llms.txt".startswith(rule), robots)


class TestTheSitemap(Base):
    def test_it_carries_a_date_that_means_something(self):
        """A lastmod of today on every page every day is the kind of claim a
        crawler learns to disregard - and then stops re-reading the pages
        that genuinely did change. This one comes from the file the figures
        are published in, so it moves when they move."""
        from app import track_record
        xml = self.client.get("/sitemap.xml").text
        stamp = track_record.updated_on()
        if stamp:
            self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}$")
            self.assertIn(f"<lastmod>{stamp}</lastmod>", xml)
        else:
            self.assertNotIn("<lastmod>", xml)

    def test_a_missing_track_record_is_no_date_rather_than_a_crash(self):
        """The sitemap is not worth a 500, and a made-up date is worse than
        no date."""
        from app import track_record
        original = track_record.PATH
        track_record.PATH = "/nonexistent/track_record.json"
        track_record._cache = None
        try:
            self.assertEqual(track_record.updated_on(), "")
            self.assertEqual(
                self.client.get("/sitemap.xml").status_code, 200)
        finally:
            track_record.PATH = original
            track_record._cache = None


class TestNobodyIsShutOutByAccident(Base):
    def test_the_assistants_this_was_all_written_for_are_allowed(self):
        """The disallow list is by prefix and grows whenever a private screen
        is added. One careless entry shuts out the readers the answer pages
        exist for, and nothing would look wrong on the site."""
        robots = self.client.get("/robots.txt").text
        rules = [line.split(":", 1)[1].strip()
                 for line in robots.splitlines()
                 if line.startswith("Disallow:")]
        for path in PUBLIC + ("/numbers.json", "/llms.txt", "/sitemap.xml"):
            for rule in rules:
                self.assertFalse(rule and path.startswith(rule),
                                 f"{path} is blocked by Disallow: {rule}")

    def test_the_sitemap_is_advertised(self):
        self.assertIn("Sitemap:", self.client.get("/robots.txt").text)


if __name__ == "__main__":
    unittest.main()
