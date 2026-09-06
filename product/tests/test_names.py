"""Company identity: the cases that cost real people something.

Every example in the second class is a bug the original machine actually
shipped, found in its own logs, and fixed. They are here so the port cannot
lose them.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.names import (CORPORATE_WORDS, company_key,  # noqa: E402
                             name_tokens, same_company)


class TestCompanyKey(unittest.TestCase):
    def test_legal_suffixes_collapse(self):
        for a, b in (("ACME Ltd.", "Acme Limited"),
                     ("Pennine Foods Ltd", "Pennine Foods"),
                     ("Kestrel PLC", "kestrel"),
                     ("Brightwater Holdings", "Brightwater")):
            self.assertTrue(same_company(a, b), f"{a} vs {b}")

    def test_noise_words_are_stripped_anywhere_not_just_at_the_end(self):
        # The weakness an endswith rule has: these are one employer, and
        # emailing both is emailing the same people twice.
        self.assertTrue(same_company("Acme Group Services", "Acme"))
        self.assertTrue(same_company("The Acme Solutions Ltd", "Acme"))
        self.assertTrue(same_company("Acme International", "Acme"))

    def test_punctuation_and_case_do_not_matter(self):
        self.assertTrue(same_company("O'Brien & Sons, Ltd.", "obrien sons"))

    def test_genuinely_different_firms_stay_apart(self):
        self.assertFalse(same_company("Pennine Foods", "Pennine Steel"))
        self.assertFalse(same_company("Wood", "Woodforest National Bank"))
        self.assertFalse(same_company("Acme", ""))

    def test_spaces_survive_so_tokens_work(self):
        self.assertEqual(company_key("Grace May"), "grace may")
        self.assertEqual(name_tokens("Grace May"), {"grace", "may"})


class TestTheExpensiveMistakes(unittest.TestCase):
    """Each of these sent, or nearly sent, an application to the wrong firm."""

    def test_wood_is_not_woodforest_national_bank(self):
        # A note meant for Wood plc reached Woodforest National Bank, because
        # the match was on a raw substring.
        wanted = name_tokens("Wood")
        hit = name_tokens("Woodforest National Bank")
        self.assertNotIn("wood", hit, "'wood' must not match inside 'woodforest'")
        self.assertFalse(wanted <= hit)

    def test_a_single_word_name_identifies_nobody(self):
        # 'Sanctuary' the housing association matched Sanctuary Clothing in
        # California - and a named person there. One token is not an identity,
        # so nothing but an exact match may be accepted.
        self.assertEqual(len(name_tokens("Sanctuary")), 1)
        self.assertTrue(name_tokens("Sanctuary") <= name_tokens("Sanctuary Clothing"),
                        "subset alone would accept this, which is why one "
                        "token must require an exact match instead")
        self.assertFalse(same_company("Sanctuary", "Sanctuary Clothing"))

    def test_grace_may_is_not_grace_and_may_home(self):
        # A recruiter matched a home furnishings shop. The extra word decides:
        # 'home' is a business, not a corporate suffix.
        wanted = name_tokens("Grace May")
        hit = name_tokens("Grace and May Home")
        self.assertTrue(wanted <= hit, "subset holds, so subset is not enough")
        extra = hit - wanted
        self.assertFalse(extra <= CORPORATE_WORDS,
                         "'home' is a business word and must reject the match")

    def test_baker_hughes_company_is_baker_hughes(self):
        # The counter-example: the extra word IS a corporate suffix.
        wanted = name_tokens("Baker Hughes")
        hit = name_tokens("Baker Hughes Company")
        self.assertTrue(wanted <= hit)
        self.assertTrue((hit - wanted) <= CORPORATE_WORDS)


if __name__ == "__main__":
    unittest.main()
