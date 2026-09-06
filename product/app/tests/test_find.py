"""Tests for the free contact finder.

This is the one page a stranger reaches with no account, so it is also the
one page an anonymous stranger can aim at us. Most of these are about what it
refuses to do rather than what it does.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_app import AppTestCase  # noqa: E402

ADVERT_WITH_A_PERSON = """
Maintenance Electrician, Aberdeen. Three shift rotation.
To apply, send your CV to sarah.mcleod@northernplant.co.uk or click Apply.
"""

ADVERT_WITH_A_GENERIC_INBOX = """
Site Engineer wanted. Competitive salary.
Enquiries to info@somebuilder.co.uk
"""

ADVERT_WITH_NOTHING = """
Field Service Engineer. Apply through our careers portal.
No agencies. Strictly no telephone enquiries.
"""


class TheFreeTool(AppTestCase):

    def find(self, advert):
        return self.client.post("/find", data={"advert": advert}).text

    def test_it_needs_no_account(self):
        r = self.client.get("/find")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Paste any job advert", r.text)

    def test_it_finds_a_named_person(self):
        page = self.find(ADVERT_WITH_A_PERSON)
        self.assertIn("sarah.mcleod@northernplant.co.uk", page)
        self.assertIn("named person", page)

    def test_it_says_when_an_inbox_is_only_generic(self):
        page = self.find(ADVERT_WITH_A_GENERIC_INBOX)
        self.assertIn("info@somebuilder.co.uk", page)
        self.assertIn("generic inbox", page)

    def test_finding_nothing_is_a_normal_answer_not_an_error(self):
        """Most adverts have no address. Saying so plainly is the honest
        result and the best argument for the paid thing."""
        page = self.find(ADVERT_WITH_NOTHING)
        self.assertIn("Nothing in the advert itself", page)
        self.assertNotIn("note bad", page)

    def test_an_empty_paste_asks_again_rather_than_pretending(self):
        page = self.find("   ")
        self.assertIn("Paste the advert text first", page)

    def test_it_never_invents_an_address(self):
        """The rule the whole product rests on. A company name and a domain
        must not become firstname@domain."""
        page = self.find("Electrician wanted at Northern Plant Ltd. "
                         "See northernplant.co.uk for more.")
        self.assertNotIn("@northernplant.co.uk", page)

    def test_what_the_visitor_pasted_is_escaped_not_executed(self):
        page = self.find("<script>alert(1)</script> jobs@example.com")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("jobs@example.com", page)

    def test_a_huge_paste_is_truncated_rather_than_chewed_on(self):
        from app import main
        page = self.find("filler " * 100_000 + " late@example.com")
        # Cut at MAX_ADVERT, so the address past the cut never arrives.
        self.assertNotIn("late@example.com", page)
        self.assertLess(main.MAX_ADVERT, 100_000)

    def test_it_is_rate_limited_per_machine(self):
        limit, _window = self.main.FIND_PER_IP
        for _ in range(limit):
            self.find(ADVERT_WITH_A_PERSON)
        self.assertIn("a lot of adverts", self.find(ADVERT_WITH_A_PERSON))

    def test_the_page_sends_people_on_to_the_method_and_the_product(self):
        page = self.find(ADVERT_WITH_A_PERSON)
        self.assertIn('href="/playbook"', page)
        self.assertIn('href="/"', page)


if __name__ == "__main__":
    unittest.main()
