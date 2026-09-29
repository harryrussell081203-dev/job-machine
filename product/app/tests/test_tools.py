"""The free tools: indexable, honest, and gone when switched off.

  1. THE CHECKER finds what it says it finds - stock phrases, American
     spellings, flat rhythm, repeats, long dashes - and leaves a plain
     human letter alone.
  2. NOTHING PASTED IS STORED, and the page escapes it.
  3. EVERY TOOL PAGE is in the sitemap and llms.txt, has its own title,
     description and Open Graph tags, and links to /find.
  4. SWITCHED OFF, the pages 404 and leave the sitemap.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app import letter_check  # noqa: E402
from app.tests.test_app import AppTestCase  # noqa: E402

AI_LETTER = """Dear Hiring Manager,

I am writing to apply for the Maintenance Technician role. I am excited to
bring my wealth of experience and strong work ethic to your organization. My
skills align with your needs and I am confident that I would be a valuable
asset — a true testament to my dedication — to the team."""

HUMAN_LETTER = """Hi Claire, saw the maintenance technician role on your
Rotherham packaging lines. That is the work I do now. Four years of PLC fault
finding on fillers. I cut unplanned downtime on our main line by a third.
Could I send my availability for a call this week?"""


class TheChecker(unittest.TestCase):
    def test_an_ai_style_letter_scores_low_and_says_why(self):
        r = letter_check.check(AI_LETTER)
        kinds = {f.kind for f in r.findings}
        self.assertLess(r.score, 60)
        self.assertTrue({"phrase", "spelling", "dash"} <= kinds, kinds)

    def test_a_plain_human_letter_is_left_alone(self):
        r = letter_check.check(HUMAN_LETTER)
        self.assertEqual(r.findings, [])
        self.assertEqual(r.score, 100)

    def test_american_spelling_names_the_british_form(self):
        r = letter_check.check("I would organize the color coding.")
        spelling = next(f for f in r.findings if f.kind == "spelling")
        self.assertIn("organise", spelling.detail)
        self.assertIn("colour", spelling.detail)

    def test_flat_rhythm(self):
        r = letter_check.check("One two three four five six seven eight. " * 6)
        self.assertIn("rhythm", {f.kind for f in r.findings})

    def test_the_marked_text_is_the_whole_text(self):
        r = letter_check.check(AI_LETTER)
        self.assertEqual("".join(p for p, _ in r.marked), AI_LETTER)
        self.assertTrue(any(why for _, why in r.marked))

    def test_it_reuses_the_products_own_banned_list(self):
        from jobseeker.pipeline.compose import BANNED
        self.assertTrue(set(BANNED) <= set(letter_check.STOCK_PHRASES))


class ThePages(AppTestCase):
    def test_the_page_has_its_own_title_description_and_og(self):
        page = self.client.get("/tools/cover-letter-ai-check").text
        self.assertIn("<title>Does my cover letter sound like AI?", page)
        self.assertIn('og:title" content="Does my cover letter sound like AI?',
                      page)
        self.assertIn('name="description" content="Paste your cover letter',
                      page)
        self.assertIn('href="/find"', page)
        self.assertIn('"@type": "WebApplication"', page)

    def test_checking_shows_the_result_and_escapes_the_letter(self):
        page = self.client.post("/tools/cover-letter-ai-check", data={
            "letter": AI_LETTER + "<script>alert(1)</script>"}).text
        self.assertIn("Reads like AI wrote it", page)
        self.assertIn("<mark", page)
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_nothing_is_stored(self):
        before = self._rows()
        self.client.post("/tools/cover-letter-ai-check",
                         data={"letter": AI_LETTER})
        self.assertEqual(self._rows(), before)

    def _rows(self):
        db = self.main.db
        with db.connect() as c:
            return sum(c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                       for t in ("drafts", "profiles", "cvs", "events"))

    def test_the_index_links_every_tool_and_find(self):
        page = self.client.get("/tools").text
        self.assertIn('href="/tools/cover-letter-ai-check"', page)
        self.assertIn('href="/find"', page)

    def test_in_the_sitemap_and_llms_txt(self):
        self.assertIn("/tools/cover-letter-ai-check",
                      self.client.get("/sitemap.xml").text)
        self.assertIn("/tools/cover-letter-ai-check",
                      self.client.get("/llms.txt").text)
        self.assertIn("Sitemap:", self.client.get("/robots.txt").text)

    def test_switched_off_they_are_gone(self):
        with patch.dict(os.environ, {"TOOLS_ENABLED": "0"}):
            self.assertEqual(self.client.get(
                "/tools/cover-letter-ai-check").status_code, 404)
            self.assertEqual(self.client.get("/tools").status_code, 404)
            self.assertNotIn("/tools", self.client.get("/sitemap.xml").text)


if __name__ == "__main__":
    unittest.main()
