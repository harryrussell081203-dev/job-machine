"""
Offline tests for the pure logic in job_machine.py.

No network, no Gmail, no Gemini: everything that talks to the outside world is
stubbed. Run with:  python -m unittest discover -s tests -v
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_machine as jm  # noqa: E402


def make_job(**over):
    job = {
        "external_id": "reed_1", "source": "reed", "title": "Electronics Technician",
        "company": "Acme Subsea Ltd", "location": "Aberdeen", "url": "",
        "description": "Bench testing of subsea sensors to IPC-A-610 Class 3.",
        "status": "ready", "score": 88, "email_tier": 3,
        "contact_email": "jane.smith@acme.com", "contact_name": "Jane",
        "found_at": jm.now(),
    }
    job.update(over)
    return job


GOOD_BODY = """Hi Jane,

Saw your Electronics Technician listing, the bench testing of subsea sensors to
IPC-A-610 Class 3 is what I do now.

1. Three years at Sonardyne building and testing subsea acoustic kit to Class 3.
2. Component level fault diagnosis on Ranger 2 and Compatt hardware, plus the
   test records that go with it.
3. Completed SCQF Level 7 apprenticeship and DV cleared.

Worth a quick call this week?

Harry
Harry Russell / 07398 530978 / CV attached"""


class TestEmailClassification(unittest.TestCase):
    def test_named_person_is_top_tier(self):
        self.assertEqual(jm.classify("jane.smith@acme.com"), (3, "Jane"))
        self.assertEqual(jm.classify("jane@acme.com"), (3, "Jane"))
        self.assertEqual(jm.classify("j.smith@acme.com"), (3, None))

    def test_greeting_name_only_when_it_is_really_a_first_name(self):
        for good in ("jane", "chris", "fraser", "stuart", "kyle", "grace"):
            with self.subTest(good=good):
                self.assertEqual(jm.name_from_email(good), good.capitalize())
        for bad in ("jsmith", "mjones", "dpatel", "rmcleod", "ab"):
            with self.subTest(bad=bad):
                self.assertIsNone(jm.name_from_email(bad))
        # still a real individual's mailbox, we just do not guess their name
        self.assertEqual(jm.classify("jsmith@acme.com"), (3, None))

    def test_hiring_and_generic_tiers(self):
        self.assertEqual(jm.classify("careers@acme.com")[0], 2)
        self.assertEqual(jm.classify("hr@acme.com")[0], 2)
        self.assertEqual(jm.classify("recruitment@acme.com")[0], 2)
        self.assertEqual(jm.classify("info@acme.com")[0], 1)
        self.assertEqual(jm.classify("enquiries@acme.com")[0], 1)

    def test_unusable_addresses_score_zero(self):
        self.assertEqual(jm.classify("a1b2c3d4@acme.com")[0], 0)
        self.assertEqual(jm.classify("2fa8ef@acme.com")[0], 0)

    def test_ranking_prefers_named_person(self):
        found = ["info@acme.com", "careers@acme.com", "jane.smith@acme.com"]
        self.assertEqual(jm.best_email(found), ("jane.smith@acme.com", "Jane", 3))
        self.assertEqual(jm.best_email(["info@acme.com", "hr@acme.com"])[2], 2)
        self.assertEqual(jm.best_email([]), (None, None, 0))

    def test_clean_emails_filters_junk(self):
        raw = ["noreply@acme.com", "postmaster@acme.com", "someone@example.com",
               "logo@acme.com.png", "hello@acme.com", "person@other.com",
               "careers@jobs.acme.com"]
        cleaned = jm.clean_emails(raw, domain="acme.com")
        self.assertIn("hello@acme.com", cleaned)
        self.assertIn("careers@jobs.acme.com", cleaned)
        for junk in ("noreply@acme.com", "postmaster@acme.com",
                     "someone@example.com", "person@other.com"):
            self.assertNotIn(junk, cleaned)

    def test_free_mail_and_placeholder_domains_rejected(self):
        cleaned = jm.clean_emails(["bob@gmail.com", "hi@yourcompany.com"])
        self.assertEqual(cleaned, [])


class TestAPlaceIsNotAPerson(unittest.TestCase):
    """Every address below is real, and every one of them was written to.

    Two applications opened 'Dear Canada' and 'Dear Africa' - to
    canada@matchtech.com and africa.recruitment@enermech.com, the wrong
    continent's office of the right company. Letters to charities went to
    fundraise@, library@ and corporate@, all classified as people with first
    names Fundraise, Library and Corporate.

    The cost is not only the greeting. A department inbox scored as a named
    person outranks the actual hiring inbox, so the wrong desk was chosen
    first and then addressed by a name nobody has."""

    def test_a_regional_inbox_is_never_greeted_by_name(self):
        for address in ("canada@matchtech.com", "americas@opito.com",
                        "africa.recruitment@enermech.com", "emea@acme.com",
                        "houston@acme.com", "apac.jobs@acme.com"):
            with self.subTest(address=address):
                self.assertIsNone(jm.classify(address)[1])

    def test_a_department_inbox_is_never_greeted_by_name(self):
        for address in ("fundraise@helpforheroes.org.uk",
                        "library@nescol.ac.uk", "corporate@ssafa.org.uk",
                        "events@legionscotland.org.uk", "donations@acme.org",
                        "membership@acme.org", "training@acme.com"):
            with self.subTest(address=address):
                self.assertIsNone(jm.classify(address)[1])

    def test_the_aberdeen_office_is_still_worth_writing_to(self):
        """A place is a shared inbox, not a useless one - and at a company
        with an Aberdeen office it is one of the better desks to reach."""
        tier, name = jm.classify("aberdeen@ashtead-technology.com")
        self.assertGreaterEqual(tier, 1)
        self.assertIsNone(name)
        self.assertTrue(jm.is_home_place("aberdeen"))
        self.assertFalse(jm.is_home_place("houston"))

    def test_people_whose_names_contain_a_place_are_not_lost(self):
        """A prefix rule would read Frances as France and Normanton as Norman.
        Getting this wrong costs a real person their name."""
        for address, first in (("frances@acme.com", "Frances"),
                               ("norman.watt@acme.com", "Norman"),
                               ("chris.brown@acme.com", "Chris"),
                               ("jane.smith@acme.com", "Jane")):
            with self.subTest(address=address):
                self.assertEqual(jm.classify(address), (3, first))


class TestWrongCompanyRegression(unittest.TestCase):
    """The first live run emailed Woodforest National Bank (Texas) a note meant
    for Wood plc, and greeted an HR inbox as 'Hi Mysupporthr'. Both here."""

    def clearbit(self, hits):
        response = mock.Mock()
        response.raise_for_status = lambda: None
        response.json = lambda: hits
        return mock.patch.object(jm.requests, "get", return_value=response)

    def test_wood_does_not_match_woodforest(self):
        with self.clearbit([{"name": "Woodforest National Bank",
                             "domain": "woodforest.com"}]):
            self.assertIsNone(jm.find_domain("Wood"))

    def test_a_real_whole_word_match_still_works(self):
        with self.clearbit([{"name": "Baker Hughes Company",
                             "domain": "bakerhughes.com"}]):
            self.assertEqual(jm.find_domain("Baker Hughes"), "bakerhughes.com")
        with self.clearbit([{"name": "EnerMech", "domain": "enermech.com"}]):
            self.assertEqual(jm.find_domain("EnerMech"), "enermech.com")

    def test_a_one_word_company_name_needs_an_exact_match(self):
        """'Sanctuary' the housing association was matched to Sanctuary
        Clothing in California, and to a named individual there - an
        application was one run away from landing in a stranger's inbox at an
        unrelated company on another continent.

        A single word does not identify a company. The whole-word subset rule
        that catches Wood/Woodforest is satisfied by ANY firm containing that
        word, so for a one-token name nothing but an exact match will do."""
        for wanted, hit, domain in (
                ("Sanctuary", "Sanctuary Clothing", "sanctuaryclothing.com"),
                ("Encore", "Encore Capital Group", "encorecapital.com"),
                ("Future", "Future Publishing Ltd", "futureplc.com")):
            with self.subTest(wanted=wanted):
                with self.clearbit([{"name": hit, "domain": domain}]):
                    self.assertIsNone(jm.find_domain(wanted))

    def test_a_one_word_name_still_matches_itself_exactly(self):
        with self.clearbit([{"name": "Sanctuary", "domain": "sanctuary.co.uk"}]):
            self.assertEqual(jm.find_domain("Sanctuary"), "sanctuary.co.uk")
        # and the suffix-stripping that company_key already does still applies
        with self.clearbit([{"name": "Hydrasun Ltd", "domain": "hydrasun.com"}]):
            self.assertEqual(jm.find_domain("Hydrasun"), "hydrasun.com")

    def test_the_domain_is_rechecked_at_the_moment_of_sending(self):
        """State is merged across runs, so a record written before a matching
        bug was fixed comes back carrying the bad domain. Clearing the two
        Sanctuary rows by hand did not hold - the merge saw main's 'ready' as
        further along than the corrected 'no_email' and restored it. A guard
        at the point of sending does not care what the file says."""
        for company, domain in (("Sanctuary", "sanctuaryclothing.com"),
                                ("Wood", "woodforest.com"),
                                ("Stork", "stork24.eu"),
                                ("Encore", "encorecapital.com")):
            with self.subTest(company=company):
                self.assertFalse(jm.domain_matches_company(company, domain))

    def test_a_company_at_its_own_domain_is_allowed(self):
        for company, domain in (("Sanctuary", "sanctuary.co.uk"),
                                ("Sanctuary", "sanctuarygroup.co.uk"),
                                ("Hydrasun", "hydrasun.com"),
                                ("EnerMech", "enermech.com")):
            with self.subTest(company=company):
                self.assertTrue(jm.domain_matches_company(company, domain))

    def test_multi_word_names_are_not_policed(self):
        """They are specific enough to trust, and gating them would throw away
        real matches - Northern Lighthouse Board really is at nlb.org.uk."""
        for company, domain in (("Northern Lighthouse Board", "nlb.org.uk"),
                                ("Baker Hughes", "bakerhughes.com"),
                                ("Dron & Dickson", "dronanddickson.co.uk")):
            with self.subTest(company=company):
                self.assertTrue(jm.domain_matches_company(company, domain))

    def test_a_word_from_the_companys_own_name_is_allowed_in_its_domain(self):
        """company_key strips 'recruitment', 'group' and the like, so 'Canmore
        Recruitment' reduces to the single token 'canmore' - and the first
        version of this guard refused canmorerecruitment.com, which is
        obviously theirs and had already been written to successfully."""
        for company, domain in (("Canmore Recruitment", "canmorerecruitment.com"),
                                ("TMM Recruitment", "tmmrecruitment.com"),
                                ("Future Group", "future-group.uk")):
            with self.subTest(company=company):
                self.assertTrue(jm.domain_matches_company(company, domain))

    def test_a_missing_domain_does_not_block_a_send(self):
        """Listings carrying an address found in the advert itself have no
        company_domain at all, and those are the best addresses we get."""
        self.assertTrue(jm.domain_matches_company("Sanctuary", None))
        self.assertTrue(jm.domain_matches_company("", "anything.com"))

    def test_a_foreign_lookalike_domain_is_rejected(self):
        with self.clearbit([{"name": "HMH", "domain": "hmh.com.vn"}]):
            self.assertIsNone(jm.find_domain("HMH"))
        self.assertFalse(jm.plausible_domain("hmh.com.vn"))
        self.assertTrue(jm.plausible_domain("hydrasun.com"))
        self.assertTrue(jm.plausible_domain("sonardyne.co.uk"))

    def test_shared_inboxes_are_never_greeted_by_name(self):
        for local in ("mysupporthr", "hrsupport", "jobsteam", "recruitmentuk",
                      "careersaberdeen", "infodesk", "salesteam"):
            with self.subTest(local=local):
                self.assertFalse(jm.is_personal(local))
                self.assertIsNone(jm.classify(f"{local}@acme.com")[1])

    def test_an_hr_inbox_is_still_usable_as_a_hiring_tier(self):
        # not a person, but a good address - it should not be thrown away
        self.assertEqual(jm.classify("mysupporthr@bakerhughes.com"), (2, None))
        self.assertEqual(jm.classify("hrsupport@acme.com")[0], 2)

    def test_real_people_are_still_recognised(self):
        for local in ("jane.smith", "fraser.mackay", "gary", "chris.brown"):
            with self.subTest(local=local):
                self.assertTrue(jm.is_personal(local))
        self.assertEqual(jm.classify("jane.smith@acme.com"), (3, "Jane"))


class TestSlopFilter(unittest.TestCase):
    def test_every_banned_phrase_is_caught(self):
        for phrase in jm.BANNED:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, jm.slop_check(f"Something {phrase} something."))

    def test_case_insensitive(self):
        self.assertTrue(jm.slop_check("I HOPE THIS EMAIL FINDS YOU WELL"))

    def test_no_false_positives_on_word_stems(self):
        self.assertEqual(jm.slop_check("I worked on hydrodynamics and alignment."), [])
        self.assertEqual(jm.slop_check(GOOD_BODY), [])


class TestComposeGuards(unittest.TestCase):
    def test_good_email_passes(self):
        body, core = jm.assemble(GOOD_BODY, "Hi Jane,")
        problems = jm.email_problems("Subsea tech for your Electronics Technician",
                                     core, make_job())
        self.assertEqual(problems, [])

    def test_greeting_and_signoff_are_forced_by_code(self):
        body, _ = jm.assemble("Dear Sir or Madam,\n\nHello.\n\nBest wishes,\nH",
                              "Hi Jane,")
        self.assertTrue(body.startswith("Hi Jane,\n\n"))
        self.assertTrue(body.endswith(f"Harry\n{jm.SIGNOFF}"))
        self.assertEqual(body.count(jm.SIGNOFF), 1)
        self.assertNotIn("Dear Sir", body)
        self.assertNotIn("Best wishes", body)

    def test_normalise_strips_markdown_dashes_and_shouting(self):
        cleaned = jm.normalise("**Bold** kit — really good!!")
        self.assertNotIn("*", cleaned)
        self.assertNotIn("—", cleaned)
        self.assertNotIn("!", cleaned)

    def test_subject_rules(self):
        job = make_job()
        _, core = jm.assemble(GOOD_BODY, "Hi Jane,")
        long_subject = "One two three four five six seven eight nine technician"
        self.assertTrue(any("max is 8" in p
                            for p in jm.email_problems(long_subject, core, job)))
        self.assertTrue(any("Application for" in p for p in jm.email_problems(
            "Application for Electronics Technician", core, job)))
        self.assertTrue(any("must name the role" in p for p in jm.email_problems(
            "Ex Navy hands on worker", core, job)))

    def test_first_line_must_name_the_role(self):
        job = make_job()
        _, core = jm.assemble(
            GOOD_BODY.replace("Saw your Electronics Technician listing,",
                              "Saw your advert,"), "Hi Jane,")
        self.assertTrue(any("first line must name" in p
                            for p in jm.email_problems("Subsea tech for your Technician",
                                                       core, job)))

    def test_body_word_count_enforced(self):
        job = make_job()
        _, short = jm.assemble("Hi.\n\n1. Short.\n2. Also short.\n\nCall?", "Hi Jane,")
        self.assertTrue(any("must be 60-90" in p
                            for p in jm.email_problems("Tech for your Technician role",
                                                       short, job)))

    def test_numbered_proof_points_required(self):
        job = make_job()
        prose = ("Saw your Electronics Technician listing and the bench testing work. "
                 * 3) + "\n\nWorth a call?"
        _, core = jm.assemble(prose, "Hi Jane,")
        self.assertTrue(any("numbered proof points" in p
                            for p in jm.email_problems("Tech for your Technician role",
                                                       core, job)))

    def test_single_question_cta_required(self):
        job = make_job()
        _, core = jm.assemble(GOOD_BODY.replace("Worth a quick call this week?",
                                                "Free? Worth a call?"), "Hi Jane,")
        self.assertTrue(any("exactly one question" in p
                            for p in jm.email_problems("Tech for your Technician role",
                                                       core, job)))

    def test_build_email_retries_then_succeeds(self):
        job = make_job()
        bad = {"subject": "I am writing to apply for this",
               "body": "I hope this email finds you well. I am passionate."}
        good = {"subject": "Subsea tech for your Electronics Technician",
                "body": GOOD_BODY}
        with mock.patch.object(jm, "gemini_json", side_effect=[bad, good]) as g:
            content = jm.build_email(job)
        self.assertIsNotNone(content)
        self.assertEqual(g.call_count, 2)
        self.assertEqual(jm.slop_check(content["subject"] + content["body"]), [])
        self.assertIn("REJECTED", g.call_args_list[1].args[0])

    def test_build_email_gives_up_and_returns_none(self):
        bad = {"subject": "Application for the role", "body": "passionate synergy"}
        with mock.patch.object(jm, "gemini_json", return_value=bad):
            self.assertIsNone(jm.build_email(make_job()))


class TestTemplateRouting(unittest.TestCase):
    def test_titles_route_to_expected_family(self):
        cases = {
            "Communications Technician": "communications",
            "Radio Systems Engineer": "communications",
            "Electronics Test Technician": "electronics_technician",
            "Workshop Assembly Technician": "electronics_technician",
            "Instrumentation Technician (Offshore)": "instrumentation_maintenance",
            "Maintenance Technician - Oil and Gas": "instrumentation_maintenance",
            "Event Production Technician": "events_production",
            "Lighting and Sound Technician": "events_production",
            "Stores Assistant": "general",
        }
        for title, family in cases.items():
            with self.subTest(title=title):
                self.assertEqual(jm.pick_family(title), family)

    def test_every_family_skeleton_has_numbered_points_and_signoff(self):
        for family, tpl in jm.TEMPLATES.items():
            with self.subTest(family=family):
                self.assertIn("\n1. ", tpl["skeleton"])
                self.assertIn("\n2. ", tpl["skeleton"])
                self.assertTrue(tpl["skeleton"].endswith(jm.SIGNOFF))


class TestHarvestHelpers(unittest.TestCase):
    def test_freshness(self):
        now_ = datetime.now(timezone.utc)
        self.assertTrue(jm.fresh_enough(now_ - timedelta(hours=5)))
        self.assertFalse(jm.fresh_enough(now_ - timedelta(hours=60)))
        self.assertTrue(jm.fresh_enough(now_ - timedelta(days=1), "date"))
        self.assertFalse(jm.fresh_enough(now_ - timedelta(days=3), "date"))
        self.assertTrue(jm.fresh_enough(None))

    def test_reed_date_parsing(self):
        self.assertEqual(jm.reed_date("31/07/2026").day, 31)
        self.assertIsNone(jm.reed_date("not a date"))

    def test_company_key_normalises(self):
        self.assertEqual(jm.company_key("ACME Subsea Ltd."),
                         jm.company_key("Acme Subsea Limited"))

    def test_dedupe_key_matches_across_boards(self):
        a = make_job(external_id="reed_1", title="Electronics Technician")
        b = make_job(external_id="adzuna_1", title="Electronics  Technician!",
                     company="Acme Subsea Limited")
        self.assertEqual(jm.dedupe_key(a), jm.dedupe_key(b))

    def test_title_exclusions(self):
        self.assertTrue(jm.title_excluded("Chartered Engineer"))
        self.assertTrue(jm.title_excluded("HGV Driver"))
        self.assertIsNone(jm.title_excluded("Electronics Technician"))

    def test_harvest_dedupes_and_drops_excluded_titles(self):
        state = {"jobs": {}, "companies_contacted": {}, "send_counts": {}}
        listings = [
            make_job(external_id="reed_1", status=None),
            make_job(external_id="adzuna_1", company="Acme Subsea Limited",
                     status=None),
            make_job(external_id="reed_2", title="HGV Driver", company="Big Haulage",
                     status=None),
        ]
        with mock.patch.object(jm, "adzuna", return_value=[]), \
             mock.patch.object(jm, "reed", return_value=listings):
            jm.harvest(state)
        self.assertEqual(len(state["jobs"]), 2)          # the duplicate never lands
        self.assertEqual(state["jobs"]["reed_1"]["status"], "new")
        self.assertEqual(state["jobs"]["reed_2"]["status"], "skipped")


class TestDiscover(unittest.TestCase):
    def _state(self, job):
        return {"jobs": {job["external_id"]: job}, "companies_contacted": {},
                "send_counts": {}}

    def test_listing_email_wins_and_needs_no_scrape(self):
        job = make_job(status="scored", contact_email=None, contact_name=None,
                       description="Apply to sarah.jones@acmesubsea.com today.")
        state = self._state(job)
        with mock.patch.object(jm, "fetch_listing_text", return_value=""), \
             mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "find_domain") as domain:
            jm.discover(state)
        domain.assert_not_called()
        self.assertEqual(job["status"], "ready")
        self.assertEqual(job["contact_email"], "sarah.jones@acmesubsea.com")
        self.assertEqual(job["contact_name"], "Sarah")
        self.assertEqual(job["email_method"], "listing")

    def test_falls_back_to_website_scrape(self):
        job = make_job(status="scored", contact_email=None, contact_name=None,
                       description="No address here.")
        state = self._state(job)
        with mock.patch.object(jm, "fetch_listing_text", return_value=""), \
             mock.patch.object(jm, "find_domain", return_value="acme.com"), \
             mock.patch.object(jm, "scrape_site",
                               return_value=(["info@acme.com", "careers@acme.com"], [])), \
             mock.patch.object(jm, "has_mx", return_value=True):
            jm.discover(state)
        self.assertEqual(job["status"], "ready")
        self.assertEqual(job["contact_email"], "careers@acme.com")  # hiring > generic
        self.assertEqual(job["email_tier"], 2)

    def test_nothing_found_means_no_email_never_a_guess(self):
        job = make_job(status="scored", contact_email=None, contact_name=None,
                       description="No address here.")
        state = self._state(job)
        with mock.patch.object(jm, "fetch_listing_text", return_value=""), \
             mock.patch.object(jm, "find_domain", return_value="acme.com"), \
             mock.patch.object(jm, "scrape_site", return_value=([], [])), \
             mock.patch.object(jm, "has_mx", return_value=True):
            jm.discover(state)
        self.assertEqual(job["status"], "no_email")
        self.assertIsNone(job.get("contact_email"))

    def test_domain_without_mx_is_rejected(self):
        job = make_job(status="scored", contact_email=None, contact_name=None,
                       description="No address here.")
        state = self._state(job)
        with mock.patch.object(jm, "fetch_listing_text", return_value=""), \
             mock.patch.object(jm, "find_domain", return_value="acme.com"), \
             mock.patch.object(jm, "has_mx", return_value=False), \
             mock.patch.object(jm, "scrape_site") as scrape:
            jm.discover(state)
        scrape.assert_not_called()
        self.assertEqual(job["status"], "no_email")


class TestSending(unittest.TestCase):
    def setUp(self):
        self.state = {"jobs": {}, "companies_contacted": {}, "send_counts": {}}
        self.content = {"subject": "Subsea tech for your Electronics Technician",
                        "body": GOOD_BODY, "family": "electronics_technician"}
        patches = [
            mock.patch.object(jm, "cv_path", return_value="/tmp/cv.pdf"),
            mock.patch.object(jm, "build_email", return_value=self.content),
            mock.patch.object(jm, "save"),
            mock.patch.object(jm.time, "sleep"),
            mock.patch.object(jm, "GMAIL_ADDRESS", "harry@gmail.com"),
            mock.patch.object(jm, "GMAIL_APP_PASSWORD", "app-password"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def add(self, **over):
        job = make_job(**over)
        self.state["jobs"][job["external_id"]] = job
        return job

    def test_test_mode_redirects_and_does_not_burn_the_company(self):
        job = self.add()
        with mock.patch.object(jm, "TEST_MODE", True), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(self.state)
        to_addr, subject, _body = send.call_args.args[:3]
        self.assertEqual(to_addr, "harry@gmail.com")
        self.assertEqual(subject,
                         "[TEST -> jane.smith@acme.com] " + self.content["subject"])
        self.assertEqual(job["status"], "test_sent")
        self.assertEqual(job["sent_body"], GOOD_BODY)
        self.assertEqual(self.state["companies_contacted"], {})

    def test_live_mode_sends_to_the_employer_and_marks_the_company(self):
        job = self.add()
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(self.state)
        self.assertEqual(send.call_args.args[0], "jane.smith@acme.com")
        self.assertNotIn("[TEST", send.call_args.args[1])
        self.assertEqual(job["status"], "sent")
        self.assertIn(jm.company_key("Acme Subsea Ltd"), self.state["companies_contacted"])
        self.assertEqual(jm.sends_today(self.state), 1)

    def test_one_email_per_company_ever(self):
        self.add(external_id="a", title="Electronics Technician")
        self.add(external_id="b", title="Test Technician", company="Acme Subsea Limited")
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(self.state)
        self.assertEqual(send.call_count, 1)
        statuses = sorted(j["status"] for j in self.state["jobs"].values())
        self.assertEqual(statuses, ["sent", "skipped"])

    def test_best_contacts_go_first(self):
        self.add(external_id="generic", email_tier=1, score=99,
                 contact_email="info@a.com", company="A")
        self.add(external_id="named", email_tier=3, score=71,
                 contact_email="jane@b.com", company="B")
        self.add(external_id="hiring", email_tier=2, score=95,
                 contact_email="careers@c.com", company="C")
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(self.state)
        order = [c.args[0] for c in send.call_args_list]
        self.assertEqual(order, ["jane@b.com", "careers@c.com", "info@a.com"])

    def test_per_run_cap(self):
        for i in range(5):
            self.add(external_id=f"j{i}", company=f"Company {i}",
                     contact_email=f"jane@c{i}.com")
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "PER_RUN_SEND_CAP", 2), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(self.state)
        self.assertEqual(send.call_count, 2)

    def test_the_per_run_cap_is_never_exceeded_by_fresh_listings(self):
        """The throttle a fresh listing skips is the off-peak one, not the cap
        that keeps twenty emails out of a single Gmail session."""
        fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        for i in range(9):
            self.add(external_id=f"f{i}", company=f"Firm {i}", posted_at=fresh,
                     contact_email=f"jane@f{i}.com", email_tier=3)
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "in_peak_window", return_value=False), \
             mock.patch.object(jm, "PER_RUN_SEND_CAP", 4), \
             mock.patch.object(jm, "OFF_PEAK_SEND_CAP", 1), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(self.state)
        self.assertEqual(send.call_count, 4)

    def test_off_peak_holds_the_stale_queue_back_for_the_window(self):
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        for i in range(6):
            self.add(external_id=f"s{i}", company=f"Old Firm {i}", posted_at=old,
                     contact_email=f"jane@s{i}.com", email_tier=3)
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "in_peak_window", return_value=False), \
             mock.patch.object(jm, "PER_RUN_SEND_CAP", 7), \
             mock.patch.object(jm, "OFF_PEAK_SEND_CAP", 2), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(self.state)
        self.assertEqual(send.call_count, 2)

    def test_in_the_window_the_whole_run_budget_is_used(self):
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        for i in range(6):
            self.add(external_id=f"w{i}", company=f"Firm {i}", posted_at=old,
                     contact_email=f"jane@w{i}.com", email_tier=3)
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "in_peak_window", return_value=True), \
             mock.patch.object(jm, "PER_RUN_SEND_CAP", 5), \
             mock.patch.object(jm, "OFF_PEAK_SEND_CAP", 2), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(self.state)
        self.assertEqual(send.call_count, 5)

    def test_daily_cap(self):
        self.state["send_counts"][jm.today()] = 20
        self.add()
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_sends(self.state)
        send.assert_not_called()

    def test_no_cv_means_no_send(self):
        self.add()
        with mock.patch.object(jm, "cv_path", return_value=None), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_sends(self.state)
        send.assert_not_called()

    def test_the_model_being_down_falls_back_to_the_plain_letter(self):
        """This test used to assert the opposite - that a compose failure
        meant no send - and that was wrong. Gemini's free tier has a daily
        ceiling this project is expected to hit, and when it did, matched jobs
        with real verified addresses were marked compose_failed and dropped. A
        rate limit on a free API must not be a hard dependency for applying
        for a job."""
        job = self.add()
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "build_email", return_value=None), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_sends(self.state)
        send.assert_called_once()
        self.assertEqual(job["status"], "sent")
        body = send.call_args[0][2]
        self.assertIn("Sonardyne", body)
        self.assertIn("Royal Navy", body)
        self.assertIsNone(jm.claims_clearance(body))

    def test_smtp_failure_leaves_company_free_for_another_go(self):
        job = self.add()
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "send_email", side_effect=OSError("smtp down")):
            jm.run_sends(self.state)
        self.assertEqual(job["status"], "send_failed")
        self.assertEqual(self.state["companies_contacted"], {})
        self.assertEqual(jm.sends_today(self.state), 0)

    def test_dry_run_sends_nothing(self):
        job = self.add()
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_sends(self.state, dry_run=True)
        send.assert_not_called()
        self.assertEqual(job["status"], "ready")
        self.assertIn("draft_body", job)


class TestFollowups(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(jm, "save"),
                   mock.patch.object(jm.time, "sleep"),
                   mock.patch.object(jm, "GMAIL_ADDRESS", "harry@gmail.com"),
                   mock.patch.object(jm, "GMAIL_APP_PASSWORD", "app-password")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def state_with(self, days_ago, status="sent", **over):
        job = make_job(status=status, message_id="<orig>",
                       sent_subject="Subsea tech for your Electronics Technician",
                       sent_at=(datetime.now(timezone.utc)
                                - timedelta(days=days_ago)).isoformat(), **over)
        return {"jobs": {"j": job}, "companies_contacted": {}, "send_counts": {}}, job

    def test_disabled_in_test_mode(self):
        state, _ = self.state_with(9, status="test_sent")
        with mock.patch.object(jm, "TEST_MODE", True), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_followups(state)
        send.assert_not_called()

    def test_too_soon_is_left_alone(self):
        state, _ = self.state_with(2)
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "has_reply_from") as check, \
             mock.patch.object(jm, "send_email") as send:
            jm.run_followups(state)
        check.assert_not_called()
        send.assert_not_called()

    def test_followup_threads_onto_the_original_without_the_cv(self):
        state, job = self.state_with(5)
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "has_reply_from", return_value=False), \
             mock.patch.object(jm, "send_email", return_value="<f>") as send:
            jm.run_followups(state)
        kwargs = send.call_args.kwargs
        self.assertFalse(kwargs["attach_cv"])
        self.assertEqual(kwargs["headers"]["In-Reply-To"], "<orig>")
        self.assertTrue(send.call_args.args[1].startswith("Re: "))
        self.assertIn("followup_sent_at", job)

    def test_a_reply_stops_everything(self):
        state, job = self.state_with(5)
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "has_reply_from", return_value=True), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_followups(state)
        send.assert_not_called()
        self.assertEqual(job["status"], "replied")

    def test_unreachable_inbox_never_marks_replied(self):
        state, job = self.state_with(5)
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "has_reply_from", return_value=None), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_followups(state)
        send.assert_not_called()
        self.assertEqual(job["status"], "sent")

    def test_never_more_than_the_two_nudges(self):
        state, job = self.state_with(30, followup_sent_at=jm.now(),
                                     followup2_sent_at=jm.now())
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "has_reply_from") as check, \
             mock.patch.object(jm, "send_email") as send:
            jm.run_followups(state)
        check.assert_not_called()
        send.assert_not_called()


class TestDailySummary(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(jm, "GMAIL_ADDRESS", "harry@gmail.com"),
                   mock.patch.object(jm, "GMAIL_APP_PASSWORD", "app-password")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def state(self, *jobs, **extra):
        state = {"jobs": {j["external_id"]: j for j in jobs},
                 "companies_contacted": {}, "send_counts": {}}
        state.update(extra)
        return state

    def sent_job(self, hours_ago=3, **over):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        fields = {"status": "sent", "sent_at": stamp,
                  "sent_subject": "Subsea tech for your Electronics Technician"}
        fields.update(over)
        return make_job(**fields)

    def test_only_sends_at_2200_uk(self):
        uk = datetime(2026, 8, 1, 22, 0, tzinfo=timezone.utc)
        with mock.patch.object(jm, "uk_now", return_value=uk):
            self.assertTrue(jm.summary_due())
        with mock.patch.object(jm, "uk_now", return_value=uk.replace(hour=21)):
            self.assertFalse(jm.summary_due())
            self.assertTrue(jm.summary_due(force=True))

    def test_skipped_outside_the_window_without_touching_smtp(self):
        state = self.state(self.sent_job())
        uk = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
        with mock.patch.object(jm, "uk_now", return_value=uk), \
             mock.patch.object(jm.smtplib, "SMTP_SSL") as smtp:
            jm.send_summary(state)
        smtp.assert_not_called()
        self.assertNotIn("last_summary_at", state)

    def test_window_covers_24h_and_stretches_to_the_last_digest(self):
        state = self.state(self.sent_job())
        self.assertAlmostEqual(
            (datetime.now(timezone.utc) - jm.summary_window(state)).total_seconds(),
            24 * 3600, delta=5)
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        state["last_summary_at"] = old
        self.assertEqual(jm.summary_window(state), jm.parse_ts(old))

    def test_digest_lists_todays_applications_only(self):
        state = self.state(
            self.sent_job(external_id="today", title="Instrumentation Technician",
                          company="North Sea Controls"),
            self.sent_job(external_id="last_week", hours_ago=24 * 7,
                          company="Old Corp"),
            make_job(external_id="queued", status="ready"),
        )
        data = jm.collect_summary(state, jm.summary_window(state))
        subject, text, html = jm.summary_bodies(data)
        self.assertIn("1 application(s) sent", subject)
        self.assertIn("North Sea Controls", text)
        self.assertIn("North Sea Controls", html)
        self.assertNotIn("Old Corp", text)
        self.assertIn("Queued and ready to send: 1", text)

    def test_quiet_day_still_gets_a_digest(self):
        subject, text, html = jm.summary_bodies(
            jm.collect_summary(self.state(), jm.summary_window(self.state())))
        self.assertIn("nothing sent today", subject)
        self.assertIn("No emails went out", text)
        self.assertIn("No applications went out", html)

    def test_test_sends_are_flagged_as_tests(self):
        state = self.state(self.sent_job(status="test_sent"))
        subject, text, _ = jm.summary_bodies(
            jm.collect_summary(state, jm.summary_window(state)))
        self.assertIn("TEST", subject)
        self.assertIn("[TEST]", text)

    def test_replies_and_followups_are_reported(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        state = self.state(
            self.sent_job(external_id="r", status="replied", company="Replier Ltd",
                          replied_at=recent),
            self.sent_job(external_id="f", company="Chased Ltd",
                          followup_sent_at=recent))
        _, text, _ = jm.summary_bodies(
            jm.collect_summary(state, jm.summary_window(state)))
        self.assertIn("Replies received: Replier Ltd", text)
        self.assertIn("Follow-ups sent: Chased Ltd", text)

    def test_sends_multipart_to_harry_and_records_the_timestamp(self):
        state = self.state(self.sent_job())
        uk = datetime(2026, 8, 1, 22, 0, tzinfo=timezone.utc)
        with mock.patch.object(jm, "uk_now", return_value=uk), \
             mock.patch.object(jm.smtplib, "SMTP_SSL") as smtp:
            jm.send_summary(state)
        msg = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(msg["To"], "harry@gmail.com")
        self.assertIn("application(s) sent", msg["Subject"])
        self.assertEqual({p.get_content_subtype() for p in msg.iter_parts()},
                         {"plain", "html"})
        self.assertIn("last_summary_at", state)


class TestScoringBudget(unittest.TestCase):
    def test_pre_filter_keeps_the_trades_and_drops_the_rest(self):
        keep = ["Electronics Technician", "Instrumentation Engineer",
                "Maintenance Fitter", "Subsea ROV Technician",
                "Communications Officer", "Calibration Technician",
                "Electrical Apprentice", "Test Engineer", "PLC Programmer"]
        drop = ["Marketing Executive", "Care Assistant", "Retail Supervisor",
                "Primary School Teacher", "Accounts Payable Clerk"]
        for title in keep:
            with self.subTest(title=title):
                self.assertTrue(jm.worth_scoring({"title": title, "description": ""}))
        for title in drop:
            with self.subTest(title=title):
                self.assertFalse(jm.worth_scoring({"title": title, "description": ""}))

    def test_a_vague_title_is_rescued_by_its_description(self):
        job = {"title": "Field Service Representative",
               "description": "You will fault-find and calibrate test equipment "
                              "on offshore assets."}
        self.assertTrue(jm.worth_scoring(job))

    def test_off_target_listings_never_reach_gemini(self):
        state = {"jobs": {
            "a": make_job(external_id="a", status="new", title="Electronics Technician"),
            "b": make_job(external_id="b", status="new", title="Care Assistant",
                          description="Support residents with daily living."),
        }, "companies_contacted": {}, "send_counts": {}}
        with mock.patch.object(jm, "score_batch",
                               return_value={0: (88, "good match")}) as batch:
            jm.score_jobs(state)
        scored = batch.call_args.args[0]
        self.assertEqual([j["external_id"] for j in scored], ["a"])
        self.assertEqual(state["jobs"]["b"]["status"], "skipped")
        self.assertIn("pre-filter", state["jobs"]["b"]["skip_reason"])
        self.assertEqual(state["jobs"]["a"]["status"], "scored")

    def test_batching_sends_one_call_per_ten_listings(self):
        jobs = {str(i): make_job(external_id=str(i), status="new",
                                 title="Electronics Technician") for i in range(25)}
        state = {"jobs": jobs, "companies_contacted": {}, "send_counts": {}}
        with mock.patch.object(jm, "score_batch",
                               side_effect=lambda b: {i: (75, "ok")
                                                      for i in range(len(b))}) as batch:
            jm.score_jobs(state)
        self.assertEqual(batch.call_count, 3)          # 25 listings, 3 calls
        self.assertEqual(sum(1 for j in jobs.values()
                             if j["status"] == "scored"), 25)

    def test_an_empty_batch_stops_the_run_rather_than_burning_quota(self):
        jobs = {str(i): make_job(external_id=str(i), status="new",
                                 title="Electronics Technician") for i in range(30)}
        state = {"jobs": jobs, "companies_contacted": {}, "send_counts": {}}
        with mock.patch.object(jm, "score_batch",
                               side_effect=[{0: (80, "ok")}, {}, {}]) as batch:
            jm.score_jobs(state)
        self.assertEqual(batch.call_count, 2)  # stopped after the empty one
        self.assertEqual(sum(1 for j in jobs.values() if j["status"] == "new"), 29)

    def test_score_batch_parses_the_array_and_clamps(self):
        batch = [make_job(external_id=str(i)) for i in range(3)]
        reply = [{"listing": 0, "score": 91, "reason": "strong"},
                 {"listing": 1, "score": 150, "reason": "over"},
                 {"listing": 2, "score": "not a number"}]
        with mock.patch.object(jm, "gemini_json", return_value=reply):
            scores = jm.score_batch(batch)
        self.assertEqual(scores[0], (91, "strong"))
        self.assertEqual(scores[1][0], 100)
        self.assertNotIn(2, scores)

    def test_score_batch_survives_a_wrapped_or_single_object(self):
        batch = [make_job(external_id="0")]
        for reply in ({"results": [{"listing": 0, "score": 80, "reason": "r"}]},
                      {"listing": 0, "score": 80, "reason": "r"}):
            with self.subTest(reply=reply):
                with mock.patch.object(jm, "gemini_json", return_value=reply):
                    self.assertEqual(jm.score_batch(batch)[0], (80, "r"))

    def test_gemini_calls_are_spaced_out(self):
        with mock.patch.object(jm, "GEMINI_MIN_INTERVAL", 5), \
             mock.patch.object(jm, "_gemini_last_call", jm.time.monotonic()), \
             mock.patch.object(jm, "GEMINI_API_KEY", "k"), \
             mock.patch.object(jm.time, "sleep") as slept, \
             mock.patch.object(jm.requests, "post") as post:
            post.return_value = mock.Mock(
                status_code=200,
                json=lambda: {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
            jm.gemini("hello")
        self.assertTrue(slept.called)
        self.assertGreater(slept.call_args_list[0].args[0], 0)

    def test_a_429_honours_the_delay_google_asks_for(self):
        response = mock.Mock(status_code=429)
        response.json = lambda: {"error": {"details": [{"retryDelay": "42s"}]}}
        self.assertEqual(jm._retry_delay(response, 10), 43.0)
        broken = mock.Mock(status_code=429)
        broken.json = mock.Mock(side_effect=ValueError)
        self.assertEqual(jm._retry_delay(broken, 10), 10)


class TestReplyWatcher(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(jm, "save"),
                   mock.patch.object(jm.time, "sleep"),
                   mock.patch.object(jm, "TEST_MODE", False),
                   mock.patch.object(jm.imaplib, "IMAP4_SSL"),
                   mock.patch.object(jm, "GMAIL_ADDRESS", "harry@gmail.com"),
                   mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def watched(self, **over):
        fields = {"status": "sent", "sent_at": jm.now(), "message_id": "<orig>",
                  "sent_subject": "Subsea tech for your Electronics Technician"}
        fields.update(over)
        job = make_job(**fields)
        return {"jobs": {job["external_id"]: job}, "companies_contacted": {},
                "send_counts": {}}, job

    REPLY = {"subject": "RE: Subsea tech", "message_id": "<theirs>",
             "text": "Thanks Harry, could you come in Thursday for a chat?"}

    def test_interview_invite_gets_availability_reply_in_minutes(self):
        state, job = self.watched()
        with mock.patch.object(jm, "fetch_latest_reply", return_value=self.REPLY), \
             mock.patch.object(jm, "classify_reply",
                               return_value=("interview_invite", "wants Thursday")), \
             mock.patch.object(jm, "send_email", return_value="<auto>") as send:
            jm.check_replies(state)
        # first send is the auto-reply to them, second is the alert to Harry
        first = send.call_args_list[0]
        self.assertEqual(first.args[0], "jane.smith@acme.com")
        # replies keep their own subject line, whatever case they wrote it in
        self.assertTrue(first.args[1].lower().startswith("re:"))
        self.assertIn("Which day works best", first.args[2])
        self.assertEqual(jm.slop_check(first.args[2]), [])
        self.assertFalse(first.kwargs["attach_cv"])
        self.assertEqual(first.kwargs["headers"]["In-Reply-To"], "<theirs>")
        alert = send.call_args_list[1]
        self.assertEqual(alert.args[0], "harry@gmail.com")
        self.assertIn("INTERVIEW", alert.args[1])
        self.assertEqual(job["status"], "replied")
        self.assertIn("auto_replied_at", job)

    def test_a_question_is_alerted_never_auto_answered(self):
        state, job = self.watched()
        with mock.patch.object(jm, "fetch_latest_reply", return_value=self.REPLY), \
             mock.patch.object(jm, "classify_reply",
                               return_value=("question", "asked about salary")), \
             mock.patch.object(jm, "send_email", return_value="<a>") as send:
            jm.check_replies(state)
        self.assertEqual(send.call_count, 1)  # alert only
        self.assertEqual(send.call_args.args[0], "harry@gmail.com")
        self.assertIn("NEEDS YOUR ANSWER", send.call_args.args[1])
        self.assertNotIn("auto_replied_at", job)

    def test_automated_receipts_stay_silent(self):
        state, job = self.watched()
        with mock.patch.object(jm, "fetch_latest_reply", return_value=self.REPLY), \
             mock.patch.object(jm, "classify_reply",
                               return_value=("auto_acknowledgement", "receipt")), \
             mock.patch.object(jm, "send_email") as send:
            jm.check_replies(state)
        send.assert_not_called()
        self.assertEqual(job["reply_category"], "auto_acknowledgement")

    def test_unclassifiable_reply_still_alerts_but_never_auto_replies(self):
        state, job = self.watched()
        with mock.patch.object(jm, "fetch_latest_reply", return_value=self.REPLY), \
             mock.patch.object(jm, "classify_reply", return_value=(None, None)), \
             mock.patch.object(jm, "send_email", return_value="<a>") as send:
            jm.check_replies(state)
        self.assertEqual(send.call_count, 1)
        self.assertNotIn("auto_replied_at", job)

    def test_a_reply_is_only_handled_once(self):
        state, job = self.watched(reply_handled_at=jm.now(), status="replied")
        with mock.patch.object(jm, "fetch_latest_reply") as fetch, \
             mock.patch.object(jm, "send_email") as send:
            jm.check_replies(state)
        fetch.assert_not_called()
        send.assert_not_called()

    def test_disabled_autorespond_still_alerts(self):
        state, job = self.watched()
        with mock.patch.object(jm, "REPLY_AUTORESPOND", False), \
             mock.patch.object(jm, "fetch_latest_reply", return_value=self.REPLY), \
             mock.patch.object(jm, "classify_reply",
                               return_value=("interview_invite", "x")), \
             mock.patch.object(jm, "send_email", return_value="<a>") as send:
            jm.check_replies(state)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[0], "harry@gmail.com")

    def test_test_mode_never_touches_the_inbox(self):
        state, _ = self.watched()
        with mock.patch.object(jm, "TEST_MODE", True), \
             mock.patch.object(jm, "fetch_latest_reply") as fetch:
            jm.check_replies(state)
        fetch.assert_not_called()


class TestSecondFollowup(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(jm, "save"),
                   mock.patch.object(jm.time, "sleep"),
                   mock.patch.object(jm, "TEST_MODE", False),
                   mock.patch.object(jm, "GMAIL_ADDRESS", "h@g.com"),
                   mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def aged(self, days, **over):
        job = make_job(status="sent", message_id="<orig>",
                       sent_subject="Subsea tech",
                       sent_at=(datetime.now(timezone.utc)
                                - timedelta(days=days)).isoformat(), **over)
        return {"jobs": {"j": job}, "companies_contacted": {},
                "send_counts": {}}, job

    def test_second_nudge_goes_out_at_nine_days(self):
        state, job = self.aged(10, followup_sent_at=jm.now())
        with mock.patch.object(jm, "has_reply_from", return_value=False), \
             mock.patch.object(jm, "send_email", return_value="<f2>") as send:
            jm.run_followups(state)
        self.assertIn("Last note from me", send.call_args.args[2])
        self.assertIn("followup2_sent_at", job)

    def test_no_second_nudge_before_nine_days(self):
        state, job = self.aged(6, followup_sent_at=jm.now())
        with mock.patch.object(jm, "has_reply_from") as check, \
             mock.patch.object(jm, "send_email") as send:
            jm.run_followups(state)
        send.assert_not_called()

    def test_after_both_nudges_silence(self):
        state, job = self.aged(20, followup_sent_at=jm.now(),
                               followup2_sent_at=jm.now())
        with mock.patch.object(jm, "send_email") as send:
            jm.run_followups(state)
        send.assert_not_called()


class TestSpeculative(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(jm, "save"),
                   mock.patch.object(jm.time, "sleep"),
                   mock.patch.object(jm, "TEST_MODE", False),
                   mock.patch.object(jm, "cv_for", return_value="/tmp/cv.pdf"),
                   mock.patch.object(jm, "has_mx", return_value=True),
                   mock.patch.object(jm, "find_domain", return_value="acme.com"),
                   mock.patch.object(jm, "scrape_site",
                                     return_value=(["careers@acme.com"], []))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.state = {"jobs": {}, "companies_contacted": {}, "send_counts": {}}
        self.content = {"subject": "DV-cleared technician asking about openings",
                        "body": "Hi,\n\nbody\n\nHarry\n" + jm.SIGNOFF,
                        "family": "speculative"}

    def test_targets_ship_and_have_notes(self):
        targets = jm.load_targets()
        self.assertGreater(len(targets), 20)
        for t in targets:
            self.assertTrue(t.get("company") and t.get("note"))

    def test_caps_at_spec_per_day(self):
        with mock.patch.object(jm, "SPEC_PER_DAY", 2), \
             mock.patch.object(jm, "build_email", return_value=self.content), \
             mock.patch.object(jm, "send_email", return_value="<s>") as send:
            jm.speculative(self.state)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(jm.spec_sends_today(self.state), 2)
        spec_jobs = [j for j in self.state["jobs"].values()
                     if j["status"] == "spec_sent"]
        self.assertEqual(len(spec_jobs), 2)
        self.assertTrue(all(j["source"] == "speculative" for j in spec_jobs))

    def test_never_a_company_already_contacted(self):
        first = jm.load_targets()[0]["company"]
        self.state["companies_contacted"][jm.company_key(first)] = {"at": jm.now()}
        with mock.patch.object(jm, "SPEC_PER_DAY", 1), \
             mock.patch.object(jm, "build_email", return_value=self.content), \
             mock.patch.object(jm, "send_email", return_value="<s>") as send:
            jm.speculative(self.state)
        sent_to = [j["company"] for j in self.state["jobs"].values()]
        self.assertNotIn(first, sent_to)

    def test_no_real_address_means_no_send_and_no_retry(self):
        with mock.patch.object(jm, "scrape_site", return_value=([], [])), \
             mock.patch.object(jm, "SPEC_PER_DAY", 1), \
             mock.patch.object(jm, "send_email") as send:
            jm.speculative(self.state)
        send.assert_not_called()
        self.assertTrue(all(v == "no real address"
                            for v in self.state["spec_done"].values()))

    def test_speculative_template_is_used_and_honest(self):
        job = {"external_id": "spec_x", "source": "speculative",
               "title": "Engineering Technician", "company": "Acme",
               "location": "Aberdeen", "contact_name": None,
               "description": "Speculative approach. What they do: subsea kit."}
        good = {"subject": "DV-cleared technician asking about openings",
                "body": ("Nothing advertised that I can see, so this is a "
                         "speculative note. I know Acme make subsea kit in "
                         "Aberdeen close to my old bench at Sonardyne.\n\n"
                         "1. Three years at Sonardyne building and testing subsea "
                         "electronics to IPC-A-610 Class 3 standard every day.\n"
                         "2. Two years Royal Navy comms on a Type 23 frigate and "
                         "I hold current DV clearance.\n\n"
                         "Any technician openings coming up this year worth a "
                         "conversation?\n\nHarry\n" + jm.SIGNOFF)}
        with mock.patch.object(jm, "gemini_json", return_value=good):
            content = jm.build_email(job)
        self.assertIsNotNone(content)
        self.assertEqual(job["template_family"], "speculative")
        self.assertNotIn("listing", content["body"].lower())


class TestTailoredCV(unittest.TestCase):
    def test_each_family_builds_a_different_pdf(self):
        import cv_tailor
        paths = {f: cv_tailor.build(f) for f in
                 ("communications", "electronics_technician",
                  "instrumentation_maintenance", "events_production")}
        for family, path in paths.items():
            with self.subTest(family=family):
                self.assertTrue(path and os.path.exists(path))
        self.assertEqual(len(set(paths.values())), 4)

    def test_general_and_unknown_fall_back_to_the_master_pdf(self):
        import cv_tailor
        self.assertIsNone(cv_tailor.build("general"))
        self.assertIsNone(cv_tailor.build("nonsense"))
        self.assertTrue(jm.cv_for(make_job(title="Stores Assistant",
                                           description="")).endswith(
                                               "Harry_Russell_CV.pdf"))

    def test_summary_actually_changes_with_the_family(self):
        import cv_tailor
        paragraphs = list(cv_tailor.read_docx(cv_tailor.DOCX))
        comms = cv_tailor.tailor_paragraphs(paragraphs, "communications")
        events = cv_tailor.tailor_paragraphs(paragraphs, "events_production")
        comms_text = " ".join(t for t, _ in comms)
        events_text = " ".join(t for t, _ in events)
        self.assertIn("cryptographic material in the Royal Navy", comms_text)
        self.assertIn("Leads2Profit", events_text.split("WORK EXPERIENCE")[0])
        # nothing invented: the facts all exist in the master CV already
        self.assertIn("HARRY DEAN RUSSELL", comms_text)

    def test_skills_are_reordered_not_rewritten(self):
        import cv_tailor
        paragraphs = list(cv_tailor.read_docx(cv_tailor.DOCX))
        original = [t for t, b in paragraphs if b]
        tailored = cv_tailor.tailor_paragraphs(paragraphs, "communications")
        tailored_bullets = [t for t, b in tailored if b]
        self.assertEqual(sorted(original), sorted(tailored_bullets))


class TestState(unittest.TestCase):
    def test_prune_keeps_history_and_drops_dead_listings(self):
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        state = {"jobs": {
            "dead": make_job(status="no_email", found_at=old),
            "old_sent": make_job(status="sent", found_at=old),
            "fresh": make_job(status="skipped"),
        }, "companies_contacted": {}, "send_counts": {}}
        jm.prune(state)
        self.assertEqual(sorted(state["jobs"]), ["fresh", "old_sent"])

    def test_cv_lookup_prefers_the_cv_folder(self):
        self.assertTrue((jm.cv_path() or "").endswith(".pdf"),
                        "a CV PDF should be committed in cv/")
        self.assertIn("cv", os.path.dirname(jm.cv_path() or ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRecruitmentAgencies(unittest.TestCase):
    """An agency is not an employer and must not be rationed like one.

    An employer has the one job they advertised. An agency is paid to place
    people, holds dozens of roles, and expects to hear from candidates - six of
    the fourteen firms in the last portal run were agencies, and the
    one-email-per-company-ever rule made each of them worth a single approach
    for the rest of time.
    """
    def state(self):
        return {"jobs": {}, "companies_contacted": {}, "send_counts": {}}

    def test_agencies_are_recognised_by_name(self):
        for name in ("Matchtech", "Orion Electrotech", "Morson Edge",
                     "Future Engineering Recruitment Ltd", "Cammach Bryant",
                     "NES Fircroft", "Thorpe Molloy Recruitment"):
            with self.subTest(name=name):
                self.assertTrue(jm.is_agency({"company": name}), name)

    def test_agencies_are_recognised_by_the_wording_they_must_use(self):
        for body in ("Our client is a leading subsea contractor",
                     "acting as an employment agency in relation to this vacancy",
                     "We are recruiting for a well established Aberdeen firm"):
            with self.subTest(body=body[:30]):
                self.assertTrue(jm.is_agency({"company": "ABC Ltd",
                                              "description": body}))

    def test_a_real_employer_is_not_mistaken_for_one(self):
        for name in ("Hydrasun", "Baker Hughes", "Dron & Dickson", "EnerMech",
                     "ScottishPower"):
            with self.subTest(name=name):
                self.assertFalse(jm.is_agency({"company": name,
                                               "description": "Join our team"}))

    def test_an_employer_still_gets_one_email_ever(self):
        state = self.state()
        job = {"company": "Hydrasun", "external_id": "a", "description": ""}
        jm.mark_contacted(state, job)
        self.assertTrue(jm.already_contacted(
            state, {"company": "Hydrasun", "external_id": "b", "description": ""}))

    def test_an_agency_can_be_approached_again_about_a_different_role(self):
        state = self.state()
        first = {"company": "Matchtech", "external_id": "a", "description": ""}
        jm.mark_contacted(state, first)
        entry = state["companies_contacted"][jm.company_key("Matchtech")]
        entry["at"] = "2026-01-01T09:00:00+00:00"          # long enough ago
        self.assertFalse(jm.already_contacted(
            state, {"company": "Matchtech", "external_id": "b", "description": ""}))

    def test_never_twice_about_the_same_vacancy(self):
        state = self.state()
        job = {"company": "Matchtech", "external_id": "a", "description": ""}
        jm.mark_contacted(state, job)
        state["companies_contacted"][jm.company_key("Matchtech")]["at"] = \
            "2026-01-01T09:00:00+00:00"
        self.assertTrue(jm.already_contacted(state, job))

    def test_an_agency_is_not_emailed_twice_in_the_same_week(self):
        state = self.state()
        jm.mark_contacted(state, {"company": "Matchtech", "external_id": "a",
                                  "description": ""})
        self.assertTrue(jm.already_contacted(
            state, {"company": "Matchtech", "external_id": "b", "description": ""}))

    def test_an_agency_is_not_pestered_for_ever(self):
        state = self.state()
        for n in range(jm.AGENCY_MAX_APPROACHES):
            job = {"company": "Matchtech", "external_id": f"job{n}",
                   "description": ""}
            jm.mark_contacted(state, job)
            state["companies_contacted"][jm.company_key("Matchtech")]["at"] = \
                "2026-01-01T09:00:00+00:00"
        self.assertTrue(jm.already_contacted(
            state, {"company": "Matchtech", "external_id": "last",
                    "description": ""}))

    def test_an_agency_advert_gets_the_consultants_email_whatever_the_trade(self):
        self.assertEqual(
            jm.pick_family("Instrumentation Technician",
                           "Our client is a subsea contractor", "Matchtech"),
            "agency")
        self.assertNotEqual(
            jm.pick_family("Instrumentation Technician",
                           "Join our Aberdeen team", "Hydrasun"),
            "agency")


class TestWhenItSends(unittest.TestCase):
    """Tuesday to Thursday, 9-11am gets the best open and reply rates; applying
    within 24-48 hours produces two to three times the interviews. Speed is
    measured in days and timing in hours, so both can be had."""
    def at(self, day, hour):
        # 3 Aug 2026 is a Monday
        return datetime(2026, 8, 3 + day, hour, tzinfo=timezone.utc)

    def test_the_window_is_tuesday_to_thursday_mid_morning(self):
        self.assertTrue(jm.in_peak_window(self.at(1, 10)))    # Tue 10am
        self.assertTrue(jm.in_peak_window(self.at(3, 9)))     # Thu 9am

    def test_monday_and_friday_are_not_the_window(self):
        self.assertFalse(jm.in_peak_window(self.at(0, 10)))
        self.assertFalse(jm.in_peak_window(self.at(4, 10)))

    def test_early_morning_and_afternoon_are_not_the_window(self):
        self.assertFalse(jm.in_peak_window(self.at(1, 7)))
        self.assertFalse(jm.in_peak_window(self.at(1, 15)))

    def test_a_listing_posted_hours_ago_does_not_wait_for_the_window(self):
        fresh = {"posted_at": (datetime.now(timezone.utc)
                               - timedelta(hours=2)).isoformat()}
        self.assertTrue(jm.brand_new(fresh))

    def test_a_listing_from_two_days_ago_can_wait(self):
        stale = {"posted_at": (datetime.now(timezone.utc)
                               - timedelta(hours=48)).isoformat()}
        self.assertFalse(jm.brand_new(stale))

    def test_a_listing_with_no_date_is_not_treated_as_fresh(self):
        self.assertFalse(jm.brand_new({}))


class TestReachingASecondPerson(unittest.TestCase):
    """Three touches to one inbox capture about 93% of the replies a sequence
    will ever earn. A fourth to the same person is pestering; a first to a
    different person at the same company is a new conversation, and it is the
    one documented reason to go past three."""

    def job(self, **over):
        base = {"external_id": "j1", "status": "sent", "title": "Test Technician",
                "company": "Acme Subsea", "contact_email": "jane@acme.com",
                "contact_name": "Jane", "message_id": "<m1>",
                "sent_subject": "Ex-Navy tech for your Test Technician",
                "sent_at": (datetime.now(timezone.utc)
                            - timedelta(days=20)).isoformat(),
                "followup_sent_at": "2026-07-20T09:00:00+00:00",
                "followup2_sent_at": "2026-07-25T09:00:00+00:00",
                "other_contacts": [{"email": "ops@acme.com", "name": None,
                                    "tier": 2}]}
        base.update(over)
        return base

    def run_one(self, job):
        state = {"jobs": {job["external_id"]: job}}
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "has_reply_from", return_value=False), \
             mock.patch.object(jm, "save"), mock.patch.object(jm.time, "sleep"), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_followups(state)
        return send

    def test_discovery_keeps_the_addresses_it_did_not_use(self):
        self.assertEqual(
            [c["email"] for c in jm.ranked_emails(
                ["info@acme.com", "jane.smith@acme.com", "careers@acme.com"])],
            ["jane.smith@acme.com", "careers@acme.com", "info@acme.com"])

    def test_a_fourth_touch_goes_to_a_different_person(self):
        job = self.job()
        send = self.run_one(job)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "ops@acme.com")
        self.assertEqual(job["stakeholder_email"], "ops@acme.com")

    def test_it_is_a_new_conversation_not_a_reply(self):
        """No Re:, no threading headers, and the CV goes with it - this reader
        has never seen any of it."""
        job = self.job()
        send = self.run_one(job)
        subject = send.call_args.args[1]
        self.assertFalse(subject.lower().startswith("re:"))
        self.assertTrue(send.call_args.kwargs["attach_cv"])
        self.assertEqual(send.call_args.kwargs["headers"], {})

    def test_nobody_is_written_to_twice(self):
        job = self.job(stakeholder_sent_at="2026-08-01T09:00:00+00:00")
        self.run_one(job).assert_not_called()

    def test_with_no_second_address_the_sequence_simply_ends(self):
        job = self.job(other_contacts=[])
        self.run_one(job).assert_not_called()

    def test_the_first_contact_is_never_the_second_contact(self):
        job = self.job(other_contacts=[{"email": "JANE@acme.com", "name": "Jane",
                                        "tier": 3}])
        self.run_one(job).assert_not_called()

    def test_it_waits_a_fortnight_before_trying_anyone_else(self):
        job = self.job(sent_at=(datetime.now(timezone.utc)
                                - timedelta(days=11)).isoformat())
        self.run_one(job).assert_not_called()

    def test_a_reply_stops_the_whole_sequence(self):
        job = self.job()
        state = {"jobs": {"j1": job}}
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "has_reply_from", return_value=True), \
             mock.patch.object(jm, "save"), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_followups(state)
        send.assert_not_called()
        self.assertEqual(job["status"], "replied")


class TestVeteranEmployers(unittest.TestCase):
    """The Armed Forces Covenant carries a guaranteed interview scheme at many
    signatories: a veteran meeting the minimum criteria gets interviewed. That
    converts an application into an interview by policy rather than by
    persuasion, which is worth more than any number of extra cold emails."""

    def test_the_shipped_list_is_usable(self):
        for company in ("Thales", "Leonardo", "Babcock International",
                        "NHS Grampian", "Police Scotland"):
            with self.subTest(company=company):
                self.assertTrue(jm.veteran_friendly({"company": company}))

    def test_a_longer_legal_name_still_matches(self):
        self.assertTrue(jm.veteran_friendly({"company": "Thales UK Ltd"}))
        self.assertTrue(jm.veteran_friendly({"company": "Babcock International Group"}))

    def test_an_unlisted_employer_is_not_assumed_to_run_a_scheme(self):
        for company in ("Hydrasun", "Dron & Dickson", "Some Local Firm", ""):
            with self.subTest(company=company):
                self.assertFalse(jm.veteran_friendly({"company": company}))

    def test_they_are_written_to_first(self):
        """Ahead of a better contact and a higher score at an ordinary firm."""
        jobs = {
            "ordinary": dict(make_job(external_id="ordinary", status="ready",
                                      company="Some Firm", score=99,
                                      email_tier=3, contact_email="jane@f.com")),
            "veteran": dict(make_job(external_id="veteran", status="ready",
                                     company="Leonardo", score=72,
                                     email_tier=1, contact_email="info@l.com")),
        }
        state = {"jobs": jobs, "companies_contacted": {}, "send_counts": {}}
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "in_peak_window", return_value=True), \
             mock.patch.object(jm, "build_email",
                               return_value={"subject": "s", "body": "b",
                                             "family": "general"}), \
             mock.patch.object(jm, "cv_for", return_value=None), \
             mock.patch.object(jm, "save"), mock.patch.object(jm.time, "sleep"), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(state)
        self.assertEqual(send.call_args_list[0].args[0], "info@l.com")

    def test_the_email_asks_about_the_scheme_and_never_claims_one(self):
        """A wrong entry in the list must be incapable of putting a false
        statement in his name, so the instruction asks rather than asserts."""
        text = jm.VETERAN_INSTRUCTION.lower()
        self.assertIn("ask", text)
        self.assertIn("never state or assume", text)
        self.assertIn("never mention awards", text)

    def test_the_instruction_is_only_added_for_listed_employers(self):
        captured = {}

        def fake_gemini(prompt, **kw):
            captured[prompt.count("ARMED FORCES COVENANT")] = True
            return {"subject": "Ex-Navy tech for your role",
                    "body": "Hi,\n\n1. x\n2. y\n\nWorth a chat?\n\nHarry\n"
                            + jm.SIGNOFF}

        for company, expected in (("Leonardo", 1), ("Hydrasun", 0)):
            captured.clear()
            with self.subTest(company=company), \
                 mock.patch.object(jm, "gemini_json", side_effect=fake_gemini):
                jm.build_email(make_job(company=company, status="ready"))
            self.assertIn(expected, captured)


class TestInboundReminder(unittest.TestCase):
    """The outbound side writes to employers. This is the other direction:
    places a recruiter finds Harry. Registering is a one-off he does himself,
    but CV databases rank by how recently a CV was touched, so the ranking
    decays every week whether he does anything or not."""

    def sunday(self, hour=22):
        return datetime(2026, 8, 9, hour, tzinfo=timezone.utc)   # a Sunday

    def test_it_arrives_on_a_sunday(self):
        text, html = jm.inbound_reminder(self.sunday())
        self.assertTrue(text)
        self.assertIn("RightJob", "\n".join(text))

    def test_it_does_not_arrive_on_any_other_day(self):
        for offset in range(1, 7):
            when = self.sunday() + timedelta(days=offset)
            with self.subTest(day=when.strftime("%A")):
                self.assertEqual(jm.inbound_reminder(when), (None, None))

    def test_the_veterans_charity_is_the_first_thing_listed(self):
        """Free to him, and they work with thousands of employers who
        specifically want ex-forces - the strongest inbound channel he has."""
        self.assertIn("RightJob", jm.INBOUND_TASKS[0][0])

    def test_every_entry_carries_a_link_and_a_reason(self):
        for name, url, why in jm.INBOUND_TASKS:
            with self.subTest(name=name):
                self.assertTrue(url.startswith("https://"))
                self.assertTrue(len(why) > 10)

    def test_the_digest_carries_it_on_a_sunday(self):
        state = {"jobs": {}, "companies_contacted": {}, "send_counts": {}}
        data = jm.collect_summary(state, jm.summary_window(state))
        with mock.patch.object(jm, "uk_now", return_value=self.sunday()):
            subject, text, html = jm.summary_bodies(data)
        self.assertIn("RightJob", text)
        self.assertIn("RightJob", html)


class TestRescoringAfterTheProfileChanges(unittest.TestCase):
    """A score is a judgement against CANDIDATE_PROFILE at the moment it was
    made, so changing the profile silently invalidates every score in the file.

    When the profile said 'Aberdeen strongly preferred', fifty-two listings
    that matched Harry's trade exactly were marked down to 65 and binned for
    being in Dundee, Inverness or Perth - one of them an Electro-Technical
    Officer post, about as close to his Navy trade as an advert gets.
    """
    def state(self, *jobs):
        return {"jobs": {j["external_id"]: j for j in jobs}}

    def skipped(self, eid, score, reason=None):
        return make_job(external_id=eid, status="skipped", score=score,
                        skip_reason=reason or f"score {score}",
                        score_reason="but it is not in Aberdeen")

    def test_a_near_miss_goes_back_in_the_queue(self):
        state = self.state(self.skipped("near", 65))
        jm.rescore(state)
        job = state["jobs"]["near"]
        self.assertEqual(job["status"], "new")
        self.assertNotIn("score", job)
        self.assertNotIn("score_reason", job)

    def test_a_genuinely_poor_match_stays_binned(self):
        state = self.state(self.skipped("poor", 20))
        jm.rescore(state)
        self.assertEqual(state["jobs"]["poor"]["status"], "skipped")

    def test_the_floor_can_be_moved(self):
        state = self.state(self.skipped("mid", 45))
        jm.rescore(state, floor=40)
        self.assertEqual(state["jobs"]["mid"]["status"], "new")

    def test_listings_the_scorer_never_judged_are_left_alone(self):
        """The title filter and the pre-filter were not judgement calls, so a
        change to the profile says nothing about them."""
        for reason in ("off target (pre-filter)", "title excluded (chef)",
                       "company already contacted", "no real address found"):
            with self.subTest(reason=reason):
                state = self.state(make_job(external_id="x", status="skipped",
                                            score=65, skip_reason=reason))
                jm.rescore(state)
                self.assertEqual(state["jobs"]["x"]["status"], "skipped")

    def test_work_already_sent_is_never_disturbed(self):
        state = self.state(make_job(external_id="sent", status="sent", score=91))
        jm.rescore(state)
        self.assertEqual(state["jobs"]["sent"]["status"], "sent")


class TestTheSendStageRunningTwice(unittest.TestCase):
    """Sending runs before the slow stages as well as after.

    A run released ninety-nine parked listings, found real addresses for six,
    and was killed by the workflow timeout in address discovery before it sent
    one of them - they sat in 'ready' with nothing to show for the run. The
    cheapest stage was queued behind the most expensive one."""

    def test_the_cap_is_per_run_not_per_call(self):
        """Two calls must not mean two caps. Carrying the count is the whole
        reason the second call takes an argument."""
        state = {"jobs": {}, "send_counts": {}, "companies_contacted": {}}
        with mock.patch.object(jm, "cv_path", return_value="cv.pdf"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"):
            already = jm.PER_RUN_SEND_CAP
            self.assertEqual(jm.run_sends(state, already_sent=already), already)

    def test_a_refusal_still_reports_the_running_total(self):
        """No CV means no send, but the count must survive so the second call
        does not start again from zero."""
        with mock.patch.object(jm, "cv_path", return_value=None):
            self.assertEqual(jm.run_sends({"jobs": {}}, already_sent=3), 3)

    def test_a_stage_that_fails_does_not_destroy_the_count(self):
        self.assertIsNone(jm.stage("boom", lambda: 1 / 0))
        self.assertEqual(jm.stage("fine", lambda: 5), 5)


class TestWhereHarryCanWork(unittest.TestCase):
    def test_the_profile_no_longer_treats_aberdeen_as_a_requirement(self):
        """He can take work anywhere that comes with the arrangements to live
        it, and the scorer was quietly costing him every rotational role.

        Aberdeen has since gone further than 'not required': he took a job
        there, so a local listing is no longer even a tie-breaker."""
        profile = jm.CANDIDATE_PROFILE.lower()
        self.assertNotIn("aberdeen strongly preferred", profile)
        for expected in ("rotational", "fly-in", "accommodation",
                         "not a preference to score up", "relocate"):
            self.assertIn(expected, profile)

    def test_it_still_records_that_he_does_not_drive(self):
        """A rotational posting is fine; a remote site with no transport is
        not, and the difference matters."""
        self.assertIn("does not drive", jm.CANDIDATE_PROFILE.lower())


class TestFindingTheRotationalMarket(unittest.TestCase):
    """An offshore posting is advertised against a base, a vessel or a whole
    country, so a twenty-five mile radius around Aberdeen has never once seen
    one - and that is the market Harry's trade actually sits in."""

    def test_the_local_sweep_is_unchanged(self):
        first = list(jm.adzuna_searches())[0]
        self.assertEqual(first[0], jm.SEARCH_LOCATIONS[0])
        self.assertEqual(first[1], jm.SEARCH_RADIUS_MILES)

    def test_a_second_sweep_covers_the_whole_country(self):
        wide = [s for s in jm.adzuna_searches() if s[1] == jm.ROTATIONAL_RADIUS]
        self.assertTrue(wide)
        self.assertTrue(all(s[0] == jm.ROTATIONAL_WHERE for s in wide))

    def test_it_looks_for_the_roles_his_trade_actually_holds(self):
        terms = " ".join(s[2] for s in jm.adzuna_searches()).lower()
        for expected in ("offshore technician", "rov technician",
                         "electro technical officer", "subsea technician"):
            self.assertIn(expected, terms)

    def test_the_wide_sweep_uses_narrow_phrases(self):
        """A whole-country search on 'technician' would drag in everything;
        these have to be specific enough to be worth the width."""
        for where, radius, kw in jm.adzuna_searches():
            if radius == jm.ROTATIONAL_RADIUS:
                with self.subTest(kw=kw):
                    self.assertGreaterEqual(len(kw.split()), 2)

    def test_every_search_carries_its_own_radius(self):
        for where, radius, kw in jm.adzuna_searches():
            self.assertIn(radius, (jm.SEARCH_RADIUS_MILES, jm.ROTATIONAL_RADIUS))


class TestAnsweringAnInterviewInvite(unittest.TestCase):
    """The reply that books the interview. Mobility is the first thing an
    offshore operator screens for, so leaving them to ask it is a wasted
    exchange when the answer is an unqualified yes."""

    def test_an_offshore_advert_is_recognised(self):
        for job in ({"title": "ROV Technician", "description": "3/3 rotation"},
                    {"title": "Technician", "description": "offshore, vessel based"},
                    {"title": "Electrician", "description": "back-to-back rota"},
                    {"title": "Tech", "description": "FIFO to the platform"}):
            with self.subTest(job=job["description"]):
                self.assertTrue(jm.rotational(job))

    def test_an_ordinary_workshop_job_is_not(self):
        self.assertFalse(jm.rotational(
            {"title": "Workshop Technician",
             "description": "Dayshift in our Aberdeen workshop, Monday to Friday."}))

    def test_the_offshore_reply_answers_the_mobility_question_unasked(self):
        body = jm.autoreply_body({"title": "ROV Technician",
                                  "description": "offshore 3/3 rotation"})
        text = body.format(greeting="Hi,", title="ROV Technician").lower()
        self.assertIn("rotation", text)
        self.assertIn("overseas", text)
        self.assertIn("available immediately", text)

    def test_it_still_offers_a_time_and_a_phone_number(self):
        """Whatever else it says, the job of this email is to book a meeting."""
        for job in ({"title": "X", "description": "offshore rota"},
                    {"title": "X", "description": "workshop dayshift"}):
            text = jm.autoreply_body(job).format(greeting="Hi,", title="X")
            with self.subTest(job=job["description"]):
                self.assertIn("07398 530978", text)
                self.assertIn("Which day works best", text)

    def test_an_onshore_reply_does_not_volunteer_irrelevant_detail(self):
        text = jm.autoreply_body({"title": "Workshop Technician",
                                  "description": "Aberdeen workshop"}).lower()
        self.assertNotIn("offshore", text)


class TestCourseAdvertsAreNotVacancies(unittest.TestCase):
    """Five of these turned up at once in the real queue - all from one
    training provider, all selling a course, all scoring just under the bar
    because the trade words matched. An email to the seller of a course is a
    wasted approach."""

    def test_a_course_being_sold_is_recognised(self):
        for title, body in (
                ("Trainee Incident Response Engineer - job guarantee",
                 "Kickstart your career, no experience needed"),
                ("IT Technician No experience needed!",
                 "Our training academy will get you qualified"),
                ("Trainee Certified Ethical Hacker", "funded training, enrol today"),
                ("Junior IT Helpdesk Technician",
                 "Once qualified we place you with an employer")):
            with self.subTest(title=title):
                self.assertEqual(
                    jm.not_worth_applying({"title": title, "description": body}),
                    "a training course being sold, not a vacancy")

    def test_a_real_vacancy_is_untouched(self):
        for title, body in (
                ("Instrumentation Technician",
                 "Calibration of pressure instrumentation offshore, 3/3 rotation"),
                ("Maintenance Technician",
                 "Planned preventative maintenance in our Aberdeen workshop"),
                ("Apprentice-trained Electronics Technician",
                 "You will have completed an apprenticeship and have experience")):
            with self.subTest(title=title):
                self.assertIsNone(jm.not_worth_applying(
                    {"title": title, "description": body}))

    def test_the_title_filter_still_works_and_is_reported_separately(self):
        self.assertEqual(
            jm.not_worth_applying({"title": "Chef de Partie", "description": ""}),
            "title excluded (chef)")

    def test_a_course_advert_never_reaches_the_scorer(self):
        state = {"jobs": {}, "companies_contacted": {}, "send_counts": {}}
        listing = {"external_id": "adzuna_1", "source": "adzuna",
                   "title": "Trainee Security Engineer - job guarantee",
                   "company": "Newto Training", "location": "United Kingdom",
                   "description": "No experience needed, we train you.",
                   "url": "https://x", "posted_at": jm.now()}
        with mock.patch.object(jm, "adzuna", return_value=[listing]), \
             mock.patch.object(jm, "reed", return_value=[]):
            jm.harvest(state)
        self.assertEqual(state["jobs"]["adzuna_1"]["status"], "skipped")


class TestScoringDoesNotEatTheWholeRun(unittest.TestCase):
    """Sending is the point of a run; scoring is only preparation for it.

    Gemini's free tier answers 429 with a retry delay of up to a minute, so a
    dozen batches can swallow the workflow's whole allowance and produce a run
    that judges a hundred listings and emails nobody. A live run demonstrated
    exactly that.
    """
    def queue(self, n):
        jobs = {}
        for i in range(n):
            j = make_job(external_id=f"n{i}", status="new",
                         title="Instrumentation Technician",
                         description="Calibration of subsea instrumentation.")
            j.pop("score", None)
            jobs[j["external_id"]] = j
        return {"jobs": jobs, "companies_contacted": {}, "send_counts": {}}

    def test_scoring_stops_at_its_budget(self):
        state = self.queue(60)
        # the first reading sets the deadline, the second is the loop's check
        clock = iter([0, 0] + [1e9] * 50)   # one batch fits, then time is up
        with mock.patch.object(jm, "SCORE_BATCH", 10), \
             mock.patch.object(jm.time, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch.object(jm, "score_batch",
                               return_value={i: (95, "good") for i in range(10)}) as sb:
            jm.score_jobs(state)
        self.assertEqual(sb.call_count, 1)

    def test_what_it_did_score_is_kept(self):
        """Stopping early must not throw away the work already done."""
        state = self.queue(30)
        clock = iter([0, 0] + [1e9] * 50)
        with mock.patch.object(jm, "SCORE_BATCH", 10), \
             mock.patch.object(jm.time, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch.object(jm, "score_batch",
                               return_value={i: (95, "good") for i in range(10)}):
            jm.score_jobs(state)
        scored = [j for j in state["jobs"].values() if j.get("status") == "scored"]
        self.assertEqual(len(scored), 10)

    def test_the_rest_stay_queued_for_the_next_run(self):
        state = self.queue(30)
        clock = iter([0, 0] + [1e9] * 50)
        with mock.patch.object(jm, "SCORE_BATCH", 10), \
             mock.patch.object(jm.time, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch.object(jm, "score_batch",
                               return_value={i: (95, "good") for i in range(10)}):
            jm.score_jobs(state)
        still_new = [j for j in state["jobs"].values() if j.get("status") == "new"]
        self.assertEqual(len(still_new), 20)

    def test_an_unhurried_run_scores_everything(self):
        state = self.queue(30)
        with mock.patch.object(jm, "SCORE_BATCH", 10), \
             mock.patch.object(jm.time, "monotonic", return_value=0), \
             mock.patch.object(jm, "score_batch",
                               return_value={i: (95, "good") for i in range(10)}) as sb:
            jm.score_jobs(state)
        self.assertEqual(sb.call_count, 3)


class TestSendingWhenTheModelIsUnavailable(unittest.TestCase):
    """Gemini's free tier has a daily ceiling, and this project is meant to
    cost nothing to run - so hitting that ceiling is a normal Tuesday, not an
    exception.

    When it happened the whole send stage stopped: the composer returned
    nothing, the listing was marked compose_failed, and a matched job with a
    real verified address went nowhere. A rate limit on a free API had been
    allowed to become a hard dependency for applying to a job."""

    def job(self, **over):
        j = {"title": "Workshop Technician", "company": "Oceaneering",
             "location": "Aberdeen", "description": "", "source": "adzuna"}
        j.update(over)
        return j

    def test_the_plain_letter_still_says_who_he_is_and_what_he_wants(self):
        body = jm.plain_email(self.job())["body"]
        self.assertIn("Sonardyne", body)
        self.assertIn("Royal Navy", body)
        self.assertIsNone(jm.claims_clearance(body))
        self.assertIn("Workshop Technician", body)
        self.assertIn("Oceaneering", body)

    def test_it_greets_a_named_person_and_copes_without_one(self):
        self.assertTrue(
            jm.plain_email(self.job(contact_name="Jane"))["body"].startswith("Hi Jane,"))
        self.assertTrue(jm.plain_email(self.job())["body"].startswith("Hi,"))

    def test_a_covenant_employer_still_gets_the_guaranteed_interview_question(self):
        """The single highest-value sentence in the whole system must not be
        the thing that gets dropped when the model is down."""
        with mock.patch.object(jm, "veteran_friendly", return_value=True):
            body = jm.plain_email(self.job(company="Babcock International"))["body"]
        self.assertIn("guaranteed interview", body.lower())

    def test_an_ordinary_employer_is_not_asked_about_veteran_schemes(self):
        with mock.patch.object(jm, "veteran_friendly", return_value=False):
            body = jm.plain_email(self.job())["body"]
        self.assertNotIn("guaranteed interview", body.lower())

    def test_it_never_invents_anything_about_him(self):
        body = jm.plain_email(self.job())["body"].lower()
        for invented in ("passionate", "excited", "dream", "perfect fit",
                         "hardship", "struggling", "desperate"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, body)

    def test_a_missing_company_does_not_produce_a_ragged_subject(self):
        content = jm.plain_email(self.job(company=""))
        self.assertNotIn(" at  ", content["subject"])
        self.assertNotIn(" at .", content["body"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTheDefenceMarket(unittest.TestCase):
    """This began as a CLEARED sweep, built on the understanding that Harry
    held DV. He does not - it lapsed after discharge - so those searches were
    hunting work he cannot be shortlisted for, and any application arising
    from one would have rested on a claim that is not true.

    What survives is the part that was never about the credential: his trade
    IS military communications and electronics, and defence employers hire
    uncleared people and sponsor the vetting themselves."""

    def test_the_sweep_describes_the_work_and_never_a_clearance(self):
        blob = " ".join(jm.DEFENCE_KEYWORDS).lower()
        for term in ("defence communications", "secure communications",
                     "radio systems", "electronic warfare"):
            with self.subTest(term=term):
                self.assertIn(term, blob)
        for banned in ("dv cleared", "sc cleared", "security cleared",
                       "developed vetting"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, blob)

    def test_it_searches_the_whole_country_not_a_radius_round_aberdeen(self):
        """Defence work clusters around sites - Faslane, Rosyth, Portsmouth,
        Corsham - not around Aberdeen, and he can take a posting anywhere that
        comes with somewhere to live."""
        defence = [s for s in jm.adzuna_searches() if s[2] in jm.DEFENCE_KEYWORDS]
        self.assertTrue(defence)
        for where, radius, _ in defence:
            self.assertGreaterEqual(radius, 100)

    def test_the_profile_forbids_claiming_a_clearance(self):
        """It used to instruct the scorer that DV was his rarest asset. He
        does not hold one, so the profile now has to say so in terms the
        composer cannot read as an invitation."""
        profile = jm.CANDIDATE_PROFILE.lower()
        self.assertIn("holds none", profile)
        self.assertIn("never state", profile)
        self.assertIsNone(jm.claims_clearance(
            jm.CANDIDATE_PROFILE.split("SECURITY CLEARANCE")[0]))


class TestTextingAboutAnInterview(unittest.TestCase):
    """Email is right for a daily digest and wrong for an invitation. An
    employer asking 'can you speak tomorrow' has put a clock on it."""

    def creds(self):
        return [mock.patch.object(jm, "SMS_API_KEY", "k"),
                mock.patch.object(jm, "SMS_FROM", "+441234"),
                mock.patch.object(jm, "SMS_TO", "+445678")]

    def test_without_a_gateway_it_does_nothing_rather_than_crashing(self):
        with mock.patch.object(jm, "SMS_API_KEY", ""):
            self.assertFalse(jm.text_harry("hello"))

    def test_an_interview_invite_is_texted(self):
        job = {"company": "Oceaneering", "title": "Workshop Technician",
               "contact_email": "jane@oceaneering.com"}
        with mock.patch.object(jm, "send_email"), \
             mock.patch.object(jm, "text_harry") as sms:
            jm.alert_harry(job, {"text": "Can you come in Thursday?"},
                           "interview_invite")
        sms.assert_called_once()
        self.assertIn("Oceaneering", sms.call_args[0][0])

    def test_a_rejection_is_not_texted(self):
        """Texting about rejections and auto-acknowledgements would train him
        to ignore the phone, which costs the one thing this protects."""
        for category in ("rejection", "auto_acknowledgement", "other"):
            with self.subTest(category=category):
                with mock.patch.object(jm, "send_email"), \
                     mock.patch.object(jm, "text_harry") as sms:
                    jm.alert_harry({"company": "X", "title": "Y"},
                                   {"text": "no thanks"}, category)
                sms.assert_not_called()

    def test_a_gateway_failure_never_stops_the_email(self):
        with mock.patch.object(jm, "send_email") as email, \
             mock.patch.object(jm, "text_harry", side_effect=None,
                               return_value=False):
            jm.alert_harry({"company": "X", "title": "Y"},
                           {"text": "hi"}, "interview_invite")
        email.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestExtraWordsMeanADifferentCompany(unittest.TestCase):
    """'Grace May', a recruiter, was matched to 'Grace and May Home' and an IT
    Support application went to a home furnishings shop.

    Subset alone was never enough. Every word of the wanted name appearing in
    the match is necessary but not sufficient - what the match ADDS is what
    tells you whether it is the same firm. 'Baker Hughes Company' adds
    'company' and is Baker Hughes; 'Grace and May Home' adds 'home' and is
    somebody else entirely."""

    def clearbit(self, name, domain):
        response = mock.Mock()
        response.raise_for_status = lambda: None
        response.json = lambda: [{"name": name, "domain": domain}]
        return mock.patch.object(jm.requests, "get", return_value=response)

    def test_an_extra_business_word_is_refused(self):
        with self.clearbit("Grace and May Home", "graceandmayhome.co.uk"):
            self.assertIsNone(jm.find_domain("Grace May"))

    def test_an_extra_corporate_word_is_accepted(self):
        for wanted, hit, domain in (
                ("Baker Hughes", "Baker Hughes Company", "bakerhughes.com"),
                ("Motive Offshore", "Motive Offshore Group", "motive-offshore.com"),
                ("Ashtead Technology", "Ashtead Technology Ltd",
                 "ashtead-technology.com")):
            with self.subTest(wanted=wanted):
                with self.clearbit(hit, domain):
                    self.assertEqual(jm.find_domain(wanted), domain)

    def test_the_older_misdirections_are_still_refused(self):
        with self.clearbit("Woodforest National Bank", "woodforest.com"):
            self.assertIsNone(jm.find_domain("Wood"))
        with self.clearbit("Sanctuary Clothing", "sanctuaryclothing.com"):
            self.assertIsNone(jm.find_domain("Sanctuary"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestNoLetterEverClaimsAClearance(unittest.TestCase):
    """Harry held a clearance during his Royal Navy service and it lapsed
    after discharge, which is the ordinary course of events. Saying he holds
    one is a false statement to an employer that would be found out at
    vetting.

    It was in the candidate profile, in five template skeletons, in the plain
    letter, in the follow-up, in the applied-note, in the charity letters and
    in the answers the portal agent types into forms - and it went out on real
    applications before he told me. So this is checked on finished text at the
    one place every outgoing message passes through, rather than trusted to a
    prompt, because a prompt is a request and this needs to be a guarantee."""

    def test_the_claim_is_caught_however_it_is_written(self):
        for text in ("I am DV cleared and available.",
                     "Holds DV security clearance.",
                     "SC cleared engineer.",
                     "I hold DV clearance.",
                     "security cleared technician",
                     "Developed Vetting completed.",
                     "DV-cleared, ex-Royal Navy."):
            with self.subTest(text=text):
                self.assertIsNotNone(jm.claims_clearance(text))

    def test_the_true_things_he_can_say_are_not_blocked(self):
        for text in ("Two years Royal Navy Communications and Information Specialist.",
                     "I was vetted during service and am eligible to go through it again.",
                     "Three years at Sonardyne to IPC-A-610 Class 3."):
            with self.subTest(text=text):
                self.assertIsNone(jm.claims_clearance(text))

    def test_sending_is_refused_outright(self):
        """Not logged and sent anyway. An application that never arrives costs
        one opportunity; one that arrives claiming a clearance he does not
        hold costs his credibility with that employer and everyone they talk
        to."""
        with self.assertRaises(ValueError) as caught:
            jm.send_email("someone@acme.com", "Technician",
                          "Hi,\n\nI am DV cleared.\n\nHarry")
        self.assertIn("clearance", str(caught.exception))

    def test_an_honest_letter_still_sends(self):
        with mock.patch.object(jm.smtplib, "SMTP_SSL"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "cv_path", return_value=None):
            jm.send_email("someone@acme.com", "Technician",
                          "Hi,\n\nTwo years Royal Navy comms.\n\nHarry")

    def test_every_letter_the_system_can_write_is_clean(self):
        """A sweep rather than a spot check - the claim was in six different
        places and I found the last of them by grepping, not by reasoning."""
        job = {"title": "Workshop Technician", "company": "Oceaneering",
               "location": "Aberdeen", "description": "", "source": "adzuna",
               "contact_name": "Jane"}
        letters = [jm.plain_email(job)["body"], jm.CANDIDATE_PROFILE]
        for tpl in jm.TEMPLATES.values():
            letters.append(str(tpl.get("skeleton", "")))
            examples = tpl.get("subject_examples", "")
            letters.append(" | ".join(examples) if isinstance(examples, (list, tuple))
                           else str(examples))
        for text in letters:
            with self.subTest(text=text[:60]):
                self.assertIsNone(jm.claims_clearance(
                    text.split("SECURITY CLEARANCE")[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSayingHeHasAnOffer(unittest.TestCase):
    """An offer in hand is the strongest thing a candidate can say to a live
    conversation, and the easiest thing in the world to overstate.

    So it comes from facts on disk with an expiry rather than from a model, and
    every rule is enforced in code rather than requested in a prompt."""

    OFFER = {"offer": {"received_at": "2026-08-17", "decide_by": "2026-08-24"}}
    BOTH = {"offer": {"decide_by": "2026-08-24"},
            "interview_booked": {"on": "2026-08-21"}}

    def test_it_says_an_offer_exists(self):
        said = jm.timeline_sentence(self.OFFER, today_str="2026-08-18")
        self.assertIn("offer", said.lower())

    def test_it_never_names_the_employer(self):
        """In a market the size of Aberdeen's that gets straight back to the
        recruiter who placed him."""
        situation = {"offer": {"decide_by": "2026-08-24",
                               "employer": "Hydro Group"}}
        said = jm.timeline_sentence(situation, today_str="2026-08-18")
        self.assertNotIn("hydro", said.lower())

    def test_it_never_says_what_the_offer_is_worth(self):
        situation = {"offer": {"decide_by": "2026-08-24", "salary": "30420",
                               "hourly": "15.00"}}
        said = jm.timeline_sentence(situation, today_str="2026-08-18")
        for leak in ("30420", "30,420", "15.00", "£"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, said)

    def test_it_never_invents_a_competing_bid(self):
        said = jm.timeline_sentence(self.BOTH, today_str="2026-08-18")
        for invented in ("better offer", "higher", "more than", "beat",
                         "competing", "another offer"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, said.lower())

    def test_it_expires_on_its_own(self):
        """'I have an offer' stops being true the moment he accepts or declines
        one, and a stale claim is a lie he did not choose to tell."""
        self.assertEqual(jm.timeline_sentence(self.OFFER, today_str="2026-08-25"), "")
        self.assertEqual(jm.timeline_sentence(self.BOTH, today_str="2026-09-01"), "")

    def test_nothing_is_said_when_there_is_nothing_to_say(self):
        self.assertEqual(jm.timeline_sentence({}, today_str="2026-08-18"), "")
        self.assertEqual(jm.timeline_sentence({"offer": None}, today_str="2026-08-18"), "")

    def test_an_interview_alone_is_stated_more_softly(self):
        said = jm.timeline_sentence({"interview_booked": {"on": "2026-08-21"}},
                                    today_str="2026-08-18")
        self.assertIn("interview", said.lower())
        self.assertNotIn("offer", said.lower())

    def test_it_is_never_added_to_a_first_approach(self):
        """Leading with leverage to somebody who has not met him reads as
        arrogance. Only a follow-up carries it."""
        job = {"title": "Workshop Technician", "company": "Oceaneering",
               "location": "Aberdeen", "description": "", "source": "adzuna"}
        self.assertNotIn("offer", jm.plain_email(job)["body"].lower())

    def test_a_followup_carries_it(self):
        job = {"external_id": "x", "company": "Vickerstock", "status": "sent",
               "title": "Maintenance Electrician", "contact_name": "Leah",
               "contact_email": "l.irwin@vickerstock.com",
               "sent_at": (datetime.now(timezone.utc)
                           - timedelta(days=5)).isoformat(),
               "sent_subject": "x", "message_id": "<a@b>"}
        state = {"jobs": {"x": job}, "send_counts": {}, "companies_contacted": {}}
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@g.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "has_reply_from", return_value=False), \
             mock.patch.object(jm, "timeline_sentence",
                               return_value="I have had an offer this week."), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send, \
             mock.patch.object(jm, "save"):
            jm.run_followups(state)
        self.assertIn("offer", send.call_args[0][2].lower())

    def test_the_shipped_file_is_valid_and_carries_an_expiry(self):
        situation = jm.load_situation()
        offer = situation.get("offer")
        if offer:
            self.assertTrue(offer.get("decide_by"),
                            "an offer with no decide_by can never expire")
            self.assertNotIn("employer", offer,
                             "the offering employer must not be recorded")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTheDoNotContactList(unittest.TestCase):
    """Allstaff Recruitment phoned Harry on 20 August to say he was asking the
    same questions again. Three messages had gone to their jobs@ inbox: the
    application, a nudge four days later, and the last of the sequence at 08:35
    that morning.

    The machine had no way to be told to stop. Every register it kept answered
    'have we written to this company yet', and the answer being 'yes' is what
    schedules the next one - so the harder somebody tried to make it stop, the
    more certain the follow-up became. This is that missing register, and it is
    enforced in send_email() rather than in the stages, because a stage added
    next month cannot be relied on to remember."""

    BLOCK = [{"name": "Allstaff Recruitment",
              "domain": "allstaffrecruitment.co.uk",
              "emails": ["jobs@allstaffrecruitment.co.uk"],
              "reason": "asked us to stop"}]

    def test_the_exact_address_is_blocked(self):
        self.assertTrue(jm.do_not_contact(
            email="jobs@allstaffrecruitment.co.uk", entries=self.BLOCK))

    def test_any_other_address_at_that_company_is_blocked(self):
        """The reason a company asked us to stop does not expire when the
        discovery stage finds a different inbox there next week."""
        self.assertTrue(jm.do_not_contact(
            email="sarah.mcleod@allstaffrecruitment.co.uk", entries=self.BLOCK))

    def test_a_subdomain_is_blocked(self):
        self.assertTrue(jm.do_not_contact(
            email="reply@mail.allstaffrecruitment.co.uk", entries=self.BLOCK))

    def test_the_company_name_is_blocked_before_an_address_is_known(self):
        """Blocking has to work at the front of the pipeline too, on a listing
        whose address has not been discovered yet."""
        self.assertTrue(jm.do_not_contact(
            company="Allstaff Recruitment Ltd", entries=self.BLOCK))

    def test_the_spelling_they_did_not_give_us_is_blocked(self):
        """They said it on the phone. Nobody spelled it."""
        self.assertTrue(jm.do_not_contact(
            company="All Staff Recruitment", entries=self.BLOCK))

    def test_an_unrelated_company_is_not_blocked(self):
        for name in ("Staffline", "All Star Staffing", "Orion Group"):
            self.assertFalse(jm.do_not_contact(company=name, entries=self.BLOCK), name)

    def test_an_unrelated_address_is_not_blocked(self):
        self.assertFalse(jm.do_not_contact(
            email="jobs@allstaffrecruitment.com.example.org", entries=self.BLOCK))

    def test_send_email_refuses_whatever_asked_it_to_send(self):
        """The guarantee. Not 'the send stage checks' - nothing gets out."""
        with mock.patch.object(jm, "load_do_not_contact", return_value=self.BLOCK), \
             mock.patch.object(jm.smtplib, "SMTP_SSL") as smtp:
            with self.assertRaises(ValueError) as caught:
                jm.send_email("jobs@allstaffrecruitment.co.uk",
                              "Field Service Engineer", "Hi, following up.")
            self.assertIn("asked not to be contacted", str(caught.exception))
            smtp.assert_not_called()

    def test_the_reason_they_gave_is_in_the_refusal(self):
        with mock.patch.object(jm, "load_do_not_contact", return_value=self.BLOCK):
            with self.assertRaises(ValueError) as caught:
                jm.send_email("jobs@allstaffrecruitment.co.uk", "x", "y")
            self.assertIn("asked us to stop", str(caught.exception))

    def test_everyone_else_still_gets_their_email(self):
        with mock.patch.object(jm, "load_do_not_contact", return_value=self.BLOCK), \
             mock.patch.object(jm.smtplib, "SMTP_SSL") as smtp, \
             mock.patch.object(jm, "cv_path", return_value=None):
            jm.send_email("careers@oriongroup.com", "Technician", "Hi there.")
            self.assertTrue(smtp.called)

    def test_the_followup_stage_leaves_a_blocked_company_alone(self):
        """The specific thing that made the phone ring: a job sitting at 'sent'
        with its second nudge due."""
        state = {"jobs": {"a": {
            "status": "sent", "company": "Allstaff Recruitment",
            "sent_to": "jobs@allstaffrecruitment.co.uk",
            "contact_email": "jobs@allstaffrecruitment.co.uk",
            "title": "Field Service Engineer",
            "sent_at": "2026-08-01T09:00:00+00:00"}}}
        with mock.patch.object(jm, "load_do_not_contact", return_value=self.BLOCK), \
             mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "x@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_followups(state)
            send.assert_not_called()
        self.assertIsNone(state["jobs"]["a"].get("followup_sent_at"))

    def test_a_blocked_company_is_dropped_from_the_send_queue(self):
        state = {"jobs": {"a": {
            "status": "ready", "company": "Allstaff Recruitment",
            "contact_email": "jobs@allstaffrecruitment.co.uk",
            "company_domain": "allstaffrecruitment.co.uk",
            "title": "Field Service Engineer", "score": 75}},
            "companies_contacted": {}, "send_counts": {}}
        with mock.patch.object(jm, "load_do_not_contact", return_value=self.BLOCK), \
             mock.patch.object(jm, "cv_path", return_value="cv.pdf"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "x@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_sends(state)
            send.assert_not_called()
        self.assertEqual(state["jobs"]["a"]["status"], "do_not_contact")

    def test_the_real_list_on_disk_blocks_allstaff(self):
        """The file, not a fixture - so a typo in it fails a test rather than
        being discovered by a second phone call."""
        self.assertTrue(jm.do_not_contact(email="jobs@allstaffrecruitment.co.uk"))

    def test_a_short_company_name_is_not_swallowed(self):
        """Matching on substrings read a company called 'A' as Allstaff,
        because 'a' is inside 'allstaff'. The first unrelated employer with a
        short name would have been dropped from the queue and nobody would
        have known why."""
        for name in ("A", "B", "C", "PSN", "Bam Nuttall"):
            self.assertFalse(jm.do_not_contact(company=name, entries=self.BLOCK), name)

    def test_a_more_specific_name_at_the_same_agency_is_blocked(self):
        self.assertTrue(jm.do_not_contact(
            company="Allstaff Recruitment Aberdeen", entries=self.BLOCK))


class TestOneInboxOnlyGetsSoMuch(unittest.TestCase):
    """Nothing counted how many messages one address had received.

    The two rules that decide sending both reason about a single vacancy. An
    agency may be approached about four roles, and each approach carries an
    application plus two nudges - so twelve messages to one consultant broke no
    rule, and one consultant at Connect Appointments got exactly twelve. The
    inbox at Canmore got eleven. Allstaff phoned after three."""

    def state(self, **counts):
        jobs = {}
        for i, (addr, n) in enumerate(counts.items()):
            addr = addr.replace("_at_", "@").replace("_dot_", ".")
            for k in range(n):
                jobs[f"{i}-{k}"] = {
                    "status": "sent", "sent_to": addr, "contact_email": addr,
                    "company": "Agency", "title": "Technician",
                    "sent_at": "2026-08-01T09:00:00+00:00",
                    "followup_sent_at": "2026-08-05T09:00:00+00:00",
                    "followup2_sent_at": "2026-08-10T09:00:00+00:00"}
        return {"jobs": jobs, "companies_contacted": {}, "send_counts": {}}

    def test_it_counts_every_message_not_every_vacancy(self):
        s = self.state(gary_at_a_dot_com=2)          # 2 vacancies x 3 messages
        self.assertEqual(jm.messages_to(s, "gary@a.com"), 6)

    def test_a_quiet_inbox_is_not_capped(self):
        s = {"jobs": {"a": {"status": "sent", "sent_to": "gary@a.com",
                            "sent_at": "2026-08-01T09:00:00+00:00"}}}
        self.assertFalse(jm.inbox_full(s, "gary@a.com"))

    def test_the_third_vacancy_at_a_worked_inbox_is_not_sent(self):
        s = self.state(gary_at_a_dot_com=2)
        s["jobs"]["new"] = {"status": "ready", "company": "Agency",
                            "contact_email": "gary@a.com",
                            "company_domain": "a.com",
                            "title": "Maintenance Electrician", "score": 80}
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "cv_path", return_value="cv.pdf"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "x@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_sends(s)
            send.assert_not_called()
        self.assertIn("messages", s["jobs"]["new"]["skip_reason"])

    def test_no_further_nudges_to_a_worked_inbox(self):
        s = self.state(gary_at_a_dot_com=2)
        due = next(iter(s["jobs"].values()))
        due.pop("followup2_sent_at")     # a second nudge is now due on this one
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "x@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_followups(s)
            send.assert_not_called()

    def test_a_different_address_at_the_same_agency_is_unaffected(self):
        """The cap is about one person's patience, not the company's."""
        s = self.state(gary_at_a_dot_com=2)
        self.assertFalse(jm.inbox_full(s, "sarah@a.com"))

    def test_the_cap_counts_only_messages_that_were_really_sent(self):
        s = {"jobs": {"a": {"status": "skipped", "contact_email": "gary@a.com"},
                      "b": {"status": "no_email", "contact_email": "gary@a.com"},
                      "c": {"status": "ready", "contact_email": "gary@a.com"}}}
        self.assertEqual(jm.messages_to(s, "gary@a.com"), 0)


class TestOnlyGoingUp(unittest.TestCase):
    """Harry started as a Technician in Aberdeen on 24 August 2026 - GBP 30,000
    a year on GBP 15 an hour - so "is this a job in his trade" stopped being
    the question.

    Nothing in this repo compared a listing's pay to anything at all. Salary
    was handed to the scorer and never mentioned in its rules, and the guide's
    own top line read "85+ strong direct match in or near Aberdeen" - so a
    GBP 24,000 job round the corner outranked a GBP 45,000 one with a travel
    budget. Left alone the machine would have spent its quota finding him
    sideways moves out of a job he is happy in."""

    def test_the_leonardo_rate_is_read_as_an_hourly_rate(self):
        """The case that makes the units matter. GBP 30.81 an hour is the
        contract he is interviewing for; read as a salary it is thirty-one
        pounds a year, and the best-paid thing in the queue is thrown out as
        the worst."""
        self.assertEqual(jm.stated_pay({"salary_min": 30.81}), (30.81, "hour"))
        self.assertTrue(jm.pays_enough({"salary_min": 30.81}))

    def test_what_he_already_earns_does_not_pass(self):
        self.assertFalse(jm.pays_enough({"salary_min": 15, "salary_max": 15}))
        self.assertFalse(jm.pays_enough({"salary_min": 28000, "salary_max": 30000}))

    def test_a_poor_day_rate_is_not_mistaken_for_a_fine_hourly_one(self):
        """The hour/day band overlaps and the readings are not equally likely.
        A day rate of GBP 120 is common and poor. An hourly rate of GBP 120
        would be a quarter of a million a year and does not exist in this
        trade, so guessing 'hour' up there passes every bad day rate going."""
        self.assertEqual(jm.stated_pay({"salary_min": 120})[1], "day")
        self.assertFalse(jm.pays_enough({"salary_min": 120}))
        self.assertTrue(jm.pays_enough({"salary_min": 350}))

    def test_the_top_of_a_range_is_what_counts(self):
        """Only reject when even the best case is too little."""
        self.assertTrue(jm.pays_enough({"salary_min": 30000, "salary_max": 42000}))

    def test_silence_passes(self):
        """Most adverts print no figure and the whole contract market quotes on
        application. Treating unstated as too little would delete the
        best-paid half of the market to save a few Gemini calls."""
        for job in ({}, {"salary_min": None, "salary_max": None},
                    {"salary_min": 0}, {"salary_min": "on application"}):
            self.assertTrue(jm.pays_enough(job), job)

    def test_an_underpaid_listing_never_reaches_the_scorer(self):
        state = {"jobs": {
            "cheap": {"status": "new", "title": "Electronics Technician",
                      "description": "fault-finding", "salary_max": 26000},
            "good": {"status": "new", "title": "Test Technician",
                     "description": "test", "salary_min": 30.81}}}
        with mock.patch.object(jm, "score_batch", return_value={}) as scored:
            jm.score_jobs(state)
            sent = [j for call in scored.call_args_list for j in call.args[0]]
        self.assertEqual([j["title"] for j in sent], ["Test Technician"])
        self.assertEqual(state["jobs"]["cheap"]["status"], "skipped")
        self.assertIn("already earns", state["jobs"]["cheap"]["skip_reason"])

    def test_aberdeen_is_no_longer_worth_points(self):
        guide = jm.CANDIDATE_PROFILE.lower()
        self.assertIn("not a preference to score up", guide)

    def test_principal_technician_titles_are_back_in_range(self):
        """Leonardo put thirteen listings into this queue and not one was
        applied for, with the scorer's reason on one reading "above the
        candidate's target level" - while Harry was that week interviewing for
        a Principal Test Technician at Leonardo on GBP 40.36 an hour."""
        self.assertNotIn("principal engineer", jm.TITLE_EXCLUSIONS)
        for still_out in ("chartered", "head of", "director"):
            self.assertIn(still_out, jm.TITLE_EXCLUSIONS)


class TestAskingAboutTravel(unittest.TestCase):
    """Being paid to work abroad is Harry's condition for leaving a job he is
    happy in. There is no reliable way to read that off an advert - the
    listings that involve heavy travel mostly never say so - so the machine
    stops guessing and asks, in the one place a letter already has a question."""

    def test_travel_is_spotted_in_a_listing(self):
        for text in ("regular travel to client sites overseas",
                     "field service role covering Europe",
                     "3 weeks on / 3 weeks off rotation",
                     "fly-in fly-out to the platform",
                     "you will need a valid passport"):
            self.assertTrue(jm.mentions_travel({"description": text}), text)

    def test_a_bench_job_is_not_mistaken_for_a_travelling_one(self):
        self.assertFalse(jm.mentions_travel(
            {"title": "Workshop Technician",
             "description": "Bench assembly and test in our Aberdeen facility."}))

    def test_an_ordinary_letter_asks_the_travel_question(self):
        job = {"title": "Service Technician", "company": "Acme",
               "description": "Servicing pumps in the workshop."}
        with mock.patch.object(jm, "veteran_friendly", return_value=False):
            body = jm.plain_email(job)["body"]
        self.assertIn("travel", body.lower())
        self.assertEqual(body.count("?"), 1)

    def test_it_does_not_ask_what_the_advert_already_answered(self):
        """Asking whether a role involves travel, of an advert that opens by
        saying it does, reads as though he never read it."""
        job = {"title": "Field Service Engineer", "company": "Acme",
               "description": "Regular overseas travel to client sites."}
        with mock.patch.object(jm, "veteran_friendly", return_value=False):
            body = jm.plain_email(job)["body"]
        self.assertIn("How much travel", body)
        self.assertEqual(body.count("?"), 1)

    def test_the_covenant_question_still_wins(self):
        """A guaranteed interview scheme converts better than anything else
        the machine has, and the style rules allow exactly one question."""
        job = {"title": "Technician", "company": "Babcock", "description": "x"}
        with mock.patch.object(jm, "veteran_friendly", return_value=True):
            body = jm.plain_email(job)["body"]
        self.assertIn("guaranteed interview scheme", body)
        self.assertEqual(body.count("?"), 1)

    def test_the_composer_is_told_which_question_to_ask(self):
        job = {"title": "Field Service Engineer", "company": "Acme",
               "location": "Aberdeen", "description": "Servicing pumps."}
        seen = {}

        def capture(prompt, **kw):
            seen["prompt"] = prompt
            return None
        with mock.patch.object(jm, "gemini_json", capture), \
             mock.patch.object(jm, "veteran_friendly", return_value=False):
            jm.build_email(job)
        self.assertIn("must ask whether this role involves travel",
                      seen["prompt"])
        self.assertNotIn("guaranteed interview scheme", seen["prompt"])

    def test_a_covenant_prompt_asks_for_travel_without_a_second_question(self):
        job = {"title": "Technician", "company": "Babcock",
               "location": "Rosyth", "description": "x"}
        seen = {}

        def capture(prompt, **kw):
            seen["prompt"] = prompt
            return None
        with mock.patch.object(jm, "gemini_json", capture), \
             mock.patch.object(jm, "veteran_friendly", return_value=True):
            jm.build_email(job)
        self.assertIn("guaranteed interview scheme", seen["prompt"])
        self.assertIn("a statement, not a question", seen["prompt"])

    def test_travelling_roles_go_out_before_the_cap_bites(self):
        state = {"jobs": {
            "bench": {"status": "ready", "company": "A", "title": "Technician",
                      "contact_email": "a@a.com", "company_domain": "a.com",
                      "score": 90, "email_tier": 3,
                      "description": "Bench work in our facility."},
            "travel": {"status": "ready", "company": "B", "title": "Technician",
                       "contact_email": "b@b.com", "company_domain": "b.com",
                       "score": 71, "email_tier": 1,
                       "description": "Overseas travel to client sites."}},
            "companies_contacted": {}, "send_counts": {}}
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "cv_path", return_value="cv.pdf"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "x@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(jm, "veteran_friendly", return_value=False), \
             mock.patch.object(jm, "build_email",
                               return_value={"subject": "s", "body": "b",
                                             "family": "plain"}), \
             mock.patch.object(jm, "send_email", return_value="<id>") as send:
            jm.run_sends(state)
        self.assertEqual([c.args[0] for c in send.call_args_list][0], "b@b.com")


class TestHisOwnEmployer(unittest.TestCase):
    """The one email that must never be sent. He started at Hydro Group on
    24 August 2026, and the machine holds his CV and writes to every engineering
    firm in Aberdeen."""

    def test_hydro_group_is_blocked(self):
        for spelling in ("Hydro Group", "HYDRO GROUP LIMITED", "Hydro Group Ltd",
                         "Hydro Group Aberdeen", "Hydro Group (Aberdeen) Ltd"):
            self.assertTrue(jm.do_not_contact(company=spelling), spelling)

    def test_send_email_refuses_them(self):
        block = [{"name": "Hydro Group", "match": "exact",
                  "domain": "hydrogroup.com", "reason": "his employer"}]
        with mock.patch.object(jm, "load_do_not_contact", return_value=block), \
             mock.patch.object(jm.smtplib, "SMTP_SSL") as smtp:
            with self.assertRaises(ValueError):
                jm.send_email("jobs@hydrogroup.com", "Technician", "Hi.")
            smtp.assert_not_called()

    def test_an_unrelated_hydro_company_is_not_blocked(self):
        """company_key() strips the word 'group', so 'Hydro Group' normalises
        to 'hydro' - and without exact matching the block would swallow every
        firm in the phone book with a river in its name."""
        for other in ("Hydro Cleansing", "Hydro Systems", "Hydro Industries",
                      "Hydro International", "Hydro Services", "Hydro Groupings",
                      "Northern Hydro Group"):
            self.assertFalse(jm.do_not_contact(company=other), other)

    def test_the_profile_never_names_the_employer(self):
        """CANDIDATE_PROFILE goes into the letter-writing prompt, so a name in
        it can end up in a letter to another firm in the same city."""
        self.assertNotIn("hydro", jm.CANDIDATE_PROFILE.lower())

    def test_the_profile_still_records_that_he_is_in_work(self):
        self.assertIn("in work", jm.CANDIDATE_PROFILE.lower())


class TestWhatHeSaysAboutHimselfNow(unittest.TestCase):
    def test_the_spent_offer_no_longer_speaks(self):
        """He accepted the offer this file described, which is exactly what
        decide_by is for."""
        self.assertIsNone(jm.load_situation().get("offer"))

    def test_being_in_work_is_the_sentence_now(self):
        sentence = jm.timeline_sentence(
            {"employed": {"since": "2026-08-24"}}, today_str="2027-01-01")
        self.assertIn("in work", sentence)
        self.assertNotIn("offer", sentence.lower())

    def test_it_never_names_where_he_works(self):
        sentence = jm.timeline_sentence(
            {"employed": {"since": "2026-08-24", "note": "Hydro Group"}})
        self.assertNotIn("hydro", sentence.lower())

    def test_a_live_offer_still_outranks_it(self):
        sentence = jm.timeline_sentence(
            {"offer": {"decide_by": "2099-01-01"}, "employed": {"since": "x"}})
        self.assertIn("offer", sentence)

    def test_nothing_is_claimed_when_there_is_nothing_true_to_say(self):
        self.assertEqual(jm.timeline_sentence({}), "")

    def test_he_is_no_longer_advertised_as_available_immediately(self):
        job = {"title": "Technician", "company": "Acme", "description": "x"}
        with mock.patch.object(jm, "veteran_friendly", return_value=False):
            body = jm.plain_email(job)["body"]
        self.assertNotIn("available immediately", body.lower())
        self.assertIn("in work", body.lower())


class TestNotLosingTheStateFile(unittest.TestCase):
    """data/state.json is the only record of who has been written to and when.
    Losing it does not cost a run, it costs the history that stops the machine
    writing to somebody twice - and it has gone missing three times already, by
    three different routes, noticed days later each time.

    During this change a --dry-run turned 8,038 listings into 2. It did not
    reproduce, which is the whole argument for a guard: the next cause will be
    a different one, and the file is worth more than the diagnosis."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "state.json")
        self.patch = mock.patch.object(jm, "STATE_PATH", self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        jm.save({"jobs": {str(i): {"status": "sent"} for i in range(1000)}})

    def on_disk(self):
        with open(self.path) as f:
            return len(json.load(f)["jobs"])

    def test_a_collapse_is_refused_and_the_file_survives(self):
        self.assertFalse(jm.save({"jobs": {"a": {}}}))
        self.assertEqual(self.on_disk(), 1000)

    def test_what_the_run_was_holding_is_kept_for_inspection(self):
        jm.save({"jobs": {"a": {}}})
        with open(self.path + ".rejected") as f:
            self.assertEqual(len(json.load(f)["jobs"]), 1)

    def test_ordinary_growth_is_written(self):
        self.assertTrue(jm.save({"jobs": {str(i): {} for i in range(1200)}}))
        self.assertEqual(self.on_disk(), 1200)

    def test_a_prune_sized_loss_is_still_written(self):
        """The guard has to let real work through. Pruning dead listings, or a
        rescore changing statuses, never halves the file."""
        self.assertTrue(jm.save({"jobs": {str(i): {} for i in range(900)}}))
        self.assertEqual(self.on_disk(), 900)

    def test_a_deliberate_reset_is_still_possible(self):
        self.assertTrue(jm.save({"jobs": {"a": {}}}, allow_shrink=True))
        self.assertEqual(self.on_disk(), 1)

    def test_a_small_file_is_not_guarded(self):
        """Early runs and fresh checkouts legitimately go from nothing to a
        handful and back."""
        jm.save({"jobs": {str(i): {} for i in range(20)}}, allow_shrink=True)
        self.assertTrue(jm.save({"jobs": {"a": {}}}))


class TestWhatHeActuallyDoesAtHydro(unittest.TestCase):
    """The CV entry for his current job was a placeholder - role, employer and
    dates only - until Harry described the work: testing and repairing subsea
    cables and connectors, and moulding assemblies in epoxy resin and
    polyurethane. The profile that writes letters needed the same update,
    without ever naming who he works for."""

    def test_the_profile_names_the_real_work(self):
        profile = jm.CANDIDATE_PROFILE.lower()
        self.assertIn("cable", profile)
        self.assertIn("connector", profile)
        self.assertIn("epoxy", profile)
        self.assertIn("polyurethane", profile)

    def test_it_still_never_names_the_employer(self):
        self.assertNotIn("hydro", jm.CANDIDATE_PROFILE.lower())


class TestFindPhones(unittest.TestCase):
    """No phone number is ever guessed or pattern-generated - the same rule
    the email side already lives by. This is the regex that decides what
    counts as a real one."""

    def test_a_call_invitation_is_found(self):
        found = jm.find_phones("Call Ben on 01224 372 000 to discuss.")
        self.assertEqual([f["number"] for f in found], ["01224372000"])
        self.assertTrue(found[0]["has_keyword"])

    def test_an_international_format_is_found(self):
        found = jm.find_phones("Contact us on +44 1224 372000 for more info.")
        self.assertEqual([f["number"] for f in found], ["01224372000"])

    def test_a_mobile_is_found(self):
        found = jm.find_phones("Mobile: 07398 530978 is the best way.")
        self.assertEqual([f["number"] for f in found], ["07398530978"])

    def test_the_real_adzuna_redaction_yields_nothing(self):
        """87% of everything this pipeline sees is Adzuna, and Adzuna redacts
        phone numbers in listing text as this literal string."""
        self.assertEqual(jm.find_phones(
            "Salary range: (phone number removed) to discuss the role."), [])

    def test_a_vat_number_is_not_a_phone_number(self):
        self.assertEqual(jm.find_phones(
            "VAT No: 123456789 Company Reg No: 09876543210"), [])

    def test_a_reference_number_embedded_in_text_is_not_a_phone_number(self):
        """The regex found a phone-shaped run of digits INSIDE a longer
        reference number before the digit-boundary check was added -
        'Ref: 20260829001' read as '0260829001', a plausible-looking UK
        number that was never really there."""
        self.assertEqual(jm.find_phones("Ref: 20260829001 for this application."), [])
        self.assertEqual(jm.find_phones("Order number 5502260829001 was processed."), [])

    def test_a_number_with_no_keyword_nearby_is_still_found(self):
        """A bare number in a footer with no verb nearby is still real, just
        lower priority - not invalid."""
        found = jm.find_phones("Our office number is 01414204321, ask for Jean.")
        self.assertEqual([f["number"] for f in found], ["01414204321"])
        self.assertFalse(found[0]["has_keyword"])

    def test_duplicates_on_one_page_are_not_repeated(self):
        found = jm.find_phones("Call 01224 372000. Ring 01224 372000 anytime.")
        self.assertEqual(len(found), 1)


class TestBestPhone(unittest.TestCase):
    def test_nothing_found_is_none(self):
        self.assertIsNone(jm.best_phone([]))

    def test_the_keyword_adjacent_number_wins(self):
        """A team page can list several people's direct lines - the one
        somebody actually invited a call on is the one worth using."""
        candidates = [{"number": "01414204321", "has_keyword": False},
                     {"number": "01224372000", "has_keyword": True}]
        self.assertEqual(jm.best_phone(candidates), "01224372000")

    def test_the_first_found_wins_when_nothing_has_a_keyword(self):
        candidates = [{"number": "01414204321", "has_keyword": False},
                     {"number": "01224372000", "has_keyword": False}]
        self.assertEqual(jm.best_phone(candidates), "01414204321")


class TestScrapeSitePhones(unittest.TestCase):
    def test_it_returns_emails_and_phones(self):
        # scrape_site tries every path in SCRAPE_PATHS - only the home page
        # (an empty path) answers here, the rest look like a dead site, same
        # as a real one where most of those paths 404.
        hit = mock.Mock(status_code=200,
                       text="Contact jane@acme.com or call 01224 372000.")
        miss = mock.Mock(status_code=404, text="")

        def fake_get(url, **kw):
            return hit if url in ("https://acme.com", "http://acme.com") else miss
        with mock.patch.object(jm.requests, "get", side_effect=fake_get), \
             mock.patch.object(jm.time, "sleep"):
            emails, phones = jm.scrape_site("acme.com")
        self.assertIn("jane@acme.com", emails)
        self.assertEqual([p["number"] for p in phones], ["01224372000"])

    def test_a_non_200_response_yields_nothing(self):
        with mock.patch.object(jm.requests, "get",
                               return_value=mock.Mock(status_code=404, text="x")):
            emails, phones = jm.scrape_site("acme.com")
        self.assertEqual((emails, phones), ([], []))


class TestDiscoverPhoneExtraction(unittest.TestCase):
    def setUp(self):
        self.state = {"jobs": {}}

    def add(self, **over):
        job = make_job(status="scored", **over)
        self.state["jobs"][job["external_id"]] = job
        return job

    def test_a_listing_phone_is_captured(self):
        self.add(description="Call Ben on 01224 372 000 to discuss the role.",
                 contact_email=None)
        with mock.patch.object(jm, "fetch_listing_text", return_value=""), \
             mock.patch.object(jm, "find_domain", return_value=None):
            jm.discover(self.state)
        job = next(iter(self.state["jobs"].values()))
        self.assertEqual(job["contact_phone"], "01224372000")
        self.assertEqual(job["phone_method"], "listing")

    def test_a_scraped_phone_only_fills_in_when_no_listing_phone_exists(self):
        self.add(description="No phone here.", company="Acme Subsea Ltd")
        with mock.patch.object(jm, "fetch_listing_text", return_value=""), \
             mock.patch.object(jm, "find_domain", return_value="acme.com"), \
             mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site",
                               return_value=(["jane@acme.com"],
                                             [{"number": "01224372000",
                                               "has_keyword": True}])):
            jm.discover(self.state)
        job = next(iter(self.state["jobs"].values()))
        self.assertEqual(job["contact_phone"], "01224372000")
        self.assertEqual(job["phone_method"], "scraped")

    def test_a_listing_phone_beats_a_scraped_one(self):
        self.add(description="Call Ben on 01224 372 000.", company="Acme Subsea Ltd")
        with mock.patch.object(jm, "fetch_listing_text", return_value=""), \
             mock.patch.object(jm, "find_domain", return_value="acme.com"), \
             mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "scrape_site",
                               return_value=(["jane@acme.com"],
                                             [{"number": "01414204321",
                                               "has_keyword": True}])):
            jm.discover(self.state)
        job = next(iter(self.state["jobs"].values()))
        self.assertEqual(job["contact_phone"], "01224372000")
        self.assertEqual(job["phone_method"], "listing")

    def test_a_no_email_job_can_still_carry_a_captured_phone(self):
        """Cheap to keep now even though nothing acts on it yet without a
        working email - a currently-wasted lead, not a bug."""
        self.add(description="Call Ben on 01224 372 000.", company="")
        with mock.patch.object(jm, "fetch_listing_text", return_value=""):
            jm.discover(self.state)
        job = next(iter(self.state["jobs"].values()))
        self.assertEqual(job["status"], "skipped")
        self.assertEqual(job["contact_phone"], "01224372000")


class TestStrongMatchForCall(unittest.TestCase):
    def job(self, **over):
        defaults = {"score": 90, "email_tier": 3, "contact_phone": "01224372000"}
        defaults.update(over)
        return make_job(**defaults)

    def test_a_strong_match_qualifies(self):
        self.assertTrue(jm.strong_match_for_call(self.job()))

    def test_below_the_score_threshold_does_not_qualify(self):
        self.assertFalse(jm.strong_match_for_call(self.job(score=84)))

    def test_exactly_at_the_threshold_qualifies(self):
        self.assertTrue(jm.strong_match_for_call(self.job(score=85)))

    def test_a_generic_inbox_does_not_qualify(self):
        """The gate needs a genuinely named contact, not a tier-1 or tier-2
        inbox that happens to have a phone number on the same page."""
        self.assertFalse(jm.strong_match_for_call(self.job(email_tier=1)))
        self.assertFalse(jm.strong_match_for_call(self.job(email_tier=2)))

    def test_no_phone_does_not_qualify(self):
        self.assertFalse(jm.strong_match_for_call(self.job(contact_phone=None)))


class TestBuildCallScript(unittest.TestCase):
    def test_it_names_the_role_company_and_phone(self):
        job = make_job(company="Acme Subsea Ltd", title="Electronics Technician",
                       contact_phone="01224372000", contact_name="Jane")
        script = jm.build_call_script(job)
        self.assertIn("Acme Subsea Ltd", script)
        self.assertIn("Electronics Technician", script)
        self.assertIn("01224372000", script)
        self.assertIn("ask for Jane", script)

    def test_it_never_implies_the_number_is_their_direct_line(self):
        """Discovery only ever verifies that a name and a number both turned
        up for the same company, never that the one rings the other."""
        job = make_job(contact_name=None, contact_phone="01224372000")
        script = jm.build_call_script(job)
        self.assertIn("ask about the role", script)

    def test_it_stays_short_even_with_a_very_long_company_name(self):
        job = make_job(
            company="The Extraordinarily Long Winded International Subsea "
                    "Offshore Engineering and Advanced Manufacturing "
                    "Solutions Consultancy Partnership Group Worldwide Limited",
            title="Senior Principal Lead Electronics and Instrumentation "
                  "Test Technician",
            contact_phone="01224372000", contact_name="Jane")
        script = jm.build_call_script(job)
        self.assertLess(len(script), 320)

    def test_the_phone_number_is_never_the_part_that_gets_cut(self):
        job = make_job(company="X" * 400, contact_phone="01224372000",
                       contact_name="Jane")
        script = jm.build_call_script(job)
        self.assertIn("01224372000", script)
        self.assertIn("ask for Jane", script)

    def test_a_veteran_employer_gets_the_covenant_prompt(self):
        job = make_job(company="Babcock International", contact_phone="01224372000")
        with mock.patch.object(jm, "veteran_friendly", return_value=True):
            script = jm.build_call_script(job)
        self.assertIn("guaranteed interview scheme", script)


class TestRunCallScripts(unittest.TestCase):
    def setUp(self):
        # A configured gateway and a harmless save() are the default for
        # every test in this class - the handful that need no gateway or a
        # real save() override them individually.
        for patcher in (mock.patch.object(jm, "SMS_API_KEY", "k"),
                       mock.patch.object(jm, "SMS_FROM", "+441234"),
                       mock.patch.object(jm, "SMS_TO", "+445678"),
                       mock.patch.object(jm, "save")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def strong_job(self, **over):
        defaults = {"status": "sent", "score": 90, "email_tier": 3,
                   "contact_phone": "01224372000", "contact_name": "Jane",
                   "contact_email": "jane@acme.com", "company": "Acme Subsea Ltd"}
        defaults.update(over)
        return make_job(**defaults)

    def test_without_a_gateway_it_does_nothing(self):
        state = {"jobs": {"a": self.strong_job()}}
        with mock.patch.object(jm, "SMS_API_KEY", ""), \
             mock.patch.object(jm, "text_harry") as sms:
            jm.run_call_scripts(state)
        sms.assert_not_called()

    def test_test_mode_is_a_no_op(self):
        state = {"jobs": {"a": self.strong_job()}}
        with mock.patch.object(jm, "TEST_MODE", True), \
             mock.patch.object(jm, "text_harry") as sms:
            jm.run_call_scripts(state)
        sms.assert_not_called()

    def test_a_strong_match_is_texted(self):
        state = {"jobs": {"a": self.strong_job()}}
        with mock.patch.object(jm, "text_harry", return_value=True) as sms:
            jm.run_call_scripts(state)
        sms.assert_called_once()
        self.assertIn("Acme Subsea Ltd", sms.call_args[0][0])
        self.assertIsNotNone(state["jobs"]["a"].get("call_script_texted_at"))

    def test_a_weak_match_is_not_texted(self):
        state = {"jobs": {"a": self.strong_job(score=60)}}
        with mock.patch.object(jm, "text_harry") as sms:
            jm.run_call_scripts(state)
        sms.assert_not_called()

    def test_a_blocked_company_is_skipped_without_touching_status(self):
        block = [{"name": "Acme Subsea Ltd", "reason": "asked to stop"}]
        state = {"jobs": {"a": self.strong_job()}}
        with mock.patch.object(jm, "load_do_not_contact", return_value=block), \
             mock.patch.object(jm, "text_harry") as sms:
            jm.run_call_scripts(state)
        sms.assert_not_called()
        self.assertEqual(state["jobs"]["a"]["status"], "sent")
        self.assertIsNotNone(state["jobs"]["a"].get("do_not_contact_at"))

    def test_a_job_already_texted_is_never_texted_again(self):
        state = {"jobs": {"a": self.strong_job(call_script_texted_at="2026-08-01")}}
        with mock.patch.object(jm, "text_harry") as sms:
            jm.run_call_scripts(state)
        sms.assert_not_called()

    def test_the_daily_cap_stops_the_loop(self):
        state = {"jobs": {"a": self.strong_job(external_id="a", company="A Ltd"),
                          "b": self.strong_job(external_id="b", company="B Ltd"),
                          "c": self.strong_job(external_id="c", company="C Ltd")}}
        with mock.patch.object(jm, "CALL_SCRIPT_PER_DAY", 2), \
             mock.patch.object(jm, "text_harry", return_value=True) as sms:
            jm.run_call_scripts(state)
        self.assertEqual(sms.call_count, 2)

    def test_a_scraped_phone_at_the_wrong_domain_is_dropped(self):
        """The phone-equivalent of run_sends' send-time domain re-check - a
        wrong-company call is exactly as capable of embarrassing Harry as a
        misdirected email."""
        state = {"jobs": {"a": self.strong_job(
            phone_method="scraped", company="Sanctuary", company_domain="sanctuaryclothing.com")}}
        with mock.patch.object(jm, "text_harry") as sms:
            jm.run_call_scripts(state)
        sms.assert_not_called()
        self.assertIsNone(state["jobs"]["a"]["contact_phone"])

    def test_a_listing_phone_is_not_re_checked_against_the_domain(self):
        """A listing-tier phone came from the advert itself, not the
        company's website, so the domain re-check does not apply to it."""
        state = {"jobs": {"a": self.strong_job(
            phone_method="listing", company="Sanctuary", company_domain="sanctuaryclothing.com")}}
        with mock.patch.object(jm, "text_harry", return_value=True) as sms:
            jm.run_call_scripts(state)
        sms.assert_called_once()

    def test_a_script_that_would_claim_clearance_is_skipped(self):
        state = {"jobs": {"a": self.strong_job()}}
        with mock.patch.object(jm, "build_call_script",
                               return_value="I hold SC clearance."), \
             mock.patch.object(jm, "text_harry") as sms:
            jm.run_call_scripts(state)
        sms.assert_not_called()

    def test_a_gateway_failure_leaves_the_job_for_a_retry(self):
        state = {"jobs": {"a": self.strong_job()}}
        with mock.patch.object(jm, "text_harry", return_value=False):
            jm.run_call_scripts(state)
        self.assertIsNone(state["jobs"]["a"].get("call_script_texted_at"))

    def test_only_sent_jobs_are_considered(self):
        state = {"jobs": {"a": self.strong_job(status="ready")}}
        with mock.patch.object(jm, "text_harry") as sms:
            jm.run_call_scripts(state)
        sms.assert_not_called()
