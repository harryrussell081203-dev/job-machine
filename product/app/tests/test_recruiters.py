"""The recruiter finder: which agencies, why, the filters, and writing to one
through the same rules as every other letter."""

import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_app import AppTestCase  # noqa: E402
from test_runner import GOOD_BODY, PROFILE, Resp, scripted_ai  # noqa: E402


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def item(company, title="Maintenance Technician", days=1, contract="",
         place="Sheffield", description="Our client needs a technician.",
         ident=None):
    return {"external_id": ident or f"x_{company}_{title}_{days}",
            "source": "adzuna", "title": title, "company": company,
            "location": place, "url": "https://example.com/j",
            "search_location": place, "description": description,
            "salary_min": None, "salary_max": None, "posted_at": ago(days),
            "contract": contract, "advertiser": "agency"}


class Grouping(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("DEV_MODE", "1")
        os.environ.setdefault("SECRET_KEY", "test")
        from app import recruiters
        self.r = recruiters

    def test_agencies_with_more_matching_jobs_come_first_with_a_reason(self):
        rows = [item("Apex Recruitment", days=1),
                item("Hays", days=2), item("Hays", "Electrical Technician", 3),
                item("Hays", "Shift Technician", 5)]
        out = self.r.group(rows, days=30)
        self.assertEqual([a["name"] for a in out], ["Hays", "Apex Recruitment"])
        self.assertEqual(out[0]["count"], 3)
        self.assertEqual(out[0]["reason"],
                         "Advertising 3 jobs like yours near Sheffield in the "
                         "last month")

    def test_the_filters(self):
        rows = [item("Apex", days=2, contract="contract"),
                item("Blue Personnel", days=20, contract="permanent"),
                item("Core Staffing", "Electrician", days=1, place="Leeds"),
                item("Dale Recruitment", days=1)]          # terms unknown
        names = lambda **f: [a["name"] for a in self.r.group(rows, **f)]
        self.assertNotIn("Blue Personnel", names(days=7))
        self.assertEqual(sorted(names(terms=["permanent"])),
                         ["Blue Personnel", "Core Staffing", "Dale Recruitment"])
        self.assertEqual(names(area="Leeds"), ["Core Staffing"])
        self.assertEqual(names(words="electrician"), ["Core Staffing"])
        self.assertEqual(names(min_roles=2), [])


class ThePage(AppTestCase):
    env = {"BILLING_ENABLED": "0", "ADZUNA_APP_ID": "a", "ADZUNA_APP_KEY": "b"}

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.db = self.main.db
        self.user = self.db.get_or_create_user("sam@example.com")
        self.db.save_profile(self.user["id"], PROFILE)
        from app import recruiters
        self.r = recruiters

    def seed(self, rows):
        self.db.set_meta(f"recruiters:{self.user['id']}",
                         json.dumps({"at": int(time.time()), "listings": rows}))

    def test_lists_agencies_with_their_adverts_and_filters(self):
        self.seed([item("Hays", "Maintenance Technician"),
                   item("Hays", "Shift Technician", days=3)])
        page = self.client.get("/recruiters").text
        self.assertIn("Hays", page)
        self.assertIn("Advertising 2 jobs like yours near Sheffield", page)
        self.assertIn("Write to them", page)
        self.assertIn("Job title contains", page)

    def test_the_search_runs_once_and_keeps_only_agency_adverts(self):
        calls = []
        payload = {"results": [
            {"id": "1", "title": "Maintenance Technician",
             "company": {"display_name": "Apex Recruitment"},
             "location": {"display_name": "Sheffield"},
             "redirect_url": "https://example.com/1",
             "description": "Our client needs a technician.",
             "created": ago(3)},
            {"id": "2", "title": "Maintenance Technician",
             "company": {"display_name": "Pennine Foods"},
             "location": {"display_name": "Rotherham"},
             "redirect_url": "https://example.com/2",
             "description": "Join our packaging team.", "created": ago(1)}]}

        class S:
            def get(self, url, **kw):
                calls.append(url)
                return Resp(payload=payload if "adzuna" in url
                            else {"results": []})
        from jobseeker.profile import Profile
        from app import runner
        got = self.r.gather(self.user["id"], Profile.from_dict(PROFILE),
                            runner.credentials(), session=S())
        self.assertEqual([i["company"] for i in got["listings"]],
                         ["Apex Recruitment"])
        n = len(calls)
        self.r.gather(self.user["id"], Profile.from_dict(PROFILE),
                      runner.credentials(), session=S())
        self.assertEqual(len(calls), n)        # from the saved search

    def test_never_contact_blocks_the_agency(self):
        self.seed([item("Apex Recruitment")])
        self.client.post("/recruiters/apex/never")
        self.assertTrue(self.db.is_blocked(self.user["id"], "Apex Recruitment"))
        self.assertIn("never-contact list",
                      self.client.get("/recruiters").text)

    def test_writing_drafts_one_letter_to_the_recruiter(self):
        self.seed([item("Apex Recruitment",
                        description="Our client needs a technician for "
                                    "packaging lines at Rotherham. Send your "
                                    "CV to claire@apexrecruit.co.uk")])
        prompts = []
        ai = scripted_ai()
        outcome = self.r.write_to(self.user["id"], "apex",
                                  ai=lambda p: prompts.append(p) or ai(p))
        self.assertEqual(outcome, "drafted")
        drafts = self.db.list_drafts(self.user["id"])
        self.assertEqual(drafts[0]["to_email"], "claire@apexrecruit.co.uk")
        self.assertEqual(drafts[0]["advertiser"], "agency")
        self.assertIn("RECRUITMENT AGENCY", prompts[-1])
        # A second press doesn't write a second letter.
        self.assertEqual(self.r.write_to(self.user["id"], "apex", ai=ai),
                         "drafted")
        self.assertEqual(self.r.status(self.user["id"], "Apex Recruitment"),
                         "drafted")
        self.assertEqual(len(self.db.list_drafts(self.user["id"])), 1)

    def test_no_published_address_means_no_letter(self):
        self.seed([item("Apex Recruitment", description="Apply online.")])
        from unittest.mock import patch
        with patch("jobseeker.pipeline.discover.find_domain",
                   return_value=None):
            self.assertEqual(self.r.write_to(self.user["id"], "apex",
                                             ai=scripted_ai()), "no_address")
        self.assertEqual(self.db.list_drafts(self.user["id"]), [])

    def test_a_blocked_agency_is_never_written_to(self):
        self.seed([item("Apex Recruitment",
                        description="CV to claire@apexrecruit.co.uk")])
        self.db.block_company(self.user["id"], "Apex Recruitment")
        self.assertEqual(self.r.write_to(self.user["id"], "apex",
                                         ai=scripted_ai()), "blocked")


if __name__ == "__main__":
    unittest.main()
