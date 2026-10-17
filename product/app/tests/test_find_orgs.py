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

    def test_one_passing_mention_is_not_enough(self):
        """What the first run on the full register let through."""
        self.no("Promotes horticulture and supports women into farming "
                "careers, including apprenticeships.",
                "The Women's Farm and Garden Association")
        self.no("Relief of need among members and former members, including "
                "help to return to work after illness.",
                "Institution Benevolent Fund")
        self.no("A residential home for older people.", "Parker Memorial Home")

    def test_two_weaker_signals_together_are_enough(self):
        self.yes("CV writing and interview skills sessions for young people.")

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


class TestTheOtherLists(unittest.TestCase):
    """Universities, colleges and councils: on the list by definition, one
    per institution, at the kind of address they really use."""

    def test_one_per_institution_and_its_main_site(self):
        rows = [
            {"name": "Department of Physics", "website": "https://www.physics.ox.ac.uk"},
            {"name": "University of Oxford", "website": "https://www.ox.ac.uk/"},
            {"name": "University of Leeds", "website": "leeds.ac.uk"},
            {"name": "Leeds campus in Dubai", "website": "https://leeds.ae"},
            {"name": "", "website": "https://noname.ac.uk"},
        ]
        out = find_orgs.listed(rows, "careers", (".ac.uk",))
        self.assertEqual([(o["name"], o["website"], o["kind"]) for o in out], [
            ("University of Leeds", "https://leeds.ac.uk", "careers"),
            ("University of Oxford", "https://www.ox.ac.uk", "careers"),
        ])

    def test_institution(self):
        self.assertEqual(find_orgs.institution("www.physics.ox.ac.uk"), "ox.ac.uk")
        self.assertEqual(find_orgs.institution("www.fife.gov.uk"), "fife.gov.uk")
        self.assertEqual(find_orgs.institution("www.club.org.uk"), "club.org.uk")

    def test_wikidata_answers_are_read(self):
        import io
        import json

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        asked = []

        def opener(req, timeout=None):
            asked.append(req)
            return Answer(json.dumps({"results": {"bindings": [
                {"areaLabel": {"value": "Fife"},
                 "bodyLabel": {"value": "Fife Council"},
                 "site": {"value": "https://www.fife.gov.uk"}},
                {"orgLabel": {"value": "Q123456"},
                 "site": {"value": "https://x.ac.uk"}},
            ]}}).encode())
        rows = find_orgs.wikidata_rows(find_orgs.COUNCILS, opener=opener)
        self.assertEqual(rows[0], {"name": "Fife Council",
                                   "website": "https://www.fife.gov.uk"})
        self.assertEqual(rows[1]["name"], "")
        self.assertIn("recruited", asked[0].get_header("User-agent"))

    def test_a_busy_query_service_is_asked_again(self):
        """The first run's university list came back 504 and was lost."""
        import io
        import json
        import urllib.error

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        calls = []

        def opener(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 504, "busy", {}, None)
            return Answer(json.dumps({"results": {"bindings": [
                {"bodyLabel": {"value": "council of Adur District Council"},
                 "site": {"value": "https://www.adur-worthing.gov.uk"}},
            ]}}).encode())
        rows = find_orgs.wikidata_rows(find_orgs.COUNCILS, opener=opener,
                                       pause=0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(rows[0]["name"], "Adur District Council")

    def test_a_refusal_is_not_asked_again(self):
        import urllib.error
        calls = []

        def opener(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            find_orgs.wikidata_rows(find_orgs.UNIVERSITIES, opener=opener,
                                    pause=0)
        self.assertEqual(len(calls), 1)

    def test_the_queries_stay_inside_the_time_limit_and_the_present(self):
        """Every subclass of every kind of higher education timed out;
        councils abolished in 2009 were on the first list."""
        self.assertNotIn("P279*", find_orgs.UNIVERSITIES)
        self.assertIn("pq:P582", find_orgs.COUNCILS)

    def test_englands_colleges_from_the_schools_register(self):
        text = ("URN,EstablishmentName,TypeOfEstablishment (name),"
                "EstablishmentStatus (name),SchoolWebsite,Postcode\n"
                "1,Leeds City College,Further education,Open,"
                "www.leedscitycollege.ac.uk,LS1 1AA\n"
                "2,Old College,Further education,Closed,www.old.ac.uk,\n"
                "3,Some Academy,Academy converter,Open,www.academy.sch.uk,\n"
                "4,Hills Sixth Form,Sixth Form Centres,Open,hills.org.uk,\n")
        out = find_orgs.colleges(text)
        self.assertEqual([o["name"] for o in out],
                         ["Hills Sixth Form", "Leeds City College"])
        self.assertTrue(all(o["kind"] == "careers" for o in out))

    def test_a_renamed_schools_register_raises(self):
        with self.assertRaises(find_orgs.SourceError):
            find_orgs.colleges("URN,Name\n1,X\n")


if __name__ == "__main__":
    unittest.main()
