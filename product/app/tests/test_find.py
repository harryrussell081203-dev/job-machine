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
        """Asserted on the promise rather than the sentence. The headline and
        lede here get rewritten - they were rewritten the night the buttons
        turned out to be below the fold - and a test that breaks on a comma
        teaches people to edit the test instead of thinking."""
        r = self.client.get("/find")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no account", r.text)
        self.assertIn("advert", r.text.lower())

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



class TestTheResultCanBeShared(AppTestCase):
    """Somebody who has just watched the tool work on their own advert is the
    most convinced this product will ever make anyone. Until now there was
    nothing for them to do about it."""

    def _result_page(self):
        return self.client.post("/find", data={
            "advert": "Field service engineer. CV to hannah.doyle@example.com"
        }).text

    def test_a_share_control_is_offered_on_a_result(self):
        self.assertIn('id="shareit"', self._result_page())

    def test_it_is_hidden_until_the_browser_can_actually_share(self):
        # A button that does nothing when pressed is worse than no button, and
        # neither navigator.share nor the clipboard exists everywhere.
        page = self._result_page()
        self.assertIn('id="shareit" hidden', page)
        self.assertIn("navigator.share", page)
        self.assertIn("navigator.clipboard", page)

    def test_the_shared_link_points_at_the_free_tool(self):
        # Not the pricing page. The tool is what converts, because it is the
        # thing the recommender just watched work.
        self.assertIn('"/find"', self._result_page())


class TheExampleMustBeReachable(AppTestCase):
    """The example button worked perfectly and nobody could see it.

    It sat below a rows=10 textarea, which is 280px of empty rectangle. On an
    iPhone SE you had to scroll 277px to learn there was anything to press; on
    an iPhone 13, 85px. So somebody landed on a headline and a big blank box
    with no visible control, and left. Nineteen people did exactly that in one
    evening, and the fix that was supposed to remove the friction was itself
    the thing they could not reach.

    Measured in a real mobile browser rather than reasoned about, which is why
    it was found at all. These tests hold the ORDER, which is what actually
    decides it: no arithmetic here can predict a font, but an action that
    comes before the tall box stays above the fold on any phone.
    """

    def test_the_example_comes_before_the_paste_box(self):
        page = self.client.get("/find").text
        example = page.find('id="tryexample"')
        box = page.find('id="advert"')
        self.assertNotEqual(example, -1, "the example button is gone")
        self.assertLess(example, box,
                        "the example is below the textarea again, which puts "
                        "it off the bottom of a small phone")

    def test_the_box_is_not_tall_enough_to_push_everything_off_screen(self):
        """rows=10 was the specific number that did it."""
        import re
        page = self.client.get("/find").text
        rows = re.search(r'id="advert"[^>]*rows="(\d+)"', page) \
            or re.search(r'rows="(\d+)"[^>]*id="advert"', page)
        self.assertIsNotNone(rows, "could not find the textarea's rows")
        self.assertLessEqual(int(rows.group(1)), 7,
                             "a taller box pushes the submit button off a "
                             "small screen again")

    def test_the_example_is_the_primary_action(self):
        """For somebody arriving with nothing in their clipboard it is the
        only thing they can do, so it must not be the quiet ghost button."""
        import re
        page = self.client.get("/find").text
        tag = re.search(r'<button[^>]*id="tryexample"[^>]*>', page)
        self.assertIsNotNone(tag)
        self.assertNotIn("ghost", tag.group(0))


if __name__ == "__main__":
    unittest.main()
