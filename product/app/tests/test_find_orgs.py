"""The filter that decides who gets written to.

This is the only part of the outreach chain with no human in front of it. A
letter goes to whoever this returns, so the failure that matters is not
missing a good organisation - that costs nothing but a name - it is including
a charity that never asked and whose time belongs to the people it exists for.

So these tests are weighted that way: a few for the obvious yeses, and most
for the near misses that a keyword match gets wrong.

They also cover the boring failure that is actually likeliest. The registers
rename their columns between releases, and a scraper that silently finds
nothing looks exactly like a register with nothing relevant in it.
"""

import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tools import find_orgs  # noqa: E402


class TestWhoCounts(unittest.TestCase):
    def yes(self, purpose, name=""):
        self.assertTrue(find_orgs.serves_jobseekers(purpose, name),
                        f"should have been kept: {name} / {purpose}")

    def no(self, purpose, name=""):
        self.assertFalse(find_orgs.serves_jobseekers(purpose, name),
                         f"should have been dropped: {name} / {purpose}")

    def test_the_organisations_this_exists_for(self):
        self.yes("To advance education and provide employability support to "
                 "young people aged 16-25 in Aberdeen.")
        self.yes("Runs a weekly job club and provides CV writing and "
                 "interview skills workshops.")
        self.yes("Helping veterans into work after service.",
                 "Forces Employment Careers Service")
        self.yes("Supports unemployed adults to return to work through "
                 "training and employment programmes.")

    def test_the_near_misses_a_keyword_match_gets_wrong(self):
        """Every one of these contains a wanted word and is not a target."""
        self.no("Provides free advice on employment law and represents "
                "workers at employment tribunal.")
        self.no("Provides sheltered employment for adults with learning "
                "disabilities in a supported workshop.")
        self.no("Maintains the village hall and supports the local scout "
                "group, including a careers advice evening once a year.")
        self.no("Promotes the welfare of animals and finds employment for "
                "retired greyhounds.")

    def test_it_reads_the_name_as_well_as_the_objects(self):
        """Registers often carry a one-line object and a telling name."""
        self.yes("Charitable purposes for the public benefit.",
                 "Dundee Employability Partnership")

    def test_silence_is_not_a_yes(self):
        self.no("")
        self.no("The advancement of the arts, heritage, culture or science.")


class TestReadingTheRegister(unittest.TestCase):
    HEADER = "Charity Name,Website,Postcode,Objectives\n"

    def rows(self, text):
        return list(find_orgs.rows_from_csv(text))

    def test_it_maps_columns_whatever_the_register_calls_them(self):
        for header in ("Charity Name,Website,Postcode,Objectives",
                       "charity_name,web,charity_postcode,charity_activities",
                       "NAME,URL,Post Code,Purposes"):
            rows = self.rows(header + "\nA,https://a.org,AB1 1AA,job club\n")
            self.assertEqual(rows[0]["name"], "A", header)
            self.assertEqual(rows[0]["website"], "https://a.org", header)

    def test_an_unrecognised_register_raises_rather_than_finding_nothing(self):
        """The failure this prevents: a renamed column, every row discarded
        for having no website, and a run that reports zero targets and looks
        like a quiet afternoon."""
        with self.assertRaises(find_orgs.SourceError):
            self.rows("col_a,col_b\n1,2\n")

    def test_a_file_with_no_header_raises(self):
        with self.assertRaises(find_orgs.SourceError):
            self.rows("")

    def test_fetch_without_a_url_says_so_instead_of_failing_obscurely(self):
        with self.assertRaises(find_orgs.SourceError):
            find_orgs.fetch("")


class TestSelecting(unittest.TestCase):
    def csv(self, *rows):
        out = io.StringIO()
        out.write("Charity Name,Website,Postcode,Objectives\n")
        for row in rows:
            out.write(",".join(row) + "\n")
        return out.getvalue()

    def test_no_website_means_not_a_target(self):
        """There is nothing to read and nothing to write to. An address is
        never guessed from a name, here or anywhere downstream."""
        text = self.csv(("Aberdeen Job Club", "", "AB1 1AA", "job club"))
        self.assertEqual(find_orgs.select(find_orgs.rows_from_csv(text)), [])

    def test_a_website_that_is_not_one_is_discarded(self):
        text = self.csv(("A", "not a website", "AB1", "job club"),
                        ("B", "n/a", "AB2", "job club"))
        self.assertEqual(find_orgs.select(find_orgs.rows_from_csv(text)), [])

    def test_one_letter_per_organisation_even_under_two_registrations(self):
        """The registers carry the same body more than once. Two letters to
        one inbox is the exact failure this approach is meant to avoid."""
        text = self.csv(
            ("Trust Ltd", "https://www.example.org", "AB1", "job club"),
            ("Trust (Scotland)", "http://example.org/about", "AB2",
             "employability"))
        picked = find_orgs.select(find_orgs.rows_from_csv(text))
        self.assertEqual(len(picked), 1)

    def test_it_tidies_the_address_without_inventing_one(self):
        text = self.csv(("A", "example.org/", "AB1", "job club"))
        picked = find_orgs.select(find_orgs.rows_from_csv(text))
        self.assertEqual(picked[0]["website"], "https://example.org")

    def test_the_limit_is_honoured(self):
        text = self.csv(*[(f"Org {n}", f"https://o{n}.org", "AB1", "job club")
                          for n in range(10)])
        self.assertEqual(
            len(find_orgs.select(find_orgs.rows_from_csv(text), limit=3)), 3)

    def test_the_output_is_json_serialisable(self):
        """It is written to a file another program reads."""
        text = self.csv(("A", "https://a.org", "AB1", "job club"))
        json.dumps(find_orgs.select(find_orgs.rows_from_csv(text)))


if __name__ == "__main__":
    unittest.main()
