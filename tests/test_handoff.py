"""
Tests for the CAPTCHA bank and the handoff page.

The bank is the answer to a hard constraint: a CAPTCHA belongs to a live
browser session, and the session that filled the form is gone by the time a
human reads about it. So what gets banked is every answer, not a frozen
browser, and the handoff page turns those answers into one paste.
"""
import json
import os
import tempfile
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
        """The whole point: fill first, then discover the bot check.

        Submit IS pressed now, because refusing to press a button the agent
        has never tried was costing completely filled applications on the
        agent's own guess about a widget. What must not change is where it
        ends up when the check really does hold: banked, with every answer,
        on Harry's list."""
        job = {"external_id": "a", "title": "Technician", "company": "Acme",
               "apply_url": "https://boards.greenhouse.io/acme/jobs/1"}
        page = mock.MagicMock()
        fields = [{"index": "0", "label": "First name", "name": "first_name",
                   "type": "text", "required": True, "options": [],
                   "placeholder": "", "aria_label": "", "group_label": "",
                   "maxlength": None, "tag": "input", "value": ""}] * 4
        with mock.patch.object(pa, "collect_fields", return_value=fields), \
             mock.patch.object(pa, "captcha_kind", return_value="challenge"), \
             mock.patch.object(pa, "plan_answers",
                               return_value=([planned("First name", "Harry")], [])), \
             mock.patch.object(pa, "apply_plan", return_value=([], [])), \
             mock.patch.object(pa, "shot", return_value=None), \
             mock.patch.object(pa, "keep_the_page", return_value=None), \
             mock.patch.object(pa, "looks_finished", return_value=False), \
             mock.patch.object(pa, "validation_errors", return_value=[]), \
             mock.patch.object(pa, "click_submit",
                               return_value=True) as submit:
            result = pa.apply_to_job(page, job, {}, submit=True)
        self.assertFalse(result)
        submit.assert_called_once()
        self.assertEqual(job["status"], "portal_awaiting_captcha")
        self.assertEqual(len(job["captcha_answers"]), 1)

    def test_a_bot_check_before_the_form_loads_still_reaches_his_list(self):
        """Nothing to bank, but it is still a CAPTCHA standing between Harry
        and an application, and he asked for every one of those on the list
        with a link. Filed as 'manual' it would never be seen again."""
        job = {"external_id": "a", "title": "T", "company": "C",
               "apply_url": "https://x/1"}
        with mock.patch.object(pa, "collect_fields", return_value=[]), \
             mock.patch.object(pa, "captcha_kind", return_value="challenge"), \
             mock.patch.object(pa, "shot", return_value=None):
            pa.apply_to_job(mock.MagicMock(), job, {}, submit=True)
        self.assertEqual(job["status"], "portal_awaiting_captcha")
        self.assertEqual(job["captcha_answers"], [])
        self.assertIn(job, handoff.pending({"jobs": {"a": job}}))

    def test_it_says_plainly_that_nothing_was_filled_in(self):
        """A prefill button that places zero fields wastes his time twice."""
        job = {"external_id": "a", "title": "T", "company": "C",
               "apply_url": "https://x/1", "status": "portal_awaiting_captcha",
               "captcha_answers": []}
        markup = handoff.card(job, 0)
        self.assertNotIn("copyFill", markup)
        self.assertIn("Nothing pre-filled", markup)


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


class TestEmailingThePage(unittest.TestCase):
    """The page has always existed. It lived in a build artifact, which means
    a login, a zip and a desktop before anyone can press a button on it."""

    def state(self, n=2):
        jobs = {}
        for i in range(n):
            jobs[str(i)] = {
                "status": "portal_awaiting_captcha", "company": f"Firm {i}",
                "title": "Instrumentation Technician", "score": 90 - i,
                "apply_url": f"https://apply.example/{i}",
                "captcha_answers": [{"label": "First name",
                                     "name": "first_name", "value": "Harry"}]}
        return {"jobs": jobs}

    def send(self, state, **kw):
        with mock.patch.object(jm, "send_email") as send, \
             mock.patch.object(jm, "GMAIL_ADDRESS", "harry@example.com"), \
             mock.patch.object(handoff, "OUT_DIR", tempfile.mkdtemp()) as d:
            handoff.OUT_FILE = os.path.join(handoff.OUT_DIR, "index.html")
            count = handoff.email_page(state, **kw)
        return count, send

    def test_it_goes_to_his_own_inbox_with_the_page_attached(self):
        count, send = self.send(self.state(2))
        self.assertEqual(count, 2)
        to_addr, subject, body = send.call_args.args[:3]
        self.assertEqual(to_addr, "harry@example.com")
        self.assertIn("2 applications need only the CAPTCHA", subject)
        attachments = send.call_args.kwargs["attachments"]
        name, maintype, subtype, payload = attachments[0]
        self.assertEqual((name, maintype, subtype),
                         ("captcha-handoff.html", "text", "html"))
        self.assertIn(b"Copy prefill snippet", payload)

    def test_the_cv_is_not_attached_to_this_one(self):
        """It is a page of links for him, not an application to anybody."""
        _, send = self.send(self.state(1))
        self.assertFalse(send.call_args.kwargs["attach_cv"])

    def test_one_reads_as_one(self):
        _, send = self.send(self.state(1))
        self.assertIn("1 application needs only the CAPTCHA",
                      send.call_args.args[1])

    def test_the_body_works_without_opening_the_attachment(self):
        """Read on a phone, it still has to be actionable."""
        _, send = self.send(self.state(2))
        body = send.call_args.args[2]
        self.assertIn("https://apply.example/0", body)
        self.assertIn("https://apply.example/1", body)
        self.assertIn("Instrumentation Technician", body)

    def test_anything_needing_his_answer_is_called_out(self):
        state = self.state(1)
        state["jobs"]["0"]["captcha_flags"] = ["'Salary' has no option '35000'"]
        _, send = self.send(state)
        self.assertIn("NEEDS YOU:", send.call_args.args[2])

    def test_nothing_waiting_means_no_email(self):
        count, send = self.send({"jobs": {}})
        self.assertEqual(count, 0)
        send.assert_not_called()

    def test_it_goes_once_a_day(self):
        state = self.state(1)
        self.send(state)
        self.assertEqual(state[handoff.EMAILED], jm.today())
        count, send = self.send(state)
        self.assertEqual(count, 0)
        send.assert_not_called()

    def test_asking_for_it_by_hand_overrides_the_daily_guard(self):
        state = self.state(1)
        state[handoff.EMAILED] = jm.today()
        count, send = self.send(state, force=True)
        self.assertEqual(count, 1)
        send.assert_called_once()
