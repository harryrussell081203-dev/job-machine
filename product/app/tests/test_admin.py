"""Tests for /admin.

Two things are worth testing here and they are not the same thing:

  - the gate, because this page lists every customer by email, and a page
    like that is only ever one wrong condition away from being public
  - the arithmetic, because a funnel that reads wrong is worse than no
    funnel: it is a wrong number that somebody makes a decision on
"""

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)          # so the shared harness imports either way

from test_app import AppTestCase, build_app  # noqa: E402

from app import admin  # noqa: E402

DAY = 86400


class TheGate(AppTestCase):
    env = {"ADMIN_EMAILS": "boss@example.com"}

    def test_an_admin_gets_in(self):
        self.sign_in("boss@example.com")
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("How far people get", r.text)

    def test_a_signed_in_stranger_gets_a_404_not_a_403(self):
        # 403 would confirm the page exists. There is nothing to gain from
        # telling somebody that.
        self.sign_in("stranger@example.com")
        self.assertEqual(self.client.get("/admin").status_code, 404)

    def test_a_signed_out_visitor_gets_the_same_404(self):
        self.assertEqual(self.client.get("/admin").status_code, 404)

    def test_the_admin_link_is_only_drawn_for_admins(self):
        self.sign_in("stranger@example.com")
        self.assertNotIn('href="/admin"', self.client.get("/dashboard").text)
        self.client.post("/logout")
        self.sign_in("boss@example.com")
        self.assertIn('href="/admin"', self.client.get("/dashboard").text)

    def test_matching_is_not_case_sensitive(self):
        self.sign_in("BOSS@Example.com")
        self.assertEqual(self.client.get("/admin").status_code, 200)


class AnEmptyAdminList(AppTestCase):
    """It must fail closed: no ADMIN_EMAILS means nobody, not everybody."""
    env = {"ADMIN_EMAILS": ""}

    def test_nobody_is_an_admin(self):
        self.sign_in("boss@example.com")
        self.assertEqual(self.client.get("/admin").status_code, 404)

    def test_an_empty_setting_is_an_empty_set(self):
        # The bug this guards: "".split(",") is [""], and a set containing the
        # empty string is a set that a blank email matches.
        main, path = build_app(ADMIN_EMAILS="")
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        self.assertEqual(main.config.ADMIN_EMAILS, frozenset())
        self.assertFalse(main.config.is_admin(""))


class TheAdminPageShowsRealNumbers(AppTestCase):
    env = {"ADMIN_EMAILS": "boss@example.com"}

    def test_it_counts_what_people_actually_did(self):
        self.sign_in("boss@example.com")
        someone = self.main.db.get_or_create_user("sam@example.com")
        self.main.db.add_draft(someone["id"], job_title="Electrician",
                               company="Acme", to_email="hire@acme.com",
                               subject="s", body="b")
        rows = self.main.db.overview()
        self.assertEqual(len(rows), 2)
        sam = [r for r in rows if r["email"] == "sam@example.com"][0]
        self.assertEqual(sam["drafts"], 1)
        self.assertEqual(sam["sent"], 0)
        self.assertIn("sam@example.com", self.client.get("/admin").text)


class TheFunnel(unittest.TestCase):

    def rows(self, *specs):
        """Each spec is the set of things that person has done."""
        out = []
        for i, done in enumerate(specs):
            out.append({"id": i, "email": f"{i}@x.com", "created_at": 0,
                        "last_seen_at": 0, "subscription_status": "none",
                        "cv_at": 1 if "cv" in done else None,
                        "profile_at": 1 if "profile" in done else None,
                        "mail_at": 1 if "mail" in done else None,
                        "drafts": 1 if "draft" in done else 0,
                        "sent": 1 if "sent" in done else 0,
                        "discarded": 0})
        return out

    def test_it_never_goes_up_in_the_middle(self):
        """The regression this exists for.

        Somebody who filled the profile in by hand has a profile and no CV.
        Counting each stage on its own puts 1 at "Finished setup" and 0 at
        "Uploaded a CV" just above it, which reads as a broken page.
        """
        counts = [s["count"] for s in admin.funnel(self.rows({"profile"}))]
        self.assertEqual(counts, sorted(counts, reverse=True), counts)
        self.assertEqual(counts[1], 1, "the CV stage must include those past it")

    def test_everybody_reaches_the_first_stage(self):
        f = admin.funnel(self.rows(set(), {"cv"}, {"cv", "profile", "draft"}))
        self.assertEqual(f[0]["count"], 3)
        self.assertEqual(f[0]["percent"], 100)

    def test_a_later_stage_counts_fewer(self):
        f = admin.funnel(self.rows(set(), {"cv"}, {"cv", "profile", "sent"}))
        by_name = {s["name"]: s["count"] for s in f}
        self.assertEqual(by_name["Signed up"], 3)
        self.assertEqual(by_name["Uploaded a CV"], 2)
        self.assertEqual(by_name["Sent one"], 1)

    def test_no_users_does_not_divide_by_zero(self):
        for stage in admin.funnel([]):
            self.assertEqual(stage["percent"], 0)


class TheSummary(unittest.TestCase):
    now = 1_700_000_000

    def person(self, **kw):
        base = {"email": "a@x.com", "created_at": self.now, "last_seen_at": None,
                "subscription_status": "none", "cv_at": None, "profile_at": None,
                "mail_at": None, "drafts": 0, "sent": 0, "discarded": 0}
        base.update(kw)
        return base

    def test_send_rate_ignores_letters_nobody_has_decided_about_yet(self):
        # 1 sent, 1 discarded, 8 still waiting. That is 50%, not 10% - a queue
        # full of fresh drafts is not a pile of rejections.
        s = admin.summarise([self.person(drafts=10, sent=1, discarded=1)],
                            now=self.now)
        self.assertEqual(s["send_rate"], 50)

    def test_send_rate_is_none_rather_than_zero_when_nothing_is_decided(self):
        s = admin.summarise([self.person(drafts=5)], now=self.now)
        self.assertIsNone(s["send_rate"], "0% would read as everyone refusing")

    def test_stalled_needs_two_days_before_it_complains(self):
        fresh = self.person(created_at=self.now - 3600)
        old = self.person(email="b@x.com", created_at=self.now - 5 * DAY)
        done = self.person(email="c@x.com", created_at=self.now - 5 * DAY,
                           profile_at=self.now)
        s = admin.summarise([fresh, old, done], now=self.now)
        self.assertEqual([r["email"] for r in s["stalled"]], ["b@x.com"])

    def test_active_counts_use_last_seen(self):
        s = admin.summarise([
            self.person(last_seen_at=self.now - 2 * DAY),
            self.person(email="b@x.com", last_seen_at=self.now - 20 * DAY),
            self.person(email="c@x.com", last_seen_at=None),
        ], now=self.now)
        self.assertEqual(s["active_7d"], 1)
        self.assertEqual(s["active_30d"], 2)


class WeeklySignups(unittest.TestCase):
    now = 1_700_000_000

    def test_the_newest_week_is_last(self):
        rows = [{"created_at": self.now},
                {"created_at": self.now - 8 * DAY}]
        weeks = admin.signups_by_week(rows, weeks=8, now=self.now)
        self.assertEqual(weeks[-1]["weeks_ago"], 0)
        self.assertEqual(weeks[-1]["count"], 1)
        self.assertEqual(weeks[-2]["count"], 1)

    def test_anything_older_than_the_window_is_dropped_not_piled_on_the_end(self):
        weeks = admin.signups_by_week([{"created_at": self.now - 400 * DAY}],
                                      weeks=8, now=self.now)
        self.assertEqual(sum(w["count"] for w in weeks), 0)

    def test_an_empty_week_does_not_divide_by_zero(self):
        for w in admin.signups_by_week([], weeks=8, now=self.now):
            self.assertEqual(w["height"], 0)


class SayingWhenSomethingHappened(unittest.TestCase):
    now = 1_700_000_000

    def test_it_reads_like_a_person_talking(self):
        for delta, expected in [(None, "never"), (60, "just now"),
                                (1800, "30m ago"), (7200, "2h ago"),
                                (DAY, "yesterday"), (3 * DAY, "3d ago"),
                                (21 * DAY, "3w ago"), (90 * DAY, "3mo ago")]:
            when = None if delta is None else self.now - delta
            self.assertEqual(admin.ago(when, now=self.now), expected,
                             f"{delta} seconds ago")


if __name__ == "__main__":
    unittest.main()
