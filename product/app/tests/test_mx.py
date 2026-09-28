"""A letter never goes to a domain that takes no mail.

A bounce is counted against the sender, and the sender is the user's own
Gmail. These hold:

  1. THE LOOKUP IS READ CORRECTLY - MX, the address-record fallback RFC 5321
     allows, and the "null MX" a domain publishes to say it takes nothing.
  2. ONLY A DEFINITE NO STOPS A LETTER. A timeout is this runner's problem,
     not the employer's.
  3. AT SEND TIME an undeliverable draft is taken off the queue, not sent and
     not counted as a failure, and it shows under its own tab.
  4. THE SWITCH turns all of it off.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dns.exception
import dns.resolver

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from jobseeker import mx  # noqa: E402
from test_sending import Base  # noqa: E402


def resolver(**answers):
    """answers: {"MX": [...hosts] | exception class, "A": [...] | exc}."""
    def resolve(domain, kind):
        got = answers.get(kind, dns.resolver.NoAnswer)
        if isinstance(got, type) and issubclass(got, Exception):
            raise got()
        return [SimpleNamespace(exchange=h) for h in got]
    return resolve


class TheLookup(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {"MX_CHECK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_mx_record_takes_mail(self):
        self.assertTrue(mx.accepts_mail(
            "acme.co.uk", resolver=resolver(MX=["mx1.acme.co.uk."])))

    def test_no_mx_but_an_address_record_takes_mail(self):
        self.assertTrue(mx.accepts_mail(
            "acme.co.uk", resolver=resolver(A=["192.0.2.1"])))

    def test_a_domain_that_does_not_exist_takes_none(self):
        self.assertFalse(mx.accepts_mail(
            "acme.co.uk", resolver=resolver(MX=dns.resolver.NXDOMAIN)))

    def test_null_mx_says_it_takes_none(self):
        self.assertFalse(mx.accepts_mail(
            "acme.co.uk", resolver=resolver(MX=["."])))

    def test_neither_record_takes_none(self):
        self.assertFalse(mx.accepts_mail("acme.co.uk", resolver=resolver()))

    def test_a_timeout_is_not_a_no(self):
        self.assertIsNone(mx.accepts_mail(
            "acme.co.uk", resolver=resolver(MX=dns.exception.Timeout)))
        self.assertFalse(mx.undeliverable(
            "hr@acme.co.uk", resolver=resolver(MX=dns.exception.Timeout)))

    def test_undeliverable_reads_the_domain_from_the_address(self):
        self.assertTrue(mx.undeliverable(
            "hr@gone.co.uk", resolver=resolver(MX=dns.resolver.NXDOMAIN)))

    def test_switched_off_nothing_is_undeliverable(self):
        with patch.dict(os.environ, {"MX_CHECK": "0"}):
            self.assertFalse(mx.undeliverable(
                "hr@gone.co.uk", resolver=resolver(MX=dns.resolver.NXDOMAIN)))


class AtSendTime(Base):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, {"MX_CHECK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)

    def domains(self, dead):
        return patch.object(mx, "accepts_mail",
                            side_effect=lambda d, **k: d not in dead)

    def test_a_dead_domain_is_not_sent_and_is_marked(self):
        dead = self.draft("Gone Ltd", email="hr@gone.example")
        self.draft("Live Ltd", email="hr@live.example")
        with self.domains({"gone.example"}):
            report = self.autosend.send_due_for_user(self.uid,
                                                     sender=self.fake_send)
        self.assertEqual([s["to_email"] for s in self.sent],
                         ["hr@live.example"])
        self.assertEqual(report.failed, 0)
        self.assertEqual(self.db.get_draft(self.uid, dead)["status"],
                         "undeliverable")

    def test_a_dns_failure_still_sends(self):
        self.draft("Acme Ltd", email="hr@acme.example")
        with patch.object(mx, "accepts_mail", return_value=None):
            self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(len(self.sent), 1)

    def test_it_has_its_own_tab(self):
        from app.tests.test_app import AppTestCase
        case = AppTestCase()
        case.client, case.main = self.client, self.main
        case.sign_in("harry@example.com")
        did = self.draft("Gone Ltd", email="hr@gone.example")
        self.db.mark_draft(self.uid, did, "undeliverable")
        page = self.client.get("/drafts?status=undeliverable").text
        self.assertIn("Gone Ltd", page)
        self.assertIn("does not accept email", page)
        self.assertNotIn("Gone Ltd", self.client.get("/drafts").text)


class AtDraftTime(unittest.TestCase):
    """Before the model is paid to write: the job is kept and marked, with
    no letter-writing call spent on it."""

    def test_a_dead_domain_costs_no_letter_and_shows_as_undeliverable(self):
        from app.tests.test_runner import RunnerTestCase, scripted_ai
        case = RunnerTestCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        prompts = []
        score_only = scripted_ai()

        def ai(prompt):
            prompts.append(prompt)
            return score_only(prompt)
        with patch.dict(os.environ, {"MX_CHECK": "1"}), \
                patch.object(mx, "accepts_mail", return_value=False):
            report = case.run_once(ai=ai)
        self.assertEqual(report.undeliverable, 1)
        self.assertEqual(report.drafted, 0)
        self.assertTrue(all("SCORE GUIDE" in p for p in prompts),
                        "a letter was written to a domain that takes no mail")
        rows = case.db.list_drafts(case.user["id"], status="undeliverable")
        self.assertEqual([r["company"] for r in rows], ["Pennine Foods"])


if __name__ == "__main__":
    unittest.main()
