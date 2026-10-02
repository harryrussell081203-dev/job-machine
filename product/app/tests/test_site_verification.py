"""Search Console and Bing verification tags: absent until set, then on the
home page only."""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import AppTestCase  # noqa: E402


class Verification(AppTestCase):
    def test_nothing_until_set(self):
        with patch.dict(os.environ, {"GOOGLE_SITE_VERIFICATION": "",
                                     "BING_SITE_VERIFICATION": ""}):
            page = self.client.get("/").text
        self.assertNotIn("google-site-verification", page)
        self.assertNotIn("msvalidate.01", page)

    def test_both_on_the_home_page(self):
        with patch.dict(os.environ, {"GOOGLE_SITE_VERIFICATION": "g123",
                                     "BING_SITE_VERIFICATION": "b456"}):
            home = self.client.get("/").text
            other = self.client.get("/find").text
        self.assertIn('<meta name="google-site-verification" content="g123">',
                      home)
        self.assertIn('<meta name="msvalidate.01" content="b456">', home)
        self.assertNotIn("g123", other)


class VerificationFile(AppTestCase):
    def test_not_found_until_set(self):
        with patch.dict(os.environ, {"GOOGLE_SITE_VERIFICATION_FILE": ""}):
            r = self.client.get("/google5a4e0d3b0c908135.html")
        self.assertEqual(r.status_code, 404)

    def test_serves_the_exact_line(self):
        for value in ("google5a4e0d3b0c908135.html", "5a4e0d3b0c908135"):
            with patch.dict(os.environ,
                            {"GOOGLE_SITE_VERIFICATION_FILE": value}):
                r = self.client.get("/google5a4e0d3b0c908135.html")
                other = self.client.get("/googleffff.html")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(
                r.text, "google-site-verification: google5a4e0d3b0c908135.html")
            self.assertEqual(other.status_code, 404)


class SearchTitles(AppTestCase):
    """What shows as the blue link on Google. The brand name alone collides
    with an existing recruitment agency, so the titles say what the page
    does."""

    def test_home_and_find_say_what_they_do(self):
        home = self.client.get("/").text
        find = self.client.get("/find").text
        self.assertIn("<title>Email the person hiring, not the job portal", home)
        self.assertIn("<title>Find the hiring manager&#39;s email for a job",
                      find.replace("'", "&#39;"))
        self.assertIn('content="Type a company&#39;s name or paste a job advert',
                      find.replace("'", "&#39;"))


if __name__ == "__main__":
    unittest.main()
