"""
Tests for asking a trade body for a conversation.

Offline. Nobody is contacted.

The rules this file protects are the ones that keep a conversation request
from quietly becoming something else - a job pitch, or worse, a solicitation
for money, which as a cold email would be a financial promotion under
section 21 of FSMA and a criminal offence to send without authorisation.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_machine as jm  # noqa: E402
import networking_outreach as no  # noqa: E402


class TestTheLetter(unittest.TestCase):
    def letter(self, name="Global Underwater Hub"):
        return no.compose({"name": name, "domain": "example.org"})

    def test_it_asks_for_a_conversation(self):
        _, body = self.letter()
        self.assertIn("short conversation", body)

    def test_it_names_the_body_it_is_writing_to(self):
        _, body = self.letter("Offshore Energies UK")
        self.assertIn("Offshore Energies UK", body)

    def test_it_never_asks_for_a_job(self):
        """A trade body does not hire technicians. An email treating one as an
        employer wastes the single approach we get."""
        subject, body = self.letter()
        for pitch in ("vacancy", "vacancies", "apply", "application",
                      "cv attached", "role at", "position at", "hiring",
                      "any openings", "put me on your books"):
            self.assertNotIn(pitch, body.lower(), pitch)
            self.assertNotIn(pitch, subject.lower(), pitch)

    def test_it_never_mentions_money_in_any_form(self):
        """Money never appears in a cold email from this project. Not phrased
        carefully - absent."""
        subject, body = self.letter()
        for money in ("invest", "investor", "funding", "fund my", "grant",
                      "loan", "capital", "backing", "sponsor", "money"):
            self.assertNotIn(money, body.lower(), money)
            self.assertNotIn(money, subject.lower(), money)

    def test_it_states_only_facts_we_hold(self):
        _, body = self.letter()
        self.assertIn("Aberdeen", body)
        self.assertIn("Sonardyne", body)
        self.assertIn("Royal Navy", body)
        self.assertIn("07398 530978", body)
        self.assertIsNone(jm.claims_clearance(body))

    def test_it_stays_short(self):
        """No vacancy to match and no case to argue - anything longer starts
        to read as a pitch for something."""
        _, body = self.letter()
        self.assertLess(len(body.split()), 140)

    def test_the_subject_asks_rather_than_sells(self):
        subject, _ = self.letter()
        self.assertTrue(subject.endswith("?"))
        self.assertLessEqual(len(subject), 70)


class TestWhoGetsWrittenTo(unittest.TestCase):
    def test_the_shipped_list_has_real_domains(self):
        targets = no.load_targets()
        self.assertGreaterEqual(len(targets), 3)
        for target in targets:
            with self.subTest(target=target["name"]):
                self.assertTrue(target.get("domain"))
                self.assertNotIn("@", json.dumps(target),
                                 "no contact address is ever recorded here")

    def test_nobody_is_asked_twice(self):
        state = {"jobs": {}}
        target = {"name": "Global Underwater Hub",
                  "domain": "globalunderwaterhub.com"}
        self.assertFalse(no.already_asked(state, target))
        no.record(state, target, "info@globalunderwaterhub.com")
        self.assertTrue(no.already_asked(state, target))

    def test_a_blocked_body_is_skipped_before_discovery_is_spent(self):
        block = [{"name": "Global Underwater Hub", "reason": "asked to stop"}]
        state = {"jobs": {}}
        with mock.patch.object(no.jm, "load_do_not_contact", return_value=block), \
             mock.patch.object(no, "find_address",
                               return_value=(None, None)) as find, \
             mock.patch.object(no.jm, "send_email") as send:
            no.run(state, send=True)
        send.assert_not_called()
        for call in find.call_args_list:
            self.assertNotEqual(call.args[0]["name"], "Global Underwater Hub")


class TestAddressesAreNeverGuessed(unittest.TestCase):
    def test_no_domain_means_no_email(self):
        self.assertEqual(no.find_address({"name": "X"}), (None, None))

    def test_a_domain_with_no_mx_is_refused(self):
        with mock.patch.object(jm, "has_mx", return_value=False):
            self.assertEqual(no.find_address({"domain": "nowhere.example"}),
                             (None, None))

    def test_a_site_with_no_address_on_it_is_refused(self):
        with mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site", return_value=([], [])):
            self.assertEqual(no.find_address({"domain": "acme.org"}),
                             (None, None))

    def test_a_real_address_is_used(self):
        with mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site",
                               return_value=(["info@acme.org"], [])):
            address, _ = no.find_address({"domain": "acme.org"})
        self.assertEqual(address, "info@acme.org")


class TestSending(unittest.TestCase):
    def target(self):
        return {"name": "Global Underwater Hub",
                "domain": "globalunderwaterhub.com"}

    def test_a_dry_run_writes_to_nobody(self):
        state = {"jobs": {}}
        with mock.patch.object(no, "find_address",
                               return_value=("info@x.org", None)), \
             mock.patch.object(jm, "send_email") as send:
            no.run(state, send=False)
        send.assert_not_called()
        self.assertEqual(state.get("network_asked", {}), {})

    def test_no_cv_is_ever_attached(self):
        """A CV is a job-application artifact; attaching one turns this into
        the job pitch it deliberately is not."""
        state = {"jobs": {}}
        with mock.patch.object(no, "load_targets", return_value=[self.target()]), \
             mock.patch.object(no, "find_address",
                               return_value=("info@x.org", None)), \
             mock.patch.object(no.jm, "save"), \
             mock.patch.object(no.time, "sleep"), \
             mock.patch.object(no.jm, "send_email") as send:
            no.run(state, send=True)
        send.assert_called_once()
        self.assertFalse(send.call_args.kwargs["attach_cv"])

    def test_a_body_with_no_findable_address_is_simply_skipped(self):
        state = {"jobs": {}}
        with mock.patch.object(no, "find_address", return_value=(None, None)), \
             mock.patch.object(jm, "send_email") as send:
            no.run(state, send=True)
        send.assert_not_called()

    def test_a_successful_send_is_recorded_so_it_never_repeats(self):
        state = {"jobs": {}}
        with mock.patch.object(no, "load_targets", return_value=[self.target()]), \
             mock.patch.object(no, "find_address",
                               return_value=("info@x.org", None)), \
             mock.patch.object(no.jm, "save"), \
             mock.patch.object(no.time, "sleep"), \
             mock.patch.object(no.jm, "send_email"):
            no.run(state, send=True)
            self.assertTrue(no.already_asked(state, self.target()))
            sent_again = no.run(state, send=True)
        self.assertEqual(sent_again, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
