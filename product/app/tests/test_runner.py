"""One full run, end to end, with no network.

This is the test that proves the four stages are actually joined up — the
thing that was missing when the drafts screen stayed empty.
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import build_app  # noqa: E402

PROFILE = {
    "name": "Sam Doherty", "location": "Sheffield", "phone": "07700 900123",
    "situation": "employed", "current_salary": 32000,
    "min_salary_annual": 38000, "min_rate_hourly": 20,
    "priorities": ["money"], "target_roles": ["maintenance technician"],
    "locations": ["Sheffield"], "radius_miles": 25,
    "qualifications": ["Level 3 NVQ"],
    "never_claim": ["that they hold a current 17th Edition certificate"],
    "history": [{"title": "Maintenance Technician", "org": "Brightwater",
                 "detail": "planned and reactive maintenance on packaging lines"}],
}

GOOD_BODY = (
    "Saw your maintenance technician role covering the packaging lines at "
    "Rotherham. That is the work I do now.\n\n"
    "1. Four years of planned and reactive maintenance on high-speed packaging "
    "lines, fault finding on PLC-controlled conveyors and fillers there.\n"
    "2. Cut unplanned downtime on our primary line by about a third across two "
    "years of shift work on that same site.\n\n"
    "Would it help if I sent over my availability for a call this week?")


def adzuna_payload(**over):
    job = {"id": "1", "title": "Maintenance Technician",
           "company": {"display_name": "Pennine Foods"},
           "location": {"display_name": "Rotherham"},
           "redirect_url": "https://example.com/1",
           "description": "Packaging lines. Send your CV to claire@pennine.co.uk",
           "salary_min": 40000, "salary_max": 44000}
    job.update(over)
    return {"results": [job]}


class Resp:
    def __init__(self, payload=None, text="", status=200):
        self._payload, self.text, self.status_code = payload, text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class Session:
    """Adzuna returns one job; everything else is empty."""

    def __init__(self, adzuna=None):
        self.adzuna = adzuna if adzuna is not None else adzuna_payload()
        self.urls = []

    def get(self, url, **kw):
        self.urls.append(url)
        if "adzuna" in url:
            return Resp(payload=self.adzuna)
        if "reed" in url:
            return Resp(payload={"results": []})
        if "clearbit" in url:
            return Resp(payload=[])
        return Resp(text="", status=404)


def scripted_ai(score=88, subject="Maintenance technician, packaging lines",
                body=GOOD_BODY):
    """Answers scoring calls with a score and composing calls with a letter."""
    def ai(prompt):
        if "SCORE GUIDE" in prompt:
            return json.dumps([{"listing": 0, "score": score, "reason": "strong"}])
        return json.dumps({"subject": subject, "body": body})
    return ai


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.main, self.db_path = build_app(DEV_MODE="1", BILLING_ENABLED="0",
                                            ADZUNA_APP_ID="a", ADZUNA_APP_KEY="b")
        self.addCleanup(lambda: os.path.exists(self.db_path)
                        and os.unlink(self.db_path))
        import importlib
        self.runner = importlib.reload(importlib.import_module("app.runner"))
        self.db = self.main.db
        self.db.init()
        self.user = self.db.get_or_create_user("sam@example.com")
        self.db.save_profile(self.user["id"], PROFILE)

    def run_once(self, **kw):
        kw.setdefault("session", Session())
        kw.setdefault("ai", scripted_ai())
        kw.setdefault("delay", 0)
        return self.runner.run_for_user(self.user["id"], **kw)


class TestAFullRun(RunnerTestCase):
    def test_a_listing_becomes_a_draft_on_the_screen(self):
        report = self.run_once()
        self.assertEqual(report.drafted, 1, report.errors)

        drafts = self.db.list_drafts(self.user["id"])
        self.assertEqual(len(drafts), 1)
        d = drafts[0]
        self.assertEqual(d["company"], "Pennine Foods")
        self.assertEqual(d["to_email"], "claire@pennine.co.uk")
        self.assertEqual(d["to_name"], "Claire")
        self.assertEqual(d["contact_tier"], 3)
        self.assertEqual(d["score"], 88)
        self.assertTrue(d["body"].startswith("Hi Claire,"))
        self.assertIn("07700 900123", d["body"])
        self.assertEqual(d["salary_text"], "£44,000")

    def test_a_second_run_does_not_redo_the_same_listing(self):
        self.run_once()
        again = self.run_once()
        self.assertEqual(again.drafted, 0)
        self.assertEqual(again.already_seen + again.harvested, 0,
                         "the listing should not even be harvested again")
        self.assertEqual(len(self.db.list_drafts(self.user["id"])), 1)


class TestItProtectsThePersonReceiving(RunnerTestCase):
    def test_an_employer_already_written_to_is_skipped_before_any_ai_call(self):
        self.db.record_contacted(self.user["id"], "Pennine Foods")
        calls = []
        report = self.run_once(ai=lambda p: (calls.append(p), "[]")[1])
        self.assertEqual(report.already_contacted, 1)
        self.assertEqual(report.drafted, 0)
        self.assertEqual(calls, [], "an AI call was spent on an unsendable listing")

    def test_a_blocked_employer_is_never_drafted(self):
        self.db.block_company(self.user["id"], "Pennine Foods", reason="asked")
        report = self.run_once()
        self.assertEqual(report.blocked, 1)
        self.assertEqual(report.drafted, 0)

    def test_the_same_employer_under_a_different_name_is_still_skipped(self):
        self.db.record_contacted(self.user["id"], "Pennine Foods Group Ltd")
        report = self.run_once()
        self.assertEqual(report.already_contacted, 1)

    def test_no_address_means_no_draft_and_nothing_guessed(self):
        s = Session(adzuna_payload(description="No contact details in this advert."))
        report = self.run_once(session=s)
        self.assertEqual(report.no_address, 1)
        self.assertEqual(report.drafted, 0)
        self.assertEqual(self.db.list_drafts(self.user["id"]), [])


class TestItExplainsItself(RunnerTestCase):
    def test_every_rejection_is_recorded_with_a_reason(self):
        # "Nothing today" needs a reason or the user stops trusting it.
        s = Session(adzuna_payload(description="No contact details."))
        self.run_once(session=s)
        outcomes = self.db.recent_outcomes(self.user["id"])
        self.assertEqual(len(outcomes), 1)
        self.assertIn("no real email address", outcomes[0]["outcome"])

    def test_a_low_score_is_recorded_with_the_models_reason(self):
        self.run_once(ai=scripted_ai(score=20))
        outcomes = self.db.recent_outcomes(self.user["id"])
        self.assertTrue(any("scored 20" in o["outcome"] for o in outcomes))

    def test_the_report_reads_as_a_sentence(self):
        self.assertIn("drafted from", self.run_once().summary())


class TestItSurvivesFailure(RunnerTestCase):
    def test_a_composing_failure_falls_back_rather_than_sending_nothing(self):
        def scoring_ok_composing_broken(prompt):
            if "SCORE GUIDE" in prompt:
                return json.dumps([{"listing": 0, "score": 88, "reason": "ok"}])
            raise RuntimeError("daily quota exhausted")

        report = self.run_once(ai=scoring_ok_composing_broken)
        self.assertEqual(report.fallback_used, 1)
        self.assertEqual(report.drafted, 1)
        body = self.db.list_drafts(self.user["id"])[0]["body"]
        self.assertIn("Brightwater", body, "the fallback uses their own history")

    def test_a_missing_profile_is_reported_not_crashed(self):
        other = self.db.get_or_create_user("nobody@example.com")
        report = self.runner.run_for_user(other["id"], ai=scripted_ai(),
                                          session=Session(), delay=0)
        self.assertEqual(report.drafted, 0)
        self.assertIn("no profile", report.errors[0])

    def test_the_draft_cap_is_honoured(self):
        # Names without digits: a digit in the local part fails the
        # is-this-a-person check, which is the original's deliberate caution.
        names = ["jane", "robert", "carol", "priya", "stephen", "amelia"]
        many = {"results": [
            {"id": str(i), "title": "Maintenance Technician",
             "company": {"display_name": f"Kestrel {n.title()} Works"},
             "location": {"display_name": "Rotherham"},
             "redirect_url": "", "salary_min": 40000, "salary_max": 44000,
             "description": f"Send your CV to {n}@kestrel{n}.co.uk"}
            for i, n in enumerate(names)]}

        def ai_scoring_all(prompt):
            if "SCORE GUIDE" in prompt:
                return json.dumps([{"listing": i, "score": 90, "reason": "ok"}
                                   for i in range(6)])
            return json.dumps({"subject": "Maintenance technician, packaging lines",
                               "body": GOOD_BODY})

        report = self.run_once(session=Session(many), ai=ai_scoring_all, cap=3)
        self.assertEqual(report.drafted, 3, report.errors)
        self.assertEqual(len(self.db.list_drafts(self.user["id"])), 3)

    def test_a_digit_in_the_address_makes_it_unusable(self):
        # Faithful to the original: parts must be alphabetic to count as a
        # person. It costs some real addresses and never invents one.
        from jobseeker.pipeline import contacts
        self.assertEqual(contacts.classify("jane0@acme.com")[0], 0)
        self.assertEqual(contacts.classify("jane@acme.com")[0], 3)


if __name__ == "__main__":
    unittest.main()
