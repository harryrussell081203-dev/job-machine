"""
Tests for recording the applications Harry finished himself.

Offline. No inbox is opened.

The failure that matters here is not a crash. It is marking a job done that
was never applied for: that takes it off the CAPTCHA list silently, and the
application is simply never made. So the receipt has to be a real receipt.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_machine as jm  # noqa: E402
import learn  # noqa: E402
from tools import applied  # noqa: E402


def banked(eid="a", company="DOF", title="ROV Supervisor", **over):
    job = {"external_id": eid, "company": company, "title": title,
           "status": "portal_awaiting_captcha", "ats": "workable",
           "apply_url": "https://apply.workable.com/j/1",
           "captcha_answers": [{"label": "First name", "value": "Harry"}],
           "portal_attempted_at": jm.now(), "score": 80}
    job.update(over)
    return job


def state(*jobs):
    return {"jobs": {j["external_id"]: j for j in jobs}}


class TestTakingItOffTheList(unittest.TestCase):
    def test_he_says_he_did_it_and_it_comes_off(self):
        job = banked()
        st = state(job)
        applied.mark(job, "he said so")
        self.assertEqual(job["status"], "portal_submitted")
        self.assertTrue(job[applied.DONE])
        self.assertEqual(applied.waiting(st), [])

    def test_it_is_found_by_company_or_by_title(self):
        st = state(banked())
        self.assertTrue(applied.by_name(st, "dof"))
        self.assertTrue(applied.by_name(st, "ROV"))
        self.assertFalse(applied.by_name(st, "Boskalis"))
        self.assertFalse(applied.by_name(st, ""))

    def test_it_counts_as_an_application_because_it_is_one(self):
        job = banked()
        applied.mark(job, "he said so")
        self.assertEqual(job["status"], "portal_submitted")
        self.assertTrue(job.get("portal_submitted_at"))

    def test_but_the_agent_is_not_credited_with_finishing_it(self):
        """A portal that always needs a human at the end must not be ranked
        as though the machine sails through it - that would work the queue in
        exactly the wrong order."""
        job = banked()
        applied.mark(job, "he said so")
        stats = learn.platform_success(state(job))
        self.assertEqual(stats["workable"]["submitted"], 0)
        self.assertEqual(stats["workable"]["tried"], 1)

    def test_one_the_agent_really_did_finish_still_counts(self):
        job = banked(status="portal_submitted", portal_filled=["a"])
        stats = learn.platform_success(state(job))
        self.assertEqual(stats["workable"]["submitted"], 1)


class TestReadingTheEmployersReceipt(unittest.TestCase):
    """An employer that takes an application sends a confirmation within
    minutes. That email is proof of the one thing the machine cannot
    otherwise know, and the inbox is already being read."""

    def mail(self, *msgs):
        return mock.patch.object(applied, "_confirmations", return_value=list(msgs))

    def test_a_confirmation_takes_it_off_the_list(self):
        job = banked()
        st = state(job)
        with self.mail(("careers@dof.com", "Thank you for applying to DOF",
                        "We have received your application.")):
            self.assertEqual(applied.scan(st), 1)
        self.assertEqual(job["status"], "portal_submitted")
        self.assertEqual(applied.waiting(st), [])

    def test_a_newsletter_from_the_same_firm_does_not(self):
        """The failure that costs a job rather than a minute: taken off the
        list without ever having been applied for."""
        job = banked()
        st = state(job)
        with self.mail(("news@dof.com", "DOF news: our latest vessels",
                        "Thanks for your interest in our company.")):
            self.assertEqual(applied.scan(st), 0)
        self.assertEqual(job["status"], "portal_awaiting_captcha")

    def test_a_receipt_from_a_different_employer_does_not(self):
        job = banked(company="DOF")
        st = state(job)
        with self.mail(("careers@boskalis.com",
                        "Thank you for applying to Boskalis",
                        "We have received your application.")):
            self.assertEqual(applied.scan(st), 0)
        self.assertEqual(job["status"], "portal_awaiting_captcha")

    def test_a_two_letter_company_is_too_vague_to_match_on(self):
        job = banked(company="BP")
        st = state(job)
        with self.mail(("x@y.com", "Thank you for applying",
                        "We have received your application at bp or wherever")):
            self.assertEqual(applied.scan(st), 0)

    def test_nothing_waiting_means_no_mailbox_is_opened(self):
        with mock.patch.object(applied, "_confirmations") as read:
            self.assertEqual(applied.scan({"jobs": {}}), 0)
        read.assert_not_called()

    def test_a_dead_mailbox_is_not_a_crash(self):
        with mock.patch.object(jm, "GMAIL_ADDRESS", ""), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", ""):
            self.assertEqual(applied._confirmations(), [])

    def test_the_wordings_employers_really_use(self):
        for subject in ("Thank you for applying",
                        "Your application has been received",
                        "We have received your application",
                        "Application received - Technician",
                        "Thanks for applying to us"):
            with self.subTest(subject=subject):
                self.assertTrue(applied.A_RECEIPT.search(subject))

    def test_and_the_ones_that_are_not_receipts(self):
        for subject in ("Jobs you might like this week",
                        "Thanks for your interest in our company",
                        "Your CV has been viewed",
                        "Complete your profile"):
            with self.subTest(subject=subject):
                self.assertFalse(applied.A_RECEIPT.search(subject))



class TestHandingThemBackInOneReply(unittest.TestCase):
    """The whole hand-back has to be one action on a desktop, because that is
    where a CAPTCHA gets solved and where he already is when he finishes. He
    does the list in one sitting and replies 'done'."""

    SUBJECT = "[job-machine] 5 applications need only the CAPTCHA"

    def setUp(self):
        patch = mock.patch.object(jm, "GMAIL_ADDRESS", "harry@example.com")
        patch.start()
        self.addCleanup(patch.stop)

    def reply(self, body, sender="Harry <harry@example.com>", subject=None):
        return (sender, subject if subject is not None else self.SUBJECT, body)

    def state(self):
        return {"jobs": {j["external_id"]: j for j in (
            banked("a", "DOF", "Data Controller"),
            banked("b", "T-Tech", "IT Field Engineer"),
            banked("c", "EnerMech", "Crane Operator"))}}

    def test_one_word_clears_the_whole_list(self):
        st = self.state()
        self.assertEqual(applied.read_his_reply(st, [self.reply("done")]), 3)
        self.assertEqual(applied.waiting(st), [])

    def test_the_words_he_might_actually_use(self):
        for word in ("done", "Done", "all done", "finished", "completed",
                     "did them", "sent", "applied"):
            with self.subTest(word=word):
                st = self.state()
                self.assertEqual(
                    applied.read_his_reply(st, [self.reply(word)]), 3)

    def test_naming_some_clears_only_those(self):
        st = self.state()
        self.assertEqual(
            applied.read_his_reply(st, [self.reply("done DOF, T-Tech")]), 2)
        left = [j["company"] for j in applied.waiting(st)]
        self.assertEqual(left, ["EnerMech"])

    def test_the_quoted_email_below_does_not_count_as_naming_them(self):
        """His reply carries the whole list underneath it. Only the first
        line is his."""
        st = self.state()
        body = ("done\n\nOn 6 Aug, job-machine wrote:\n"
                "> 1. Data Controller - DOF\n> 2. IT Field Engineer - T-Tech\n")
        self.assertEqual(applied.read_his_reply(st, [self.reply(body)]), 3)

    def test_nobody_else_can_clear_his_list(self):
        st = self.state()
        self.assertEqual(applied.read_his_reply(
            st, [self.reply("done", sender="recruiter@agency.com")]), 0)
        self.assertEqual(len(applied.waiting(st)), 3)

    def test_a_reply_to_some_other_email_does_not_clear_it(self):
        st = self.state()
        self.assertEqual(applied.read_his_reply(
            st, [self.reply("done", subject="Re: your morning brief")]), 0)
        self.assertEqual(len(applied.waiting(st)), 3)

    def test_a_reply_that_is_not_saying_done_does_nothing(self):
        for body in ("can you look at the DOF one again",
                     "not done yet", "I will do these tonight",
                     "why is this one on the list?"):
            with self.subTest(body=body):
                st = self.state()
                self.assertEqual(
                    applied.read_his_reply(st, [self.reply(body)]), 0)

    def test_replying_twice_does_not_double_count(self):
        st = self.state()
        mail = [self.reply("done"), self.reply("done")]
        self.assertEqual(applied.read_his_reply(st, mail), 3)

    def test_they_are_recorded_as_applications_he_made(self):
        st = self.state()
        applied.read_his_reply(st, [self.reply("done")])
        for job in st["jobs"].values():
            self.assertEqual(job["status"], "portal_submitted")
            self.assertTrue(job["finished_by_hand_at"])
            self.assertIn("replied", job["portal_reason"])

    def test_an_empty_list_needs_no_reply_read(self):
        self.assertEqual(applied.read_his_reply({"jobs": {}},
                                                [self.reply("done")]), 0)


class TestTheListIsBuiltToGrow(unittest.TestCase):
    """Three of these is a different job from fifteen. It has to stay
    workable as the machine fills more of them."""

    def test_the_text_list_is_numbered_so_he_can_keep_his_place(self):
        from tools import handoff
        jobs = [banked("a", "DOF"), banked("b", "T-Tech"),
                banked("c", "EnerMech")]
        text = handoff.plain_list(jobs)
        self.assertIn("1. ", text)
        self.assertIn("3. ", text)

    def test_the_email_says_how_to_hand_them_back(self):
        from tools import handoff
        st = {"jobs": {"a": banked("a")}, "handoff_emailed_on": None}
        with mock.patch.object(handoff, "OUT_FILE", "/tmp/hb_test.html"), \
             mock.patch.object(handoff, "OUT_DIR", "/tmp"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "harry@example.com"), \
             mock.patch.object(jm, "send_email") as send:
            handoff.email_page(st, force=True)
        body = send.call_args.args[2]
        self.assertIn("REPLY TO THIS EMAIL WITH 'DONE'", body)
        self.assertIn("done DOF, T-Tech", body)

    def test_the_page_says_it_too(self):
        from tools import handoff
        with mock.patch.object(handoff, "OUT_FILE", "/tmp/hb_page.html"), \
             mock.patch.object(handoff, "OUT_DIR", "/tmp"):
            handoff.build({"jobs": {"a": banked("a")}})
        page = open("/tmp/hb_page.html").read()
        self.assertIn("reply to the email", page)
        self.assertIn("<code>done</code>", page)

if __name__ == "__main__":
    unittest.main(verbosity=2)
