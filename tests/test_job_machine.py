"""
Offline tests for the pure logic in job_machine.py.

No network, no Gmail, no Gemini: everything that talks to the outside world is
stubbed. Run with:  python -m unittest discover -s tests -v
"""
import os
import sys
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
                               return_value=["info@acme.com", "careers@acme.com"]), \
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
             mock.patch.object(jm, "scrape_site", return_value=[]), \
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
        self.assertIn("DV cleared", body)

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

    def test_portal_activity_is_reported(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        state = self.state(
            make_job(external_id="p1", status="portal_submitted",
                     company="Greenhouse Co", portal_attempted_at=recent,
                     portal_reason="", apply_url="https://boards.greenhouse.io/x"),
            make_job(external_id="p2", status="portal_review", company="Flagged Ltd",
                     portal_attempted_at=recent, portal_reason="1 question(s) need Harry",
                     portal_flags=["convictions: only Harry can answer it"]),
            make_job(external_id="p3", status="portal_manual", company="Workday Co",
                     portal_attempted_at=recent,
                     portal_reason="workday portal - needs an account"))
        _, text, html = jm.summary_bodies(
            jm.collect_summary(state, jm.summary_window(state)))
        self.assertIn("APPLICATION PORTALS", text)
        self.assertIn("Submitted in full", text)
        self.assertIn("Greenhouse Co", text)
        self.assertIn("needs you: convictions", text)
        self.assertIn("Workday Co", text)
        self.assertIn("Application portals", html)

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


class TestAppliedNotes(unittest.TestCase):
    def test_note_goes_to_the_named_person_after_a_portal_submit(self):
        job = make_job(status="portal_submitted", email_tier=3)
        state = {"jobs": {"j": job}, "companies_contacted": {}, "send_counts": {}}
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "save"), mock.patch.object(jm.time, "sleep"), \
             mock.patch.object(jm, "cv_for", return_value="/tmp/cv.pdf"), \
             mock.patch.object(jm, "send_email", return_value="<n>") as send:
            jm.run_applied_notes(state)
        to, subject, body = send.call_args.args[:3]
        self.assertEqual(to, "jane.smith@acme.com")
        self.assertIn("Just applied", subject)
        self.assertIn("through the online portal", body)
        self.assertEqual(jm.slop_check(body), [])
        self.assertIn("applied_note_sent_at", job)

    def test_no_note_to_generic_inboxes_and_never_twice(self):
        a = make_job(external_id="a", status="portal_submitted", email_tier=2)
        b = make_job(external_id="b", status="portal_submitted", email_tier=3,
                     applied_note_sent_at=jm.now())
        state = {"jobs": {"a": a, "b": b}, "companies_contacted": {},
                 "send_counts": {}}
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_applied_notes(state)
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
                                     return_value=["careers@acme.com"])]
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
        with mock.patch.object(jm, "scrape_site", return_value=[]), \
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


class TestPortalFallback(unittest.TestCase):
    """A form we cannot drive is not a job we cannot apply for.

    'portal_manual' was terminal in every direction: the email route only
    reads 'scored', and the portal agent skips anything already parked so it
    does not retry it. Sixty-eight listings had collected there - Oceaneering,
    Survitec, Trescal, Dron & Dickson, scoring 88 to 90 in exactly Harry's
    trade - found, judged, matched, and then silently dropped, most of them
    because the portal wanted an account or ran a bot check.
    """
    def state(self, **over):
        job = make_job(external_id="p1", status="portal_manual", score=90,
                       portal_reason="unknown portal - needs an account")
        job.update(over)
        return {"jobs": {job["external_id"]: job}}

    def test_a_blocked_portal_goes_back_on_the_email_route(self):
        state = self.state()
        jm.portal_fallback(state)
        self.assertEqual(state["jobs"]["p1"]["status"], "scored")

    def test_a_job_awaiting_review_is_included_too(self):
        state = self.state(status="portal_review")
        jm.portal_fallback(state)
        self.assertEqual(state["jobs"]["p1"]["status"], "scored")

    def test_it_happens_once_and_cannot_loop(self):
        state = self.state()
        self.assertEqual(jm.portal_fallback(state), 1)
        state["jobs"]["p1"]["status"] = "portal_manual"
        self.assertEqual(jm.portal_fallback(state), 0)
        self.assertEqual(state["jobs"]["p1"]["status"], "portal_manual")

    def test_a_stale_advert_is_not_revived(self):
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        state = self.state(posted_at=old, found_at=old)
        jm.portal_fallback(state)
        self.assertEqual(state["jobs"]["p1"]["status"], "portal_manual")

    def test_an_application_already_submitted_is_never_disturbed(self):
        for status in ("portal_submitted", "sent", "replied"):
            with self.subTest(status=status):
                state = self.state(status=status)
                jm.portal_fallback(state)
                self.assertEqual(state["jobs"]["p1"]["status"], status)

    def test_a_job_that_has_already_waited_is_not_held_again(self):
        """The off-peak hold trades a few hours for a better open rate. That is
        a good trade once, for a listing that will still be there this
        afternoon - not twice, for one that was parked days ago. Without this,
        eighty-six of the best-matched roles in the file go out at three a
        run."""
        state = self.state()
        jm.portal_fallback(state)
        self.assertTrue(jm.already_waited(state["jobs"]["p1"]))

    def test_an_ordinary_listing_still_waits_for_the_window(self):
        self.assertFalse(jm.already_waited(make_job(external_id="ordinary")))

    def test_the_score_is_kept_because_it_was_never_in_doubt(self):
        """Unlike a rescore, nothing here says the judgement was wrong - only
        that the way in was blocked. Dropping the score would send it back
        through the scorer and spend a Gemini call re-deciding a settled
        question."""
        state = self.state()
        jm.portal_fallback(state)
        self.assertEqual(state["jobs"]["p1"]["score"], 90)


class TestWhereHarryCanWork(unittest.TestCase):
    def test_the_profile_no_longer_treats_aberdeen_as_a_requirement(self):
        """He can take work anywhere that comes with the arrangements to live
        it, and the scorer was quietly costing him every rotational role."""
        profile = jm.CANDIDATE_PROFILE.lower()
        self.assertNotIn("aberdeen strongly preferred", profile)
        for expected in ("rotational", "fly-in", "accommodation", "not a requirement"):
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
        self.assertIn("DV cleared", body)
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


class TestGuessingADomainClearbitNeverHeardOf(unittest.TestCase):
    """82 of the 114 jobs the letter route could not write to failed at 'no
    domain found' - the largest single blockage in the system, on the one
    channel that has ever produced a reply. Clearbit knows the big names and
    has never heard of Ernest Gordon Recruitment of Bristol.

    Small UK firms are consistent: the domain is the trading name with the
    spaces taken out, on .co.uk or .com."""

    def test_it_builds_the_domains_a_firm_would_actually_own(self):
        got = jm.domain_candidates("Ernest Gordon Recruitment Limited")
        self.assertIn("ernestgordonrecruitment.co.uk", got)
        self.assertIn("ernestgordon.co.uk", got)

    def test_the_legal_tail_is_dropped_but_the_trade_word_is_not(self):
        """'Limited' is never in a domain. 'Recruitment' constantly is."""
        got = " ".join(jm.domain_candidates("Rise Technical Recruitment Limited"))
        self.assertNotIn("limited", got)
        self.assertIn("risetechnicalrecruitment.co.uk", got)

    def test_an_ampersand_does_not_produce_a_nonsense_stem(self):
        """'Dron & Dickson' must not offer up 'dronand.co.uk', which is
        nobody."""
        got = jm.domain_candidates("Dron & Dickson")
        self.assertIn("dronanddickson.co.uk", got)
        self.assertNotIn("dronand.co.uk", got)

    def test_it_is_bounded_so_one_company_cannot_eat_a_run(self):
        many = jm.domain_candidates("A Very Long Company Name Indeed Limited")
        self.assertLessEqual(len(many), jm.DOMAIN_GUESS_CAP)

    def test_a_domain_that_takes_mail_but_is_someone_else_is_refused(self):
        """The guard that makes guessing safe. Without it a guess is a
        coin-toss, and the failure is not an empty inbox - it is Harry's
        application landing at a stranger's firm."""
        with mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "site_confirms_company", return_value=False):
            self.assertIsNone(jm.guess_domain("Ernest Gordon Recruitment"))

    def test_a_domain_that_passes_both_gates_is_used(self):
        with mock.patch.object(jm, "has_mx", side_effect=lambda d: d.endswith(".co.uk")), \
             mock.patch.object(jm, "site_confirms_company", return_value=True):
            self.assertEqual(jm.guess_domain("Speedy Hire"), "speedyhire.co.uk")

    def test_a_domain_with_no_mail_is_never_used(self):
        with mock.patch.object(jm, "has_mx", return_value=False), \
             mock.patch.object(jm, "site_confirms_company", return_value=True):
            self.assertIsNone(jm.guess_domain("Speedy Hire"))

    def test_a_one_word_name_is_refused_outright(self):
        """'Sanctuary' matched Sanctuary Clothing in California, and an
        application about Harry's naval service was one run from a stranger's
        inbox. A single word on a front page is not identification."""
        with mock.patch.object(jm, "has_mx", return_value=True), \
             mock.patch.object(jm, "site_confirms_company", return_value=True):
            self.assertIsNone(jm.guess_domain("Sanctuary"))
            self.assertIsNone(jm.guess_domain("Cammach Ltd"))

    def test_the_site_check_needs_every_word_of_the_name(self):
        page = mock.Mock(status_code=200,
                         text="<h1>Ernest Gordon Recruitment</h1> Bristol")
        with mock.patch.object(jm.requests, "get", return_value=page):
            self.assertTrue(jm.site_confirms_company(
                "ernestgordon.co.uk", "Ernest Gordon Recruitment Ltd"))
        other = mock.Mock(status_code=200, text="<h1>Gordon's Gin</h1>")
        with mock.patch.object(jm.requests, "get", return_value=other):
            self.assertFalse(jm.site_confirms_company(
                "gordon.co.uk", "Ernest Gordon Recruitment Ltd"))

    def test_a_dead_site_is_not_a_confirmation(self):
        with mock.patch.object(jm.requests, "get",
                               side_effect=OSError("refused")):
            self.assertFalse(jm.site_confirms_company("x.co.uk", "A B"))
        with mock.patch.object(jm.requests, "get",
                               return_value=mock.Mock(status_code=404, text="")):
            self.assertFalse(jm.site_confirms_company("x.co.uk", "A B"))
