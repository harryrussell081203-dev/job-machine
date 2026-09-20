"""The demo is on the front page, not one tap from it.

Over a week, 122 real people reached "/" and 17 reached "/find". The landing
page already led with the free tool rather than a sign-up box, and that was
the right fix for an earlier problem - it is not the fix for this one. A link
to a demo is an errand, and 86% of people did not run it.

So the finder moved onto the landing page itself. These tests hold the two
things that would quietly undo that: the form drifting into two copies, one of
which nobody looks at, and the example button appearing without the script
that makes it do anything.
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

TEMPLATES = os.path.join(ROOT, "app", "templates")

from app.tests.test_sending import Base  # noqa: E402


class TestTheFinderIsOnTheFrontPage(Base):
    def test_the_landing_page_carries_the_form_itself(self):
        page = self.client.get("/").text
        self.assertIn('action="/find"', page)
        self.assertIn('name="advert"', page)

    def test_it_is_still_on_its_own_page_too(self):
        """/find keeps working. It is what every post links to, it is in the
        sitemap, and it is where a shared link lands."""
        page = self.client.get("/find").text
        self.assertIn('action="/find"', page)
        self.assertIn('name="advert"', page)

    def test_pasting_from_the_front_page_finds_the_address(self):
        """The whole point, end to end. A form that renders and does not work
        is worse than the link it replaced."""
        advert = ("Maintenance Technician, Aberdeen. Send your CV to "
                  "fiona.menzies@kestrelfoods.example quoting MT-0925.")
        r = self.client.post("/find", data={"advert": advert})
        self.assertEqual(r.status_code, 200)
        self.assertIn("fiona.menzies@kestrelfoods.example", r.text)

    def test_the_example_button_never_appears_without_its_script(self):
        """It is hidden until JavaScript unhides it, so shipping the button
        to a page without the handler leaves a control that does nothing."""
        for path in ("/", "/find"):
            page = self.client.get(path).text
            self.assertIn('id="tryexample"', page, path)
            self.assertIn("kestrelfoods.example", page, path)

    def test_the_example_is_not_a_real_company(self):
        """A real address here would point a stream of strangers' curiosity at
        some real business's inbox, which is the thing this product exists not
        to do. .example is reserved and cannot be registered."""
        page = self.client.get("/").text
        for address in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", page):
            if "kestrel" in address.lower():
                self.assertTrue(address.endswith(".example"), address)

    def test_there_is_only_one_copy_of_the_form(self):
        """Two copies drift, and the one on the page nobody tests is the one
        that rots. Checked in the templates rather than in the rendered page,
        because that is where a second copy would be written."""
        copies = []
        for name in os.listdir(TEMPLATES):
            if not name.endswith(".html"):
                continue
            body = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
            if '<form method="post" action="/find"' in body:
                copies.append(name)
        self.assertEqual(copies, ["_finder.html"], copies)

    def test_both_pages_get_it_by_including_that_one_copy(self):
        for name in ("landing.html", "find.html"):
            body = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
            self.assertIn('include "_finder.html"', body, name)


if __name__ == "__main__":
    unittest.main()
