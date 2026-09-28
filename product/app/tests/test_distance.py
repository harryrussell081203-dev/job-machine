"""Distance on the job card, and jobs the boards leaked from too far away.

  1. A DRAFT CARRIES "N miles from you" when both ends can be found.
  2. A LISTING WELL PAST THE RADIUS is set aside before any model call.
  3. UNKNOWN IS SHOWN, NEVER HIDDEN: a job or a search area that cannot be
     found keeps the job.
  4. THE SWITCH turns it all off.
"""

import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from jobseeker import geo  # noqa: E402
from app.tests.test_runner import (RunnerTestCase, Resp, Session,  # noqa: E402
                                   adzuna_payload)

SHEFFIELD = {"latitude": 53.3811, "longitude": -1.4701, "country": "England"}
ROTHERHAM = {"latitude": 53.4326, "longitude": -1.3635, "country": "England"}


class GeoSession(Session):
    """The runner's fake boards, plus postcodes.io."""

    def __init__(self, places, **kw):
        super().__init__(**kw)
        self.places = places

    def get(self, url, **kw):
        if "postcodes.io" in url:
            self.urls.append(url)
            for name, answer in self.places.items():
                if f"q={name}" in url:
                    return Resp(payload={"result": [answer]})
            return Resp(payload={"result": []})
        return super().get(url, **kw)


class DistanceCase(RunnerTestCase):
    def setUp(self):
        super().setUp()
        geo._memory.clear()
        self.addCleanup(geo._memory.clear)
        patcher = patch.dict(os.environ, {"DISTANCE": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_with(self, places, **job):
        return self.run_once(session=GeoSession(
            places, adzuna=adzuna_payload(**job)))


class OnTheCard(DistanceCase):
    def test_the_draft_says_how_far(self):
        report = self.run_with({"Sheffield": SHEFFIELD,
                                "Rotherham": ROTHERHAM})
        self.assertEqual(report.drafted, 1, report.errors)
        d = self.db.list_drafts(self.user["id"])[0]
        self.assertEqual(d["distance_miles"], 6)

    def test_board_coordinates_need_no_lookup_for_the_job(self):
        self.run_with({"Sheffield": SHEFFIELD}, latitude=53.4326,
                      longitude=-1.3635, location={"display_name": "Nowhere"})
        d = self.db.list_drafts(self.user["id"])[0]
        self.assertEqual(d["distance_miles"], 6)


class TooFar(DistanceCase):
    def test_a_job_far_past_the_radius_costs_no_model_call(self):
        prompts = []
        report = self.run_once(
            session=GeoSession({"Sheffield": SHEFFIELD},
                               adzuna=adzuna_payload(latitude=57.15,
                                                     longitude=-2.09)),
            ai=lambda p: (prompts.append(p), "[]")[1])
        self.assertEqual(report.too_far, 1)
        self.assertEqual(prompts, [])

    def test_an_unknown_job_location_is_kept(self):
        report = self.run_with({"Sheffield": SHEFFIELD})
        self.assertEqual(report.too_far, 0)
        self.assertEqual(report.drafted, 1, report.errors)
        self.assertIsNone(
            self.db.list_drafts(self.user["id"])[0]["distance_miles"])

    def test_an_area_with_no_centre_filters_nothing(self):
        profile = dict(self.db.load_profile(self.user["id"]))
        profile["locations"] = ["Sheffield", "United Kingdom"]
        self.db.save_profile(self.user["id"], profile)
        report = self.run_once(session=GeoSession(
            {"Sheffield": SHEFFIELD},
            adzuna=adzuna_payload(latitude=57.15, longitude=-2.09)))
        self.assertEqual(report.too_far, 0)

    def test_switched_off_nothing_is_asked(self):
        session = GeoSession({"Sheffield": SHEFFIELD})
        with patch.dict(os.environ, {"DISTANCE": "0"}):
            self.run_once(session=session)
        self.assertFalse([u for u in session.urls if "postcodes.io" in u])


if __name__ == "__main__":
    unittest.main()
