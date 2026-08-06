"""
Tests for the portal agent.

The logic tests run offline. The browser test drives a real Chromium against a
local replica of a Greenhouse/Lever style form (tests/fixtures/fake_ats.html) -
it is skipped automatically if Playwright is not installed. Nothing here ever
touches a real employer's portal.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_machine as jm  # noqa: E402
import portal_agent as pa  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "fake_ats.html")

JOB = {"external_id": "reed_1", "title": "Instrumentation Technician",
       "company": "North Sea Controls", "location": "Aberdeen",
       "description": "Calibration of pressure and flow instrumentation offshore.",
       "score": 91, "status": "scored"}


def field(**over):
    base = {"index": "0", "tag": "input", "type": "text", "name": "", "id": "",
            "placeholder": "", "aria_label": "", "label": "", "group_label": "",
            "required": False, "maxlength": None, "options": [], "value": ""}
    base.update(over)
    return base


class TestAnswerBank(unittest.TestCase):
    def test_ships_with_harrys_real_details(self):
        answers = pa.load_answers()
        self.assertEqual(answers["first_name"], "Harry")
        self.assertEqual(answers["phone"], "07398 530978")
        self.assertEqual(answers["right_to_work_uk"], "Yes")

    def test_the_details_harry_supplied_are_in_place(self):
        answers = pa.load_answers()
        self.assertEqual(answers["postcode"], "AB25 3AJ")
        self.assertEqual(answers["address_line_1"], "31 Cadenhead Road")
        self.assertEqual(answers["salary_expectation"], "35000")
        self.assertEqual(answers["driving_licence"], "No")
        self.assertEqual(answers["earliest_start_date"], "Immediately")
        self.assertIn("immediately", answers["notice_period"].lower())

    def test_unknowns_are_still_null_not_guessed(self):
        answers = pa.load_answers()
        for unknown in ("willing_to_relocate", "offshore_willing", "linkedin",
                        "reference_1"):
            self.assertIsNone(answers[unknown],
                              f"{unknown} must stay null until Harry fills it in")

    def test_environment_overrides_the_file(self):
        with mock.patch.dict(os.environ, {"ANSWER_POSTCODE": "AB10 1XX"}):
            self.assertEqual(pa.load_answers()["postcode"], "AB10 1XX")
        with mock.patch.dict(os.environ, {"ANSWER_POSTCODE": "  "}):
            self.assertEqual(pa.load_answers()["postcode"], "AB25 3AJ")

    def test_readme_keys_are_not_treated_as_answers(self):
        self.assertFalse([k for k in pa.load_answers() if k.startswith("_")])


class TestQuestionsItRefusesToAnswer(unittest.TestCase):
    def test_refuses_the_things_only_harry_can_answer(self):
        cases = {
            "Do you have any unspent criminal convictions?": "convictions",
            "Have you ever been subject to a DBS check?": "convictions",
            "National Insurance number": "identity",
            "Passport number": "identity",
            "Date of birth": "date_of_birth",
            "Bank account number": "financial",
            "What is your ethnic origin?": "protected",
            "Sexual orientation": "protected",
            "Do you consider yourself to have a disability?": "health",
            "Do you have any health conditions we should know about?": "health",
            "I certify that the above is true and complete": "certification",
        }
        for question, reason in cases.items():
            with self.subTest(question=question):
                self.assertEqual(pa.refusal_reason(field(label=question)), reason)

    def test_monitoring_questions_take_the_forms_own_prefer_not_to_say(self):
        for label in ("What is your ethnic origin?", "Gender identity",
                      "Do you consider yourself to have a disability?"):
            with self.subTest(label=label):
                f = field(index="0", label=label, type="select", required=True,
                          options=["Yes", "No", "Prefer not to say"])
                plan, flags = pa.plan_answers([f], JOB, pa.load_answers())
                self.assertEqual(flags, [])
                self.assertEqual(plan[0]["value"], "Prefer not to say")
                self.assertTrue(plan[0]["source"].startswith("declined:"))

    def test_decline_option_is_recognised_in_its_many_wordings(self):
        for wording in ("Prefer not to say", "I would rather not say",
                        "Do not wish to disclose", "Decline to answer",
                        "Not disclosed"):
            with self.subTest(wording=wording):
                self.assertEqual(
                    pa.decline_option(field(options=["Male", "Female", wording])),
                    wording)

    def test_monitoring_with_no_decline_option_is_still_flagged(self):
        f = field(index="0", label="Ethnic origin", type="select", required=True,
                  options=["White", "Black", "Asian", "Other"])
        plan, flags = pa.plan_answers([f], JOB, pa.load_answers())
        self.assertEqual(plan, [])
        self.assertTrue(any("protected" in f for f in flags))

    def test_identity_and_financial_fields_are_never_placeholder_filled(self):
        # a placeholder here would be false information submitted under his name
        for label in ("National Insurance number", "Bank account number",
                      "Sort code", "Passport number", "Date of birth"):
            with self.subTest(label=label):
                f = field(index="0", label=label, required=True)
                plan, flags = pa.plan_answers([f], JOB, pa.load_answers())
                self.assertEqual(plan, [])
                self.assertTrue(flags)

    def test_convictions_are_never_answered_even_with_a_decline_option(self):
        f = field(index="0", label="Do you have any unspent convictions?",
                  type="select", required=True,
                  options=["Yes", "No", "Prefer not to say"])
        plan, flags = pa.plan_answers([f], JOB, pa.load_answers())
        self.assertEqual(plan, [])
        self.assertTrue(any("convictions" in f for f in flags))

    def test_ordinary_questions_are_not_refused(self):
        for question in ("First name", "Email address", "Current job title",
                         "Why are you interested in this role?",
                         "Do you have the right to work in the UK?"):
            with self.subTest(question=question):
                self.assertIsNone(pa.refusal_reason(field(label=question)))

    def test_a_refused_question_never_gets_filled_even_if_the_bank_has_it(self):
        answers = dict(pa.load_answers(), date_of_birth="12/03/2003")
        fields = [field(index="0", label="Date of birth", required=True),
                  field(index="1", label="First name", required=True)]
        plan, flags = pa.plan_answers(fields, JOB, answers)
        self.assertEqual([p["field"]["label"] for p in plan], ["First name"])
        self.assertTrue(any("date_of_birth" in f for f in flags))


class TestFieldMatching(unittest.TestCase):
    def test_labels_map_to_the_right_answer(self):
        cases = {
            "First name *": "first_name", "Surname": "last_name",
            "Email address": "email", "Mobile number": "phone",
            "Town or city": "city", "Post code": "postcode",
            "Do you have the right to work in the UK?": "right_to_work_uk",
            "Do you require sponsorship?": "needs_sponsorship",
            "Do you hold a security clearance?": "security_clearance",
            "Notice period": "notice_period",
            "Expected salary": "salary_expectation",
            "Are you an armed forces veteran?": "armed_forces_veteran",
            "How did you hear about us?": "how_did_you_hear",
        }
        for label, key in cases.items():
            with self.subTest(label=label):
                self.assertEqual(pa.match_key(field(label=label)), key)

    def test_falls_back_to_the_input_name_when_there_is_no_label(self):
        self.assertEqual(pa.match_key(field(name="first_name")), "first_name")

    def test_option_matching_survives_different_wording(self):
        select = field(options=["None", "SC", "DV (Developed Vetting)"])
        self.assertEqual(pa.choose_option(select, "DV (Developed Vetting)"),
                         "DV (Developed Vetting)")
        radio = field(options=["Yes", "No"])
        self.assertEqual(pa.choose_option(radio, "Yes"), "Yes")
        self.assertEqual(pa.choose_option(field(options=["I do", "I do not"]), "Yes"),
                         "I do")
        self.assertIsNone(pa.choose_option(field(options=["Blue", "Green"]), "Yes"))


class TestValueCoercion(unittest.TestCase):
    def test_immediately_becomes_a_real_date_in_a_date_picker(self):
        from datetime import datetime, timedelta, timezone
        tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        for phrase in ("Immediately", "ASAP", "tomorrow", "None, available immediately"):
            with self.subTest(phrase=phrase):
                self.assertEqual(pa.coerce_value(field(type="date"), phrase), tomorrow)

    def test_real_dates_are_normalised(self):
        self.assertEqual(pa.coerce_value(field(type="date"), "02/09/2026"),
                         "2026-09-02")

    def test_an_unparseable_date_is_refused_rather_than_mangled(self):
        self.assertIsNone(pa.coerce_value(field(type="date"), "whenever suits"))

    def test_salary_fits_a_number_only_box(self):
        self.assertEqual(pa.coerce_value(field(type="number"), "35000"), "35000")
        self.assertEqual(pa.coerce_value(field(type="number"), "£35,000 a year"),
                         "35000")
        self.assertIsNone(pa.coerce_value(field(type="number"), "negotiable"))

    def test_text_fields_are_left_alone(self):
        self.assertEqual(pa.coerce_value(field(type="text"), "31 Cadenhead Road"),
                         "31 Cadenhead Road")

    def test_start_date_and_salary_now_fill_themselves(self):
        answers = pa.load_answers()
        fields = [field(index="0", label="Earliest start date", type="date",
                        required=True),
                  field(index="1", label="Expected salary", type="number",
                        required=True),
                  field(index="2", label="Post code", required=True)]
        plan, flags = pa.plan_answers(fields, JOB, answers)
        self.assertEqual(flags, [])
        self.assertEqual([p["value"] for p in plan][1:], ["35000", "AB25 3AJ"])

    def test_a_value_that_cannot_fit_is_flagged_not_forced(self):
        answers = dict(pa.load_answers(), salary_expectation="negotiable")
        _, flags = pa.plan_answers(
            [field(index="0", label="Expected salary", type="number", required=True)],
            JOB, answers)
        self.assertTrue(any("could not fit" in f for f in flags))


class TestPlanning(unittest.TestCase):
    def setUp(self):
        self.answers = pa.load_answers()
        cv = mock.patch.object(jm, "cv_for", return_value="/tmp/cv.pdf")
        cv.start()
        self.addCleanup(cv.stop)

    def test_known_fields_are_filled_from_the_bank(self):
        fields = [field(index="0", label="First name"),
                  field(index="1", label="Email address"),
                  field(index="2", label="Town or city")]
        plan, flags = pa.plan_answers(fields, JOB, self.answers)
        self.assertEqual([p["value"] for p in plan],
                         ["Harry", "harryrussell081203@gmail.com", "Aberdeen"])
        self.assertEqual(flags, [])

    def test_a_required_unknown_is_flagged_never_invented(self):
        fields = [field(index="0", label="Are you willing to relocate?",
                        required=True)]
        plan, flags = pa.plan_answers(fields, JOB, self.answers)
        self.assertEqual(plan, [])
        self.assertTrue(any("willing_to_relocate" in f for f in flags))

    def test_an_optional_unknown_is_simply_left_blank(self):
        fields = [field(index="0", label="LinkedIn profile", required=False)]
        plan, flags = pa.plan_answers(fields, JOB, self.answers)
        self.assertEqual((plan, flags), ([], []))

    def test_cv_is_uploaded_and_privacy_is_ticked(self):
        fields = [field(index="0", type="file", label="Upload your CV", required=True),
                  field(index="1", type="checkbox",
                        label="I agree to the privacy policy", required=True)]
        plan, _ = pa.plan_answers(fields, JOB, self.answers)
        self.assertEqual([p["kind"] for p in plan], ["file", "check"])
        self.assertEqual(plan[0]["value"], "/tmp/cv.pdf")

    def test_missing_cv_is_flagged(self):
        with mock.patch.object(jm, "cv_for", return_value=None):
            _, flags = pa.plan_answers(
                [field(index="0", type="file", label="Upload your CV", required=True)],
                JOB, self.answers)
        self.assertIn("no CV PDF to upload", flags)

    def test_free_text_uses_a_grounded_answer(self):
        fields = [field(index="0", type="textarea", required=True,
                        label="Why are you interested in this role?")]
        grounded = {"answer": "I test and maintain subsea acoustic positioning "
                              "systems at Sonardyne, so calibrating pressure and "
                              "flow instrumentation is the same work.",
                    "fact_used": "Workshop Technician at Sonardyne 2023-2026"}
        with mock.patch.object(jm, "gemini_json", return_value=grounded):
            plan, flags = pa.plan_answers(fields, JOB, self.answers)
        self.assertEqual(flags, [])
        self.assertIn("Sonardyne", plan[0]["value"])
        self.assertEqual(plan[0]["source"], "grounded")

    def test_ungrounded_free_text_is_flagged_not_faked(self):
        fields = [field(index="0", type="textarea", required=True,
                        label="Describe your experience with PLC programming")]
        for reply in ({"answer": None, "fact_used": None},
                      {"answer": "I have extensive PLC experience", "fact_used": None},
                      None):
            with self.subTest(reply=reply):
                with mock.patch.object(jm, "gemini_json", return_value=reply):
                    plan, flags = pa.plan_answers(fields, JOB, self.answers)
                self.assertEqual(plan, [])
                self.assertTrue(any("could not answer" in f for f in flags))

    def test_grounded_answer_still_obeys_the_banned_phrase_list(self):
        slop = {"answer": "I am passionate about this role and a proven team player.",
                "fact_used": "Sonardyne"}
        with mock.patch.object(jm, "gemini_json", return_value=slop):
            plan, flags = pa.plan_answers(
                [field(index="0", type="textarea", required=True,
                       label="Why do you want this job?")], JOB, self.answers)
        self.assertEqual(plan, [])
        self.assertTrue(flags)

    def test_select_with_no_matching_option_is_flagged(self):
        fields = [field(index="0", label="Do you have the right to work in the UK?",
                        type="select", options=["Sponsored", "Student visa"])]
        _, flags = pa.plan_answers(fields, JOB, self.answers)
        self.assertTrue(any("no option matching" in f for f in flags))


class TestPortalTargeting(unittest.TestCase):
    def test_supported_portals_are_recognised(self):
        for url, ats in {
            "https://boards.greenhouse.io/acme/jobs/123": "greenhouse",
            "https://jobs.lever.co/acme/abc-123": "lever",
            "https://apply.workable.com/acme/j/ABC/": "workable",
            "https://jobs.ashbyhq.com/acme/xyz": "ashby",
            "https://acme.recruitee.com/o/technician": "recruitee",
        }.items():
            with self.subTest(url=url):
                self.assertEqual(pa.classify_url(url), (ats, True))

    def test_login_and_captcha_portals_are_left_for_a_human(self):
        for url, ats in {
            "https://acme.wd3.myworkdayjobs.com/en-US/careers/job/123": "workday",
            "https://acme.taleo.net/careersection/jobdetail.ftl": "taleo",
            "https://www.linkedin.com/jobs/view/123": "linkedin",
            "https://www.reed.co.uk/jobs/technician/123": "reed",
        }.items():
            with self.subTest(url=url):
                self.assertEqual(pa.classify_url(url), (ats, False))

    def test_unknown_hosts_are_not_assumed_safe(self):
        self.assertEqual(pa.classify_url("https://careers.acme.co.uk/apply"),
                         (None, False))

    def test_the_advert_text_itself_can_name_the_portal(self):
        """The cheapest route of all: no network, no slug, no board. Employers
        and agencies routinely paste the link into the description."""
        job = {"description": "Great role in Aberdeen. Apply here: "
                              "https://boards.greenhouse.io/acme/jobs/99 - "
                              "no agencies."}
        self.assertEqual(pa.ats_url_in_listing(job),
                         ("https://boards.greenhouse.io/acme/jobs/99",
                          "greenhouse"))

    def test_an_advert_with_no_portal_link_gives_nothing(self):
        self.assertEqual(pa.ats_url_in_listing({"description": "Call us on 01224"}),
                         (None, None))

    def test_a_listing_that_is_already_a_portal_needs_no_lookup(self):
        job = {"url": "https://jobs.lever.co/acme/abc-123"}
        with mock.patch.object(pa, "ats_url_in_listing") as listing:
            url, ats = pa.best_apply_url(mock.MagicMock(), job)
        self.assertEqual((url, ats), ("https://jobs.lever.co/acme/abc-123", "lever"))
        listing.assert_not_called()

    def test_a_known_employer_board_beats_following_the_job_board(self):
        """resolve_in_browser is the last resort, not the first: five diagnose
        runs proved Adzuna's interstitial renders blank to a headless browser."""
        job = {"url": "https://www.adzuna.co.uk/jobs/details/1",
               "company": "Acme", "description": ""}
        import ats_finder
        with mock.patch.object(ats_finder, "find_application_url",
                               return_value=("https://jobs.lever.co/acme/1", "lever")), \
             mock.patch.object(pa, "resolve_in_browser") as browser:
            url, ats = pa.best_apply_url(mock.MagicMock(), job)
        self.assertEqual(url, "https://jobs.lever.co/acme/1")
        browser.assert_not_called()

    def test_a_real_board_without_this_advert_stops_rather_than_guessing(self):
        job = {"url": "https://www.adzuna.co.uk/jobs/details/1",
               "company": "Acme", "description": ""}
        import ats_finder
        with mock.patch.object(ats_finder, "find_application_url",
                               return_value=(None, "greenhouse")), \
             mock.patch.object(pa, "resolve_in_browser") as browser:
            url, ats = pa.best_apply_url(mock.MagicMock(), job)
        self.assertIsNone(url)
        self.assertEqual(ats, "greenhouse")
        browser.assert_not_called()

    def test_an_unknown_employer_still_falls_back_to_the_job_board(self):
        job = {"url": "https://www.adzuna.co.uk/jobs/details/1",
               "company": "Tiny Local Firm", "description": ""}
        import ats_finder
        with mock.patch.object(ats_finder, "find_application_url",
                               return_value=(None, None)), \
             mock.patch.object(pa, "resolve_in_browser",
                               return_value=("https://careers.tiny.co.uk/1", None)) as b:
            url, ats = pa.best_apply_url(mock.MagicMock(), job)
        self.assertEqual(url, "https://careers.tiny.co.uk/1")
        b.assert_called_once()

    def test_apply_link_is_dug_out_of_the_employers_page(self):
        html = ('<a href="https://boards.greenhouse.io/acme/jobs/99">Apply</a>')
        response = mock.Mock(url="https://careers.acme.co.uk/roles/1", text=html)
        with mock.patch.object(pa.requests, "get", return_value=response):
            url, ats = pa.resolve_apply_url({"url": "https://careers.acme.co.uk/roles/1"})
        self.assertEqual((url, ats), ("https://boards.greenhouse.io/acme/jobs/99",
                                      "greenhouse"))

    def test_an_unsupported_portal_does_not_eat_the_run_budget(self):
        """A --limit 1 run must apply to one real form, not stop at the first
        council portal it cannot use."""
        jobs = {
            "manual": dict(JOB, external_id="manual", score=99,
                           found_at=jm.now(), url="https://acme.taleo.net/x",
                           company="Council"),
            "good": dict(JOB, external_id="good", score=90, found_at=jm.now(),
                         url="https://boards.greenhouse.io/acme/jobs/1",
                         company="Acme"),
        }
        state = {"jobs": jobs, "companies_contacted": {}, "send_counts": {}}
        applied = []

        def fake_apply(page, job, answers, submit, state=None):
            applied.append(job["external_id"])
            job["status"] = "portal_submitted"
            return True

        fake_pw = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(),
                                             "playwright.sync_api": fake_pw}), \
             mock.patch.object(pa, "apply_to_job", side_effect=fake_apply), \
             mock.patch.object(pa, "best_apply_url",
                               side_effect=lambda page, j: (
                                   j["url"], pa.classify_url(j["url"])[0])), \
             mock.patch.object(jm, "save"), mock.patch.object(pa.time, "sleep"), \
             mock.patch.object(jm, "mark_contacted"):
            pa.run(state, submit=True, limit=1)

        self.assertEqual(applied, ["good"])
        self.assertEqual(jobs["manual"]["status"], "portal_manual")

    def test_board_hosts_are_recognised_as_not_being_the_employer(self):
        for url in ("https://www.adzuna.co.uk/jobs/details/123",
                    "https://www.reed.co.uk/jobs/technician/456",
                    "https://uk.indeed.com/viewjob?jk=abc"):
            with self.subTest(url=url):
                self.assertTrue(pa.on_board(url))
        self.assertFalse(pa.on_board("https://boards.greenhouse.io/acme/jobs/1"))
        self.assertFalse(pa.on_board("https://careers.hydrasun.com/apply"))

    def chained_page(self, start, chain):
        page = mock.MagicMock()
        page.url = start
        page.goto.side_effect = lambda url, **kw: setattr(page, "url", url)
        page.evaluate.side_effect = lambda js: chain.get(
            page.url, {"offsite": [], "onsite": []})
        return page

    def test_browser_follows_a_board_through_to_the_employer(self):
        """Plain HTTP left every candidate stuck on www.adzuna.co.uk, because
        the board redirects with JavaScript."""
        page = self.chained_page(
            "https://www.adzuna.co.uk/jobs/details/123",
            {"https://www.adzuna.co.uk/jobs/details/123": {
                "offsite": [{"href": "https://boards.greenhouse.io/acme/jobs/9",
                             "text": "Apply"}], "onsite": []}})
        url, ats = pa.resolve_in_browser(
            page, {"url": "https://www.adzuna.co.uk/jobs/details/123"})
        self.assertEqual(url, "https://boards.greenhouse.io/acme/jobs/9")
        self.assertEqual(ats, "greenhouse")

    def test_follows_the_boards_own_interstitial_before_leaving(self):
        """Adzuna's apply button points at its OWN /jobs/land/ad/... which only
        then redirects onward. Rejecting same-host links is what left the second
        diagnose run still sitting on www.adzuna.co.uk."""
        page = self.chained_page(
            "https://www.adzuna.co.uk/jobs/details/1",
            {"https://www.adzuna.co.uk/jobs/details/1": {
                "offsite": [],
                "onsite": [{"href": "https://www.adzuna.co.uk/jobs/land/ad/1",
                            "text": "Apply"}]},
             "https://www.adzuna.co.uk/jobs/land/ad/1": {
                "offsite": [{"href": "https://apply.workable.com/acme/j/AB/",
                             "text": "Apply now"}], "onsite": []}})
        url, ats = pa.resolve_in_browser(
            page, {"url": "https://www.adzuna.co.uk/jobs/details/1"})
        self.assertEqual(url, "https://apply.workable.com/acme/j/AB/")
        self.assertEqual(ats, "workable")

    def test_a_direct_employer_link_is_left_alone(self):
        page = self.chained_page("https://boards.greenhouse.io/acme/jobs/9", {})
        url, ats = pa.resolve_in_browser(
            page, {"url": "https://boards.greenhouse.io/acme/jobs/9"})
        page.evaluate.assert_not_called()      # no need to hunt for a link
        self.assertEqual(ats, "greenhouse")

    def test_a_board_with_no_apply_link_stays_put_rather_than_guessing(self):
        page = self.chained_page("https://www.adzuna.co.uk/jobs/details/123", {})
        url, ats = pa.resolve_in_browser(
            page, {"url": "https://www.adzuna.co.uk/jobs/details/123"})
        self.assertEqual(url, "https://www.adzuna.co.uk/jobs/details/123")

    def test_a_loop_of_interstitials_gives_up_rather_than_spinning(self):
        page = self.chained_page(
            "https://www.adzuna.co.uk/a",
            {"https://www.adzuna.co.uk/a": {
                "offsite": [], "onsite": [{"href": "https://www.adzuna.co.uk/b",
                                           "text": "Apply"}]},
             "https://www.adzuna.co.uk/b": {
                "offsite": [], "onsite": [{"href": "https://www.adzuna.co.uk/a",
                                           "text": "Apply"}]}})
        url, ats = pa.resolve_in_browser(page, {"url": "https://www.adzuna.co.uk/a"})
        self.assertTrue(pa.on_board(url))      # stayed on the board, but stopped

    def test_diagnose_never_fills_or_submits_anything(self):
        """Reconnaissance must stay read-only - it runs against live employer
        pages, so it may look and screenshot, nothing more."""
        jobs = {"a": dict(JOB, external_id="a", score=90, found_at=jm.now(),
                          url="https://boards.greenhouse.io/acme/jobs/1")}
        state = {"jobs": jobs, "companies_contacted": {}, "send_counts": {}}
        fake_page = mock.MagicMock()
        fake_pw = mock.MagicMock()
        fake_pw.sync_playwright.return_value.__enter__.return_value \
            .chromium.launch.return_value.new_context.return_value \
            .new_page.return_value = fake_page

        with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(),
                                             "playwright.sync_api": fake_pw}), \
             mock.patch.object(pa, "collect_fields", return_value=[]), \
             mock.patch.object(pa, "has_captcha", return_value=False), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "best_apply_url",
                               return_value=("https://boards.greenhouse.io/acme/jobs/1",
                                             "greenhouse")), \
             mock.patch.object(pa, "apply_plan") as fill, \
             mock.patch.object(pa, "click_submit") as submit, \
             mock.patch.object(pa, "apply_to_job") as apply_job:
            pa.diagnose(state, limit=5)

        fill.assert_not_called()
        submit.assert_not_called()
        apply_job.assert_not_called()
        fake_page.fill.assert_not_called()
        fake_page.check.assert_not_called()
        fake_page.set_input_files.assert_not_called()
        # and the job's status is untouched by looking at it
        self.assertEqual(jobs["a"]["status"], "scored")

    def test_queue_is_scored_recent_and_untried(self):
        state = {"jobs": {
            "good": dict(JOB, external_id="good", score=90, found_at=jm.now()),
            "low": dict(JOB, external_id="low", score=40, found_at=jm.now()),
            "old": dict(JOB, external_id="old", score=95,
                        found_at="2020-01-01T00:00:00+00:00"),
            "done": dict(JOB, external_id="done", score=99, found_at=jm.now(),
                         status="portal_submitted"),
        }, "companies_contacted": {}, "send_counts": {}}
        self.assertEqual([j["external_id"] for j in pa.portal_candidates(state)],
                         ["good"])


@unittest.skipUnless(os.environ.get("PORTAL_BROWSER_TESTS") == "1",
                     "set PORTAL_BROWSER_TESTS=1 to drive a real browser")
class TestAgainstALocalPortal(unittest.TestCase):
    """Drives a real Chromium against a local copy of a typical ATS form."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        launch = {}
        if os.path.exists("/opt/pw-browsers/chromium"):
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        cls.browser = cls._pw.chromium.launch(**launch)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.goto("file://" + FIXTURE)
        self.addCleanup(self.page.close)

    def test_reads_every_question_on_the_form(self):
        fields = pa.collect_fields(self.page)
        labels = " | ".join(f.get("label") or f.get("aria_label") or f.get("name")
                            for f in fields)
        for expected in ("First name", "Surname", "Email", "Town or city",
                         "security clearance", "right to work", "Why are you",
                         "CV", "convictions", "privacy"):
            self.assertIn(expected.lower(), labels.lower())
        # the two radio buttons are one question, not two
        self.assertEqual(sum(1 for f in fields if f["type"] == "radio"), 1)

    def test_fills_the_form_and_refuses_the_two_it_should(self):
        fields = pa.collect_fields(self.page)
        grounded = {"answer": "I maintain subsea acoustic positioning systems at "
                              "Sonardyne, which is the same calibration work.",
                    "fact_used": "Sonardyne 2023-2026"}
        with mock.patch.object(jm, "gemini_json", return_value=grounded), \
             mock.patch.object(jm, "cv_for", return_value=FIXTURE):
            plan, flags = pa.plan_answers(fields, JOB, pa.load_answers())
            filled, failed = pa.apply_plan(self.page, plan)

        self.assertEqual(failed, [])
        values = self.page.evaluate(
            "() => Object.fromEntries(new FormData(document.getElementById("
            "'application')).entries())")
        self.assertEqual(values["first_name"], "Harry")
        self.assertEqual(values["last_name"], "Russell")
        self.assertEqual(values["email"], "harryrussell081203@gmail.com")
        self.assertEqual(values["town"], "Aberdeen")
        self.assertEqual(values["clearance"], "DV (Developed Vetting)")
        self.assertEqual(values["rtw"], "y")
        self.assertIn("Sonardyne", values["why"])
        self.assertEqual(values["privacy"], "on")
        # a File in FormData serialises to {}, so check the input itself
        self.assertEqual(self.page.evaluate(
            "() => document.getElementById('cv').files[0]?.name"),
            os.path.basename(FIXTURE))

        # monitoring question answered with the form's own opt-out
        self.assertEqual(values["ethnicity"], "Prefer not to say")
        # convictions and NI number are required, so the form goes to Harry
        self.assertEqual(values["convictions"], "")
        self.assertEqual(values["nino"], "")
        self.assertTrue(any("convictions" in f for f in flags))
        self.assertTrue(any("identity" in f for f in flags))

    def test_a_flagged_form_is_never_submitted(self):
        job = dict(JOB)
        with mock.patch.object(jm, "gemini_json",
                               return_value={"answer": "Same work as my day job.",
                                             "fact_used": "Sonardyne"}), \
             mock.patch.object(jm, "cv_for", return_value=FIXTURE), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "click_submit") as submit:
            self.page.goto("file://" + FIXTURE)
            job["apply_url"] = "file://" + FIXTURE
            pa.apply_to_job(self.page, job, pa.load_answers(), submit=True)
        submit.assert_not_called()
        self.assertEqual(job["status"], "portal_review")
        self.assertTrue(any("convictions" in f for f in job["portal_flags"]))

    def test_captcha_banks_the_filled_form_rather_than_being_solved(self):
        """A bot check is never fought. On a real form the work is kept: every
        answer is banked so Harry only solves the CAPTCHA and submits."""
        job = dict(JOB, apply_url="file://" + FIXTURE)
        with mock.patch.object(jm, "gemini_json",
                               return_value={"answer": "Same work as my day job.",
                                             "fact_used": "Sonardyne"}), \
             mock.patch.object(jm, "cv_for", return_value=FIXTURE), \
             mock.patch.object(pa, "captcha_kind", return_value="challenge"), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "click_submit") as submit:
            pa.apply_to_job(self.page, job, pa.load_answers(), submit=True)
        submit.assert_not_called()
        self.assertEqual(job["status"], "portal_awaiting_captcha")
        # the real form was read and answered before the bot check stopped us
        self.assertGreater(len(job["captcha_answers"]), 3)
        values = {a["label"]: a["value"] for a in job["captcha_answers"]}
        self.assertIn("Harry", values.values())


if __name__ == "__main__":
    unittest.main(verbosity=2)


    def test_a_role_off_an_employers_own_board_fills_in_end_to_end(self):
        """Board-sourced roles are built by harvest_boards, not by the job-board
        harvest: no posted_at, an apply_url already set, a source of
        'board:<ats>'. This drives that exact record through the real filler
        against a real browser, because a shape mismatch here would only show
        up as a failed live application."""
        import ats_finder
        state = {"jobs": {}}
        board = {"ats": "workable", "slug": "dof", "whole_name": True,
                 "url": "https://apply.workable.com/dof/",
                 "jobs": [{"title": "Hydraulic Engineer",
                           "location": "Aberdeen, Scotland",
                           "url": "https://apply.workable.com/j/39EE851D53/apply",
                           "description": "Offshore hydraulic systems, "
                                          "calibration and fault finding."}]}
        with mock.patch.object(ats_finder, "find_board", return_value=board), \
             mock.patch.object(jm, "load_targets", return_value=[{"company": "DOF"}]):
            pa.harvest_boards(state)
        job = next(iter(state["jobs"].values()))
        self.assertEqual(job["source"], "board:workable")
        self.assertIsNone(job["posted_at"])

        job["apply_url"] = "file://" + FIXTURE          # the fixture stands in
        with mock.patch.object(jm, "gemini_json",
                               return_value={"answer": "Same work as my day job.",
                                             "fact_used": "Sonardyne"}), \
             mock.patch.object(jm, "cv_for", return_value=FIXTURE), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "click_submit") as submit:
            pa.apply_to_job(self.page, job, pa.load_answers(), submit=False)

        submit.assert_not_called()
        values = self.page.evaluate(
            "() => Object.fromEntries(new FormData(document.getElementById("
            "'application')).entries())")
        self.assertEqual(values["first_name"], "Harry")
        self.assertEqual(values["town"], "Aberdeen")
        self.assertTrue(job.get("portal_filled"))

class TestHarvestingEmployerBoards(unittest.TestCase):
    """Vacancies straight off an employer's own board.

    The job boards only carry what an employer chose to advertise there -
    about four in-trade listings a day for Aberdeen. Their own ATS board
    carries everything they have open.
    """
    def board(self, *postings):
        return {"ats": "greenhouse", "slug": "acme", "whole_name": True,
                "url": "https://boards.greenhouse.io/acme", "jobs": list(postings)}

    def posting(self, title, where="Aberdeen, Scotland", url=None):
        return {"title": title, "location": where,
                "url": url or f"https://boards.greenhouse.io/acme/jobs/{title[:4]}",
                "description": "Calibration of pressure instrumentation offshore."}

    def state(self):
        return {"jobs": {}, "companies_contacted": {}}

    def harvest(self, board, state=None, targets=(("Acme",))):
        import ats_finder
        state = state or self.state()
        with mock.patch.object(ats_finder, "find_board", return_value=board), \
             mock.patch.object(jm, "load_targets",
                               return_value=[{"company": c} for c in targets]):
            pa.harvest_boards(state)
        return state

    def test_a_role_the_job_boards_never_showed_is_picked_up(self):
        state = self.harvest(self.board(self.posting("Instrumentation Technician")))
        self.assertEqual(len(state["jobs"]), 1)
        job = next(iter(state["jobs"].values()))
        self.assertEqual(job["company"], "Acme")
        self.assertEqual(job["apply_url"], job["url"])
        self.assertEqual(job["ats"], "greenhouse")

    def test_a_role_on_the_other_side_of_the_world_is_not(self):
        """Employer boards are global. Without this he gets offered Houston."""
        state = self.harvest(self.board(
            self.posting("Instrumentation Technician", "Houston, TX, USA")))
        self.assertEqual(state["jobs"], {})

    def test_a_role_outside_his_trade_is_not(self):
        state = self.harvest(self.board(self.posting("Head of Tax", "Aberdeen")))
        self.assertEqual(state["jobs"], {})

    def test_the_same_vacancy_is_not_added_twice_across_runs(self):
        """Python salts hash() per process, so an id built from it would
        re-add every vacancy on every run."""
        board = self.board(self.posting("Instrumentation Technician"))
        state = self.harvest(board)
        first = set(state["jobs"])
        self.harvest(board, state)
        self.assertEqual(set(state["jobs"]), first)

    def test_a_vacancy_already_known_from_a_job_board_is_not_duplicated(self):
        state = self.state()
        state["jobs"]["adz_1"] = dict(JOB, company="Acme",
                                      title="Instrumentation Technician")
        self.harvest(self.board(self.posting("Instrumentation Technician")), state)
        self.assertEqual(len(state["jobs"]), 1)

    def test_it_arrives_ready_to_apply_with_no_job_board_to_resolve(self):
        state = self.harvest(self.board(self.posting("Instrumentation Technician")))
        job = next(iter(state["jobs"].values()))
        url, ats = pa.best_apply_url(mock.MagicMock(), job)
        self.assertEqual(url, job["url"])
        self.assertEqual(ats, "greenhouse")


class TestReachableLocations(unittest.TestCase):
    def test_places_harry_could_actually_work(self):
        for place in ("Aberdeen, Scotland", "Dyce", "Glasgow, UK",
                      "Westhill, Aberdeenshire", "Montrose"):
            self.assertTrue(pa.in_reach(place), place)

    def test_places_he_could_not(self):
        for place in ("Houston, TX", "London", "Remote - Singapore", ""):
            self.assertFalse(pa.in_reach(place), place)


class TestBoardDiscoveryIsRemembered(unittest.TestCase):
    """Finding a board costs up to eighteen requests across six platforms;
    listing a known one costs a single request."""
    def setUp(self):
        self.state = {"jobs": {}}
        self.board = {"ats": "greenhouse", "slug": "acme", "jobs": [],
                      "url": "https://boards.greenhouse.io/acme"}

    def test_a_company_is_only_searched_for_once(self):
        import ats_finder
        with mock.patch.object(ats_finder, "find_board",
                               return_value=self.board) as find, \
             mock.patch.object(ats_finder, "api_board",
                               return_value=self.board) as listing:
            pa.cached_board(self.state, "Acme")
            pa.cached_board(self.state, "Acme Ltd")     # same company
        find.assert_called_once()
        listing.assert_called_once()                    # relisted, not researched

    def test_a_company_with_no_board_is_not_searched_for_again(self):
        import ats_finder
        with mock.patch.object(ats_finder, "find_board", return_value=None) as find:
            self.assertIsNone(pa.cached_board(self.state, "Tiny Firm"))
            self.assertIsNone(pa.cached_board(self.state, "Tiny Firm"))
        find.assert_called_once()

    def test_a_stale_answer_is_checked_again(self):
        import ats_finder
        self.state["ats_boards"] = {jm.company_key("Acme"): {
            "ats": None, "slug": None, "checked_at": "2020-01-01T00:00:00+00:00"}}
        with mock.patch.object(ats_finder, "find_board",
                               return_value=self.board) as find:
            pa.cached_board(self.state, "Acme")
        find.assert_called_once()

    def test_a_board_that_has_gone_away_is_forgotten(self):
        import ats_finder
        with mock.patch.object(ats_finder, "find_board", return_value=self.board):
            pa.cached_board(self.state, "Acme")
        with mock.patch.object(ats_finder, "api_board", return_value=None):
            self.assertIsNone(pa.cached_board(self.state, "Acme"))
        self.assertIsNone(self.state["ats_boards"][jm.company_key("Acme")]["ats"])


class TestApplyingOffTheKnownPlatforms(unittest.TestCase):
    """A probe across fourteen Aberdeen employers found exactly one hosted
    ATS, and it needed an account. Most of them take applications on a form of
    their own, so refusing every unrecognised host means refusing the market.
    """
    def form(self, *labels, upload=False):
        fields = [field(index=str(i), label=l, name=l.lower().replace(" ", "_"))
                  for i, l in enumerate(labels)]
        if upload:
            fields.append(field(index="9", type="file", label="Upload your CV",
                                name="cv"))
        return fields

    def test_a_real_application_form_is_recognised_without_a_brand(self):
        self.assertTrue(pa.is_application_form(
            self.form("First name", "Last name", "Email", "Phone",
                      "Why this role?", upload=True)))

    def test_a_contact_form_is_not_mistaken_for_one(self):
        self.assertFalse(pa.is_application_form(
            self.form("Your name", "Email", "Message")))

    def test_a_newsletter_signup_is_not_mistaken_for_one(self):
        self.assertFalse(pa.is_application_form(self.form("Email address")))

    def test_name_email_and_phone_together_are_enough_without_an_upload(self):
        self.assertTrue(pa.is_application_form(
            self.form("First name", "Last name", "Email", "Mobile number",
                      "Current job title")))

    def test_an_unrecognised_host_is_no_longer_refused_out_of_hand(self):
        """It used to be marked manual before anyone looked at the page."""
        jobs = {"own": dict(JOB, external_id="own", score=95, found_at=jm.now(),
                            url="https://drondickson.com/careers",
                            status="scored")}
        state = {"jobs": jobs, "portal_counts": {}, "companies_contacted": {}}
        looked = []

        fake_pw = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(),
                                             "playwright.sync_api": fake_pw}), \
             mock.patch.object(pa, "best_apply_url",
                               return_value=("https://drondickson.com/apply", None)), \
             mock.patch.object(pa, "apply_to_job",
                               side_effect=lambda p, j, a, s, st=None:
                               looked.append(j) or False), \
             mock.patch.object(jm, "save"), mock.patch.object(pa.time, "sleep"):
            pa.run(state, submit=False)

        self.assertEqual(len(looked), 1)
        self.assertEqual(jobs["own"]["apply_url"], "https://drondickson.com/apply")

    def test_a_login_portal_is_still_refused_without_looking(self):
        jobs = {"wd": dict(JOB, external_id="wd", score=95, found_at=jm.now(),
                           url="https://x.wd5.myworkdayjobs.com/j", status="scored")}
        state = {"jobs": jobs, "portal_counts": {}, "companies_contacted": {}}

        fake_pw = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(),
                                             "playwright.sync_api": fake_pw}), \
             mock.patch.object(pa, "best_apply_url",
                               return_value=("https://x.wd5.myworkdayjobs.com/j",
                                             "workday")), \
             mock.patch.object(pa, "apply_to_job") as apply_job, \
             mock.patch.object(jm, "save"), mock.patch.object(pa.time, "sleep"):
            pa.run(state, submit=False)

        apply_job.assert_not_called()
        self.assertEqual(jobs["wd"]["status"], "portal_manual")


class TestWallClockBudgets(unittest.TestCase):
    """A run that overruns the workflow's sixty minutes is killed mid-flight
    and loses everything it found."""
    def test_discovery_stops_at_its_budget_and_keeps_what_it_has(self):
        import ats_finder
        state = {"jobs": {}}
        clock = iter([0, 0, 1e9, 1e9, 1e9])   # first company fits, then time is up
        board = {"ats": "greenhouse", "slug": "acme", "whole_name": True,
                 "url": "https://boards.greenhouse.io/acme",
                 "jobs": [{"title": "Instrumentation Technician",
                           "location": "Aberdeen",
                           "url": "https://boards.greenhouse.io/acme/jobs/1",
                           "description": "offshore calibration"}]}
        with mock.patch.object(ats_finder, "find_board", return_value=board), \
             mock.patch.object(jm, "load_targets",
                               return_value=[{"company": "Acme"},
                                             {"company": "Beta"},
                                             {"company": "Gamma"}]), \
             mock.patch.object(pa.time, "monotonic", side_effect=lambda: next(clock)):
            added = pa.harvest_boards(state)
        self.assertEqual(added, 1)
        self.assertEqual(len(state["jobs"]), 1)


@unittest.skipUnless(os.environ.get("PORTAL_BROWSER_TESTS") == "1",
                     "set PORTAL_BROWSER_TESTS=1 to drive a real browser")
class TestTellingBotChecksApart(unittest.TestCase):
    """An invisible reCAPTCHA and a puzzle both put 'recaptcha' in the HTML.
    Every one of the 46 vacancies found on employers' own Workable boards
    reported a bot check under the old text scan; if they are really invisible
    checks, none of them needed a human at all."""

    def page_showing(self, html):
        page = self.browser.new_page()
        page.set_content(html)
        self.addCleanup(page.close)
        return page

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        launch = {}
        if os.path.exists("/opt/pw-browsers/chromium"):
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        cls.browser = cls._pw.chromium.launch(**launch)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def test_an_invisible_score_check_is_not_a_blocker(self):
        page = self.page_showing(
            '<script src="https://www.google.com/recaptcha/api.js?render=abc123">'
            '</script><div class="grecaptcha-badge" style="width:70px;height:60px">'
            '</div><form><input name="email"></form>')
        self.assertEqual(pa.captcha_kind(page), "scored")
        self.assertFalse(pa.has_captcha(page))

    def test_a_visible_tickbox_challenge_needs_a_human(self):
        page = self.page_showing(
            '<div class="g-recaptcha" data-sitekey="abc" '
            'style="width:304px;height:78px"></div>')
        self.assertEqual(pa.captcha_kind(page), "challenge")
        self.assertTrue(pa.has_captcha(page))

    def test_an_invisible_widget_declares_itself(self):
        page = self.page_showing(
            '<div class="g-recaptcha" data-sitekey="abc" data-size="invisible" '
            'style="width:304px;height:78px"></div>')
        self.assertNotEqual(pa.captcha_kind(page), "challenge")

    def test_an_ordinary_form_has_no_bot_check_at_all(self):
        page = self.page_showing('<form><input name="email"></form>')
        self.assertIsNone(pa.captcha_kind(page))
        self.assertFalse(pa.has_captcha(page))

    def test_bot_protection_we_do_not_recognise_is_treated_as_a_blocker(self):
        """The conservative half of the rule: unknown means human, never waved
        past. Nothing here is ever an attempt to defeat bot protection."""
        page = self.page_showing('<div>arkoselabs funcaptcha widget</div>')
        self.assertEqual(pa.captcha_kind(page), "challenge")

    def test_a_hidden_challenge_frame_does_not_count(self):
        page = self.page_showing(
            '<iframe src="https://www.google.com/recaptcha/api2/anchor?k=x" '
            'style="display:none;width:300px;height:80px"></iframe>'
            '<div class="grecaptcha-badge" style="width:70px;height:60px"></div>')
        self.assertEqual(pa.captcha_kind(page), "scored")


class TestSpottingAnApplyControl(unittest.TestCase):
    """No browser needed: the thing that must not go wrong is pressing
    something that says apply and is not an application."""

    def test_the_controls_it_should_press(self):
        for text in ("Apply now", "Apply for this job", "I'm interested",
                     "Start your application", "Apply online"):
            with self.subTest(text=text):
                self.assertTrue(any(t in text.lower() for t in pa.APPLY_TEXTS))

    def test_the_ones_it_must_not(self):
        for text in ("Apply filters", "How to apply", "Apply via our portal",
                     "Applied", "Reapply", "Apply search"):
            with self.subTest(text=text):
                self.assertTrue(pa.NOT_APPLY.search(text))

    def test_a_real_apply_button_is_not_caught_by_the_exclusions(self):
        for text in ("Apply now", "Apply for this job", "I'm interested"):
            with self.subTest(text=text):
                self.assertIsNone(pa.NOT_APPLY.search(text))


@unittest.skipUnless(os.environ.get("PORTAL_BROWSER_TESTS") == "1",
                     "set PORTAL_BROWSER_TESTS=1 to drive a real browser")
class TestReachingTheFormInARealBrowser(unittest.TestCase):
    """The fix for the biggest single failure this agent has, driven for real.

    121 applications opened, 0 submitted, and 61 abandoned with 'only 0, 1 or 2
    form fields found'. These two fixtures are what those pages actually were."""

    GATED = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fixtures", "gated_ats.html")
    EMBEDDED = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "embedded_ats.html")

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        launch = {}
        if os.path.exists("/opt/pw-browsers/chromium"):
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        cls.browser = cls._pw.chromium.launch(**launch)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def open(self, path):
        page = self.browser.new_page()
        page.goto("file://" + path)
        self.addCleanup(page.close)
        return page

    def test_the_old_behaviour_would_have_given_up(self):
        """The advert alone carries two fields - a search box and a location -
        which is exactly the '2 form fields found' in the real data."""
        page = self.open(self.GATED)
        seen = pa.visible_fields(pa.collect_fields(page))
        self.assertLess(len(seen), pa.MIN_FORM_FIELDS)

    def test_it_presses_apply_and_finds_the_form(self):
        page = self.open(self.GATED)
        surface, fields = pa.reach_the_form(page)
        self.assertIs(surface, page)
        self.assertGreaterEqual(len(pa.visible_fields(fields)),
                                pa.MIN_FORM_FIELDS)
        labels = " | ".join(f.get("label") or f.get("aria_label") or f.get("name")
                            for f in fields).lower()
        for expected in ("first name", "surname", "email", "cv"):
            self.assertIn(expected, labels)

    def test_it_does_not_press_apply_filters(self):
        """If it pressed the wrong control the form would never open, so the
        form appearing at all is the proof - and the search box must not be
        what it filled in."""
        page = self.open(self.GATED)
        _, fields = pa.reach_the_form(page)
        names = [f.get("name") for f in fields]
        self.assertIn("first_name", names)

    def test_it_finds_a_form_inside_an_iframe(self):
        page = self.open(self.EMBEDDED)
        surface, fields = pa.reach_the_form(page)
        self.assertIsNot(surface, page)
        self.assertGreaterEqual(len(fields), 8)

    def test_it_fills_the_form_through_the_iframe(self):
        page = self.open(self.EMBEDDED)
        surface, fields = pa.reach_the_form(page)
        plan, _ = pa.plan_answers(fields, JOB, pa.load_answers())
        filled, failed = pa.apply_plan(surface, plan)
        self.assertTrue(filled)
        self.assertEqual(surface.locator("#fname").input_value(), "Harry")

    def test_a_page_that_really_has_no_form_is_still_refused(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content("<h1>Sign in to continue</h1><p>Members only.</p>")
        _, fields = pa.reach_the_form(page)
        self.assertLess(len(pa.visible_fields(fields)), pa.MIN_FORM_FIELDS)


class TestReopeningWhatTheOldBugsParked(unittest.TestCase):
    """When the agent could not reach a form it released the job to the email
    route - correct, an emailed application beats none. But the email route
    then found no address for most of them, so they carry BOTH no_email and
    portal_fallback_at, and neither route will ever touch them again. 120 jobs
    sat in that dead end because of a bug that no longer exists."""

    def state(self, **job):
        base = {"status": "no_email", "apply_url": "https://apply.example/1",
                "portal_fallback_at": "2026-08-04T15:00:00+00:00",
                "portal_reason": "only 1 form fields found, the application is "
                                 "probably behind a login", "score": 85}
        base.update(job)
        return {"jobs": {"a": base}}

    def test_a_misdiagnosed_job_goes_back_in_the_queue(self):
        state = self.state()
        self.assertEqual(pa.reopen_fallbacks(state, dry_run=False), 1)
        job = state["jobs"]["a"]
        self.assertNotIn("portal_fallback_at", job)
        self.assertEqual(job["status"], "scored")
        self.assertIn(job, pa.portal_candidates(state))

    def test_a_real_login_wall_stays_parked(self):
        """Nothing about Reed wanting an account has changed."""
        state = self.state(portal_reason="reed portal - needs an account or "
                                         "runs a bot check")
        self.assertEqual(pa.reopen_fallbacks(state, dry_run=False), 0)
        self.assertIn("portal_fallback_at", state["jobs"]["a"])

    def test_a_bot_check_stays_parked(self):
        state = self.state(portal_reason="bot check before the form loaded")
        self.assertEqual(pa.reopen_fallbacks(state, dry_run=False), 0)

    def test_an_application_the_email_route_rescued_is_left_alone(self):
        """A sent application is a sent application."""
        for status in ("sent", "replied", "ready"):
            with self.subTest(status=status):
                state = self.state(status=status)
                self.assertEqual(pa.reopen_fallbacks(state, dry_run=False), 0)

    def test_a_job_with_nowhere_to_apply_is_not_reopened(self):
        state = self.state(apply_url=None)
        self.assertEqual(pa.reopen_fallbacks(state, dry_run=False), 0)

    def test_a_dry_run_counts_and_changes_nothing(self):
        state = self.state()
        self.assertEqual(pa.reopen_fallbacks(state, dry_run=True), 1)
        self.assertIn("portal_fallback_at", state["jobs"]["a"])

    def test_it_records_why_so_the_change_is_auditable(self):
        state = self.state()
        pa.reopen_fallbacks(state, dry_run=False)
        job = state["jobs"]["a"]
        self.assertTrue(job.get("portal_reopened_at"))
        self.assertIn("press Apply", job["portal_reason"])


class TestItNeverClaimsAnApplicationItDidNotSend(unittest.TestCase):
    """The worst bug in this file, and it was in the success path.

    After pressing submit, if the page carried no confirmation wording, the
    agent recorded portal_submitted anyway. The commonest reason a page comes
    back saying nothing is that it came back REJECTING the application - so
    the one case that most needed catching was being counted as a win, and
    Harry would have been told he had applied for jobs he had not."""

    def job(self):
        # A recognised ATS, so the run gets past 'is this an application form
        # at all' and reaches the submit, which is what is under test here.
        return {"external_id": "a", "title": "Tech", "company": "Acme",
                "apply_url": "https://boards.greenhouse.io/acme/jobs/1"}

    def run_submit(self, page, errors, finished=False, moved=True):
        """Drive apply_to_job to the submit, with the far side's reply faked."""
        fields = [{"index": "0", "name": "full_name", "label": "Full name",
                   "type": "text", "required": True, "options": [],
                   "option_map": {}, "visible": True, "id": "", "hint": "",
                   "placeholder": "", "aria_label": "", "group_label": "",
                   "maxlength": None, "tag": "input", "value": ""}] * 4
        # Three calls a pass: the loop's own, the one taken before pressing
        # submit, and the one taken after. Only the third differs when the
        # page really moved on.
        signatures = iter(["same", "same", "moved" if moved else "same"])
        job = self.job()
        with mock.patch.object(pa, "collect_fields", return_value=fields), \
             mock.patch.object(pa, "captcha_kind", return_value=None), \
             mock.patch.object(pa, "has_captcha", return_value=False), \
             mock.patch.object(pa, "plan_answers", return_value=([], [])), \
             mock.patch.object(pa, "apply_plan", return_value=([], [])), \
             mock.patch.object(pa, "page_instructions", return_value=""), \
             mock.patch.object(pa, "click_next", return_value=None), \
             mock.patch.object(pa, "click_submit", return_value=True), \
             mock.patch.object(pa, "looks_finished", return_value=finished), \
             mock.patch.object(pa, "validation_errors", side_effect=errors), \
             mock.patch.object(pa, "page_signature",
                               side_effect=lambda *a: next(signatures, "moved")), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "keep_the_page", return_value=None):
            result = pa.apply_to_job(page, job, {}, submit=True)
        return result, job

    def test_a_rejected_application_is_not_recorded_as_sent(self):
        errors = [[{"message": "This field is required", "field": "reference"}],
                  [{"message": "This field is required", "field": "reference"}]]
        sent, job = self.run_submit(mock.MagicMock(), errors)
        self.assertFalse(sent)
        self.assertEqual(job["status"], "portal_review")
        self.assertIn("This field is required", job["portal_reason"])
        self.assertEqual(job["portal_rejected_with"], ["This field is required"])

    def test_what_the_form_objected_to_is_kept_word_for_word(self):
        """Every one of these is a line Harry can add to data/answers.json,
        and learn.py counts them - that is the self-improvement loop."""
        errors = [[{"message": "Enter a valid UK postcode", "field": "pc"}]] * 2
        _, job = self.run_submit(mock.MagicMock(), errors)
        self.assertIn("form rejected: Enter a valid UK postcode",
                      job["portal_flags"])

    def test_the_same_form_coming_back_silently_is_not_a_submission(self):
        """No confirmation, no complaint, and the form still sitting there."""
        sent, job = self.run_submit(mock.MagicMock(), [[], []], moved=False)
        self.assertFalse(sent)
        self.assertEqual(job["status"], "portal_review")
        self.assertIn("nothing was sent", job["portal_reason"])

    def test_a_page_that_moves_on_without_objecting_is_still_counted(self):
        """Plenty of real portals confirm in wording nothing recognises. That
        is a doubt to record, not a reason to throw the application away."""
        sent, job = self.run_submit(mock.MagicMock(), [[], []], moved=True)
        self.assertTrue(sent)
        self.assertEqual(job["status"], "portal_submitted")
        self.assertIn("not recognised", job["portal_confirmation"])

    def test_a_real_confirmation_is_still_a_clean_submission(self):
        sent, job = self.run_submit(mock.MagicMock(), [[]], finished=True)
        self.assertTrue(sent)
        self.assertEqual(job["status"], "portal_submitted")
        self.assertNotIn("portal_confirmation", job)

    def test_it_goes_round_once_and_only_once(self):
        """A form that refuses the same answer twice will not take it a
        third time, and the run has other applications waiting."""
        calls = []

        def errors(*a):
            calls.append(1)
            return [{"message": "Required", "field": "x"}]

        page = mock.MagicMock()
        fields = [{"index": "0", "name": "n", "label": "L", "type": "text",
                   "required": True, "options": [], "option_map": {},
                   "visible": True, "id": "", "hint": "", "placeholder": "",
                   "aria_label": "", "group_label": "", "maxlength": None,
                   "tag": "input", "value": ""}] * 4
        job = self.job()
        with mock.patch.object(pa, "collect_fields", return_value=fields), \
             mock.patch.object(pa, "captcha_kind", return_value=None), \
             mock.patch.object(pa, "has_captcha", return_value=False), \
             mock.patch.object(pa, "plan_answers", return_value=([], [])), \
             mock.patch.object(pa, "apply_plan", return_value=([], [])), \
             mock.patch.object(pa, "page_instructions", return_value=""), \
             mock.patch.object(pa, "click_next", return_value=None), \
             mock.patch.object(pa, "click_submit", return_value=True), \
             mock.patch.object(pa, "looks_finished", return_value=False), \
             mock.patch.object(pa, "validation_errors", side_effect=errors), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "keep_the_page", return_value=None):
            pa.apply_to_job(page, job, {}, submit=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(job["status"], "portal_review")

    def test_the_second_pass_is_told_what_the_form_objected_to(self):
        """Otherwise it writes the same answer again and the retry is
        theatre."""
        seen = []
        page = mock.MagicMock()
        fields = [{"index": "0", "name": "n", "label": "L", "type": "text",
                   "required": True, "options": [], "option_map": {},
                   "visible": True, "id": "", "hint": "", "placeholder": "",
                   "aria_label": "", "group_label": "", "maxlength": None,
                   "tag": "input", "value": ""}] * 4
        with mock.patch.object(pa, "collect_fields", return_value=fields), \
             mock.patch.object(pa, "captcha_kind", return_value=None), \
             mock.patch.object(pa, "has_captcha", return_value=False), \
             mock.patch.object(pa, "plan_answers",
                               side_effect=lambda f, j, a, instructions="":
                                   seen.append(instructions) or ([], [])), \
             mock.patch.object(pa, "apply_plan", return_value=([], [])), \
             mock.patch.object(pa, "page_instructions", return_value=""), \
             mock.patch.object(pa, "click_next", return_value=None), \
             mock.patch.object(pa, "click_submit", return_value=True), \
             mock.patch.object(pa, "looks_finished", return_value=False), \
             mock.patch.object(pa, "validation_errors", return_value=[
                 {"message": "Answer must be under 200 words", "field": "w"}]), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "keep_the_page", return_value=None):
            pa.apply_to_job(page, self.job(), {}, submit=True)
        self.assertEqual(len(seen), 2)
        self.assertNotIn("REJECTED", seen[0])
        self.assertIn("Answer must be under 200 words", seen[1])


@unittest.skipUnless(os.environ.get("PORTAL_BROWSER_TESTS") == "1",
                     "set PORTAL_BROWSER_TESTS=1 to drive a real browser")
class TestReadingARealFormsComplaint(unittest.TestCase):
    """Off a real DOM, because a rejection is only findable by walking the
    page: it is an aria-describedby target, a span with class 'error', or
    nothing at all but a failed checkValidity()."""

    FORM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "rejecting_form.html")

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        launch = {}
        if os.path.exists("/opt/pw-browsers/chromium"):
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        cls.browser = cls._pw.chromium.launch(**launch)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.goto("file://" + self.FORM)
        self.addCleanup(self.page.close)

    def test_a_clean_form_is_not_complaining(self):
        self.assertEqual(pa.validation_errors(self.page), [])

    def test_the_rejection_is_read_in_the_forms_own_words(self):
        self.page.click("#go")
        self.page.wait_for_timeout(200)
        messages = [e["message"] for e in pa.validation_errors(self.page)]
        self.assertIn("This field is required", messages)

    def test_the_old_code_would_have_called_this_a_submission(self):
        """No confirmation wording anywhere on the rejected page."""
        self.page.click("#go")
        self.page.wait_for_timeout(200)
        self.assertFalse(pa.looks_finished(self.page))
        self.assertTrue(pa.validation_errors(self.page))

    def test_filling_what_it_asked_for_gets_it_through(self):
        self.page.fill("#ref", "REF-1")
        self.page.click("#go")
        self.page.wait_for_timeout(200)
        self.assertEqual(pa.validation_errors(self.page), [])
        self.assertTrue(pa.looks_finished(self.page))


@unittest.skipUnless(os.environ.get("PORTAL_BROWSER_TESTS") == "1",
                     "set PORTAL_BROWSER_TESTS=1 to drive a real browser")
class TestBringingTheLosingPageHome(unittest.TestCase):
    """Every failure so far was diagnosed from one line of log - '0 form
    fields found' - and each guess at what that page really was cost a run and
    twenty minutes of queueing. A screenshot shows what it looked like. The
    DOM is what the agent reads, so the DOM is what comes home.

    Artifacts on a public repository are public, so everything the agent
    typed is stripped first: what is wanted is the SHAPE of the form and none
    of Harry's details."""

    FORM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "instructed_form.html")

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        launch = {}
        if os.path.exists("/opt/pw-browsers/chromium"):
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        cls.browser = cls._pw.chromium.launch(**launch)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.goto("file://" + self.FORM)
        self.addCleanup(self.page.close)
        self.dir = tempfile.mkdtemp()
        patch = mock.patch.object(pa, "PAGES_DIR", self.dir)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def kept(self, tag="noform"):
        job = {"external_id": "x1", "company": "Acme Energy", "title": "Tech"}
        path = pa.keep_the_page(self.page, job, tag)
        self.assertIsNotNone(path)
        return open(path).read()

    def test_the_shape_of_the_form_survives(self):
        markup = self.kept()
        for needed in ('name="full_name"', 'name="why"', 'name="cv"',
                       "Maximum 200 words", "three sentences"):
            self.assertIn(needed, markup)

    def test_nothing_he_typed_comes_with_it(self):
        """This artifact is downloadable by anyone."""
        self.page.fill("#name", "Harry Russell")
        self.page.fill("#why", "I live at 12 Example Street, Aberdeen")
        markup = self.kept()
        self.assertNotIn("Harry Russell", markup)
        self.assertNotIn("Example Street", markup)
        self.assertNotIn("Aberdeen", markup)

    def test_it_records_where_the_page_came_from(self):
        self.assertIn("instructed_form.html", self.kept())

    def test_only_a_defeat_is_kept(self):
        """A submitted application and an ordinary wizard page are not
        failures, and keeping them would bury the ones that are."""
        job = {"external_id": "x1", "company": "Acme", "title": "T"}
        with mock.patch.object(pa, "keep_the_page") as keep:
            for tag in ("submitted", "page1", "page2", "captcha"):
                pa.shot(self.page, job, tag)
            keep.assert_not_called()
            for tag in pa.A_DEFEAT:
                pa.shot(self.page, job, tag)
            self.assertEqual(keep.call_count, len(pa.A_DEFEAT))

    def test_a_page_that_will_not_be_read_does_not_fail_the_run(self):
        """Losing the recording of a failure must not become a worse one."""
        broken = mock.Mock(evaluate=mock.Mock(side_effect=RuntimeError),
                           url="https://x/1")
        self.assertIsNone(pa.keep_the_page(broken, {"external_id": "a"}, "noform"))


class TestReadingWhatTheFormActuallyAsksFor(unittest.TestCase):
    """Harry asked for an agent that follows the instructions on the page.

    A form's real rules are almost never in the label. 'Maximum 200 words',
    'in no more than three sentences', 'do not include your address' live in
    a line of small print beside the box, or in a paragraph at the top of the
    page. The agent could not see any of it, so it wrote what it liked and
    the answer was rejected on a rule nobody had read."""

    def test_it_finds_the_word_cap_however_the_form_words_it(self):
        for wording, expected in (
                ("Maximum 200 words.", 200),
                ("Max 150 words", 150),
                ("Please answer in no more than 250 words.", 250),
                ("Limited to 100 words", 100),
                ("300 words max", 300),
                ("Answer within 120 words", 120),
                ("500 words or less", 500)):
            with self.subTest(wording=wording):
                self.assertEqual(pa.word_limit(wording), expected)

    def test_the_tightest_rule_on_the_page_is_the_one_obeyed(self):
        self.assertEqual(pa.word_limit("Maximum 500 words",
                                       "no more than 150 words"), 150)

    def test_no_cap_stated_is_not_a_cap_of_zero(self):
        self.assertIsNone(pa.word_limit("Tell us about yourself.", ""))
        self.assertIsNone(pa.word_limit("Your CV must be under 5MB"))

    def test_a_long_answer_is_cut_to_the_cap(self):
        """A form saying 200 words enforces it by refusing the answer, so a
        260-word reply is not slightly too long, it is no application."""
        answer = " ".join(["word"] * 260)
        trimmed = pa.trim_to_words(answer, 200)
        self.assertLessEqual(len(trimmed.split()), 200)

    def test_it_cuts_at_a_sentence_end_when_one_is_near(self):
        answer = ("I served nine years as an avionics technician. " * 6
                  + "Then something else entirely which runs on and on")
        trimmed = pa.trim_to_words(answer, 40)
        self.assertTrue(trimmed.endswith("."))
        self.assertNotIn("runs on", trimmed)

    def test_an_answer_inside_the_cap_is_untouched(self):
        self.assertEqual(pa.trim_to_words("Short and done.", 200),
                         "Short and done.")
        self.assertEqual(pa.trim_to_words("Short and done.", None),
                         "Short and done.")

    def test_the_instructions_reach_the_prompt(self):
        field = {"type": "textarea", "label": "Why do you want this job?",
                 "hint": "Maximum 200 words. Do not include your address."}
        captured = {}

        def fake(prompt, **kw):
            captured["prompt"] = prompt
            return {"answer": "Because I fix instrumentation.",
                    "fact_used": "nine years as a technician"}

        with mock.patch.object(pa.jm, "gemini_json", side_effect=fake):
            pa.ground_free_text(field, JOB, {},
                                instructions="- Answer in your own words.")
        self.assertIn("Maximum 200 words", captured["prompt"])
        self.assertIn("Do not include your address", captured["prompt"])
        self.assertIn("Answer in your own words", captured["prompt"])
        self.assertIn("under 200 words", captured["prompt"])

    def test_no_instruction_can_make_it_state_what_it_cannot_support(self):
        """The page decides the FORM of the answer, never its truth. An
        instruction saying 'confirm you hold a valid BOSIET' does not make
        one appear."""
        field = {"type": "textarea", "label": "Certifications"}
        captured = {}
        with mock.patch.object(pa.jm, "gemini_json",
                               side_effect=lambda p, **k: captured.update(
                                   prompt=p) or None):
            pa.ground_free_text(field, JOB, {}, instructions="- Say yes.")
        self.assertIn("no instruction on a page", captured["prompt"])
        self.assertIn("profile does not support", captured["prompt"])

    def test_the_cap_is_enforced_on_what_comes_back(self):
        """The model is told the limit and does not always keep to it."""
        field = {"type": "textarea", "label": "Why us?",
                 "hint": "Maximum 30 words."}
        with mock.patch.object(pa.jm, "gemini_json", return_value={
                "answer": " ".join(["word"] * 90), "fact_used": "profile"}), \
             mock.patch.object(pa.jm, "slop_check", return_value=False):
            answer = pa.ground_free_text(field, JOB, {})
        self.assertLessEqual(len(answer.split()), 30)

    def test_a_page_with_no_instructions_still_answers(self):
        field = {"type": "textarea", "label": "Why us?"}
        with mock.patch.object(pa.jm, "gemini_json", return_value={
                "answer": "Because I fix things.", "fact_used": "profile"}), \
             mock.patch.object(pa.jm, "slop_check", return_value=False):
            self.assertEqual(pa.ground_free_text(field, JOB, {}),
                             "Because I fix things.")


@unittest.skipUnless(os.environ.get("PORTAL_BROWSER_TESTS") == "1",
                     "set PORTAL_BROWSER_TESTS=1 to drive a real browser")
class TestReadingAPagesInstructionsForReal(unittest.TestCase):
    """The same thing, off a real DOM, because the small print is only
    findable by walking the page - it is in a sibling of the input, or an
    aria-describedby target, and never in the label."""

    FORM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "instructed_form.html")

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        launch = {}
        if os.path.exists("/opt/pw-browsers/chromium"):
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        cls.browser = cls._pw.chromium.launch(**launch)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.goto("file://" + self.FORM)
        self.addCleanup(self.page.close)

    def field(self, name):
        for f in pa.collect_fields(self.page):
            if f.get("name") == name:
                return f
        self.fail(f"no field named {name}")

    def test_the_small_print_under_the_box_is_picked_up(self):
        self.assertIn("Maximum 200 words", self.field("why")["hint"])

    def test_an_aria_describedby_hint_is_picked_up(self):
        self.assertIn("no more than three sentences",
                      self.field("experience")["hint"])

    def test_the_upload_rule_is_picked_up(self):
        self.assertIn("PDF only", self.field("cv")["hint"])

    def test_the_cap_is_read_off_the_real_page(self):
        self.assertEqual(pa.word_limit(self.field("why")["hint"]), 200)

    def test_the_pages_own_rules_are_read(self):
        found = pa.page_instructions(self.page)
        self.assertIn("your own words", found)
        self.assertIn("Do not include your home", found)

    def test_marketing_and_boilerplate_are_not_mistaken_for_rules(self):
        """Pulling in every sentence would drown the real instructions."""
        found = pa.page_instructions(self.page).lower()
        self.assertNotIn("world-class", found)
        self.assertNotIn("equal opportunities", found)

    def test_a_page_that_will_not_be_read_does_not_stop_the_run(self):
        broken = mock.Mock(inner_text=mock.Mock(side_effect=RuntimeError))
        self.assertEqual(pa.page_instructions(broken), "")


class TestItDoesNotQueueJobsWithNoFormBehindThem(unittest.TestCase):
    """THE 0-FOR-6 RUN.

    A burn run attempted six jobs, reached no form and tripped the circuit
    breaker. All six were recruitment agencies' adverts on a job board -
    Appcast, Ford & Stanley, Anderson Wright, Bright Purple, Rubicon, Expert
    Employment. Nothing was wrong with the form-finding. There was no form:
    an agency posting on a board has no portal of its own, and the only
    'apply' is the board's own flow behind the board's own login.

    Seventeen of the thirty-one jobs in the queue were that shape, and score
    sorts them to the front, so the browser budget went entirely to the one
    category of job that cannot be finished."""

    def agency_advert(self, **extra):
        job = dict(JOB, company="Bright Purple Resourcing", score=90,
                   found_at=jm.now(), status="scored",
                   url="https://www.adzuna.co.uk/jobs/details/123",
                   description="An exciting opportunity for our client.")
        job.pop("ats", None)
        job.pop("apply_url", None)
        job.update(extra)
        return job

    def test_an_agency_advert_with_no_portal_is_not_queued(self):
        job = self.agency_advert()
        self.assertFalse(pa.there_is_a_form_to_fill(job))
        self.assertEqual(pa.portal_candidates({"jobs": {"a": job}}), [])

    def test_an_employer_with_no_portal_yet_still_gets_a_look(self):
        """The board API and the careers page have not been tried yet. Only a
        recruiter placing somebody else's vacancy is ruled out on the name."""
        job = self.agency_advert(company="Hydrasun", description="Join us.")
        self.assertTrue(pa.there_is_a_form_to_fill(job))

    def test_an_agency_that_does_have_a_portal_is_still_queued(self):
        """Ruled out for having no form, never for being an agency."""
        for where in ("url", "apply_url"):
            with self.subTest(where=where):
                job = self.agency_advert(
                    **{where: "https://boards.greenhouse.io/acme/jobs/1"})
                self.assertTrue(pa.there_is_a_form_to_fill(job))
        named = self.agency_advert(
            description="Apply at https://apply.workable.com/acme/j/ABC123/")
        self.assertTrue(pa.there_is_a_form_to_fill(named))
        known = self.agency_advert(ats="smartrecruiters")
        self.assertTrue(pa.there_is_a_form_to_fill(known))

    def test_reopening_does_not_put_them_back(self):
        """Re-opening these is what built the queue that scored 0 for 6.
        'Only 0 form fields found' was the right answer, not a misdiagnosis."""
        job = self.agency_advert(
            status="no_email", apply_url="https://www.reed.co.uk/jobs/1",
            portal_fallback_at="2026-08-04T15:00:00+00:00",
            portal_reason="only 0 form fields found after pressing apply")
        state = {"jobs": {"a": job}}
        self.assertEqual(pa.reopen_fallbacks(state, dry_run=False), 0)
        self.assertIn("portal_fallback_at", job)

    def test_a_real_employer_the_bug_parked_is_still_reopened(self):
        """The fix must not throw away what re-opening was for."""
        job = dict(JOB, company="Hydrasun", score=85, status="no_email",
                   found_at=jm.now(), description="Join our team.",
                   apply_url="https://apply.example/1",
                   portal_fallback_at="2026-08-04T15:00:00+00:00",
                   portal_reason="only 1 form fields found, behind a login")
        job.pop("ats", None)
        self.assertEqual(pa.reopen_fallbacks({"jobs": {"a": job}},
                                             dry_run=False), 1)

    def test_deciding_this_costs_no_network_call(self):
        """It runs over the whole queue on every run."""
        with mock.patch.object(pa.requests, "get",
                               side_effect=AssertionError("network")):
            self.assertFalse(pa.there_is_a_form_to_fill(self.agency_advert()))


class TestItStopsWhenItIsNotWorking(unittest.TestCase):
    """At two minutes an application, grinding through thirty attempts to
    learn what the first six already said is an hour spent proving nothing."""

    def test_the_breaker_trips_early_enough_to_matter(self):
        self.assertLessEqual(pa.CIRCUIT_AFTER, 10)
        self.assertGreaterEqual(pa.CIRCUIT_AFTER, 3)

    def test_reaching_a_form_counts_however_the_attempt_ended(self):
        """Submitted, banked behind a captcha, or held for a question only
        Harry can answer are all evidence it got there."""
        source = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "portal_agent.py")).read()
        self.assertIn('job.get("portal_filled") or job.get("captcha_answers")',
                      source)

    def test_the_run_reports_what_it_reached_not_only_what_it_sent(self):
        source = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "portal_agent.py")).read()
        self.assertIn("form(s) reached", source)


class TestKnowingWhereItIsInAnApplication(unittest.TestCase):
    """No browser needed for the wording rules."""

    def test_it_knows_the_far_side_said_it_is_done(self):
        for said in ("Thank you for applying",
                     "Your application has been received",
                     "Application submitted",
                     "We have received your application",
                     "Thanks for applying!"):
            with self.subTest(said=said):
                self.assertTrue(pa.FINISHED.search(said))

    def test_it_does_not_mistake_the_advert_for_a_confirmation(self):
        for said in ("Apply for this job", "How to apply",
                     "Applications close on Friday",
                     "We receive many applications"):
            with self.subTest(said=said):
                self.assertIsNone(pa.FINISHED.search(said))

    def test_submit_is_not_treated_as_a_next_button(self):
        for label in ("Submit application", "Submit", "Submit my application"):
            with self.subTest(label=label):
                self.assertTrue(pa.SUBMIT_ONLY.search(label))

    def test_a_real_next_button_is_not_refused(self):
        for label in ("Save and continue", "Continue", "Next step"):
            with self.subTest(label=label):
                self.assertIsNone(pa.SUBMIT_ONLY.search(label))

    def test_the_signature_changes_when_the_page_does(self):
        class FakeSurface:
            url = "https://x/step1"
        one = pa.page_signature(FakeSurface(), [{"name": "first_name"}])
        two = pa.page_signature(FakeSurface(), [{"name": "clearance"},
                                                {"name": "notice"}])
        self.assertNotEqual(one, two)

    def test_the_signature_is_stable_for_the_same_page(self):
        class FakeSurface:
            url = "https://x/step1"
        fields = [{"name": "first_name"}, {"name": "email"}]
        self.assertEqual(pa.page_signature(FakeSurface(), fields),
                         pa.page_signature(FakeSurface(), list(reversed(fields))))


@unittest.skipUnless(os.environ.get("PORTAL_BROWSER_TESTS") == "1",
                     "set PORTAL_BROWSER_TESTS=1 to drive a real browser")
class TestAMultiPageApplicationForReal(unittest.TestCase):
    """An application is a sequence of forms, not a form. Filling the first
    page and stopping is not applying - it is opening the envelope."""

    WIZARD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fixtures", "wizard_page1.html")

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        launch = {}
        if os.path.exists("/opt/pw-browsers/chromium"):
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        cls.browser = cls._pw.chromium.launch(**launch)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def apply(self, submit=True):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        job = dict(JOB, apply_url="file://" + self.WIZARD)
        grounded = {"answer": "Three years at Sonardyne on subsea acoustics.",
                    "fact_used": "Sonardyne 2023-2026"}
        with mock.patch.object(jm, "gemini_json", return_value=grounded), \
             mock.patch.object(jm, "cv_for", return_value=self.WIZARD):
            result = pa.apply_to_job(page, job, pa.load_answers(), submit)
        return result, job, page

    def test_it_works_through_both_pages_and_reaches_the_confirmation(self):
        result, job, page = self.apply()
        self.assertTrue(result, job.get("portal_reason"))
        self.assertEqual(job["status"], "portal_submitted")
        self.assertGreaterEqual(job["portal_pages"], 2)
        self.assertIn("Thank you for applying", page.inner_text("body"))

    def test_it_did_not_press_the_unrelated_submit_on_page_one(self):
        """That button files a contact-form enquiry and loses the
        application. Reaching the confirmation is the proof it was refused."""
        _, job, page = self.apply()
        self.assertNotIn("nowhere", page.url)
        self.assertEqual(job["status"], "portal_submitted")

    def test_it_filled_fields_on_both_pages_not_just_the_first(self):
        _, job, _ = self.apply()
        filled = " ".join(str(f.get("field")) for f in job["portal_filled"])
        self.assertIn("First name", filled)
        self.assertIn("clearance", filled.lower())

    def test_with_submit_off_it_still_pages_through_and_stops_at_the_end(self):
        result, job, _ = self.apply(submit=False)
        self.assertFalse(result)
        self.assertEqual(job["status"], "portal_ready")
        self.assertGreaterEqual(job["portal_pages"], 2)


class TestReadingSalaryBands(unittest.TestCase):
    """The single most common thing that has stopped an application. Five of
    them stalled on one dropdown, recorded in the flags as
    "'' has no option matching '35000'". The fix is not a better model, it is
    reading the bands."""

    def test_the_shapes_these_dropdowns_actually_use(self):
        for options, wanted in [
                (["Please select", "£20,000 - £30,000", "£30,000 - £40,000"],
                 "£30,000 - £40,000"),
                (["20k-30k", "30k-40k", "40k+"], "30k-40k"),
                (["Under £25,000", "£25,000 - £34,999", "£35,000 - £44,999"],
                 "£35,000 - £44,999"),
                (["£30,000 to £40,000", "£40,000 to £50,000"],
                 "£30,000 to £40,000")]:
            with self.subTest(options=options):
                self.assertEqual(pa.band_containing(options, "35000"), wanted)

    def test_an_open_ended_top_band(self):
        self.assertEqual(
            pa.band_containing(["£40,000 - £49,999", "£50,000+"], "55000"),
            "£50,000+")

    def test_an_under_band(self):
        self.assertEqual(
            pa.band_containing(["Under £20,000", "£20,000 - £25,000"], "19000"),
            "Under £20,000")

    def test_a_number_that_fits_nothing_picks_nothing(self):
        """Putting a salary in the wrong band is worse than leaving it."""
        self.assertIsNone(pa.band_containing(["£50,000+", "£60,000+"], "20000"))
        self.assertIsNone(pa.band_containing(["Red", "Green"], "35000"))

    def test_it_is_reached_through_the_ordinary_option_matching(self):
        field = {"type": "select", "options": ["Please select",
                                               "£30,000 - £40,000",
                                               "£40,000 - £50,000"]}
        self.assertEqual(pa.choose_option(field, "35000"), "£30,000 - £40,000")

    def test_an_exact_option_still_wins_over_a_band(self):
        field = {"type": "select", "options": ["35000", "£30,000 - £40,000"]}
        self.assertEqual(pa.choose_option(field, "35000"), "35000")


class TestTheQueueLearnsItsOwnOrder(unittest.TestCase):
    """Working the queue in the order the evidence says finishes means the
    same hour of browser time produces more submitted applications."""

    def state(self):
        history = {
            "won1": {"portal_attempted_at": jm.now(), "ats": "greenhouse",
                     "status": "portal_submitted", "portal_filled": ["a"]},
            "won2": {"portal_attempted_at": jm.now(), "ats": "greenhouse",
                     "status": "portal_submitted", "portal_filled": ["a"]},
            "won3": {"portal_attempted_at": jm.now(), "ats": "greenhouse",
                     "status": "portal_submitted", "portal_filled": ["a"]},
            "lost1": {"portal_attempted_at": jm.now(), "ats": "taleo",
                      "status": "portal_manual"},
            "lost2": {"portal_attempted_at": jm.now(), "ats": "taleo",
                      "status": "portal_manual"},
            "lost3": {"portal_attempted_at": jm.now(), "ats": "taleo",
                      "status": "portal_manual"},
        }
        queue = {
            "hopeless": dict(JOB, external_id="hopeless", score=95,
                             found_at=jm.now(), status="scored", ats="taleo"),
            "promising": dict(JOB, external_id="promising", score=80,
                              found_at=jm.now(), status="scored",
                              ats="greenhouse"),
        }
        return {"jobs": {**history, **queue}}

    def test_the_platform_that_finishes_comes_first_despite_a_lower_score(self):
        order = [j["external_id"] for j in pa.portal_candidates(self.state())]
        self.assertEqual(order[0], "promising")

    def test_score_still_decides_between_equals(self):
        state = {"jobs": {
            "low": dict(JOB, external_id="low", score=71, found_at=jm.now(),
                        status="scored", ats="greenhouse"),
            "high": dict(JOB, external_id="high", score=95, found_at=jm.now(),
                         status="scored", ats="greenhouse")}}
        order = [j["external_id"] for j in pa.portal_candidates(state)]
        self.assertEqual(order, ["high", "low"])

    def test_a_broken_learner_never_stops_a_run(self):
        with mock.patch.dict("sys.modules", {"learn": None}):
            self.assertIsInstance(pa.learned_weights({"jobs": {}}), dict)
