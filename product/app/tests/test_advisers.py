"""The page the outreach letters point careers advisers at: something they
can pass on in a minute, counted when it is, and held to the same rules as
everything else the site says."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")

from test_app import AppTestCase  # noqa: E402


class ForAdvisers(AppTestCase):
    def test_the_page_and_its_paragraph(self):
        r = self.client.get("/advisers")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/find?c=advisers", r.text)
        self.assertIn("/advisers/handout", r.text)

    def test_the_paragraph_reads_like_a_person_wrote_it(self):
        import outreach
        from app import main
        blurb = main.ADVISER_BLURB.format(link="https://recruited.org.uk/find")
        self.assertEqual(outreach.check("", blurb), [])
        self.assertIn("never guesses", blurb)

    def test_the_handout_prints_with_a_code_to_scan(self):
        r = self.client.get("/advisers/handout")
        self.assertEqual(r.status_code, 200)
        self.assertIn("recruited.org.uk/find", r.text)
        self.assertIn("noindex", r.text)
        try:
            import segno  # noqa: F401
        except ImportError:
            self.skipTest("segno is not installed here")
        self.assertIn("<svg", r.text)

    def test_the_handout_without_the_qr_library_still_works(self):
        from unittest.mock import patch

        from app import main
        with patch.dict(sys.modules, {"segno": None}):
            self.assertEqual(main.qr_svg("https://x"), "")

    def test_in_the_sitemap_and_counted(self):
        from app import main, pulse
        self.assertIn("/advisers", main.public_pages())
        self.assertNotIn("/advisers/handout", main.public_pages())
        self.assertEqual(pulse.campaign("advisers"), "advisers")
        self.assertEqual(pulse.campaign("handout"), "handout")
        self.assertEqual(pulse.campaign("careers"), "careers")

    def test_the_letter_points_at_it(self):
        import outreach
        _, body = outreach.compose_letter(
            {"name": "X", "website": "https://x.org"}, {"email": "info@x.org"})
        self.assertIn("/advisers", body)
        self.assertEqual(outreach.check(*outreach.compose_letter(
            {"name": "X", "website": "https://x.org"}, {"email": "info@x.org"})), [])


if __name__ == "__main__":
    unittest.main()
