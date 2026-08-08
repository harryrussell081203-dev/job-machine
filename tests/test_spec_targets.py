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
import json
import os
import sys
import unittest
from unittest import mock

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


class TestTheCovenantSignatoriesGoFirst(unittest.TestCase):
    """The register was only ever used reactively - when a job happened to
    come up at a signatory, the letter mentioned the Covenant. That waits for
    the employer to advertise, and leaves the best thing about the list on the
    table: many signatories run a guaranteed interview scheme, which is the
    only route in this project that produces an interview by policy rather
    than by persuasion."""

    def register(self, *employers):
        return mock.patch("builtins.open", mock.mock_open(
            read_data=json.dumps({"employers": list(employers)})))

    def test_a_signatory_becomes_a_speculative_target(self):
        with self.register({"company": "NHS Grampian",
                            "note": "Aberdeen medical engineering"}):
            got = st.covenant_candidates({"jobs": {}}, [])
        self.assertEqual(got[0]["company"], "NHS Grampian")
        self.assertTrue(got[0]["covenant"])

    def test_the_note_states_the_signing_and_never_the_scheme(self):
        """That they signed is published fact with their name against it.
        That they run a guaranteed interview scheme is theirs to say, and
        claiming it to their face while asking for a job is not on."""
        note = st.covenant_note({"company": "BAE", "note": "defence"})
        self.assertIn("signed the Armed Forces Covenant", note)
        self.assertNotIn("guaranteed", note.lower())
        self.assertNotIn("interview", note.lower())

    def test_near_home_comes_before_the_rest(self):
        """A guaranteed interview he can attend tomorrow beats one in
        Portsmouth - though neither is filtered out."""
        entries = [{"company": "Portsmouth Naval Base", "note": "dockyard"},
                   {"company": "NHS Grampian", "note": "Aberdeen"}]
        self.assertLess(st.covenant_rank(entries[1]),
                        st.covenant_rank(entries[0]))

    def test_a_bracket_or_a_digit_no_longer_wins(self):
        """Sorting on the name put '(TMFL) Titan Facilities' and '2CL
        Communications' above Aberdeen City Council and BAE Systems, purely
        because of the bracket and the digit."""
        entries = [{"company": "(TMFL) Titan Facilities Management",
                    "note": "Covenant signatory"},
                   {"company": "Aberdeen City Council",
                    "note": "local authority technical roles"}]
        self.assertLess(st.covenant_rank(entries[1]),
                        st.covenant_rank(entries[0]))

    def test_a_recruiter_on_the_register_gets_a_different_letter(self):
        with self.register({"company": "Morson Group Recruitment",
                            "note": "staffing"}):
            got = st.covenant_candidates({"jobs": {}}, [])
        self.assertEqual(got, [])

    def test_one_already_written_to_is_not_written_to_again(self):
        with self.register({"company": "NHS Grampian", "note": "Aberdeen"}):
            got = st.covenant_candidates(
                {"jobs": {}}, [{"company": "NHS Grampian"}])
        self.assertEqual(got, [])

    def test_a_missing_register_is_not_a_crash(self):
        with mock.patch("builtins.open", side_effect=OSError("gone")):
            self.assertEqual(st.covenant_candidates({"jobs": {}}, []), [])

    def test_they_are_written_to_before_the_rest_of_the_list(self):
        """Appended to the end of a 72-entry file at two notes a day, the
        first signatory would have been written to in five weeks."""
        targets = [{"company": "Ordinary Firm"},
                   {"company": "NHS Grampian", "covenant": True}]
        first = sorted(targets, key=lambda t: not t.get("covenant"))[0]
        self.assertEqual(first["company"], "NHS Grampian")
