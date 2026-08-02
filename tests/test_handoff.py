"""
Tests for the CAPTCHA bank and the handoff page.

The bank is the answer to a hard constraint: a CAPTCHA belongs to a live
browser session, and the session that filled the form is gone by the time a
human reads about it. So what gets banked is every answer, not a frozen
browser, and the handoff page turns those answers into one paste.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import job_machine as jm  # noqa: E402
import portal_agent as pa  # noqa: E402
import handoff  # noqa: E402


def planned(label, value, name="", kind="text", ftype="text"):
    return {"field": {"label": label, "name": name, "type": ftype},
            "value": value, "kind": kind, "source": "bank:x"}


class TestBankingACaptchaBlockedApplication(unittest.TestCase):
    def test_every_answer_is_saved_for_the_human_to_reuse(self):
        job = {"external_id": "a", "title": "Instrumentation Technician",
               "company": "Hydrasun", "score": 88}
        plan = [planned("First name", "Harry", "first_name"),
                planned("Email address", "harryrussell081203@gmail.com", "email"),
                planned("Upload CV", "/tmp/cv.pdf", kind="file", ftype="file")]
        with mock.patch.object(pa, "shot", return_value="shot.png"):
            pa.bank_for_captcha(mock.MagicMock(), job, plan, [])
        self.assertEqual(job["status"], "portal_awaiting_captcha")
        labels = [a["label"] for a in job["captcha_answers"]]
        self.assertEqual(labels, ["First name", "Email address"])  # file excluded
        self.assertEqual(job["captcha_answers"][0]["value"], "Harry")
        self.assertIn("captcha_banked_at", job)

    def test_outstanding_questions_travel_with_it(self):
        job = {"external_id": "a", "title": "T", "company": "C"}
        with mock.patch.object(pa, "shot", return_value=None):
            pa.bank_for_captcha(mock.MagicMock(), job, [],
                                ["convictions: only Harry can answer it"])
        self.assertEqual(job["captcha_flags"],
                         ["convictions: only Harry can answer it"])

    def test_a_filled_form_behind_a_captcha_is_banked_not_discarded(self):
        """The whole point: fill first, then discover the bot check."""
        job = {"external_id": "a", "title": "Technician", "company": "Acme",
               "apply_url": "https://boards.greenhouse.io/acme/jobs/1"}
        page = mock.MagicMock()
        fields = [{"index": "0", "label": "First name", "name": "first_name",
                   "type": "text", "required": True, "options": [],
                   "placeholder": "", "aria_label": "", "group_label": "",
                   "maxlength": None, "tag": "input", "value": ""}] * 4
        with mock.patch.object(pa, "collect_fields", return_value=fields), \
             mock.patch.object(pa, "has_captcha", return_value=True), \
             mock.patch.object(pa, "plan_answers",
                               return_value=([planned("First name", "Harry")], [])), \
             mock.patch.object(pa, "apply_plan", return_value=([], [])), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "click_submit") as submit:
            result = pa.apply_to_job(page, job, {}, submit=True)
        self.assertFalse(result)
        submit.assert_not_called()
        self.assertEqual(job["status"], "portal_awaiting_captcha")
        self.assertEqual(len(job["captcha_answers"]), 1)

    def test_a_bot_check_before_the_form_loads_has_nothing_to_bank(self):
        job = {"external_id": "a", "title": "T", "company": "C",
               "apply_url": "https://x/1"}
        with mock.patch.object(pa, "collect_fields", return_value=[]), \
             mock.patch.object(pa, "has_captcha", return_value=True), \
             mock.patch.object(pa, "shot", return_value=None):
            pa.apply_to_job(mock.MagicMock(), job, {}, submit=True)
        self.assertEqual(job["status"], "portal_manual")
        self.assertNotIn("captcha_answers", job)


class TestHandoffPage(unittest.TestCase):
    def state(self, *jobs):
        return {"jobs": {j["external_id"]: j for j in jobs},
                "companies_contacted": {}, "send_counts": {}}

    def banked(self, eid="a", **over):
        job = {"external_id": eid, "title": "Instrumentation Technician",
               "company": "Hydrasun", "score": 88,
               "status": "portal_awaiting_captcha", "ats": "greenhouse",
               "apply_url": "https://boards.greenhouse.io/hydrasun/jobs/9",
               "captcha_answers": [
                   {"label": "First name", "name": "first_name",
                    "type": "text", "value": "Harry", "source": "bank"},
                   {"label": "Email address", "name": "email",
                    "type": "email", "value": "harryrussell081203@gmail.com",
                    "source": "bank"}],
               "captcha_flags": []}
        job.update(over)
        return job

    def test_only_unfinished_applications_are_listed(self):
        state = self.state(
            self.banked("waiting"),
            self.banked("done", captcha_done_at=jm.now()),
            {"external_id": "sent", "status": "portal_submitted"})
        self.assertEqual([j["external_id"] for j in handoff.pending(state)],
                         ["waiting"])

    def test_the_page_carries_the_link_and_the_answers(self):
        with mock.patch.object(handoff, "OUT_FILE", "/tmp/handoff_test.html"), \
             mock.patch.object(handoff, "OUT_DIR", "/tmp"):
            count = handoff.build(self.state(self.banked()))
            page = open("/tmp/handoff_test.html").read()
        self.assertEqual(count, 1)
        self.assertIn("boards.greenhouse.io/hydrasun/jobs/9", page)
        self.assertIn("Instrumentation Technician", page)
        self.assertIn("Harry", page)
        self.assertIn("Copy prefill snippet", page)

    def test_the_prefill_snippet_is_valid_json_the_browser_can_use(self):
        job = self.banked()
        snippet = handoff.PREFILL_JS.replace(
            "__ANSWERS__", json.dumps(job["captcha_answers"]))
        self.assertNotIn("__ANSWERS__", snippet)
        payload = snippet.split("const answers = ", 1)[1].split(";\n", 1)[0]
        self.assertEqual(json.loads(payload)[0]["value"], "Harry")

    def test_outstanding_questions_are_shown_as_a_warning(self):
        job = self.banked(captcha_flags=["convictions: only you can answer it"])
        with mock.patch.object(handoff, "OUT_FILE", "/tmp/handoff_test2.html"), \
             mock.patch.object(handoff, "OUT_DIR", "/tmp"):
            handoff.build(self.state(job))
            page = open("/tmp/handoff_test2.html").read()
        self.assertIn("Needs your answer", page)
        self.assertIn("convictions", page)

    def test_an_empty_bank_still_produces_a_readable_page(self):
        with mock.patch.object(handoff, "OUT_FILE", "/tmp/handoff_test3.html"), \
             mock.patch.object(handoff, "OUT_DIR", "/tmp"):
            count = handoff.build(self.state())
            page = open("/tmp/handoff_test3.html").read()
        self.assertEqual(count, 0)
        self.assertIn("Nothing waiting", page)

    def test_job_titles_cannot_inject_html(self):
        job = self.banked(title="<script>alert(1)</script>Technician")
        with mock.patch.object(handoff, "OUT_FILE", "/tmp/handoff_test4.html"), \
             mock.patch.object(handoff, "OUT_DIR", "/tmp"):
            handoff.build(self.state(job))
            page = open("/tmp/handoff_test4.html").read()
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
