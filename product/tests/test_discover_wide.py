"""The wider website search the employer directory uses.

Clearbit alone found a website for one company in forty. The two extra
sources are only worth having if they are as hard to fool as the first:
these hold that a site counts only when it names itself as the company,
that a parked domain or a redirect elsewhere does not, and that a one-word
name never matches on a guess.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.pipeline import discover as d  # noqa: E402


class Resp:
    def __init__(self, payload=None, text="", status=200, url=""):
        self._payload, self.text, self.status_code = payload, text, status
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def home(title, extra=""):
    return (f"<html><head><title>{title}</title>{extra}</head><body>"
            + "Welcome. " * 50 + "</body></html>")


class Web:
    """Clearbit, Wikidata and home pages, by URL. `sites` maps a domain to
    (html, the address it ends up at)."""

    def __init__(self, clearbit=None, search=None, entities=None, sites=None):
        self.clearbit = clearbit or []
        self.search = search or []
        self.entities = entities or {}
        self.sites = sites or {}
        self.fetched = []

    def get(self, url, params=None, **kw):
        self.fetched.append(url)
        if "clearbit" in url:
            return Resp(payload=self.clearbit)
        if "wikidata" in url:
            if params["action"] == "wbsearchentities":
                return Resp(payload={"search": self.search})
            return Resp(payload={"entities": self.entities})
        for domain, (html, final) in self.sites.items():
            if url in (f"https://www.{domain}/", f"https://{domain}/"):
                return Resp(text=html, url=final or url)
        return Resp(status=404)


def entity(site, country="Q145"):
    claims = {"P856": [{"mainsnak": {"datavalue": {"value": site}}}]}
    if country:
        claims["P17"] = [{"mainsnak": {"datavalue": {"value": {"id": country}}}}]
    return {"claims": claims}


class FromWikidata(unittest.TestCase):
    def test_the_official_website_of_the_same_name(self):
        web = Web(search=[{"id": "Q1", "label": "Booker Group"}],
                  entities={"Q1": entity("https://www.booker.co.uk/home")})
        self.assertEqual(d.from_wikidata("Booker Group", session=web),
                         "booker.co.uk")

    def test_a_different_name_is_not_used(self):
        web = Web(search=[{"id": "Q1", "label": "Booker Prize"}],
                  entities={"Q1": entity("https://thebookerprizes.com")})
        self.assertIsNone(d.from_wikidata("Booker Group", session=web))

    def test_one_word_needs_a_uk_company(self):
        web = Web(search=[{"id": "Q1", "label": "Sanctuary"}],
                  entities={"Q1": entity("https://sanctuary.com", country="Q30")})
        self.assertIsNone(d.from_wikidata("Sanctuary", session=web))

    def test_a_failure_is_survived(self):
        class Broken:
            def get(self, *a, **k):
                raise RuntimeError("down")
        self.assertIsNone(d.from_wikidata("Booker Group", session=Broken()))


class TheSiteMustSaySo(unittest.TestCase):
    def test_its_own_title(self):
        self.assertTrue(d.site_says("Clark Contracts Ltd", "clarkcontracts.co.uk",
                                    home("Clark Contracts | Building contractors")))
        self.assertTrue(d.site_says("Clark Contracts Ltd", "clarkcontracts.co.uk",
                                    home("Home", '<meta property="og:site_name" '
                                                 'content="Clark Contracts">')))

    def test_a_near_miss_is_not_enough(self):
        # The extra word is a different business, as in names.py.
        self.assertFalse(d.site_says("Grace May", "gracemay.co.uk",
                                     home("Grace and May Home | Furniture")))
        self.assertFalse(d.site_says("Clark Contracts", "clarkcontracts.co.uk",
                                     home("Welcome")))

    def test_a_parked_domain_never_counts(self):
        self.assertFalse(d.site_says(
            "Clark Contracts", "clarkcontracts.co.uk",
            home("Clark Contracts", "<p>This domain is for sale</p>")))


class FromGuess(unittest.TestCase):
    def test_the_obvious_addresses(self):
        self.assertIn("clarkcontracts.co.uk", d.guesses("Clark Contracts Ltd"))
        self.assertIn("clark-contracts.com", d.guesses("Clark Contracts Ltd"))
        self.assertIn("bookergroup.co.uk", d.guesses("Booker Group"))
        # One word: UK addresses only.
        self.assertFalse([g for g in d.guesses("Hyble") if g.endswith(".com")])

    def test_kept_only_when_the_site_names_itself(self):
        web = Web(sites={
            "clarkcontracts.com": (home("Clark Electrical Supplies"), ""),
            "clarkcontracts.co.uk": (home("Clark Contracts - Glasgow"), ""),
        })
        live = {"clarkcontracts.com", "clarkcontracts.co.uk"}
        self.assertEqual(d.from_guess("Clark Contracts", session=web,
                                      resolves=lambda x: x in live),
                         "clarkcontracts.co.uk")

    def test_a_redirect_to_another_site_is_not_theirs(self):
        web = Web(sites={"clarkcontracts.co.uk": (
            home("Clark Contracts"), "https://www.somebodyelse.com/")})
        self.assertIsNone(d.from_guess(
            "Clark Contracts", session=web,
            resolves=lambda x: x == "clarkcontracts.co.uk"))

    def test_nothing_resolves_nothing_fetched(self):
        web = Web()
        self.assertIsNone(d.from_guess("Clark Contracts", session=web,
                                       resolves=lambda x: False))
        self.assertEqual(web.fetched, [])


class TheOrder(unittest.TestCase):
    def setUp(self):
        d.FOUND_BY.clear()

    def test_clearbit_first_then_wikidata_then_checked(self):
        web = Web(clearbit=[{"name": "Clark Contracts", "domain": "clark.co.uk"}])
        self.assertEqual(d.find_domain_wide("Clark Contracts", session=web,
                                            resolves=lambda x: False),
                         "clark.co.uk")
        web = Web(search=[{"id": "Q1", "label": "Clark Contracts"}],
                  entities={"Q1": entity("clarkcontracts.co.uk")})
        self.assertEqual(d.find_domain_wide("Clark Contracts", session=web,
                                            resolves=lambda x: False),
                         "clarkcontracts.co.uk")
        web = Web(sites={"clarkcontracts.co.uk": (home("Clark Contracts"), "")})
        self.assertEqual(d.find_domain_wide(
            "Clark Contracts", session=web,
            resolves=lambda x: x == "clarkcontracts.co.uk"),
            "clarkcontracts.co.uk")
        self.assertIsNone(d.find_domain_wide("Clark Contracts", session=Web(),
                                             resolves=lambda x: False))
        self.assertEqual(d.FOUND_BY, {"clearbit": 1, "wikidata": 1,
                                      "checked": 1, "none": 1})


if __name__ == "__main__":
    unittest.main()
