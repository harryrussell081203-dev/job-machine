"""Attribution, the funnel, and the referral reward.

Three properties, and they fail in different directions.

  1. ATTRIBUTION IS FIRST TOUCH AND SURVIVES THE INBOX. Signing in here is a
     link in an email, so the request that creates the account carries no
     campaign and no referrer. Read it there and every user is "direct",
     including the ones a channel worked for.

  2. THE FUNNEL COUNTS FIRSTS, ONCE. Every rate in the report has an event
     count as its numerator, so one duplicated row is an activation rate
     above 100% - a number that gets explained away rather than investigated.

  3. THE REWARD IS PAID ONCE AND NEVER SHORTENS ANYBODY. It is free access,
     which is real money, and the failure that costs most is paying twice.
"""

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import AppTestCase, build_app  # noqa: E402


class TestAttribution(unittest.TestCase):
    """The pure part, with no database in the way."""

    def setUp(self):
        build_app()
        from app import attribution
        self.a = attribution

    def fake(self, query=None, cookies=None):
        class R:
            query_params = query or {}
            cookies = {}
        r = R()
        r.cookies = cookies or {}
        return r

    def test_a_campaign_in_the_url_is_read(self):
        got = self.a.from_query({"utm_source": "tiktok",
                                 "utm_campaign": "no-replies"})
        self.assertEqual(got["utm_source"], "tiktok")
        self.assertEqual(got["utm_campaign"], "no-replies")

    def test_a_url_with_no_campaign_reads_as_nothing_rather_than_blank(self):
        """The difference matters: an empty dict lets a later page set a real
        source, and a dict of empty strings would lock in 'direct'."""
        self.assertEqual(self.a.from_query({"q": "welder"}), {})

    def test_case_is_folded_so_one_channel_is_one_row(self):
        self.assertEqual(self.a.from_query({"utm_source": "TikTok"}),
                         {"utm_source": "tiktok", "utm_campaign": "", "ref": ""})

    def test_markup_cannot_survive_into_the_report(self):
        """These arrive in a URL anybody can write and end up in a database,
        an admin screen and a CSV. Escaping in the template is the defence
        that stops being true the moment somebody prints it somewhere else."""
        got = self.a.from_query({"utm_source": "<script>alert(1)</script>"})
        self.assertNotIn("<", got["utm_source"])
        self.assertNotIn(">", got["utm_source"])

    def test_a_long_value_is_cut(self):
        got = self.a.from_query({"utm_campaign": "a" * 500})
        self.assertLessEqual(len(got["utm_campaign"]), self.a.MAX)

    def test_a_referral_is_named_as_one_rather_than_a_campaign(self):
        """'users we were given by other users' is the number the referral
        programme lives on, and it must not be able to hide inside a
        campaign name."""
        self.assertEqual(self.a.describe({"ref": "abc1234"}), "referral")
        self.assertEqual(self.a.describe({"utm_source": "tiktok"}), "tiktok")
        self.assertEqual(self.a.describe({}), "direct")

    def test_a_corrupt_cookie_reads_as_absent_not_as_empty(self):
        self.assertEqual(self.a.read(self.fake(cookies={"src": "{not json"})), {})


class TestFirstTouchSurvivesTheInbox(AppTestCase):
    def test_a_campaign_seen_on_the_landing_page_lands_on_the_account(self):
        """The whole reason attribution.py exists. The account is created by
        a request from a mail client, which carries nothing."""
        self.client.get("/?utm_source=tiktok&utm_campaign=no-replies")
        self.sign_in("ash@example.com")
        user = self.main.db.get_user_by_email("ash@example.com")
        self.assertEqual(user["utm_source"], "tiktok")
        self.assertEqual(user["utm_campaign"], "no-replies")

    def test_the_landing_page_is_recorded_too(self):
        self.client.get("/answers/why-no-reply?utm_source=google")
        self.sign_in("bo@example.com")
        user = self.main.db.get_user_by_email("bo@example.com")
        self.assertEqual(user["landing_path"], "/answers/why-no-reply")

    def test_the_first_source_wins_not_the_last(self):
        """Somebody arrives from a video, reads for a week, comes back through
        a search and signs up. The video is what got them; crediting the
        search would quietly say stop making videos."""
        self.client.get("/?utm_source=tiktok")
        self.client.get("/?utm_source=google")
        self.sign_in("cal@example.com")
        user = self.main.db.get_user_by_email("cal@example.com")
        self.assertEqual(user["utm_source"], "tiktok")

    def test_signing_in_again_does_not_move_a_user_between_channels(self):
        """A report that has already been read must not change next week."""
        self.client.get("/?utm_source=tiktok")
        self.sign_in("dee@example.com")
        self.client.cookies.clear()
        self.client.get("/?utm_source=reddit")
        self.sign_in("dee@example.com")
        user = self.main.db.get_user_by_email("dee@example.com")
        self.assertEqual(user["utm_source"], "tiktok")

    def test_somebody_who_arrives_with_no_campaign_is_direct(self):
        self.sign_in("eli@example.com")
        user = self.main.db.get_user_by_email("eli@example.com")
        self.assertEqual(user["utm_source"], "")
        rows = self.main.db.users_by_source()
        self.assertIn("direct", [r["source"] for r in rows])

    def test_signing_up_is_recorded_as_an_event(self):
        self.sign_in("fay@example.com")
        user = self.main.db.get_user_by_email("fay@example.com")
        self.assertIsNotNone(
            self.main.db.first_event_at(user["id"], "signed_up"))


class TestTheFunnelCountsFirstsOnce(AppTestCase):
    def user(self, email="gus@example.com"):
        self.sign_in(email)
        return self.main.db.get_user_by_email(email)

    def test_the_same_stage_twice_is_one_row(self):
        u = self.user()
        self.assertTrue(self.main.db.record_event(u["id"], "first_email_sent"))
        self.assertFalse(self.main.db.record_event(u["id"], "first_email_sent"))
        self.assertEqual(self.main.db.event_counts()["first_email_sent"], 1)

    def test_an_unknown_stage_raises_rather_than_inserting_quietly(self):
        """A typo'd event name inserts cleanly and the stage it was meant to
        record reads zero for ever."""
        u = self.user()
        with self.assertRaises(ValueError):
            self.main.db.record_event(u["id"], "frist_email_sent")

    def test_a_referral_can_happen_many_times_to_one_account(self):
        """The one kind that is not once-ever, which is why events carry a
        ref column at all."""
        u = self.user()
        self.assertTrue(self.main.db.record_event(
            u["id"], self.main.db.REFERRED_USER, ref="9:onboarded"))
        self.assertTrue(self.main.db.record_event(
            u["id"], self.main.db.REFERRED_USER, ref="10:onboarded"))
        self.assertFalse(self.main.db.record_event(
            u["id"], self.main.db.REFERRED_USER, ref="9:onboarded"))

    def test_the_funnel_stages_are_in_order(self):
        """The order IS the report: an activation rate is two adjacent stages
        divided by each other."""
        self.assertEqual(self.main.db.FUNNEL[0], "signed_up")
        self.assertEqual(self.main.db.FUNNEL[-1], "first_interview")
        self.assertNotIn(self.main.db.REFERRED_USER, self.main.db.FUNNEL)

    def test_a_failed_write_never_reaches_the_caller(self):
        """funnel.reached is called mid-upload and mid-send. A gap in a
        report is acceptable; a letter not going out is not."""
        from app import funnel
        u = self.user()
        original = self.main.db.record_event
        self.main.db.record_event = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("database is gone"))
        try:
            self.assertFalse(funnel.reached(u["id"], "onboarded"))
        finally:
            self.main.db.record_event = original


class TestTheReferralReward(AppTestCase):
    def two_users(self):
        self.sign_in("hal@example.com")
        self.client.cookies.clear()
        referrer = self.main.db.get_user_by_email("hal@example.com")
        code = self.main.referrals.code_for(referrer["id"])
        self.client.get(f"/?ref={code}")
        self.sign_in("ivy@example.com")
        return referrer, self.main.db.get_user_by_email("ivy@example.com")

    def test_arriving_on_a_link_records_who_referred_you(self):
        referrer, referred = self.two_users()
        self.assertEqual(referred["referred_by"], referrer["id"])

    def test_a_signup_alone_pays_nothing(self):
        """The one step somebody can manufacture in bulk from one keyboard."""
        referrer, _ = self.two_users()
        self.assertEqual(
            self.main.db.referral_stats(referrer["id"])["months"], 0)

    def test_uploading_a_cv_pays_the_referrer_a_month(self):
        from app import funnel, referrals
        referrer, referred = self.two_users()
        before = self.main.db.get_user(referrer["id"])["paid_until"] or 0
        funnel.reached(referred["id"], "cv_uploaded")
        after = self.main.db.get_user(referrer["id"])["paid_until"]
        self.assertGreaterEqual(after - max(before, self.main.db.now()),
                                referrals.MONTH - 5)
        self.assertEqual(
            self.main.db.referral_stats(referrer["id"])["months"], 1)

    def test_sending_a_first_letter_pays_a_second_month(self):
        from app import funnel
        referrer, referred = self.two_users()
        funnel.reached(referred["id"], "cv_uploaded")
        funnel.reached(referred["id"], "first_auto_send")
        self.assertEqual(
            self.main.db.referral_stats(referrer["id"])["months"], 2)

    def test_the_same_step_never_pays_twice(self):
        """The failure that costs real money. Ordered so the ledger is
        written before the payment, which makes a retry a no-op rather than
        a second month."""
        from app import funnel
        referrer, referred = self.two_users()
        funnel.reached(referred["id"], "cv_uploaded")
        paid = self.main.db.get_user(referrer["id"])["paid_until"]
        funnel.reached(referred["id"], "cv_uploaded")
        funnel.reached(referred["id"], "cv_uploaded")
        self.assertEqual(self.main.db.get_user(referrer["id"])["paid_until"],
                         paid)

    def test_a_reward_never_shortens_a_paying_subscriber(self):
        """Setting paid_until to now+30d would turn a reward into a
        punishment for the only people already giving us money."""
        from app import funnel, referrals
        referrer, referred = self.two_users()
        far = self.main.db.now() + 365 * 24 * 3600
        self.main.db.set_billing(referrer["id"], paid_until=far)
        funnel.reached(referred["id"], "cv_uploaded")
        self.assertEqual(self.main.db.get_user(referrer["id"])["paid_until"],
                         far + referrals.MONTH)

    def test_referring_yourself_earns_nothing(self):
        self.sign_in("jo@example.com")
        user = self.main.db.get_user_by_email("jo@example.com")
        code = self.main.referrals.code_for(user["id"])
        self.assertFalse(self.main.referrals.credit_signup(user["id"], code))

    def test_a_second_link_does_not_move_an_account_to_a_new_referrer(self):
        """The first reward may already have been paid on it, and the second
        referrer would be paid for the same person."""
        referrer, referred = self.two_users()
        self.sign_in("kit@example.com")
        other = self.main.db.get_user_by_email("kit@example.com")
        other_code = self.main.referrals.code_for(other["id"])
        self.main.referrals.credit_signup(referred["id"], other_code)
        self.assertEqual(
            self.main.db.get_user(referred["id"])["referred_by"],
            referrer["id"])

    def test_an_unknown_code_is_ignored_rather_than_failing_the_signup(self):
        self.client.get("/?ref=notacode")
        self.sign_in("lou@example.com")
        user = self.main.db.get_user_by_email("lou@example.com")
        self.assertIsNone(user["referred_by"])

    def test_steps_a_thumb_can_fake_pay_nothing(self):
        """The funnel counts a six-question form as onboarded and a "mark as
        sent" tap as a send, which is right for measuring. Paying for either
        would make a free month a form and a button away."""
        from app import funnel
        referrer, referred = self.two_users()
        funnel.reached(referred["id"], "onboarded")
        funnel.reached(referred["id"], "first_email_sent")
        self.assertEqual(
            self.main.db.referral_stats(referrer["id"])["months"], 0)

    def test_a_code_is_stable_once_issued(self):
        self.sign_in("mo@example.com")
        user = self.main.db.get_user_by_email("mo@example.com")
        first = self.main.referrals.code_for(user["id"])
        self.assertEqual(self.main.referrals.code_for(user["id"]), first)

    def test_codes_avoid_the_characters_people_get_wrong(self):
        """A referral code is meant to be sayable out loud."""
        for _ in range(50):
            code = self.main.referrals.make_code()
            self.assertFalse(set(code) & set("01loi"), code)

    def test_the_cap_stops_paying_but_still_records_what_happened(self):
        """The event is the history; the cap is a decision about what we pay
        for it. Rolling back the record would hide that it was ever hit."""
        from app import referrals
        referrer, referred = self.two_users()
        for i in range(referrals.MAX_MONTHS + 2):
            self.main.db.record_event(referrer["id"], self.main.db.REFERRED_USER,
                                      ref=f"filler{i}:onboarded")
        before = self.main.db.get_user(referrer["id"])["paid_until"] or 0
        self.assertEqual(referrals.reward_for(referred["id"], "cv_uploaded"), 0)
        self.assertEqual(self.main.db.get_user(referrer["id"])["paid_until"] or 0,
                         before)


class TestTheMetricsReport(AppTestCase):
    def test_rates_say_so_when_there_is_nothing_to_divide_by(self):
        """0% and 'no data yet' are different statements, and printing the
        first for the second says the channel does not convert when nobody
        has been through it."""
        from tools import metrics
        self.assertEqual(metrics.pct(0, 0).strip(), "-")
        self.assertEqual(metrics.pct(0, 10).strip(), "0%")

    def test_retention_excludes_accounts_too_young_to_have_returned(self):
        """Counting them makes retention fall every time signups rise, which
        reads as growth breaking the product."""
        from tools import metrics
        self.sign_in("nan@example.com")
        self.assertEqual(metrics.retention(7)["eligible"], 0)

    def test_the_funnel_counts_signups_from_users_not_events(self):
        """Events only start when this code ships. Every account older than
        that would otherwise read as never having signed up, and every rate
        below it would be divided by the wrong number."""
        from tools import metrics
        with self.main.db.connect() as c:
            c.execute("INSERT INTO users (email, created_at) VALUES (?, ?)",
                      ("old@example.com", int(time.time()) - 90 * 24 * 3600))
        stages = {r["stage"]: r["users"] for r in metrics.funnel()}
        self.assertEqual(stages["signed_up"], 1)

    def test_the_report_prints_without_a_single_user(self):
        """The first time anybody runs this, there is nothing in it."""
        from tools import metrics
        text = metrics.report(metrics.collect())
        self.assertIn("THE FUNNEL", text)
        self.assertIn("nobody yet", text)


if __name__ == "__main__":
    unittest.main()
