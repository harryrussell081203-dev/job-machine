"""Whether anybody came back.

This is the one measurement the product had wrong rather than missing, which
is worse, because a wrong number is read and acted on. `last_seen_at` was
written only when somebody typed their email into the sign-in form, while
everything that read it - the admin page's "active this week", and every
judgement made from it - took it to mean "last used the app".

Those are different events. Somebody who asks for a sign-in link and never
clicks it has not come back, and was being counted as though they had. On a
product about to be shown to a few thousand people at once, the difference
between "they arrived" and "they stayed" is the only thing worth learning,
and it was unmeasurable.

So these tests pin the distinction in both directions: asking for a link is
not a visit, and using the app is.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_sending import Base  # noqa: E402


class TestComingBack(Base):
    def sign_in(self, email="harry@example.com"):
        """Follows the redirect, because a browser does.

        /auth/verify sets the cookie and redirects; it does not render, so it
        does not go through current_user. The visit is recorded when the page
        it lands on loads, which is the correct moment - a link fetched by a
        mail client's link-checker is not somebody using the app."""
        link = self.main.auth.make_login_link(email)
        token = link.split("token=", 1)[1]
        self.client.get(f"/auth/verify?token={token}")

    def seen(self, email="harry@example.com"):
        return self.db.get_user_by_email(email)["last_seen_at"]

    # -- a new account has not been used yet ---------------------------
    def test_a_brand_new_account_has_never_been_seen(self):
        """NULL rather than the signup time, so "signed up and never came
        back" is distinguishable from "came back at some point". With the
        two collapsed there is no query that separates them afterwards."""
        self.db.get_or_create_user("new@example.com")
        self.assertIsNone(self.seen("new@example.com"))

    def test_asking_for_a_sign_in_link_is_not_a_visit(self):
        """The whole bug, in one test. Requesting a link and never clicking
        it is the commonest thing an abandoning user does."""
        self.db.get_or_create_user("new@example.com")
        self.client.post("/login", data={"email": "new@example.com"})
        self.assertIsNone(self.seen("new@example.com"))

    # -- using it is -----------------------------------------------------
    def test_using_the_app_is_a_visit(self):
        self.sign_in()
        self.assertIsNotNone(self.seen())

    def test_it_moves_on_a_later_visit(self):
        self.sign_in()
        first = self.seen()
        self.assertIsNotNone(first)
        # Wound back far enough that the throttle cannot be what this
        # measures, then a page load, which should bring it forward again.
        stale = first - 10 * self.db.TOUCH_AFTER
        self.db.touch_user(self.uid, None, at=stale)
        self.assertEqual(self.seen(), stale)
        self.client.get("/dashboard", follow_redirects=False)
        self.assertGreater(self.seen(), stale)

    def test_an_anonymous_visitor_touches_nobody(self):
        before = self.seen()
        self.client.get("/find")
        self.client.get("/answers")
        self.assertEqual(self.seen(), before)

    # -- and it does not cost a write per request ------------------------
    def test_a_second_request_inside_the_window_writes_nothing(self):
        """The dashboard polls. Without the throttle this is a write every
        few seconds per signed-in user, on the busiest screen in the app."""
        self.sign_in()
        first = self.seen()
        self.db.touch_user(self.uid, first, at=first + self.db.TOUCH_AFTER - 1)
        self.assertEqual(self.seen(), first)

    def test_a_user_never_seen_before_is_touched_whatever_the_window(self):
        """NULL is not a recent timestamp, and reading it as one would leave
        the people this exists to count permanently unrecorded."""
        self.db.get_or_create_user("new@example.com")
        uid = self.db.get_user_by_email("new@example.com")["id"]
        self.db.touch_user(uid, None, at=1_700_000_000)
        self.assertEqual(self.seen("new@example.com"), 1_700_000_000)

    def test_a_request_past_the_window_writes(self):
        self.sign_in()
        first = self.seen()
        self.db.touch_user(self.uid, first, at=first + self.db.TOUCH_AFTER + 1)
        self.assertEqual(self.seen(), first + self.db.TOUCH_AFTER + 1)

    # -- and it is never worth a 500 -------------------------------------
    def test_a_broken_touch_raises_nothing(self):
        """It is a metric, and a metric that can take down somebody's
        dashboard is a worse trade than not having the metric at all."""
        def boom(*a, **kw):
            raise RuntimeError("the database is having a moment")

        original = self.db.connect
        self.db.connect = boom
        try:
            self.db.touch_user(self.uid, None)      # must not raise
        finally:
            self.db.connect = original

    # -- what the admin page then reports --------------------------------
    def test_active_this_week_counts_use_and_not_link_requests(self):
        """The number Harry actually reads. Two people: one who signed up and
        went away, one who came back. It has to say one."""
        import time
        from app import admin

        now = time.time()
        rows = [
            {"created_at": now - 3 * 86400, "last_seen_at": None},
            {"created_at": now - 3 * 86400, "last_seen_at": now - 3600},
        ]
        self.assertEqual(admin.summarise(rows, now=now)["active_7d"], 1)


if __name__ == "__main__":
    unittest.main()
