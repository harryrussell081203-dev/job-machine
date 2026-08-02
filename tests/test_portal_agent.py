"""
Tests for the portal agent.

The logic tests run offline. The browser test drives a real Chromium against a
local replica of a Greenhouse/Lever style form (tests/fixtures/fake_ats.html) -
it is skipped automatically if Playwright is not installed. Nothing here ever
touches a real employer's portal.
"""
import json
import os
import sys
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

        def fake_apply(page, job, answers, submit):
            applied.append(job["external_id"])
            job["status"] = "portal_submitted"
            return True

        fake_pw = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(),
                                             "playwright.sync_api": fake_pw}), \
             mock.patch.object(pa, "apply_to_job", side_effect=fake_apply), \
             mock.patch.object(pa, "resolve_apply_url",
                               side_effect=lambda j: (j["url"],
                                                      pa.classify_url(j["url"])[0])), \
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

    def test_browser_follows_a_board_through_to_the_employer(self):
        """Plain HTTP left every candidate stuck on www.adzuna.co.uk, because
        the board redirects with JavaScript."""
        page = mock.MagicMock()
        # lands on Adzuna, finds an off-site apply link, follows it
        page.url = "https://www.adzuna.co.uk/jobs/details/123"
        page.evaluate.return_value = "https://boards.greenhouse.io/acme/jobs/9"

        def goto(url, **kw):
            page.url = url
        page.goto.side_effect = goto

        url, ats = pa.resolve_in_browser(
            page, {"url": "https://www.adzuna.co.uk/jobs/details/123"})
        self.assertEqual(url, "https://boards.greenhouse.io/acme/jobs/9")
        self.assertEqual(ats, "greenhouse")

    def test_a_direct_employer_link_is_left_alone(self):
        page = mock.MagicMock()
        page.url = "https://boards.greenhouse.io/acme/jobs/9"
        page.goto.side_effect = lambda url, **kw: None
        url, ats = pa.resolve_in_browser(
            page, {"url": "https://boards.greenhouse.io/acme/jobs/9"})
        page.evaluate.assert_not_called()      # no need to hunt for a link
        self.assertEqual(ats, "greenhouse")

    def test_a_board_with_no_apply_link_stays_put_rather_than_guessing(self):
        page = mock.MagicMock()
        page.url = "https://www.adzuna.co.uk/jobs/details/123"
        page.goto.side_effect = lambda url, **kw: None
        page.evaluate.return_value = None
        url, ats = pa.resolve_in_browser(
            page, {"url": "https://www.adzuna.co.uk/jobs/details/123"})
        self.assertEqual(url, "https://www.adzuna.co.uk/jobs/details/123")

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
             mock.patch.object(pa, "resolve_apply_url",
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

    def test_captcha_stops_the_agent_rather_than_being_solved(self):
        job = dict(JOB, apply_url="file://" + FIXTURE)
        with mock.patch.object(pa, "has_captcha", return_value=True), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "click_submit") as submit:
            pa.apply_to_job(self.page, job, pa.load_answers(), submit=True)
        submit.assert_not_called()
        self.assertEqual(job["status"], "portal_manual")
        self.assertIn("captcha", job["portal_reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
