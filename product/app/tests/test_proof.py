"""The founder's results on the landing page: the figures add up, they are
the ones on the page, and no employer is named."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")

from test_app import AppTestCase  # noqa: E402


class TheFigures(unittest.TestCase):
    def test_the_totals_are_the_sum_of_the_two_periods(self):
        from app import proof
        s = proof.summary()
        self.assertEqual(s["sent"], s["guessed"]["sent"] + s["published"]["sent"])
        self.assertEqual(s["replied"],
                         s["guessed"]["replied"] + s["published"]["replied"])

    def test_a_rate_is_never_worked_out_of_nothing(self):
        from app import proof
        self.assertEqual(proof.pct(3, 0), 0)


class OnTheLandingPage(AppTestCase):
    def test_every_figure_is_on_the_page(self):
        from app import proof
        page = self.client.get("/").text
        for n in (proof.SENT, proof.REPLIED, proof.INTERVIEWS,
                  proof.OFFERS):
            self.assertIn(f"<b>{n}</b>", page)
        self.assertIn("job offers", page)

    def test_no_employer_is_named(self):
        """One of them is his current employer, and public copy never names
        it. The others did not agree to be on a sales page."""
        page = self.client.get("/").text.lower()
        for name in ("hydro", "tekever"):
            self.assertNotIn(name, page)
        # Nor anything that dates the offer or says which one he took.
        for tell in ("accepted", "1 october", "15 september"):
            self.assertNotIn(tell, page)

    def test_the_sign_up_clip_is_there_and_its_files_are_served(self):
        """Muted, looping and inline, or a phone opens it full screen or
        not at all; and every file it names has to exist."""
        page = self.client.get("/").text
        for attr in ("autoplay", "muted", "loop", "playsinline"):
            self.assertIn(attr, page.split('<figure class="demo">', 1)[1][:400])
        for path in ("/static/demo/signup.webm", "/static/demo/signup.mp4",
                     "/static/demo/signup-poster.jpg"):
            self.assertIn(path, page)
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_the_account_button_comes_before_the_finder(self):
        body = self.client.get("/").text
        self.assertLess(body.find('class="hero-cta"'), body.find('id="try"'))


if __name__ == "__main__":
    unittest.main()
