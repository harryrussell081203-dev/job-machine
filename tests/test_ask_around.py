"""
Tests for asking people how they got in.

Offline. Nothing is sent, no site is fetched.

This avenue is different in kind from everything else here: it asks for
nothing. The tests are mostly about what it must NOT do, because the failure
mode is not a wasted email - it is a letter that reads as a mailshot dressed
as a question, which burns the contact permanently and makes Harry look like
somebody worth ignoring.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ask_around as aa  # noqa: E402
import job_machine as jm  # noqa: E402


class TestTheLetter(unittest.TestCase):
    def setUp(self):
        self.subject, self.body = aa.compose(
            "Sarah McLeod", "OPITO",
            "OPITO sets the BOSIET and MIST standards themselves")

    def test_it_asks_exactly_one_question(self):
        """Three questions cannot be answered from a phone, so three
        questions get no reply at all."""
        self.assertEqual(self.body.count("?"), 1)

    def test_it_says_out_loud_that_he_is_looking(self):
        """The pure-advice letter that gets round to the job later is a
        trick, and the reader works it out on the reply."""
        self.assertIn("what i am trying to do", self.body.lower())
        self.assertIn("offshore", self.body.lower())

    def test_it_does_not_ask_for_a_job(self):
        low = self.body.lower()
        self.assertIn("not asking you for a job", low)
        for forbidden in ("are you hiring", "any vacancies", "any openings",
                          "opportunities at", "consider me"):
            self.assertNotIn(forbidden, low)

    def test_it_is_honest_about_the_tickets_and_the_experience(self):
        low = self.body.lower()
        self.assertIn("no civilian offshore time", low)
        self.assertIn("none of the tickets", low)
        self.assertIn("job centre", low)

    def test_it_never_claims_offshore_experience(self):
        low = self.body.lower()
        self.assertNotIn("years offshore", low)
        self.assertNotIn("offshore experience i", low)

    def test_the_reason_for_writing_is_specific_to_them(self):
        """Filler is what makes a letter look sent rather than written, and
        the reader spots it instantly."""
        self.assertIn("BOSIET and MIST standards", self.body)

    def test_it_greets_a_real_first_name(self):
        self.assertTrue(self.body.startswith("Hello Sarah,"))

    def test_no_name_is_still_a_civil_letter(self):
        _, body = aa.compose("", "OPITO", "you set the standards")
        self.assertTrue(body.startswith("Hello,"))


class TestWhoItWillNotWriteTo(unittest.TestCase):
    def test_never_his_own_employer(self):
        """A cold email to your current employer asking how to get out is a
        message that can reach a line manager. What it costs is not a wasted
        approach - it is the job he already has."""
        for name in ("Sonardyne International", "Sonardyne", "sonardyne ltd"):
            with self.subTest(name=name):
                self.assertTrue(aa.his_own_employer(name))
        self.assertFalse(aa.due({}, {"organisation": "Sonardyne"}))

    def test_his_employer_is_not_in_the_file_either(self):
        names = " ".join(o["organisation"] for o in aa.load_people())
        self.assertNotIn("Sonardyne", names)

    def test_never_a_shared_inbox(self):
        """A letter to info@ asking how you got into the industry is a letter
        to nobody, and it is what turns this into a mailshot."""
        with mock.patch.object(jm, "find_domain", return_value="opito.com"), \
             mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site", return_value=["info@opito.com"]), \
             mock.patch.object(jm, "best_email",
                               return_value=("info@opito.com", None, 1)):
            self.assertIsNone(aa.find_person("OPITO"))

    def test_a_real_named_person_is_used(self):
        with mock.patch.object(jm, "find_domain", return_value="opito.com"), \
             mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site", return_value=[]), \
             mock.patch.object(jm, "best_email",
                               return_value=("s.mcleod@opito.com", "Sarah", 3)):
            found = aa.find_person("OPITO")
        self.assertEqual(found["name"], "Sarah")

    def test_never_twice_into_the_same_building(self):
        state = {"companies_contacted": {jm.company_key("OPITO"): {}}}
        self.assertFalse(aa.due(state, {"organisation": "OPITO"}))

    def test_never_the_same_person_twice(self):
        state = {aa.ASKED: {jm.company_key("OPITO"): {"at": jm.now()}}}
        self.assertFalse(aa.due(state, {"organisation": "OPITO"}))


class TestItStaysSmall(unittest.TestCase):
    """This only works while it looks like a man asking around. The moment it
    looks like a campaign it stops working."""

    def test_they_are_spaced_out(self):
        state = {aa.ASKED: {"x": {"at": jm.now()}}}
        self.assertTrue(aa.too_soon(state))

    def test_an_empty_register_is_not_too_soon(self):
        self.assertFalse(aa.too_soon({}))

    def test_one_at_a_time_by_default(self):
        self.assertLessEqual(aa.PER_RUN, 2)

    def test_no_cv_is_ever_attached(self):
        """Attaching one turns this into an application and it gets filed as
        one. It is offered in the last paragraph instead."""
        state = {"companies_contacted": {}}
        with mock.patch.object(aa, "find_person",
                               return_value={"email": "s@opito.com",
                                             "name": "Sarah",
                                             "domain": "opito.com"}), \
             mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "send_email") as send:
            aa.run(state, dry_run=False, limit=1)
        self.assertIs(send.call_args.kwargs["attach_cv"], False)

    def test_a_dry_run_sends_nothing(self):
        with mock.patch.object(aa, "find_person",
                               return_value={"email": "s@opito.com",
                                             "name": "Sarah",
                                             "domain": "opito.com"}), \
             mock.patch.object(jm, "send_email") as send:
            aa.run({"companies_contacted": {}}, dry_run=True, limit=1)
        send.assert_not_called()

    def test_an_organisation_with_no_named_person_is_noted_not_used_up(self):
        """See TestAMissIsNotAnAsk - a miss is a maybe-later, an ask is
        forever, and they must not share a register."""
        state = {"companies_contacted": {}}
        with mock.patch.object(aa, "find_person", return_value=None):
            aa.run(state, dry_run=False, limit=1)
        self.assertTrue(aa.missed_register(state))
        self.assertEqual(aa.asked_register(state), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestAMissIsNotAnAsk(unittest.TestCase):
    """The first version wrote 'no named person found' into the same register
    as 'asked'. One bad network day would have burned the entire list
    permanently, with nobody written to and nothing to show why."""

    def test_failing_to_find_somebody_does_not_use_them_up(self):
        state = {"companies_contacted": {}}
        with mock.patch.object(aa, "find_person", return_value=None):
            aa.run(state, dry_run=False, limit=1)
        self.assertEqual(aa.asked_register(state), {})
        self.assertTrue(aa.missed_register(state))

    def test_it_looks_again_later(self):
        """People get promoted onto the 'our team' page."""
        key = jm.company_key("OPITO")
        state = {"companies_contacted": {},
                 aa.MISSED: {key: "2020-01-01T00:00:00+00:00"}}
        self.assertTrue(aa.due(state, {"organisation": "OPITO"}))

    def test_but_not_the_very_next_day(self):
        key = jm.company_key("OPITO")
        state = {"companies_contacted": {}, aa.MISSED: {key: jm.now()}}
        self.assertFalse(aa.due(state, {"organisation": "OPITO"}))

    def test_somebody_actually_asked_is_never_asked_again(self):
        key = jm.company_key("OPITO")
        state = {"companies_contacted": {},
                 aa.ASKED: {key: {"at": "2020-01-01T00:00:00+00:00"}}}
        self.assertFalse(aa.due(state, {"organisation": "OPITO"}))
