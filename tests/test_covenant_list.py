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


class TestReadingTheRegister(unittest.TestCase):
    def test_companies_are_pulled_off_an_index_page(self):
        html = ('<a href="/armed-forces-covenant-businesses/petrofac">Petrofac</a>'
                '<a href="/armed-forces-covenant-businesses/port-of-aberdeen">'
                'Port of Aberdeen</a>'
                '<a href="/some/other/page">Not a business</a>')
        with mock.patch.object(cl, "fetch", return_value=html):
            found = cl.signatories()
        self.assertEqual(found["petrofac"], "Petrofac")
        self.assertEqual(found["port-of-aberdeen"], "Port of Aberdeen")
        self.assertEqual(len(found), 2)

    def test_an_unreachable_register_yields_nothing_rather_than_nonsense(self):
        with mock.patch.object(cl, "fetch", return_value=""):
            self.assertEqual(cl.signatories(), {})


class TestChoosingWhoIsWorthAsking(unittest.TestCase):
    def test_an_aberdeen_engineering_firm_is_kept(self):
        self.assertTrue(cl.worth_keeping(
            "Bilfinger Salamis UK Ltd",
            "We commit to honour the Covenant. Based in Aberdeen, providing "
            "offshore maintenance services."))

    def test_a_scottish_utility_is_kept(self):
        self.assertTrue(cl.worth_keeping(
            "Scottish Water", "Water and waste engineering across Scotland."))

    def test_a_company_far_from_him_is_not(self):
        self.assertFalse(cl.worth_keeping(
            "Southern Electronics Ltd", "Electronics manufacturing in Kent."))

    def test_a_scottish_firm_in_the_wrong_trade_is_not(self):
        self.assertFalse(cl.worth_keeping(
            "Highland Bakery", "We bake bread in Inverness."))

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
        html = '<a href="/armed-forces-covenant-businesses/acme-marine">Acme Marine</a>'
        with mock.patch.object(cl, "fetch", return_value=html), \
             mock.patch.object(cl, "pledge_text",
                               return_value="Based in Aberdeen, subsea engineering."):
            found = cl.build()
        self.assertEqual(len(found), 1)
        self.assertIn("gov.uk/armed-forces-covenant-businesses/acme-marine",
                      found[0]["source"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
