"""
Tests for asking support organisations whether they can help.

Offline. No charity is contacted.

These letters go to charities, whose time and money belong to people who need
them, so the rules here are stricter than anywhere else in the project.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_machine as jm  # noqa: E402
import support_outreach as so  # noqa: E402


class TestTheLetter(unittest.TestCase):
    def bodies(self):
        return [so.compose({"name": "X", "group": g})[1]
                for g in ("veteran", "young", "local", "industry", "")]

    def test_it_asks_rather_than_asserts(self):
        for body in self.bodies():
            self.assertIn("Is either of those something you are able to help",
                          body)
            for claim in ("I am entitled", "I qualify", "as a veteran I am owed"):
                self.assertNotIn(claim.lower(), body.lower())

    def test_it_says_nothing_about_his_finances(self):
        """His situation is not something I know, and inventing hardship in his
        name to a charity would be indefensible."""
        for body in self.bodies():
            low = body.lower()
            for invented in ("cannot afford", "cannot fund", "no money",
                             "struggling financially", "hardship", "broke",
                             "desperate", "in debt"):
                self.assertNotIn(invented, low)

    def test_it_leads_with_the_thing_that_actually_blocks_him(self):
        for body in self.bodies():
            self.assertIn("OPITO", body)
            self.assertLess(body.index("OPITO"), body.index("driving licence"))

    def test_it_says_what_he_is_doing_for_himself(self):
        for body in self.bodies():
            self.assertIn("applying steadily", body)

    def test_every_fact_in_it_is_one_we_hold(self):
        for body in self.bodies():
            self.assertIn("07398 530978", body)
            self.assertIn("DV clearance", body)

    def test_the_subject_line_says_who_is_writing(self):
        for group in ("veteran", "young", "local", "industry"):
            subject, _ = so.compose({"name": "X", "group": group})
            self.assertLessEqual(len(subject), 60)
            self.assertTrue(subject.endswith("?"))


class TestWhoGetsWrittenTo(unittest.TestCase):
    def test_the_shipped_list_covers_all_three_routes(self):
        groups = {o.get("group") for o in so.load_orgs()}
        for expected in ("veteran", "young", "local", "industry"):
            self.assertIn(expected, groups)

    def test_an_organisation_already_dealt_with_is_left_alone(self):
        """He has signed up with the Forces Employment Charity himself."""
        self.assertNotIn("forcesemployment.org.uk",
                         [o.get("domain") for o in so.load_orgs()])

    def test_nobody_is_asked_twice(self):
        state = {"jobs": {}, "support_asked": {}}
        org = {"name": "The King's Trust", "domain": "kingstrust.org.uk",
               "group": "young"}
        self.assertFalse(so.already_asked(state, org))
        so.record(state, org, "info@kingstrust.org.uk")
        self.assertTrue(so.already_asked(state, org))


class TestAddressesAreNeverGuessed(unittest.TestCase):
    """The same rule as the rest of the project: a real, MX-checked address or
    no email at all."""

    def test_no_domain_means_no_email(self):
        self.assertEqual(so.find_address({"name": "X"}), (None, None))

    def test_a_domain_with_no_mx_is_refused(self):
        with mock.patch.object(jm, "has_mx", return_value=False):
            self.assertEqual(so.find_address({"domain": "nowhere.example"}),
                             (None, None))

    def test_a_site_with_no_address_on_it_is_refused(self):
        with mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site", return_value=[]):
            self.assertEqual(so.find_address({"domain": "acme.org"}),
                             (None, None))

    def test_a_real_address_is_used(self):
        with mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site",
                               return_value=["grants@acme.org"]):
            address, _ = so.find_address({"domain": "acme.org"})
        self.assertEqual(address, "grants@acme.org")

    def test_a_run_without_send_writes_to_nobody(self):
        state = {"jobs": {}, "support_asked": {}}
        with mock.patch.object(so, "find_address",
                               return_value=("grants@acme.org", None)), \
             mock.patch.object(jm, "send_email") as send:
            so.run(state, send=False)
        send.assert_not_called()
        self.assertEqual(state["support_asked"], {})

    def test_an_organisation_with_no_findable_address_is_simply_skipped(self):
        state = {"jobs": {}, "support_asked": {}}
        with mock.patch.object(so, "find_address", return_value=(None, None)), \
             mock.patch.object(jm, "send_email") as send:
            so.run(state, send=True)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
