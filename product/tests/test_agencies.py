"""Telling an agency's advert from an employer's, and reading its terms."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker import agencies as a  # noqa: E402


class WhoPlacedIt(unittest.TestCase):
    def test_the_legal_disclosure_is_enough_on_its_own(self):
        self.assertEqual(a.advertiser(
            "Northern Talent", "Great role. Northern Talent is acting as an "
            "Employment Agency in relation to this vacancy."), a.AGENCY)
        self.assertEqual(a.advertiser(
            "Xyz", "Xyz is acting as an employment business."), a.AGENCY)

    def test_our_client_is_an_agency_talking(self):
        self.assertEqual(a.advertiser(
            "Bluefin", "Our client, a leading food manufacturer, needs..."),
            a.AGENCY)

    def test_agency_words_in_the_name(self):
        for name in ("Scot Recruitment Ltd", "Apex Resourcing",
                     "Blue Personnel", "Staffing Solutions UK",
                     "North Search & Selection"):
            self.assertEqual(a.advertiser(name), a.AGENCY, name)

    def test_the_big_brands(self):
        for name in ("Hays", "Hays Specialist Ltd", "Adecco UK", "Morson Group",
                     "Allstaff"):
            self.assertEqual(a.advertiser(name), a.AGENCY, name)

    def test_an_ordinary_employer_is_an_employer(self):
        for name, text in (("Pennine Foods", "Join our packaging team."),
                           ("Hayston Engineering", "We build gearboxes."),
                           ("Morsons Bakery", "Family bakery since 1950."),
                           ("Aberdeen City Council", "")):
            self.assertEqual(a.advertiser(name, text), a.EMPLOYER, name)

    def test_working_with_clients_is_not_our_client(self):
        self.assertEqual(a.advertiser(
            "Brightwater", "You will visit clients on site."), a.EMPLOYER)


class WhatTerms(unittest.TestCase):
    def test_the_title_wins(self):
        self.assertEqual(a.contract("Temporary Warehouse Operative",
                                    "permanent opportunity"), a.TEMP)
        self.assertEqual(a.contract("Electrician - 6 month contract"),
                         a.CONTRACT)
        self.assertEqual(a.contract("Fitter (Permanent)"), a.PERMANENT)

    def test_adzunas_own_field(self):
        self.assertEqual(a.contract("Fitter", "", "permanent"), a.PERMANENT)
        self.assertEqual(a.contract("Fitter", "", "contract"), a.CONTRACT)
        self.assertEqual(a.contract("Fitter", "Temporary cover for 8 weeks",
                                    "contract"), a.TEMP)

    def test_the_text_when_the_title_is_silent(self):
        self.assertEqual(a.contract("Fitter", "This is a fixed-term role"),
                         a.CONTRACT)
        self.assertEqual(a.contract("Fitter", "Day rate, outside IR35"),
                         a.CONTRACT)
        self.assertEqual(a.contract("Picker", "Temp to perm, weekly pay"),
                         a.TEMP)

    def test_silence_is_unknown_not_a_guess(self):
        self.assertEqual(a.contract("Fitter", "Join our team."), "")


if __name__ == "__main__":
    unittest.main()
