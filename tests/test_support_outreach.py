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

    def test_the_forces_employment_charity_is_written_to_after_all(self):
        """This entry used to be skipped as 'already registered - RightJob'.

        Registered and still entitled are not the same thing. RightJob is the
        CTP job board, CTP support runs from two years before discharge to two
        years after, and Harry left in 2023 - so the resettlement door has
        closed behind him. Op ASCEND, which the same charity delivers, is the
        one that opens at exactly that point and lasts for life. The letter
        asks them to sign him up rather than assuming either way."""
        orgs = {o.get("domain"): o for o in so.load_orgs()}
        self.assertIn("forcesemployment.org.uk", orgs)
        self.assertEqual(orgs["forcesemployment.org.uk"]["ask"], "registration")

    def test_the_organisation_that_holds_the_covenant_list_is_still_skipped(self):
        """A central government mailbox is the wrong door for one job search."""
        self.assertNotIn("Defence Relationship Management",
                         [o.get("name") for o in so.load_orgs()])

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

    def test_a_verified_published_address_is_used_when_the_site_is_unreadable(self):
        """The big charities sit behind a bot check, which is not a reason to
        drop the letter - but it is only allowed if the file says where the
        address was read from."""
        org = {"name": "X", "domain": "acme.org",
               "email": "info@acme.org",
               "email_source": "their own contact page"}
        with mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site", return_value=[]):
            self.assertEqual(so.find_address(org), ("info@acme.org", None))

    def test_an_address_with_no_stated_source_is_refused(self):
        org = {"name": "X", "domain": "acme.org", "email": "info@acme.org"}
        with mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site", return_value=[]):
            self.assertEqual(so.find_address(org), (None, None))

    def test_the_scraped_address_still_wins(self):
        org = {"name": "X", "domain": "acme.org", "email": "info@acme.org",
               "email_source": "their own contact page"}
        with mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site",
                               return_value=["grants@acme.org"]):
            self.assertEqual(so.find_address(org)[0], "grants@acme.org")

    def test_every_shipped_address_says_where_it_came_from(self):
        for org in so.load_orgs():
            if org.get("email"):
                with self.subTest(org=org["name"]):
                    self.assertTrue(org.get("email_source"))

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


class TestAskingForEmployersRatherThanMoney(unittest.TestCase):
    """Some of these organisations have no money to give and are the wrong
    people to ask for any. What the RFCAs have is the list: which employers in
    a given area have committed to the Covenant, and which of those run a
    guaranteed interview scheme. Asking them for training funding would be
    asking exactly the right people exactly the wrong question."""

    def letter(self):
        return so.compose({"name": "Highland RFCA", "group": "veteran",
                           "ask": "employer introductions"})

    def test_it_asks_about_the_guaranteed_interview_scheme(self):
        _, body = self.letter()
        self.assertIn("guaranteed interview", body.lower())

    def test_it_asks_who_to_approach(self):
        _, body = self.letter()
        self.assertIn("north east of scotland", body.lower())

    def test_it_does_not_ask_them_for_money(self):
        """The funding letter's asks must not leak into this one."""
        _, body = self.letter()
        for money in ("thousand pounds", "funding", "driving licence",
                      "BEng", "OPITO BOSIET"):
            with self.subTest(money=money):
                self.assertNotIn(money.lower(), body.lower())

    def test_it_still_says_he_is_applying_on_his_own(self):
        _, body = self.letter()
        self.assertIn("on my own", body.lower())

    def test_the_funding_letter_is_unchanged_for_everyone_else(self):
        _, body = so.compose({"name": "Poppyscotland", "group": "veteran"})
        self.assertIn("OPITO", body)
        self.assertNotIn("guaranteed interview", body.lower())


class TestAskingToBeSignedUp(unittest.TestCase):
    """The Forces Employment Charity and CTP are not being asked for money or
    for introductions. They run the scheme itself, so the question is whether
    they will put him on it."""

    def letter(self):
        return so.compose({"name": "Forces Employment Charity",
                           "group": "veteran", "ask": "registration"})

    def test_it_asks_to_be_registered(self):
        _, body = self.letter()
        self.assertIn("register me for Op ASCEND", body)

    def test_it_asks_about_rightjob_rather_than_assuming(self):
        _, body = self.letter()
        low = body.lower()
        self.assertIn("rightjob still open to me", low)
        for claim in ("i am entitled to", "i still have access",
                      "my account is", "for life"):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, low)

    def test_it_states_the_two_year_rule_as_the_reason_for_writing(self):
        """Getting this wrong in either direction wastes their time: claiming
        entitlement he does not have, or not writing at all because a previous
        note in the data file said he was already signed up."""
        _, body = self.letter()
        self.assertIn("two years after", body)
        self.assertIn("past", body)

    def test_it_does_not_ask_them_for_money(self):
        _, body = self.letter()
        for money in ("thousand pounds", "OPITO BOSIET", "driving licence"):
            with self.subTest(money=money):
                self.assertNotIn(money.lower(), body.lower())

    def test_the_subject_says_what_it_wants(self):
        subject, _ = self.letter()
        self.assertIn("Op ASCEND", subject)
        self.assertLessEqual(len(subject), 60)

    def test_both_doors_are_asked(self):
        asks = {o["name"]: o.get("ask") for o in so.load_orgs()}
        self.assertEqual(asks.get("Forces Employment Charity"), "registration")
        self.assertEqual(asks.get("Career Transition Partnership"),
                         "registration")

    def test_this_errand_does_not_post_the_whole_charity_list(self):
        """One specific errand, not a reason to empty the queue behind it."""
        state = {"jobs": {}, "support_asked": {}}
        written = []
        with mock.patch.object(so, "find_address",
                               return_value=("info@example.org", None)), \
             mock.patch.object(so, "SUPPORT_INTERVAL_SECONDS", 0), \
             mock.patch.object(jm, "save"), \
             mock.patch.object(jm, "send_email",
                               side_effect=lambda *a, **k: written.append(a)):
            so.run(state, send=True, only="registration")
        self.assertEqual(len(written), 2)
        for subject in (call[1] for call in written):
            self.assertIn("Op ASCEND", subject)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTheRecruiterLetter(unittest.TestCase):
    """A recruiter is not being asked for help. A placeable technician is the
    thing their business runs on, so the letter tells them what is available
    rather than asking them for anything."""

    def letter(self):
        return so.compose({"name": "Orion Group", "group": "recruiter",
                           "ask": "representation"})

    def test_it_says_he_is_available_now(self):
        subject, body = self.letter()
        self.assertIn("available", (subject + body).lower())

    def test_it_leads_with_the_trade_not_with_the_navy(self):
        """A recruiter is placing a technician. The service record is
        evidence, not the headline."""
        _, body = self.letter()
        first = body.split("\n\n")[1].lower()
        self.assertIn("technician", first)

    def test_it_turns_the_no_licence_problem_into_the_rotational_pitch(self):
        _, body = self.letter()
        self.assertIn("do not drive", body.lower())
        self.assertIn("rotational", body.lower())

    def test_it_does_not_ask_a_recruiter_for_charity(self):
        _, body = self.letter()
        for wrong in ("funding", "OPITO BOSIET", "grant", "help with"):
            with self.subTest(wrong=wrong):
                self.assertNotIn(wrong.lower(), body.lower())


class TestTheCtpLetter(unittest.TestCase):
    """CTP is a service he is entitled to for life, not a favour. The account
    verifies his service record, so it has to be opened by him - the letter
    asks how and never pretends otherwise."""

    def letter(self):
        return so.compose({"name": "Career Transition Partnership",
                           "group": "veteran", "ask": "ctp access"})

    def test_it_asks_how_to_get_access(self):
        _, body = self.letter()
        self.assertIn("rightjob", body.lower())

    def test_it_states_the_entitlement_without_demanding_it(self):
        _, body = self.letter()
        self.assertIn("for life", body.lower())
        self.assertIn("could you tell me", body.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
