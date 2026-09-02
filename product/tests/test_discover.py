"""Finding an address, and refusing to invent one.

The domain-matching tests are the ones that matter. Each corresponds to an
application the original machine sent, or nearly sent, to a completely
different company.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.pipeline import discover as d  # noqa: E402
from jobseeker.pipeline.harvest import Listing  # noqa: E402
from jobseeker.profile import Profile, Role  # noqa: E402


def profile(**over):
    base = dict(name="Sam Doherty", location="Sheffield", phone="07700 900123",
                situation="unemployed", min_salary_annual=30000,
                priorities=["money"], target_roles=["technician"],
                locations=["Sheffield"],
                history=[Role(title="Tech", org="Acme", detail="x")])
    base.update(over)
    return Profile(**base)


def listing(**over):
    base = dict(external_id="a1", source="adzuna", title="Technician",
                company="Pennine Foods", location="Rotherham", url="",
                description="Maintain the lines.")
    base.update(over)
    return Listing(**base)


class Resp:
    def __init__(self, payload=None, text="", status=200):
        self._payload, self.text, self.status_code = payload, text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class Session:
    """Routes by URL so a test can serve clearbit and a website at once."""

    def __init__(self, clearbit=None, pages=None):
        self.clearbit = clearbit if clearbit is not None else []
        self.pages = pages or {}
        self.fetched = []

    def get(self, url, **kw):
        self.fetched.append(url)
        if "clearbit" in url:
            return Resp(payload=self.clearbit)
        for fragment, html in self.pages.items():
            if fragment in url:
                return Resp(text=html)
        return Resp(text="", status=404)


class TestDomainMatching(unittest.TestCase):
    def test_an_exact_name_matches(self):
        self.assertTrue(d.domain_matches("Pennine Foods", "Pennine Foods Ltd",
                                         "penninefoods.co.uk"))

    def test_wood_does_not_match_woodforest_national_bank(self):
        # A note meant for Wood plc reached Woodforest National Bank.
        self.assertFalse(d.domain_matches("Wood", "Woodforest National Bank",
                                          "woodforest.com"))

    def test_a_single_word_name_needs_an_exact_match(self):
        # 'Sanctuary' matched Sanctuary Clothing in California, and a named
        # person there.
        self.assertFalse(d.domain_matches("Sanctuary", "Sanctuary Clothing",
                                          "sanctuaryclothing.com"))
        self.assertTrue(d.domain_matches("Sanctuary", "Sanctuary",
                                         "sanctuary.co.uk"))

    def test_grace_may_does_not_match_grace_and_may_home(self):
        # A recruiter matched a furniture shop: subset holds, but 'home' is a
        # business word, not a corporate suffix.
        self.assertFalse(d.domain_matches("Grace May", "Grace and May Home",
                                          "graceandmayhome.com"))

    def test_baker_hughes_company_is_baker_hughes(self):
        self.assertTrue(d.domain_matches("Baker Hughes", "Baker Hughes Company",
                                         "bakerhughes.com"))

    def test_an_implausible_tld_is_refused(self):
        # A far-flung ccTLD means a different company with a similar name.
        self.assertFalse(d.domain_matches("Pennine Foods", "Pennine Foods",
                                          "penninefoods.ru"))

    def test_an_empty_company_matches_nothing(self):
        self.assertFalse(d.domain_matches("", "Anything", "anything.com"))


class TestFindDomain(unittest.TestCase):
    def test_a_confident_hit_is_returned(self):
        s = Session(clearbit=[{"name": "Pennine Foods Ltd",
                               "domain": "penninefoods.co.uk"}])
        self.assertEqual(d.find_domain("Pennine Foods", session=s),
                         "penninefoods.co.uk")

    def test_a_wrong_company_is_rejected_rather_than_used(self):
        s = Session(clearbit=[{"name": "Woodforest National Bank",
                               "domain": "woodforest.com"}])
        self.assertIsNone(d.find_domain("Wood", session=s))

    def test_no_hits_is_not_an_error(self):
        self.assertIsNone(d.find_domain("Nowhere Ltd", session=Session()))

    def test_a_clearbit_failure_is_survived(self):
        class Broken:
            def get(self, *a, **k):
                raise RuntimeError("clearbit down")
        self.assertIsNone(d.find_domain("Pennine Foods", session=Broken()))


class TestEmailExtraction(unittest.TestCase):
    def test_plain_addresses_are_found(self):
        self.assertIn("jane@acme.com", d.emails_in("write to jane@acme.com today"))

    def test_mailto_links_are_found_and_decoded(self):
        self.assertIn("jane@acme.com",
                      d.emails_in('<a href="mailto:jane%40acme.com">mail</a>'))

    def test_nothing_in_empty_text(self):
        self.assertEqual(d.emails_in(""), [])
        self.assertEqual(d.emails_in(None), [])


class TestScrapeSite(unittest.TestCase):
    def test_addresses_are_collected_across_pages(self):
        s = Session(pages={"/contact": "reach jane.smith@acme.com",
                           "/team": "bob@acme.com and old@other.com"})
        got = d.scrape_site("acme.com", session=s, paths=("/contact", "/team"),
                            delay=0)
        self.assertIn("jane.smith@acme.com", got)
        self.assertIn("bob@acme.com", got)
        self.assertNotIn("old@other.com", got, "off-domain address kept")

    def test_a_dead_site_yields_nothing_rather_than_raising(self):
        self.assertEqual(d.scrape_site("gone.com", session=Session(),
                                       paths=("/",), delay=0), [])


class TestDiscover(unittest.TestCase):
    def test_an_address_in_the_advert_wins(self):
        # 67% reply rate, the best source there is.
        l = listing(description="Send your CV to claire.hughes@pennine.co.uk")
        s = Session(clearbit=[{"name": "Pennine Foods", "domain": "pennine.co.uk"}],
                    pages={"pennine.co.uk": "info@pennine.co.uk"})
        got = d.discover(l, profile(), session=s, delay=0)
        self.assertEqual(got["email"], "claire.hughes@pennine.co.uk")
        self.assertEqual(got["name"], "Claire")
        self.assertEqual(got["tier"], 3)
        self.assertEqual(got["source"], "listing")

    def test_the_site_is_used_when_the_advert_has_nothing(self):
        s = Session(clearbit=[{"name": "Pennine Foods", "domain": "pennine.co.uk"}],
                    pages={"pennine.co.uk": "careers@pennine.co.uk"})
        got = d.discover(listing(), profile(), session=s, delay=0)
        self.assertEqual(got["email"], "careers@pennine.co.uk")
        self.assertEqual(got["tier"], 2)
        self.assertEqual(got["source"], "scraped")

    def test_a_named_person_outranks_a_generic_inbox(self):
        s = Session(clearbit=[{"name": "Pennine Foods", "domain": "pennine.co.uk"}],
                    pages={"pennine.co.uk": "info@pennine.co.uk jane@pennine.co.uk"})
        got = d.discover(listing(), profile(), session=s, delay=0)
        self.assertEqual(got["email"], "jane@pennine.co.uk")

    def test_nothing_found_means_no_letter(self):
        # 516 of the original's listings ended here. That is correct.
        self.assertIsNone(d.discover(listing(), profile(), session=Session(),
                                     delay=0))

    def test_an_address_is_never_invented_from_a_pattern(self):
        # The site is reachable but publishes nothing, so there is no address.
        # A guessed firstname.lastname@ would bounce and cost the sending
        # reputation that delivers the next thirty letters.
        s = Session(clearbit=[{"name": "Pennine Foods", "domain": "pennine.co.uk"}],
                    pages={"pennine.co.uk": "<p>No contact details here.</p>"})
        self.assertIsNone(d.discover(listing(), profile(), session=s, delay=0))

    def test_a_free_mail_address_is_not_used(self):
        s = Session(clearbit=[],
                    pages={})
        l = listing(description="email me at somebody@gmail.com")
        self.assertIsNone(d.discover(l, profile(), session=s, delay=0))

    def test_the_users_own_region_is_preferred_on_a_tie(self):
        p = profile(locations=["Sheffield"])
        s = Session(clearbit=[{"name": "Pennine Foods", "domain": "pennine.co.uk"}],
                    pages={"pennine.co.uk": "houston@pennine.co.uk sheffield@pennine.co.uk"})
        got = d.discover(listing(), p, session=s, delay=0)
        self.assertEqual(got["email"], "sheffield@pennine.co.uk")


if __name__ == "__main__":
    unittest.main()
