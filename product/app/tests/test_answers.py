"""The pages written to be the answer an assistant quotes.

An assistant that recommends something is not remembering it: it runs a search
as it answers and quotes the pages that come back. So these are not marketing
pages and the tests are not about marketing. They check the three things that
decide whether a page can be quoted at all - that it is reachable, that its
answer is in the markup where a parser looks, and that the structured data
says the same thing the prose does - plus the one thing that matters more than
being quoted, which is that a page being repeated at scale is not repeating
something false.
"""

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_sending import Base  # noqa: E402


class TestAnswerPages(Base):
    def setUp(self):
        super().setUp()
        from app import answers
        self.answers = answers

    def shown(self, text):
        """What that text looks like once Jinja has escaped it into the page.
        Every one of these questions contains an apostrophe sooner or later,
        and a test comparing against the unescaped string passes today and
        fails on the first question written the way a person speaks."""
        from markupsafe import escape
        return str(escape(text))

    def ld(self, page):
        """The JSON-LD on a page, parsed. Parsed rather than pattern matched,
        because markup that does not parse is markup no reader will act on and
        a substring check would never notice."""
        found = re.search(
            r'<script type="application/ld\+json">(.*?)</script>',
            page, re.S)
        self.assertIsNotNone(found, "no structured data on the page")
        return json.loads(found.group(1))

    # -- reachable -----------------------------------------------------
    def test_every_answer_has_a_page_and_needs_no_account(self):
        for a in self.answers.ANSWERS:
            r = self.client.get(f"/answers/{a.slug}")
            self.assertEqual(r.status_code, 200, a.slug)
            self.assertIn(self.shown(a.question), r.text)

    def test_the_index_lists_all_of_them(self):
        page = self.client.get("/answers").text
        for a in self.answers.ANSWERS:
            self.assertIn(f"/answers/{a.slug}", page)
            self.assertIn(self.shown(a.question), page)

    def test_an_unknown_answer_is_a_404_not_a_crash(self):
        r = self.client.get("/answers/how-do-i-become-a-wizard")
        self.assertEqual(r.status_code, 404)

    # -- quotable ------------------------------------------------------
    def test_the_answer_is_the_first_thing_under_the_question(self):
        """Question as the heading, answer as the paragraph under it. That
        order is the shape a machine reading the page looks for, and it is
        also the right order for a person who arrived with the question."""
        for a in self.answers.ANSWERS:
            page = self.client.get(f"/answers/{a.slug}").text
            heading = page.index(f"<h1>{self.shown(a.question)}</h1>")
            # Searched from the heading onwards, not from the top: the same
            # words appear earlier in the structured data, and finding them
            # there would pass this test on a page whose visible prose said
            # something else entirely.
            body = page[heading:]
            self.assertIn(self.shown(a.answer), body, a.slug)

    def test_the_structured_data_says_what_the_page_says(self):
        """Markup claiming one answer while the text gives another is worse
        than no markup: it is the reason a parser learns to ignore a site."""
        for a in self.answers.ANSWERS:
            data = self.ld(self.client.get(f"/answers/{a.slug}").text)
            self.assertEqual(data["@type"], "FAQPage", a.slug)
            entry = data["mainEntity"][0]
            self.assertEqual(entry["name"], a.question, a.slug)
            self.assertEqual(entry["acceptedAnswer"]["text"], a.answer, a.slug)

    def test_the_index_is_a_list_not_a_claim_to_hold_the_answers(self):
        data = self.ld(self.client.get("/answers").text)
        self.assertEqual(data["@type"], "ItemList")
        self.assertEqual(len(data["itemListElement"]),
                         len(self.answers.ANSWERS))
        self.assertEqual(data["itemListElement"][0]["position"], 1)

    def test_an_answer_stands_up_on_its_own(self):
        """It gets lifted and shown somewhere this site cannot follow it, with
        no heading above it and no page around it. If it needs the page to
        make sense, it will be quoted as something else."""
        for a in self.answers.ANSWERS:
            self.assertGreater(len(a.answer), 120, a.slug)
            # A figure, because the reason to quote this rather than any of
            # the hundred pages of generic advice is that it counted something.
            self.assertTrue(re.search(r"\d", a.answer),
                            f"{a.slug}: no figure in the answer")
            for vague in ("as mentioned above", "on this page", "below",
                          "as we saw"):
                self.assertNotIn(vague, a.answer.lower(), a.slug)

    # -- findable ------------------------------------------------------
    def test_they_are_in_the_sitemap(self):
        xml = self.client.get("/sitemap.xml").text
        self.assertIn("/answers</loc>", xml)
        for a in self.answers.ANSWERS:
            self.assertIn(f"/answers/{a.slug}</loc>", xml)

    def test_robots_does_not_shut_them_out(self):
        """The disallow list is by prefix, so a new public section is one
        careless line away from being invisible."""
        robots = self.client.get("/robots.txt").text
        for line in robots.splitlines():
            if line.startswith("Disallow:"):
                self.assertFalse(line.split(":", 1)[1].strip().startswith("/answers"),
                                 robots)

    def test_something_links_to_them_from_every_page(self):
        """A page in the sitemap and linked from nowhere is a page crawlers
        reach last and readers never reach at all."""
        for path in ("/", "/find", "/playbook"):
            self.assertIn('href="/answers"', self.client.get(path).text, path)

    # -- honest --------------------------------------------------------
    def test_the_numbers_match_the_playbook_they_came_from(self):
        """These pages are the playbook's findings split up, so a figure that
        drifts from it is one of the two wrong. Checked against the source
        document rather than against a copy of the figures."""
        playbook = open(os.path.join(ROOT, "PLAYBOOK.md"), encoding="utf-8").read()
        for claim in ("86", "26%", "67%", "38%", "50%", "10%", "21%"):
            self.assertIn(claim, playbook, f"{claim} is not in the playbook")

        pages = "".join(self.client.get(f"/answers/{a.slug}").text
                        for a in self.answers.ANSWERS)
        # The sample size travels with every rate quoted anywhere on the site.
        # A rate without it is the kind of number this product exists not to
        # print, and these are the pages most likely to be repeated.
        self.assertIn("86", pages)

    def test_a_thin_row_is_never_quoted_without_its_caveat(self):
        """67% is nine emails and 50% is ten. Both are in the playbook with a
        warning attached, and both are exactly the sort of figure that gets
        lifted on its own."""
        for slug, rate, caveat in (
                ("find-hiring-manager", "67%", "small sample"),
                ("email-directly", "50%", "ten emails"),
                ("why-no-reply", "50%", "only ten emails"),
                ("info-at-address", "50%", "only ten emails"),
                ("recruitment-agency", "50%", "far too few"),
                ("good-reply-rate", "50%", "out of ten"),
                ("no-contact-details", "67%", "small sample")):
            page = self.client.get(f"/answers/{slug}").text
            self.assertIn(rate, page, slug)
            self.assertIn(caveat, page, slug)

    def test_the_timing_rates_reconcile_with_their_own_counts(self):
        """The timing page is the only one quoting the live 166-application
        set rather than the fixed 86-email study, so its figures cannot be
        checked against the playbook. They can be checked against each other,
        which is the next best thing and catches the failure that actually
        happens: a rate updated and the count under it left alone."""
        page = self.client.get("/answers/best-time-to-send").text
        for sent, replied, rate in ((110, 21, "19.1%"), (56, 10, "17.9%")):
            self.assertIn(f">{sent}<", page)
            self.assertIn(f">{replied}<", page)
            self.assertEqual(f"{100 * replied / sent:.1f}%", rate,
                             f"{replied}/{sent} is not {rate}")
            self.assertIn(rate, page)

    def test_no_page_tells_anybody_to_guess_an_address(self):
        """The whole differentiator, and the one claim that must never slip.
        Every competing page on this search tells the reader to guess a
        pattern and verify it."""
        for a in self.answers.ANSWERS:
            page = self.client.get(f"/answers/{a.slug}").text.lower()
            if "firstname.lastname" in page:
                self.assertTrue(
                    "never guess" in page or "do not guess" in page
                    or "not to" in page,
                    f"{a.slug} shows an email pattern without refusing it")


if __name__ == "__main__":
    unittest.main()
