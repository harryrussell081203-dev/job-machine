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

        def fake_apply(page, job, answers, submit):
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
                               side_effect=lambda p, j, a, s: looked.append(j) or False), \
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
