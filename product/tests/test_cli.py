"""The zero-cost run: no server, no database, no domain.

State is one JSON file, drafts arrive as an email. This is the path that
costs nothing to operate, so it gets the same care as the app.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker import cli  # noqa: E402
from jobseeker.pipeline import harvest  # noqa: E402
from jobseeker.profile import Profile, Role  # noqa: E402

GOOD_BODY = (
    "Saw your maintenance technician role covering the packaging lines at "
    "Rotherham. That is the work I do now.\n\n"
    "1. Four years of planned and reactive maintenance on high-speed packaging "
    "lines, fault finding on PLC-controlled conveyors and fillers there.\n"
    "2. Cut unplanned downtime on our primary line by about a third across two "
    "years of shift work on that same site.\n\n"
    "Would it help if I sent over my availability for a call this week?")


def profile():
    return Profile(
        name="Sam Doherty", location="Sheffield", phone="07700 900123",
        email="sam@example.com", situation="employed", current_salary=32000,
        min_salary_annual=38000, priorities=["money"],
        target_roles=["maintenance technician"], locations=["Sheffield"],
        history=[Role(title="Maintenance Technician", org="Brightwater",
                      detail="planned and reactive maintenance")])


class Resp:
    def __init__(self, payload=None, text="", status=200):
        self._payload, self.text, self.status_code = payload, text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class Session:
    def __init__(self, adzuna=None):
        self.adzuna = adzuna if adzuna is not None else {"results": [{
            "id": "1", "title": "Maintenance Technician",
            "company": {"display_name": "Pennine Foods"},
            "location": {"display_name": "Rotherham"},
            "redirect_url": "https://example.com/1",
            "description": "Packaging lines. CV to claire@pennine.co.uk",
            "salary_min": 40000, "salary_max": 44000}]}

    def get(self, url, **kw):
        if "adzuna" in url:
            return Resp(payload=self.adzuna)
        if "reed" in url:
            return Resp(payload={"results": []})
        if "clearbit" in url:
            return Resp(payload=[])
        return Resp(text="", status=404)


def ai(prompt):
    if "SCORE GUIDE" in prompt:
        return json.dumps([{"listing": 0, "score": 88, "reason": "strong"}])
    return json.dumps({"subject": "Maintenance technician, packaging lines",
                       "body": GOOD_BODY})


CREDS = harvest.Credentials(adzuna_app_id="a", adzuna_app_key="b")


class TestState(unittest.TestCase):
    def test_a_missing_state_file_starts_empty(self):
        s = cli.load_state("/nonexistent/state.json")
        self.assertEqual(s["seen"], {})
        self.assertEqual(s["contacted"], [])

    def test_state_round_trips(self):
        path = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        cli.save_state(path, {"version": 1, "seen": {"a": "drafted"},
                              "contacted": ["acme"], "do_not_contact": []})
        self.assertEqual(cli.load_state(path)["seen"], {"a": "drafted"})

    def test_a_contacted_employer_is_refused_under_any_spelling(self):
        state = {"seen": {}, "contacted": ["pennine foods"], "do_not_contact": []}
        self.assertFalse(cli.may_contact(state, "Pennine Foods Ltd"))
        self.assertFalse(cli.may_contact(state, "PENNINE FOODS GROUP"))
        self.assertTrue(cli.may_contact(state, "Kestrel Engineering"))

    def test_a_blocked_employer_is_refused(self):
        state = {"seen": {}, "contacted": [], "do_not_contact": ["kestrel"]}
        self.assertFalse(cli.may_contact(state, "Kestrel Ltd"))


class TestRun(unittest.TestCase):
    def fresh(self):
        return {"seen": {}, "contacted": [], "do_not_contact": []}

    def test_a_listing_becomes_a_draft(self):
        drafts, counts = cli.run(profile(), CREDS, self.fresh(), ai,
                                 session=Session(), delay=0)
        self.assertEqual(counts["drafted"], 1)
        self.assertEqual(drafts[0]["to_email"], "claire@pennine.co.uk")
        self.assertTrue(drafts[0]["body"].startswith("Hi Claire,"))

    def test_the_second_run_skips_what_it_has_seen(self):
        state = self.fresh()
        cli.run(profile(), CREDS, state, ai, session=Session(), delay=0)
        _, counts = cli.run(profile(), CREDS, state, ai, session=Session(),
                            delay=0)
        self.assertEqual(counts["drafted"], 0)
        self.assertEqual(counts["harvested"], 0)

    def test_an_already_contacted_employer_is_skipped(self):
        state = self.fresh()
        state["contacted"].append("pennine foods")
        _, counts = cli.run(profile(), CREDS, state, ai, session=Session(),
                            delay=0)
        self.assertEqual(counts["already_contacted"], 1)
        self.assertEqual(counts["drafted"], 0)

    def test_every_skipped_listing_records_why(self):
        s = Session({"results": [{
            "id": "9", "title": "Maintenance Technician",
            "company": {"display_name": "Quiet Ltd"},
            "location": {"display_name": "Leeds"}, "redirect_url": "",
            "description": "No contact details here.",
            "salary_min": 40000, "salary_max": 44000}]})
        state = self.fresh()
        _, counts = cli.run(profile(), CREDS, state, ai, session=s, delay=0)
        self.assertEqual(counts["no_address"], 1)
        self.assertIn("no real email address", state["seen"]["adzuna_9"])

    def test_one_bad_listing_does_not_lose_the_run(self):
        def explodes_on_compose(prompt):
            if "SCORE GUIDE" in prompt:
                return json.dumps([{"listing": 0, "score": 88}])
            raise RuntimeError("quota gone")

        drafts, counts = cli.run(profile(), CREDS, self.fresh(),
                                 explodes_on_compose, session=Session(), delay=0)
        # falls back rather than losing the application
        self.assertEqual(counts["fallback"], 1)
        self.assertEqual(len(drafts), 1)


class TestDigest(unittest.TestCase):
    def build(self):
        drafts, counts = cli.run(profile(), CREDS,
                                 {"seen": {}, "contacted": [], "do_not_contact": []},
                                 ai, session=Session(), delay=0)
        return cli.digest_html(drafts, counts, profile())

    def test_it_contains_the_letter_and_a_one_tap_send_link(self):
        html = self.build()
        self.assertIn("Maintenance Technician", html)
        self.assertIn("Pennine Foods", html)
        self.assertIn("mailto:claire%40pennine.co.uk", html)
        self.assertIn("Open in your mail app", html)

    def test_it_says_who_the_letter_reaches_and_how_good_that_is(self):
        self.assertIn("a named person", self.build())

    def test_a_quiet_day_explains_itself_rather_than_being_blank(self):
        html = cli.digest_html([], {"harvested": 12, "scored_out": 9,
                                    "no_address": 3, "already_contacted": 0},
                               profile())
        self.assertIn("Nothing today", html)
        self.assertIn("nothing is ever guessed", html)

    def test_the_mailto_link_carries_the_whole_letter(self):
        html = self.build()
        self.assertIn("Hi%20Claire", html)


class TestEntryPoint(unittest.TestCase):
    def test_a_bad_profile_path_fails_with_a_readable_message(self):
        rc = cli.main(["--profile", "/nonexistent/profile.yaml"])
        self.assertEqual(rc, 1)

    def test_a_missing_model_key_is_reported_not_crashed(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        example = os.path.join(here, "profile.example.yaml")
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        old = os.environ.pop("GEMINI_API_KEY", None)
        try:
            self.assertEqual(cli.main(["--profile", example]), 1)
        finally:
            if old:
                os.environ["GEMINI_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
