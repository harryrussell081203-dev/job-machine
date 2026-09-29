"""No letter to an employer on a bank holiday, in the right part of the UK.

  1. THE DIVISION follows where the person lives: St Andrew's Day holds a
     Scottish user's letters and not an English one's.
  2. HELD, NOT DROPPED: the draft stays due and sends on the next working day.
  3. FETCHED ONCE A DAY, and a GOV.UK outage never stops sending.
  4. FOLLOW-UPS are held too.
"""

import datetime as dt
import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from jobseeker import geo  # noqa: E402
from test_sending import Base  # noqa: E402

GOVUK = {
    "england-and-wales": {"events": [{"date": "2026-12-25"},
                                     {"date": "2026-08-31"}]},
    "scotland": {"events": [{"date": "2026-12-25"},
                            {"date": "2026-11-30"}]},
    "northern-ireland": {"events": [{"date": "2026-12-25"}]},
}
ST_ANDREWS = dt.datetime(2026, 11, 30, 10, 0, tzinfo=dt.timezone.utc)
ORDINARY = dt.datetime(2026, 12, 1, 10, 0, tzinfo=dt.timezone.utc)


class Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


class Case(Base):
    def setUp(self):
        super().setUp()
        import importlib
        self.holidays = importlib.reload(importlib.import_module("app.holidays"))
        geo._memory.clear()
        self.addCleanup(geo._memory.clear)
        patcher = patch.dict(os.environ, {"BANK_HOLIDAYS": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.asked = []

    def lives_in(self, country):
        geo._memory["geo:place:HOME"] = {"lat": 0, "lon": 0,
                                         "country": country}
        self.db.save_profile(self.uid, {"name": "Sam", "location": "Home"})

    def govuk(self, status=200):
        def get(url, timeout):
            self.asked.append(url)
            if isinstance(status, Exception):
                raise status
            return Resp(status, GOVUK)
        return get


class TheDivision(Case):
    def test_st_andrews_day_holds_a_scottish_user(self):
        self.lives_in("Scotland")
        self.assertEqual(self.holidays.holiday_today(
            self.uid, now=ST_ANDREWS, get=self.govuk()), "Scotland")

    def test_but_not_an_english_one(self):
        self.lives_in("England")
        self.assertEqual(self.holidays.holiday_today(
            self.uid, now=ST_ANDREWS, get=self.govuk()), "")

    def test_unknown_home_uses_the_fallback(self):
        self.db.save_profile(self.uid, {"name": "Sam", "location": ""})
        with patch.dict(os.environ, {"BANK_HOLIDAY_DIVISION":
                                     "england-and-wales"}):
            self.assertEqual(self.holidays.division_for(self.uid),
                             "england-and-wales")
        self.assertEqual(self.holidays.division_for(self.uid), "scotland")


class FetchedOnceADay(Case):
    def test_asked_once_then_cached(self):
        self.lives_in("Scotland")
        for _ in range(3):
            self.holidays.holiday_today(self.uid, now=ORDINARY,
                                        get=self.govuk())
        self.assertEqual(len(self.asked), 1)

    def test_an_outage_with_nothing_cached_never_blocks(self):
        self.lives_in("Scotland")
        self.assertEqual(self.holidays.holiday_today(
            self.uid, now=ST_ANDREWS, get=self.govuk(OSError("down"))), "")

    def test_an_outage_uses_yesterdays_copy(self):
        self.lives_in("Scotland")
        yesterday = ST_ANDREWS - dt.timedelta(days=1)
        self.holidays.holiday_today(self.uid, now=yesterday, get=self.govuk())
        self.assertEqual(self.holidays.holiday_today(
            self.uid, now=ST_ANDREWS, get=self.govuk(500)), "Scotland")


class HeldNotDropped(Case):
    def setUp(self):
        super().setUp()
        self.lives_in("Scotland")
        self.db.set_meta("bank_holidays", __import__("json").dumps(
            {"fetched": "2026-11-30", "data": GOVUK}))
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)

    def send_at(self, when):
        return self.autosend.send_due_for_user(
            self.uid, sender=self.fake_send, now=int(when.timestamp()),
            verifier=lambda **k: None)

    def test_nothing_goes_on_the_holiday_and_it_goes_the_next_day(self):
        did = self.draft("Acme Ltd")
        report = self.send_at(ST_ANDREWS)
        self.assertEqual(self.sent, [])
        self.assertIn("bank holiday in Scotland", report.reason)
        self.assertEqual(self.db.get_draft(self.uid, did)["status"], "draft")
        self.db.set_meta("bank_holidays", __import__("json").dumps(
            {"fetched": "2026-12-01", "data": GOVUK}))
        self.send_at(ORDINARY)
        self.assertEqual(len(self.sent), 1)

    def test_switched_off_it_sends(self):
        self.draft("Acme Ltd")
        with patch.dict(os.environ, {"BANK_HOLIDAYS": "0"}):
            self.send_at(ST_ANDREWS)
        self.assertEqual(len(self.sent), 1)

    def test_follow_ups_are_held_too(self):
        from app import followups
        self.db.save_send_settings(self.uid, follow_up=1)
        report = followups.send_for_user(self.uid,
                                         now=int(ST_ANDREWS.timestamp()),
                                         sender=self.fake_send)
        self.assertIn("bank holiday", report.reason)


if __name__ == "__main__":
    unittest.main()
