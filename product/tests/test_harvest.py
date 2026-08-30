"""Harvest: what reaches the queue, and what is binned before it costs anything.

No network. A fake session returns canned board responses, so these run
offline and assert on behaviour rather than on whether Adzuna is up.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.pipeline import harvest as h  # noqa: E402
from jobseeker.profile import Profile, Role  # noqa: E402

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def profile(**over):
    base = dict(
        name="Sam Doherty", location="Sheffield", phone="07700 900123",
        situation="unemployed", min_salary_annual=30000,
        priorities=["money"], target_roles=["maintenance technician"],
        locations=["Sheffield"], radius_miles=25,
        history=[Role(title="Tech", org="Acme", detail="fixed things")],
    )
    base.update(over)
    return Profile(**base)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Returns queued payloads in order; records every call."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append({"url": url, **kw})
        if not self.payloads:
            return FakeResponse({"results": []})
        nxt = self.payloads.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return FakeResponse(nxt)


def adzuna_job(**over):
    job = {"id": "1", "title": "Maintenance Technician",
           "company": {"display_name": "Pennine Foods"},
           "location": {"display_name": "Rotherham"},
           "redirect_url": "https://example.com/1",
           "description": "<p>Maintain the <b>packaging</b> lines.</p>",
           "salary_min": 38000, "salary_max": 42000,
           "created": (NOW - timedelta(hours=3)).isoformat()}
    job.update(over)
    return job


CREDS = h.Credentials(adzuna_app_id="a", adzuna_app_key="b", reed_api_key="c")


class TestFreshness(unittest.TestCase):
    def test_a_recent_listing_is_fresh(self):
        self.assertTrue(h.fresh_enough(NOW - timedelta(hours=5), "hours", now=NOW))

    def test_an_old_listing_is_not(self):
        self.assertFalse(h.fresh_enough(NOW - timedelta(hours=80), "hours", now=NOW))

    def test_no_date_is_allowed_through_for_the_scorer_to_judge(self):
        self.assertTrue(h.fresh_enough(None, "hours", now=NOW))

    def test_date_only_sources_need_the_whole_day_inside_the_window(self):
        # Reed gives a day, not a time. A job dated D could have gone up at
        # 00:00, so D only counts if the entire day is within the window.
        two_days = NOW - timedelta(days=2)
        self.assertFalse(h.fresh_enough(two_days, "date", now=NOW))
        self.assertTrue(h.fresh_enough(NOW - timedelta(days=1), "date", now=NOW))


class TestSearches(unittest.TestCase):
    def test_every_role_is_searched_in_every_location(self):
        p = profile(locations=["Sheffield", "Leeds"],
                    target_roles=["fitter", "welder"])
        got = list(h.searches(p))
        self.assertEqual(len(got), 4)
        self.assertIn(("Sheffield", 25, "fitter"), got)
        self.assertIn(("Leeds", 25, "welder"), got)

    def test_wanting_travel_adds_a_national_sweep(self):
        # Field service work is advertised against a site, not a town, so a
        # radius search around one city never sees it.
        p = profile(wants_travel=True, priorities=["money", "travel"])
        national = [s for s in h.searches(p) if s[0] == "UK"]
        self.assertTrue(national)
        self.assertTrue(any("commissioning" in s[2] for s in national))

    def test_no_national_sweep_when_neither_is_wanted(self):
        self.assertFalse([s for s in h.searches(profile()) if s[0] == "UK"])


class TestExclusions(unittest.TestCase):
    def test_course_adverts_are_binned_for_everyone(self):
        listing = h.Listing(
            external_id="x", source="adzuna", title="Trainee Engineer",
            company="Skills Co", location="Leeds", url="",
            description="Job guarantee on completion. Course fee applies.")
        self.assertIn("training course", h.not_worth_applying(listing))

    def test_the_users_own_exclusions_are_applied(self):
        listing = h.Listing(external_id="x", source="adzuna",
                            title="Sales Executive", company="Acme",
                            location="Leeds", url="", description="")
        self.assertIsNone(h.not_worth_applying(listing))
        self.assertIn("sales executive",
                      h.not_worth_applying(listing, ["sales executive"]))

    def test_nothing_trade_specific_is_excluded_by_default(self):
        # The original binned "sales executive" and "care assistant". That is
        # right for one man and wrong for anyone who does either for a living.
        for title in ("Sales Executive", "Care Assistant", "HGV Driver",
                      "Chef de Partie", "Head of Engineering"):
            listing = h.Listing(external_id="x", source="adzuna", title=title,
                                company="Acme", location="Leeds", url="",
                                description="")
            self.assertIsNone(h.not_worth_applying(listing), title)

    def test_a_listing_with_no_employer_is_useless(self):
        listing = h.Listing(external_id="x", source="adzuna", title="Fitter",
                            company="  ", location="Leeds", url="",
                            description="")
        self.assertIn("no employer", h.not_worth_applying(listing))


class TestAdzuna(unittest.TestCase):
    def test_a_listing_is_mapped_and_its_html_stripped(self):
        s = FakeSession([{"results": [adzuna_job()]}])
        got = h.adzuna(profile(), CREDS, session=s)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].company, "Pennine Foods")
        self.assertEqual(got[0].description, "Maintain the packaging lines.")
        self.assertEqual(got[0].external_id, "adzuna_1")

    def test_stale_listings_are_dropped_at_the_source(self):
        old = adzuna_job(created=(datetime.now(timezone.utc)
                                  - timedelta(days=9)).isoformat())
        s = FakeSession([{"results": [old]}])
        self.assertEqual(h.adzuna(profile(), CREDS, session=s), [])

    def test_no_credentials_means_no_calls_rather_than_a_crash(self):
        s = FakeSession([{"results": [adzuna_job()]}])
        self.assertEqual(h.adzuna(profile(), h.Credentials(), session=s), [])
        self.assertEqual(s.calls, [])

    def test_one_failed_sweep_does_not_lose_the_others(self):
        p = profile(locations=["Sheffield", "Leeds"])
        s = FakeSession([RuntimeError("boom"), {"results": [adzuna_job(id="2")]}])
        got = h.adzuna(p, CREDS, session=s)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].external_id, "adzuna_2")


class TestReed(unittest.TestCase):
    def test_a_reed_listing_is_mapped(self):
        payload = {"results": [{
            "jobId": 77, "jobTitle": "Shift Engineer",
            "employerName": "Aldwarke Steel", "locationName": "Rotherham",
            "jobUrl": "https://example.com/77",
            "jobDescription": "<p>Nights.</p>",
            "minimumSalary": 39500, "maximumSalary": 41000,
            "date": datetime.now(timezone.utc).strftime("%d/%m/%Y")}]}
        got = h.reed(profile(), CREDS, session=FakeSession([payload]))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].external_id, "reed_77")
        self.assertEqual(got[0].description, "Nights.")

    def test_reed_dates_parse_in_uk_order(self):
        self.assertEqual(h.reed_date("05/03/2026").month, 3)


class TestHarvestRun(unittest.TestCase):
    def test_the_same_job_on_both_boards_is_collapsed(self):
        a = {"results": [adzuna_job()]}
        r = {"results": [{"jobId": 9, "jobTitle": "Maintenance Technician",
                          "employerName": "Pennine Foods Ltd",
                          "locationName": "Rotherham", "jobUrl": "",
                          "jobDescription": "same job",
                          "date": datetime.now(timezone.utc).strftime("%d/%m/%Y")}]}
        out = h.harvest(profile(), CREDS, session=FakeSession([a, r]))
        self.assertEqual(len(out["keep"]), 1)

    def test_already_known_listings_are_skipped(self):
        s = FakeSession([{"results": [adzuna_job()]}])
        out = h.harvest(profile(), CREDS, session=s, known_ids=["adzuna_1"])
        self.assertEqual(out["keep"], [])

    def test_dropped_listings_are_returned_with_a_reason(self):
        # Silence is the worst answer to "why did nothing come through?".
        job = adzuna_job(title="Trainee Fitter",
                         description="Course fee applies, job guarantee.")
        out = h.harvest(profile(), CREDS, session=FakeSession([{"results": [job]}]))
        self.assertEqual(out["keep"], [])
        self.assertEqual(len(out["dropped"]), 1)
        self.assertIn("training course", out["dropped"][0].skipped)

    def test_freshest_first(self):
        old = adzuna_job(id="old", title="Older Role",
                         created=(datetime.now(timezone.utc)
                                  - timedelta(hours=30)).isoformat())
        new = adzuna_job(id="new", title="Newer Role",
                         created=(datetime.now(timezone.utc)
                                  - timedelta(hours=1)).isoformat())
        out = h.harvest(profile(), CREDS,
                        session=FakeSession([{"results": [old, new]}]))
        self.assertEqual([l.title for l in out["keep"]],
                         ["Newer Role", "Older Role"])

    def test_no_credentials_at_all_is_an_empty_run_not_an_error(self):
        out = h.harvest(profile(), h.Credentials(), session=FakeSession([]))
        self.assertEqual(out, {"keep": [], "dropped": []})


class TestCompanyKey(unittest.TestCase):
    def test_suffixes_and_punctuation_collapse(self):
        for a, b in (("Kestrel Foods Ltd", "Kestrel Foods"),
                     ("ACME Limited", "acme"),
                     ("Pennine Group", "pennine")):
            self.assertEqual(h.company_key(a), h.company_key(b), f"{a} vs {b}")


if __name__ == "__main__":
    unittest.main()
