"""Contact classification: the 4x lever, so it gets the most tests.

Two things must never happen, and most of these guard one or the other:

  - a shared inbox greeted by a first name ("Dear Fundraise")
  - a real person refused a greeting because a role word hides inside
    their name ('hr' inside 'chris')
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.pipeline import contacts as c  # noqa: E402


class TestTiers(unittest.TestCase):
    def test_a_named_person_is_tier_three_and_greetable(self):
        for address, expected in (
            ("jane.smith@acme.co.uk", "Jane"),
            ("sarah@kestrel.com", "Sarah"),
            ("chris.brown@acme.com", "Chris"),
            ("frances.doyle@acme.com", "Frances"),
        ):
            tier, name = c.classify(address)
            self.assertEqual(tier, 3, address)
            self.assertEqual(name, expected, address)

    def test_an_initial_is_a_person_but_not_a_greeting(self):
        # 'j.smith' is plainly a human, but 'Dear J' is worse than 'Hi,'.
        tier, name = c.classify("j.smith@acme.com")
        self.assertEqual(tier, 3)
        self.assertIsNone(name)

    def test_a_surname_with_an_initial_stuck_on_is_not_greeted(self):
        tier, name = c.classify("jsmith@acme.com")
        self.assertEqual(tier, 3)
        self.assertIsNone(name)

    def test_hiring_inboxes_are_tier_two_and_never_greeted(self):
        for address in ("careers@acme.com", "hr@acme.com", "recruitment@acme.com",
                        "jobs@acme.com", "talent@acme.com", "vacancies@acme.com"):
            tier, name = c.classify(address)
            self.assertEqual(tier, 2, address)
            self.assertIsNone(name, address)

    def test_a_role_word_anywhere_still_means_a_shared_inbox(self):
        # The bug this was written for: 'mysupporthr@' was greeted by name.
        tier, name = c.classify("mysupporthr@acme.com")
        self.assertLess(tier, 3)
        self.assertIsNone(name)

    def test_generic_inboxes_are_tier_one(self):
        for address in ("info@acme.com", "hello@acme.com", "enquiries@acme.com",
                        "reception@acme.com"):
            tier, name = c.classify(address)
            self.assertEqual(tier, 1, address)
            self.assertIsNone(name, address)

    def test_departments_that_are_not_people_are_never_greeted(self):
        # Real misfires from the original: 'Dear Fundraise', 'Dear Library'.
        for address in ("fundraising@charity.org", "library@college.ac.uk",
                        "volunteer@charity.org", "bookings@venue.com",
                        "estates@council.gov.uk"):
            _, name = c.classify(address)
            self.assertIsNone(name, address)

    def test_a_place_is_a_desk_but_not_a_person(self):
        tier, name = c.classify("aberdeen@acme.com")
        self.assertEqual(tier, 1)
        self.assertIsNone(name)

    def test_frances_is_a_woman_not_a_country(self):
        # 'frances' starts with 'france'; a prefix rule gets this wrong.
        self.assertFalse(c.is_place("frances"))
        self.assertEqual(c.classify("frances@acme.com"), (3, "Frances"))

    def test_chris_is_a_man_not_an_hr_desk(self):
        # 'hr' sits inside 'chris'.
        self.assertEqual(c.classify("chris@acme.com"), (3, "Chris"))

    def test_noreply_and_friends_are_unusable(self):
        for address in ("noreply@acme.com", "no-reply@acme.com",
                        "postmaster@acme.com", "unsubscribe@acme.com"):
            self.assertEqual(c.clean_emails([address]), [], address)


class TestNamePlausibility(unittest.TestCase):
    def test_real_names_pass(self):
        for word in ("jane", "chris", "sarah", "tom", "priya", "wojciech",
                     "stephen", "bradley", "clare"):
            self.assertTrue(c.plausible_first_name(word), word)

    def test_mangled_ones_do_not(self):
        for word in ("jsmith", "rbrown", "xz", "a", "j"):
            self.assertFalse(c.plausible_first_name(word), word)


class TestCleaning(unittest.TestCase):
    def test_free_mail_hosts_are_dropped(self):
        # Scraped personal addresses are how this method gets a bad name.
        self.assertEqual(
            c.clean_emails(["someone@gmail.com", "jane@acme.co.uk"]),
            ["jane@acme.co.uk"])

    def test_image_and_asset_lookalikes_are_dropped(self):
        self.assertEqual(c.clean_emails(["logo@2x.png", "a@b.css"]), [])

    def test_punctuation_and_case_are_normalised(self):
        self.assertEqual(c.clean_emails(["  Jane.Smith@ACME.co.uk. "]),
                         ["jane.smith@acme.co.uk"])

    def test_off_domain_addresses_are_dropped_when_a_domain_is_given(self):
        got = c.clean_emails(["jane@acme.com", "bob@other.com",
                              "sue@careers.acme.com"], domain="acme.com")
        self.assertEqual(got, ["jane@acme.com", "sue@careers.acme.com"])

    def test_rubbish_is_dropped(self):
        self.assertEqual(c.clean_emails(["not-an-email", "a@b@c", ""]), [])


class TestRanking(unittest.TestCase):
    def test_a_person_outranks_a_hiring_inbox_outranks_info(self):
        ranked = c.rank(["info@acme.com", "careers@acme.com", "jane@acme.com"])
        self.assertEqual([r["tier"] for r in ranked], [3, 2, 1])
        self.assertEqual(ranked[0]["email"], "jane@acme.com")

    def test_unusable_addresses_are_dropped_entirely(self):
        # Numeric and single-letter locals belong to nobody identifiable.
        ranked = c.rank(["12345@acme.com", "x@acme.com", "jane@acme.com"])
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["email"], "jane@acme.com")

    def test_an_unpronounceable_local_is_still_a_person_just_not_greeted(self):
        # Deliberate split: is_personal is permissive about what might be a
        # human (initials, unusual spellings), while the greeting is strict.
        # Writing to it is fine; calling it "Dear Zzz" is not.
        tier, name = c.classify("zzz@acme.com")
        self.assertEqual(tier, 3)
        self.assertIsNone(name)

    def test_the_users_own_region_wins_a_tie(self):
        ranked = c.rank(["houston@acme.com", "sheffield@acme.com"],
                        home_places=("sheffield", "rotherham"))
        self.assertEqual(ranked[0]["email"], "sheffield@acme.com")

    def test_order_is_kept_within_a_tier_so_the_advert_address_stays_first(self):
        # Addresses printed in the advert replied at 67%. The caller passes
        # them first; ranking must not shuffle them behind a scraped peer.
        ranked = c.rank(["jane@acme.com", "bob@acme.com"])
        self.assertEqual([r["email"] for r in ranked],
                         ["jane@acme.com", "bob@acme.com"])

    def test_best_returns_nothing_when_nothing_is_usable(self):
        self.assertIsNone(c.best(["noreply@acme.com"]))
        self.assertIsNone(c.best([]))

    def test_tier_names_are_reported_for_the_ui(self):
        self.assertEqual(c.best(["jane@acme.com"])["tier_name"], "named person")
        self.assertEqual(c.best(["careers@acme.com"])["tier_name"], "hiring inbox")


class TestHomePlacesFromProfile(unittest.TestCase):
    def test_home_places_come_from_the_users_own_search_area(self):
        from jobseeker.profile import Profile, Role
        p = Profile(name="Sam", location="Sheffield, England",
                    phone="07700 900123", situation="unemployed",
                    min_salary_annual=30000, priorities=["money"],
                    target_roles=["technician"],
                    locations=["Sheffield", "Rotherham"],
                    history=[Role(title="Tech", org="Acme", detail="x")])
        places = c.home_places_from(p)
        self.assertIn("sheffield", places)
        self.assertIn("rotherham", places)
        # and it actually changes the ranking
        ranked = c.rank(["houston@acme.com", "sheffield@acme.com"],
                        home_places=places)
        self.assertEqual(ranked[0]["email"], "sheffield@acme.com")


class TestDomains(unittest.TestCase):
    def test_plausible_tlds(self):
        for d in ("acme.co.uk", "acme.com", "acme.scot", "acme.org.uk"):
            self.assertTrue(c.plausible_domain(d), d)

    def test_far_flung_tlds_are_a_sign_of_a_wrong_match(self):
        for d in ("acme.com.br", "acme.ru", "acme.tk", ""):
            self.assertFalse(c.plausible_domain(d), d)


if __name__ == "__main__":
    unittest.main()


class TheWrongDeskEntirely(unittest.TestCase):
    """Addresses that are real, staffed, and must never get a job application.

    Every one of these was written to for real. complaints@matchtech.com was
    greeted "Hi Complaints,"; board@gevernova.com was greeted "Hi Board,";
    investors@ ranked top because an unrecognised word looked like a name.
    Wrong department is not a near miss - it is the kind of thing an employer
    remembers about a candidate.
    """

    def refuses(self, address):
        tier, name = c.classify(address)
        self.assertEqual(tier, 0, f"{address} is still writable")
        self.assertIsNone(name, f"{address} would be greeted by name")

    def test_the_complaints_desk(self):
        self.refuses("complaints@matchtech.com")

    def test_investor_relations(self):
        self.refuses("investors@company.com")
        self.refuses("investor.relations@company.com")

    def test_the_board(self):
        self.refuses("board@gevernova.com")

    def test_others_of_the_same_kind(self):
        for local in ("audit", "ombudsman", "refunds", "billing", "governance",
                      "trustees", "shareholders", "disputes", "chairman"):
            self.refuses(f"{local}@company.com")

    # -- and the names it must not take down with them -----------------
    def test_a_man_called_boardman_is_not_the_board(self):
        tier, name = c.classify("boardman@company.co.uk")
        self.assertEqual(tier, 3)
        self.assertEqual(name, "Boardman")

    def test_a_woman_called_frances_is_not_france(self):
        self.assertEqual(c.classify("frances@company.com"), (3, "Frances"))

    def test_a_real_person_at_the_same_company_still_gets_through(self):
        self.assertEqual(c.classify("sarah.mcleod@company.co.uk"),
                         (3, "Sarah"))
