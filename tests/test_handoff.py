"""
Tests for the CAPTCHA bank and the handoff page.

The bank is the answer to a hard constraint: a CAPTCHA belongs to a live
browser session, and the session that filled the form is gone by the time a
human reads about it. So what gets banked is every answer, not a frozen
browser, and the handoff page turns those answers into one paste.
"""
import html
import json
import os
import urllib.parse
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
        self.assertIn("Fill and open", page)

    def test_what_the_button_copies_is_the_answers_and_nothing_else(self):
        """It used to copy a whole program, because the console was going to
        run it. The bookmark holds the program now, so what travels on the
        clipboard is data - which is also why it fits and why the bookmark
        never needs dragging twice."""
        job = self.banked()
        with mock.patch.object(handoff, "OUT_FILE", "/tmp/handoff_test5.html"), \
             mock.patch.object(handoff, "OUT_DIR", "/tmp"):
            handoff.build(self.state(job))
            page = open("/tmp/handoff_test5.html").read()
        payload = page.split('id="fill0" class=hidden>', 1)[1].split("<", 1)[0]
        answers = json.loads(html.unescape(payload))
        self.assertEqual(answers[0]["value"], "Harry")
        self.assertNotIn("function", payload)

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
        self.assertIn(b"Fill and open", payload)

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

class TestOneClickAndHandItBack(unittest.TestCase):
    """His words: "simplify the captcha thing... to the point where all I need
    to do is click it and then hand it back to you".

    What it used to ask, written out: open the form, press Copy, press F12,
    click Console, press Ctrl+V, press Enter, attach the CV, solve the puzzle,
    press Submit, then write a reply saying he had done it. Nine steps, and one
    of them had stopped working - Chrome, Edge and Firefox all now refuse a
    paste into the console until you type "allow pasting" by hand."""

    def state(self, *jobs):
        return {"jobs": {j["external_id"]: j for j in jobs}}

    def banked(self, eid="a", **over):
        job = {"external_id": eid, "title": "ETO", "company": "DOF",
               "score": 88, "status": "portal_awaiting_captcha",
               "ats": "workable",
               "apply_url": "https://apply.workable.com/dof/j/1/",
               "captcha_answers": [{"label": "First name", "name": "first_name",
                                    "value": "Harry"}]}
        job.update(over)
        return job

    def page(self, state, name="/tmp/handoff_click.html"):
        with mock.patch.object(handoff, "OUT_FILE", name), \
             mock.patch.object(handoff, "OUT_DIR", "/tmp"):
            handoff.build(state)
            return open(name).read()

    def test_the_console_is_gone_from_the_instructions(self):
        """It is not merely long, it is wrong: that route no longer works."""
        page = self.page(self.state(self.banked()))
        for word in ("F12", "console", "Console"):
            with self.subTest(word=word):
                self.assertNotIn(word, page)

    def test_the_bookmark_carries_the_filling_and_none_of_the_answers(self):
        """This is what makes it permanent. A bookmark with answers baked in
        would go stale the moment a new application landed, and he would be
        dragging a new one onto the bar every morning."""
        mark = handoff.bookmarklet()
        self.assertTrue(mark.startswith("javascript:"))
        self.assertNotIn("Harry", urllib.parse.unquote(mark))
        self.assertIn("clipboard", urllib.parse.unquote(mark))

    def test_the_bookmark_is_the_same_one_tomorrow(self):
        first = handoff.bookmarklet()
        self.page(self.state(self.banked()))
        self.page(self.state(self.banked(), self.banked("b", company="T-Tech")))
        self.assertEqual(first, handoff.bookmarklet())

    def test_it_is_offered_as_something_to_drag_once(self):
        page = self.page(self.state(self.banked()))
        self.assertIn("bookmarks bar", page)
        self.assertIn("job-machine fill", page)

    def test_handing_one_back_is_a_link_with_the_message_already_written(self):
        page = self.page(self.state(self.banked()))
        self.assertIn("mailto:", page)
        self.assertIn("job-machine+done", page)
        self.assertIn("body=done+DOF", page)

    def test_there_is_one_link_for_the_whole_list(self):
        page = self.page(self.state(self.banked()))
        self.assertIn("body=done+all", page)

    def test_the_marker_is_what_the_reader_looks_for(self):
        """A mailto: hand-back is a FRESH message, not a reply, so matching on
        the original subject would never have seen it."""
        from tools import applied
        self.assertTrue(applied.HANDOFF_SUBJECT.search(
            f"{handoff.DONE_MARKER} DOF"))

    def test_the_ones_he_can_do_in_seconds_come_first(self):
        """A from-scratch form at the top because it scored two points higher
        is how a list of eleven stops getting finished at number three."""
        state = self.state(
            self.banked("filled", score=80),
            self.banked("scratch", score=95, company="T-Tech",
                        captcha_answers=[]))
        self.assertEqual([j["external_id"] for j in handoff.pending(state)],
                         ["filled", "scratch"])

    def test_the_bookmarklet_is_valid_javascript(self):
        """It cannot be run to find out. A syntax error here is a bookmark that
        does nothing at all when clicked, on a page he cannot debug."""
        import subprocess
        import tempfile as tf
        node = None
        for candidate in ("node", "/opt/node22/bin/node", "/usr/bin/node"):
            if subprocess.run(["which", candidate],
                              capture_output=True).returncode == 0:
                node = candidate
                break
        if not node:
            self.skipTest("no node to parse with")
        body = urllib.parse.unquote(
            handoff.bookmarklet()[len("javascript:"):])
        with tf.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(body)
        result = subprocess.run([node, "--check", f.name],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(os.environ.get("PORTAL_BROWSER_TESTS") == "1",
                     "set PORTAL_BROWSER_TESTS=1 to drive a real browser")
class TestTheBookmarkActuallyFillsAForm(unittest.TestCase):
    """The claim being made to him is 'click the bookmark and every field
    fills'. Parsing is not that claim. This runs the real bookmarklet against
    a real ATS form in a real Chromium and reads the values back off it."""

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
        self.page.goto("file://" + os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "fixtures", "fake_ats.html"))
        self.addCleanup(self.page.close)
        self.messages = []
        self.page.on("dialog", lambda d: (self.messages.append(d.message),
                                          d.dismiss()))

    def run_bookmarklet(self, answers):
        """The bookmarklet, given the clipboard it would have read."""
        body = urllib.parse.unquote(
            handoff.bookmarklet()[len("javascript:"):])
        # Stand in for the clipboard. Playwright cannot click a bookmark, and
        # what is being tested is the filling, not the browser's own paste
        # permission - which is why the bookmarklet falls back to a prompt.
        self.page.evaluate(
            "text => { navigator.clipboard.readText = () => "
            "Promise.resolve(text); }", json.dumps(answers))
        self.page.evaluate(body)
        self.page.wait_for_timeout(300)

    def test_it_fills_the_fields_it_was_given(self):
        self.run_bookmarklet([
            {"label": "First name", "name": "first_name", "value": "Harry"},
            {"label": "Surname", "name": "last_name", "value": "Russell"}])
        self.assertEqual(self.page.input_value("[name=first_name]"), "Harry")
        self.assertEqual(self.page.input_value("[name=last_name]"), "Russell")

    def test_it_matches_on_the_label_when_the_name_is_no_help(self):
        self.run_bookmarklet([
            {"label": "Email", "name": "", "value": "h@example.com"}])
        # Read the live value, not the markup: a value set by script never
        # appears in the serialised HTML, which is how a filler that does
        # nothing can look like it worked.
        self.assertEqual(self.page.input_value("[name=email]"),
                         "h@example.com")

    def test_it_says_how_many_it_placed(self):
        self.run_bookmarklet([
            {"label": "First name", "name": "first_name", "value": "Harry"}])
        self.assertTrue(self.messages)
        self.assertIn("Filled 1 field", self.messages[0])

    def test_a_wrong_clipboard_says_so_instead_of_failing_silently(self):
        """The likeliest mistake in the whole flow: clicking the bookmark
        before pressing Fill and open. It has to say which way round it goes."""
        body = urllib.parse.unquote(
            handoff.bookmarklet()[len("javascript:"):])
        self.page.evaluate("navigator.clipboard.readText = () => "
                           "Promise.resolve('some unrelated text');")
        self.page.evaluate(body)
        self.page.wait_for_timeout(300)
        self.assertTrue(self.messages)
        self.assertIn("Fill and open", self.messages[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
