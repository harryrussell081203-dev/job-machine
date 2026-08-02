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

    def test_compose_failure_is_recorded_not_sent(self):
        job = self.add()
        with mock.patch.object(jm, "TEST_MODE", False), \
             mock.patch.object(jm, "build_email", return_value=None), \
             mock.patch.object(jm, "send_email") as send:
            jm.run_sends(self.state)
        send.assert_not_called()
        self.assertEqual(job["status"], "compose_failed")

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
