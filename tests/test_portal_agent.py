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

    def test_unknowns_are_null_not_guessed(self):
        answers = pa.load_answers()
        for unknown in ("postcode", "notice_period", "salary_expectation",
                        "driving_licence"):
            self.assertIsNone(answers[unknown],
                              f"{unknown} must stay null until Harry fills it in")

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


class TestPlanning(unittest.TestCase):
    def setUp(self):
        self.answers = pa.load_answers()
        cv = mock.patch.object(jm, "cv_path", return_value="/tmp/cv.pdf")
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
        fields = [field(index="0", label="Notice period", required=True)]
        plan, flags = pa.plan_answers(fields, JOB, self.answers)
        self.assertEqual(plan, [])
        self.assertTrue(any("notice_period" in f for f in flags))

    def test_an_optional_unknown_is_simply_left_blank(self):
        fields = [field(index="0", label="Post code", required=False)]
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
        with mock.patch.object(jm, "cv_path", return_value=None):
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
             mock.patch.object(jm, "cv_path", return_value=FIXTURE):
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

        # the convictions question is required, so the form is handed to Harry
        self.assertEqual(values["convictions"], "")
        self.assertEqual(values.get("ethnicity", ""), "")
        self.assertTrue(any("convictions" in f for f in flags))

    def test_a_flagged_form_is_never_submitted(self):
        job = dict(JOB)
        with mock.patch.object(jm, "gemini_json",
                               return_value={"answer": "Same work as my day job.",
                                             "fact_used": "Sonardyne"}), \
             mock.patch.object(jm, "cv_path", return_value=FIXTURE), \
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
