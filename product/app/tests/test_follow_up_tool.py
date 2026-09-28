"""The follow-up calculator: the product's own rule, given away.

  1. FIVE WORKING DAYS on, weekends skipped; stop after three weeks.
  2. A CLOSING DATE moves the clock to when applications are read.
  3. A PORTAL has nobody to chase, and says so, pointing at /find.
  4. THE PAGE is indexable, its answer has its own address, and a bad date
     is an error rather than a crash.
"""

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import AppTestCase  # noqa: E402


class TheRule(AppTestCase):
    def calc(self):
        from app import followup_calc
        return followup_calc

    def test_five_working_days_skipping_the_weekend(self):
        # Friday 25 September 2026 -> Friday 2 October.
        p = self.calc().plan(dt.date(2026, 9, 25), "email")
        self.assertEqual(p.follow_up_on, dt.date(2026, 10, 2))
        self.assertEqual(p.stop_after, dt.date(2026, 10, 16))

    def test_it_is_the_products_own_rule(self):
        from app import followups
        p = self.calc().plan(dt.date(2026, 9, 21), "email")
        self.assertEqual((p.stop_after - p.starts_from).days,
                         followups.WITHIN_DAYS)

    def test_a_closing_date_starts_the_clock(self):
        p = self.calc().plan(dt.date(2026, 9, 21), "closing",
                             dt.date(2026, 10, 2))
        self.assertEqual(p.starts_from, dt.date(2026, 10, 2))
        self.assertEqual(p.follow_up_on, dt.date(2026, 10, 9))

    def test_an_agency_is_chased_through_the_recruiter(self):
        self.assertIn("recruiter",
                      self.calc().plan(dt.date(2026, 9, 21), "agency").who)

    def test_a_portal_has_nobody_to_chase(self):
        p = self.calc().plan(dt.date(2026, 9, 21), "portal")
        self.assertIsNone(p.follow_up_on)


class ThePage(AppTestCase):
    def test_indexable_with_its_own_tags(self):
        page = self.client.get("/tools/follow-up").text
        self.assertIn("<title>When should I follow up on a job application?",
                      page)
        self.assertIn('og:title" content="When should I follow up', page)
        self.assertIn('href="/find"', page)
        self.assertIn("/tools/follow-up", self.client.get("/sitemap.xml").text)
        self.assertIn('href="/tools/follow-up"', self.client.get("/tools").text)

    def test_the_answer(self):
        page = self.client.get("/tools/follow-up?applied=2026-09-25&how=email"
                               "&role=Fitter").text
        self.assertIn("Follow up on Friday 2 October", page)
        self.assertIn("about the Fitter role", page)

    def test_a_portal_points_at_find(self):
        page = self.client.get("/tools/follow-up?applied=2026-09-25"
                               "&how=portal").text
        self.assertIn("nobody to follow up with", page)

    def test_a_bad_date_is_an_error_not_a_crash(self):
        r = self.client.get("/tools/follow-up?applied=yesterday&how=email")
        self.assertEqual(r.status_code, 200)
        self.assertIn("did not look right", r.text)

    def test_the_role_is_escaped(self):
        page = self.client.get("/tools/follow-up?applied=2026-09-25&how=email"
                               "&role=%3Cscript%3Ex%3C/script%3E").text
        self.assertNotIn("<script>x</script>", page)


if __name__ == "__main__":
    unittest.main()
