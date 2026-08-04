"""
Tests for growing the speculative target list from what the machine has seen.

Offline. Nothing is fetched and nothing is written.

The point of this file: the reactive route is capped by reality at about four
in-trade Aberdeen listings a day, because that is how many exist. The
speculative route has no such ceiling - but its target list was thirty
companies typed out by hand, which at two notes a day is a fortnight's work.
The state file already holds the answer, because every company that advertised
a role the scorer rated in-trade is by definition an employer of that trade.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spec_targets as st  # noqa: E402


def job(company, score=85, status="scored", title="Workshop Technician",
        description=""):
    return {"company": company, "score": score, "status": status,
            "title": title, "description": description}


def state(*jobs):
    return {"jobs": {str(i): j for i, j in enumerate(jobs)}}


class TestWhoCountsAsEvidence(unittest.TestCase):
    def test_a_firm_that_advertised_a_strong_match_counts(self):
        self.assertTrue(st.evidence(job("Trescal LTD", score=90)))

    def test_a_weak_match_does_not(self):
        """A firm whose only in-trade advert scraped a 60 is weaker evidence
        than one that posted an 85, and the list is long enough already."""
        self.assertFalse(st.evidence(job("Someone Ltd", score=45)))

    def test_a_listing_the_whole_pipeline_backed_counts_whatever_the_score(self):
        """Reaching a portal or an application means more than one number."""
        for status in ("portal_manual", "sent", "ready", "replied"):
            with self.subTest(status=status):
                self.assertTrue(st.evidence(
                    job("Oceaneering", score=0, status=status)))


class TestAgenciesAreNotSpeculativeTargets(unittest.TestCase):
    """Writing 'you don't seem to be advertising anything' to a firm whose
    entire business is advertising things marks the sender as not knowing who
    he is writing to. Every name here came out of the real data as an
    'employer'."""

    def test_the_obvious_ones_are_excluded(self):
        for name in ("Rise Technical Recruitment Limited", "Hays",
                     "Ernest Gordon Recruitment", "Matchtech"):
            with self.subTest(name=name):
                self.assertFalse(st.evidence(job(name)))

    def test_the_ones_that_slipped_through_are_excluded_too(self):
        for name in ("Reed", "Outsource UK", "Pioneer Selection",
                     "Connect Appointments", "Anson Mccade",
                     "Morgan Hunt Group Limited", "Engineering Employment",
                     "Appcast Enterprise", "First Military Recruitment Ltd"):
            with self.subTest(name=name):
                self.assertFalse(st.evidence(job(name)))

    def test_an_agency_is_caught_by_the_advert_wording_too(self):
        self.assertFalse(st.evidence(job(
            "Quiet Name Ltd",
            description="We are recruiting for our client, a major operator.")))

    def test_a_real_employer_is_not_swept_up(self):
        for name in ("Trescal LTD", "Survitec Group", "Dron & Dickson",
                     "Northern Lighthouse Board", "ScottishPower",
                     "Konecranes Demag UK Ltd", "Circet Ireland & UK"):
            with self.subTest(name=name):
                self.assertTrue(st.evidence(job(name)))


class TestNamesThatAreNotCompanies(unittest.TestCase):
    def test_a_scraped_address_is_not_an_employer(self):
        self.assertFalse(st.looks_like_an_employer("Fisher House, PO Box 4"))

    def test_a_training_provider_is_not_an_employer(self):
        """They are selling a course, not hiring a technician."""
        for name in ("Newto Training", "Aberdeen Skills Academy",
                     "North East Scotland College"):
            with self.subTest(name=name):
                self.assertFalse(st.looks_like_an_employer(name))

    def test_a_placeholder_is_not_an_employer(self):
        for name in ("", "  ", "Confidential", "Undisclosed", "Various"):
            with self.subTest(name=name):
                self.assertFalse(st.looks_like_an_employer(name))

    def test_an_ordinary_firm_is_fine(self):
        self.assertTrue(st.looks_like_an_employer("Dron & Dickson"))


class TestBuildingTheList(unittest.TestCase):
    def test_a_new_employer_is_proposed_with_a_note_from_its_own_adverts(self):
        found = st.candidates(state(job("Trescal LTD",
                                        title="Calibration Engineer")), [])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["company"], "Trescal LTD")
        self.assertIn("Calibration Engineer", found[0]["note"])

    def test_the_note_is_never_invented(self):
        """The letter says what the firm does as its reason for writing. A
        made-up line there tells the reader it went to a hundred people."""
        found = st.candidates(state(job("Nameless Ltd", title="")), [])
        self.assertEqual(found, [])

    def test_a_company_already_on_the_list_is_not_added_again(self):
        found = st.candidates(state(job("Trescal LTD")),
                              [{"company": "Trescal Ltd", "note": "x"}])
        self.assertEqual(found, [])

    def test_a_company_already_written_to_is_not_added(self):
        s = state(job("Hydrasun"))
        s["companies_contacted"] = {st.jm.company_key("Hydrasun"): {"at": "x"}}
        self.assertEqual(st.candidates(s, []), [])

    def test_a_company_already_approached_speculatively_is_not_added(self):
        s = state(job("Wood"))
        s["spec_done"] = {st.jm.company_key("Wood"): "sent"}
        self.assertEqual(st.candidates(s, []), [])

    def test_one_entry_per_company_however_many_adverts(self):
        found = st.candidates(state(job("Survitec Group", title="Technician"),
                                    job("Survitec Group", title="Fitter")), [])
        self.assertEqual(len(found), 1)
        self.assertIn("Technician", found[0]["note"])
        self.assertIn("Fitter", found[0]["note"])

    def test_the_strongest_evidence_comes_first(self):
        found = st.candidates(state(job("Weaker Ltd", score=72),
                                    job("Stronger Ltd", score=95)), [])
        self.assertEqual([e["company"] for e in found],
                         ["Stronger Ltd", "Weaker Ltd"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
