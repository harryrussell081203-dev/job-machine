"""
Tests for building the Armed Forces Covenant list from the official register.

Offline. The register is never touched.

Every employer on that register is a company where Harry's Royal Navy service
moves him from the pile to the shortlist, so a name wrongly dropped here is a
guaranteed interview never asked for.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import covenant_list as cl  # noqa: E402


INDEX_HTML = (
    '<a href="/armed-forces-covenant-businesses/petrofac">Petrofac</a>'
    '<a href="/armed-forces-covenant-businesses/port-of-aberdeen">'
    'Port of Aberdeen</a>'
    '<a href="/some/other/page">Not a business</a>')


def api(results, total=None):
    return json.dumps({"total": total if total is not None else len(results),
                       "results": results})


class TestReadingTheRegister(unittest.TestCase):
    """Two routes to the same names. The first version had one, built its URLs
    from a template, and the template was wrong - an --inspect run on a machine
    with open access to GOV.UK got zero characters back for the letter A,
    because that page is really at /armed-forces-corporate-covenant-signed-
    pledges. The slugs are not consistent, so they are no longer guessed."""

    def test_business_pages_are_paged_out_of_the_search_index(self):
        def fake(url, session=None):
            if "filter_document_type" in url:
                if "start=0" in url or "start" not in url:
                    return api([{"title": "Petrofac",
                                 "link": "/armed-forces-covenant-businesses/petrofac"},
                                {"title": "Port of Aberdeen",
                                 "link": "/armed-forces-covenant-businesses/port-of-aberdeen"}])
                return api([])
            return api([{"link": "/armed-forces-covenant-businesses/petrofac",
                         "document_type": "covenant_business"}])
        with mock.patch.object(cl, "fetch", side_effect=fake):
            found = cl.signatories_via_api()
        self.assertEqual(found["petrofac"], "Petrofac")
        self.assertEqual(found["port-of-aberdeen"], "Port of Aberdeen")

    def test_the_az_page_urls_come_from_search_not_from_a_template(self):
        """The A page is not where its name suggests, so it must be looked up."""
        real = ("/government/publications/"
                "armed-forces-corporate-covenant-signed-pledges")
        with mock.patch.object(cl, "fetch", return_value=api([
                {"title": "Businesses who have signed the Armed Forces "
                          "Covenant (company names beginning with A)",
                 "link": real},
                {"title": "Something else entirely", "link": "/unrelated"}])):
            pages = cl.register_pages()
        self.assertEqual(pages, [f"https://www.gov.uk{real}"])

    def test_names_are_read_off_the_az_pages_too(self):
        def fake(url, session=None):
            if url.startswith(cl.SEARCH_API):
                return api([{"title": "Businesses who have signed the Covenant",
                             "link": "/government/publications/x"}])
            return INDEX_HTML
        with mock.patch.object(cl, "fetch", side_effect=fake):
            found = cl.signatories_via_pages()
        self.assertEqual(set(found), {"petrofac", "port-of-aberdeen"})

    def test_either_route_alone_still_produces_the_list(self):
        """They break for different reasons - a renamed document type, a
        restructured publication - so one surviving is the whole point."""
        def only_pages(url, session=None):
            if "filter_document_type" in url or "document_type" in url:
                return ""
            if url.startswith(cl.SEARCH_API):
                return api([{"title": "Businesses who have signed the Covenant",
                             "link": "/government/publications/x"}])
            return INDEX_HTML
        with mock.patch.object(cl, "fetch", side_effect=only_pages):
            self.assertIn("petrofac", cl.signatories())

    def test_an_unreachable_register_yields_nothing_rather_than_nonsense(self):
        with mock.patch.object(cl, "fetch", return_value=""):
            self.assertEqual(cl.signatories(), {})

    def test_html_where_json_was_expected_is_not_mistaken_for_data(self):
        with mock.patch.object(cl, "fetch", return_value="<html>nope</html>"):
            self.assertEqual(cl.signatories_via_api(), {})


class TestChoosingWhoIsWorthAsking(unittest.TestCase):
    def test_an_aberdeen_engineering_firm_is_kept(self):
        self.assertTrue(cl.worth_keeping(
            "Bilfinger Salamis UK Ltd",
            "We commit to honour the Covenant. Based in Aberdeen, providing "
            "offshore maintenance services."))

    def test_a_scottish_utility_is_kept(self):
        self.assertTrue(cl.worth_keeping(
            "Scottish Water", "Water and waste engineering across Scotland."))

    def test_a_firm_in_his_trade_outside_scotland_is_kept(self):
        """This used to be excluded. It was written when the whole search was a
        commute from Aberdeen, and it is not - he will take rotational and
        offshore work anywhere that comes with somewhere to live. Screening out
        a Covenant employer for being in Kent throws away the exact thing this
        list exists to find."""
        self.assertTrue(cl.worth_keeping(
            "Southern Electronics Ltd", "Electronics manufacturing in Kent."))

    def test_a_firm_in_the_wrong_trade_is_not(self):
        self.assertFalse(cl.worth_keeping(
            "Highland Bakery", "We bake bread in Inverness."))

    def test_geography_orders_the_list_instead_of_filtering_it(self):
        self.assertEqual(cl.nearness("Aberdeen Marine", "Based in Aberdeen"), 0)
        self.assertEqual(cl.nearness("Southern Electronics", "Kent"), 1)

    def test_the_trade_test_is_generous_on_purpose(self):
        """A wrong name only means an employer is asked a question they can
        answer with 'no'. A missing one is an interview never asked for."""
        for name in ("Aberdeen Marine Systems", "Grampian Controls",
                     "Fife Fabrication", "Glasgow Rail Maintenance"):
            with self.subTest(name=name):
                self.assertTrue(cl.worth_keeping(name, ""))


class TestMerging(unittest.TestCase):
    def file_with(self, employers):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"_README": ["notes"], "employers": employers}, handle)
        handle.close()
        return handle.name

    def test_curated_entries_are_never_lost(self):
        path = self.file_with([{"company": "Thales", "award": "gold",
                                "note": "hand added"}])
        self.addCleanup(os.unlink, path)
        data, added = cl.merge([{"company": "Petrofac", "award": "signatory",
                                 "note": "from the register"}], path=path)
        names = [e["company"] for e in data["employers"]]
        self.assertIn("Thales", names)
        self.assertIn("Petrofac", names)
        self.assertEqual(len(added), 1)

    def test_a_company_already_listed_is_not_duplicated(self):
        path = self.file_with([{"company": "Petrofac Ltd", "award": "gold",
                                "note": "hand added"}])
        self.addCleanup(os.unlink, path)
        data, added = cl.merge([{"company": "Petrofac", "award": "signatory",
                                 "note": "from the register"}], path=path)
        self.assertEqual(added, [])
        self.assertEqual(len(data["employers"]), 1)

    def test_every_entry_carries_the_page_it_came_from(self):
        with mock.patch.object(cl, "signatories",
                               return_value={"acme-marine": "Acme Marine"}), \
             mock.patch.object(cl, "pledge_text",
                               return_value="Based in Aberdeen, subsea engineering."):
            found = cl.build()
        self.assertEqual(len(found), 1)
        self.assertIn("gov.uk/armed-forces-covenant-businesses/acme-marine",
                      found[0]["source"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
