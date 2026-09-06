"""Route and paywall tests.

The two that matter most are the ones about money and identity:

  - a magic link must not work twice
  - a user must not become paid without a *verified* Stripe webhook

Both are the kind of bug that is invisible until it is expensive.
"""

import hashlib
import hmac
import importlib
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def build_app(**env):
    """A fresh app with a fresh database, configured per test."""
    defaults = {
        "DEV_MODE": "1",
        "SECRET_KEY": "test-secret-key-not-for-production",
        "BILLING_ENABLED": "0",
        "STRIPE_SECRET_KEY": "",
        "STRIPE_PRICE_ID": "",
        "STRIPE_WEBHOOK_SECRET": "",
        "FREE_ACCESS_EMAILS": "",
        "ADMIN_EMAILS": "",
        "FREE_SPOTS": "0",
        "BASE_URL": "http://testserver",
    }
    defaults.update(env)
    for k, v in defaults.items():
        os.environ[k] = v

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path

    for mod in ("app.config", "app.db", "app.auth", "app.billing",
                "app.delivery", "app.main"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
        else:
            importlib.import_module(mod)
    main = sys.modules["app.main"]
    return main, path


class AppTestCase(unittest.TestCase):
    env: dict = {}

    def setUp(self):
        from fastapi.testclient import TestClient
        self.main, self.db_path = build_app(**self.env)
        self.addCleanup(lambda: os.path.exists(self.db_path)
                        and os.unlink(self.db_path))
        self.client = TestClient(self.main.app)
        self.client.__enter__()               # fires startup, creates schema
        self.addCleanup(self.client.__exit__, None, None, None)

    def sign_in(self, email="sam@example.com"):
        link = self.main.auth.make_login_link(email)
        token = link.split("token=", 1)[1]
        r = self.client.get(f"/auth/verify?token={token}", follow_redirects=False)
        return r


class TestPublicPages(AppTestCase):
    def test_landing_renders_and_shows_the_evidence(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("27%", r.text)
        self.assertIn("Get your CV in front of a human", r.text)

    def test_the_headline_number_says_replies_came_from_a_person(self):
        """The claim is a human reply rate, not a reply rate.

        Counting autoresponders is how every other cold-email tool gets to a
        big number, and it is the first thing a sceptical reader will check.
        If somebody ever quietly relabels this, the claim stops being the one
        that was verified against a real mailbox.
        """
        page = self.client.get("/").text
        self.assertIn("replies from a person", page)
        self.assertIn("Autoresponders are not counted", page)

    def test_playbook_is_free_and_needs_no_account(self):
        r = self.client.get("/playbook")
        self.assertEqual(r.status_code, 200)
        self.assertIn("banned", r.text.lower())

    def test_health(self):
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})


class TestSignIn(AppTestCase):
    def test_a_bad_address_is_rejected(self):
        r = self.client.post("/login", data={"email": "not-an-email"})
        self.assertIn("not an email address", r.text)

    def test_the_reply_does_not_reveal_who_has_an_account(self):
        # Same response either way, or the form becomes a customer-list oracle.
        a = self.client.post("/login", data={"email": "stranger@example.com"})
        self.sign_in("known@example.com")
        b = self.client.post("/login", data={"email": "known@example.com"})
        self.assertIn("on its way", a.text)
        self.assertIn("on its way", b.text)

    def test_a_valid_link_signs_you_in(self):
        r = self.sign_in()
        self.assertEqual(r.status_code, 303)
        self.assertIn("jm_session", r.cookies)

    def test_a_new_account_lands_on_setup(self):
        """With no profile the dashboard can only say so. Send them to the
        work instead - the CV upload there fills in most of the next screen."""
        r = self.sign_in()
        self.assertEqual(r.headers["location"], "/setup")

    def test_a_set_up_account_lands_on_the_dashboard(self):
        r = self.sign_in()
        user = self.main.db.get_or_create_user("sam@example.com")
        self.main.db.save_profile(user["id"], {
            "name": "Sam", "email": "sam@example.com", "phone": "07000000000",
            "location": "Aberdeen", "target_roles": ["technician"],
            "locations": ["Aberdeen"], "min_salary_annual": 30000,
        })
        self.client.cookies.clear()
        r = self.sign_in()
        self.assertEqual(r.headers["location"], "/dashboard")

    def test_a_link_cannot_be_used_twice(self):
        link = self.main.auth.make_login_link("sam@example.com")
        token = link.split("token=", 1)[1]
        first = self.client.get(f"/auth/verify?token={token}",
                                follow_redirects=False)
        self.assertEqual(first.status_code, 303)

        self.client.cookies.clear()
        second = self.client.get(f"/auth/verify?token={token}",
                                 follow_redirects=False)
        self.assertEqual(second.status_code, 200)
        self.assertIn("expired or was already used", second.text)

    def test_a_forged_token_is_refused(self):
        r = self.client.get("/auth/verify?token=made.up.token",
                            follow_redirects=False)
        self.assertIn("expired or was already used", r.text)

    def test_signed_out_users_are_sent_to_login(self):
        for path in ("/dashboard", "/profile", "/drafts", "/account"):
            r = self.client.get(path, follow_redirects=False)
            self.assertEqual(r.status_code, 303, path)
            self.assertEqual(r.headers["location"], "/login", path)


class TestPaywall(AppTestCase):
    env = {"BILLING_ENABLED": "1", "DEV_MODE": "1",
           "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_PRICE_ID": "price_x",
           "STRIPE_WEBHOOK_SECRET": "whsec_test"}

    def test_an_unpaid_user_hits_the_paywall(self):
        self.sign_in()
        r = self.client.get("/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Subscribe to start", r.text)

    def test_the_success_redirect_does_not_grant_access(self):
        # Anyone can type this URL. It must not be worth anything.
        self.sign_in()
        self.client.get("/billing/done?ok=1")
        r = self.client.get("/dashboard")
        self.assertIn("Subscribe to start", r.text)

    def test_a_verified_webhook_does_grant_access(self):
        self.sign_in()
        user = self.main.db.get_or_create_user("sam@example.com")
        body = json.dumps({
            "type": "checkout.session.completed",
            "data": {"object": {"customer": "cus_1", "subscription": "sub_1",
                                "client_reference_id": str(user["id"]),
                                "metadata": {"user_id": str(user["id"])}}},
        }).encode()
        r = self.client.post("/webhooks/stripe", content=body,
                             headers={"stripe-signature": self._sig(body)})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Dashboard", self.client.get("/dashboard").text)

    def test_an_unsigned_webhook_is_refused(self):
        body = b'{"type":"checkout.session.completed"}'
        r = self.client.post("/webhooks/stripe", content=body)
        self.assertEqual(r.status_code, 400)

    def test_a_wrongly_signed_webhook_is_refused(self):
        body = b'{"type":"checkout.session.completed"}'
        bad = f"t={int(time.time())},v1=deadbeef"
        r = self.client.post("/webhooks/stripe", content=body,
                             headers={"stripe-signature": bad})
        self.assertEqual(r.status_code, 400)
        self.assertIn("signature did not match", r.text)

    def test_a_replayed_old_webhook_is_refused(self):
        body = b'{"type":"checkout.session.completed"}'
        old = int(time.time()) - 4000
        sig = hmac.new(b"whsec_test", b"%d.%s" % (old, body),
                       hashlib.sha256).hexdigest()
        r = self.client.post("/webhooks/stripe", content=body,
                             headers={"stripe-signature": f"t={old},v1={sig}"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("outside tolerance", r.text)

    def test_a_cancelled_subscription_closes_access_again(self):
        self.sign_in()
        user = self.main.db.get_or_create_user("sam@example.com")
        self.main.db.set_billing(user["id"], status="active",
                                 customer_id="cus_1")
        self.assertIn("Dashboard", self.client.get("/dashboard").text)

        body = json.dumps({
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_1", "id": "sub_1",
                                "current_period_end": int(time.time()) - 10,
                                "metadata": {"user_id": str(user["id"])}}},
        }).encode()
        self.client.post("/webhooks/stripe", content=body,
                         headers={"stripe-signature": self._sig(body)})
        self.assertIn("Subscribe to start", self.client.get("/dashboard").text)

    def _sig(self, body: bytes) -> str:
        ts = int(time.time())
        mac = hmac.new(b"whsec_test", b"%d.%s" % (ts, body),
                       hashlib.sha256).hexdigest()
        return f"t={ts},v1={mac}"


class TestProfileForm(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()

    def _good_form(self, **over):
        form = {
            "name": "Sam Doherty", "location": "Sheffield",
            "phone": "07700 900123", "email": "sam@example.com",
            "situation": "employed", "current_salary": "32000",
            "min_salary_annual": "38000", "min_rate_hourly": "20",
            "priorities": ["money", "progression"],
            "h_title": ["Maintenance Technician"], "h_org": ["Brightwater"],
            "h_detail": ["fault finding on PLC lines"],
            "qualifications": "Level 3 NVQ",
            "never_claim": "that I hold a current 17th Edition",
            "locations": "Sheffield\nRotherham",
            "target_roles": "maintenance technician",
            "radius_miles": "25",
        }
        form.update(over)
        return form

    def test_a_good_profile_saves(self):
        r = self.client.post("/profile", data=self._good_form(),
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        saved = self.main.db.load_profile(1)
        self.assertEqual(saved["name"], "Sam Doherty")
        self.assertEqual(saved["never_claim"],
                         ["that I hold a current 17th Edition"])
        self.assertEqual(saved["locations"], ["Sheffield", "Rotherham"])

    def test_a_floor_below_current_salary_is_refused_in_the_ui(self):
        r = self.client.post("/profile",
                             data=self._good_form(min_salary_annual="28000"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("pay cut", r.text)
        self.assertIsNone(self.main.db.load_profile(1))

    def test_the_form_and_the_engine_share_one_validator(self):
        # A profile the web form accepts must be one the pipeline can load.
        from jobseeker.profile import Profile
        self.client.post("/profile", data=self._good_form())
        Profile.from_dict(self.main.db.load_profile(1))


class TestDrafts(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.draft_id = self.main.db.add_draft(
            1, job_title="Shift Engineer", company="Kestrel Foods Ltd",
            location="Rotherham", to_email="j.smith@kestrel.example",
            to_name="Jo Smith", contact_tier=3, score=82,
            subject="Shift engineer, nights covered",
            body="Hi Jo,\n\nSaw the shift engineer role...")

    def test_drafts_render_with_a_send_link(self):
        r = self.client.get("/drafts")
        self.assertIn("Shift Engineer", r.text)
        self.assertIn("mailto:j.smith%40kestrel.example", r.text)
        self.assertIn("named person", r.text)

    def test_marking_sent_records_the_employer_as_contacted(self):
        self.assertTrue(self.main.db.may_contact(1, "Kestrel Foods Ltd"))
        self.client.post(f"/drafts/{self.draft_id}/sent", follow_redirects=False)
        self.assertFalse(self.main.db.may_contact(1, "Kestrel Foods Ltd"))
        # and the same employer under a slightly different name
        self.assertFalse(self.main.db.may_contact(1, "Kestrel Foods"))

    def test_blocking_an_employer_is_permanent_and_discards_the_draft(self):
        self.client.post(f"/drafts/{self.draft_id}/block", follow_redirects=False)
        self.assertTrue(self.main.db.is_blocked(1, "Kestrel Foods Ltd"))
        self.assertFalse(self.main.db.may_contact(1, "KESTREL FOODS LIMITED"))

    def test_one_user_cannot_touch_another_users_draft(self):
        other = self.main.db.get_or_create_user("someone.else@example.com")
        self.assertIsNone(self.main.db.get_draft(other["id"], self.draft_id))
        self.client.cookies.clear()
        self.sign_in("someone.else@example.com")
        self.client.post(f"/drafts/{self.draft_id}/sent", follow_redirects=False)
        row = self.main.db.get_draft(1, self.draft_id)
        self.assertEqual(row["status"], "draft")   # untouched


class TestConfigRefusals(unittest.TestCase):
    def test_production_without_a_secret_key_refuses_to_start(self):
        os.environ.pop("SECRET_KEY", None)
        os.environ["DEV_MODE"] = "0"
        os.environ["BILLING_ENABLED"] = "0"
        with self.assertRaises(RuntimeError) as ctx:
            importlib.reload(importlib.import_module("app.config"))
        self.assertIn("SECRET_KEY", str(ctx.exception))

    def test_billing_on_without_stripe_keys_refuses_to_start(self):
        os.environ["SECRET_KEY"] = "x" * 40
        os.environ["DEV_MODE"] = "0"
        os.environ["BILLING_ENABLED"] = "1"
        for k in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(k, None)
        with self.assertRaises(RuntimeError) as ctx:
            importlib.reload(importlib.import_module("app.config"))
        self.assertIn("STRIPE_SECRET_KEY", str(ctx.exception))

    def tearDown(self):
        os.environ["DEV_MODE"] = "1"
        os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
        os.environ["BILLING_ENABLED"] = "0"
        importlib.reload(importlib.import_module("app.config"))




class TestLoginRateLimit(AppTestCase):
    """Without this, the app is a free email cannon pointed at any address."""

    def test_repeated_requests_to_one_address_stop_sending(self):
        sent = []
        self.main.auth.send_login_email = lambda a, l: sent.append(a)

        for _ in range(5):
            r = self.client.post("/login", data={"email": "victim@example.com"})
            self.assertIn("on its way", r.text)
        self.assertEqual(len(sent), 5)

        # Sixth is silently dropped - and looks identical, so an abuser
        # learns nothing and a real user is not told their address exists.
        r = self.client.post("/login", data={"email": "victim@example.com"})
        self.assertIn("on its way", r.text)
        self.assertEqual(len(sent), 5, "a 6th email escaped the limit")

    def test_one_machine_cannot_walk_a_list_of_addresses(self):
        sent = []
        self.main.auth.send_login_email = lambda a, l: sent.append(a)

        for i in range(25):
            self.client.post("/login", data={"email": f"target{i}@example.com"})
        # per-IP cap is 20/hour, and no single address hit its own cap
        self.assertEqual(len(sent), 20)

    def test_the_limiter_does_not_block_a_normal_person(self):
        sent = []
        self.main.auth.send_login_email = lambda a, l: sent.append(a)
        for _ in range(3):
            self.client.post("/login", data={"email": "sam@example.com"})
        self.assertEqual(len(sent), 3)


class TestAccountDeletion(AppTestCase):
    """A deletion that leaves a live subscription behind is the worst bug
    this app could have: it charges somebody whose account is gone."""

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.main.db.save_profile(1, {"name": "Sam"})
        self.draft_id = self.main.db.add_draft(
            1, job_title="Fitter", company="Acme", subject="s", body="b")
        self.main.db.record_contacted(1, "Acme")
        self.main.db.block_company(1, "Bad Employer")

    def test_the_form_needs_the_word_delete(self):
        r = self.client.post("/account/delete", data={"confirm": "yes"})
        self.assertIn("Type &#34;delete&#34; to confirm", r.text)
        self.assertIsNotNone(self.main.db.get_user(1))

    def test_deleting_takes_everything_with_it(self):
        r = self.client.post("/account/delete", data={"confirm": "delete"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIsNone(self.main.db.get_user(1))
        self.assertIsNone(self.main.db.load_profile(1))
        self.assertEqual(self.main.db.list_drafts(1), [])
        # the cascade must reach the contacted and blocked lists too
        self.assertFalse(self.main.db.already_contacted(1, "Acme"))
        self.assertFalse(self.main.db.is_blocked(1, "Bad Employer"))

    def test_deleting_signs_you_out(self):
        self.client.post("/account/delete", data={"confirm": "delete"},
                         follow_redirects=False)
        r = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(r.headers["location"], "/login")

    def test_confirmation_is_case_and_space_insensitive(self):
        self.client.post("/account/delete", data={"confirm": "  DELETE "},
                         follow_redirects=False)
        self.assertIsNone(self.main.db.get_user(1))

    def test_the_page_says_what_will_be_lost(self):
        r = self.client.get("/account/delete")
        self.assertIn("cannot be undone", r.text)
        self.assertIn("sam@example.com", r.text)


class TestFreeAccessList(AppTestCase):
    """The people the app was given to rather than sold to.

    The whole point of putting this list in the environment is that Stripe
    cannot reach it, so the tests that matter are the ones where the Stripe
    columns say no and the answer is still yes.
    """
    env = {"BILLING_ENABLED": "1", "DEV_MODE": "1",
           "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_PRICE_ID": "price_x",
           "STRIPE_WEBHOOK_SECRET": "whsec_test",
           "FREE_ACCESS_EMAILS": "friend@example.com, Second.Friend@Example.com"}

    def test_a_listed_email_walks_past_the_paywall(self):
        self.sign_in("friend@example.com")
        r = self.client.get("/dashboard")
        self.assertNotIn("Subscribe to start", r.text)

    def test_the_list_is_not_case_sensitive(self):
        # Nobody types their friends' addresses back exactly as they wrote
        # them the first time, and an address is not case sensitive anyway.
        self.sign_in("SECOND.FRIEND@example.com")
        r = self.client.get("/dashboard")
        self.assertNotIn("Subscribe to start", r.text)

    def test_an_unlisted_email_still_has_to_pay(self):
        self.sign_in("stranger@example.com")
        self.assertIn("Subscribe to start", self.client.get("/dashboard").text)

    def test_a_cancelled_subscription_cannot_shut_a_listed_person_out(self):
        self.sign_in("friend@example.com")
        user = self.main.db.get_or_create_user("friend@example.com")
        self.main.db.set_billing(user["id"], status="canceled",
                                 paid_until=int(time.time()) - 10)
        r = self.client.get("/dashboard")
        self.assertNotIn("Subscribe to start", r.text)

    def test_a_free_account_is_not_asked_to_subscribe(self):
        # The account page used to decide by BILLING_ENABLED alone, so a
        # comped person was shown "No subscription on this account yet" and a
        # Subscribe button - an invitation to pay for what they already have.
        self.sign_in("friend@example.com")
        page = self.client.get("/account").text
        self.assertNotIn("/billing/checkout", page)
        self.assertIn("Free access", page)

    def test_an_unlisted_account_is_still_asked_to_subscribe(self):
        self.sign_in("stranger@example.com")
        self.assertIn("/billing/checkout", self.client.get("/account").text)

    def test_stripes_own_words_never_reach_the_account_page(self):
        """subscription_status holds Stripe's vocabulary, not English."""
        self.sign_in("stranger@example.com")
        user = self.main.db.get_or_create_user("stranger@example.com")
        for stripe_word in ("none", "canceled", "past_due", "incomplete"):
            self.main.db.set_billing(user["id"], status=stripe_word)
            page = self.client.get("/account").text
            self.assertNotIn(stripe_word, page,
                             f"the page printed Stripe's {stripe_word!r} at a "
                             "person")

    def test_an_empty_list_grants_nothing(self):
        main, path = build_app(BILLING_ENABLED="1", DEV_MODE="1",
                               STRIPE_WEBHOOK_SECRET="whsec_test",
                               STRIPE_PAYMENT_LINK="https://buy.stripe.com/x",
                               FREE_ACCESS_EMAILS="")
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        # The guard is a set membership test; an empty string must not become
        # a one-element set containing "", which every blank email would match.
        self.assertEqual(main.config.FREE_ACCESS_EMAILS, frozenset())


class TestDeletionWithBilling(AppTestCase):
    env = {"BILLING_ENABLED": "1", "DEV_MODE": "1",
           "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_PRICE_ID": "price_x",
           "STRIPE_WEBHOOK_SECRET": "whsec_test"}

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.main.db.set_billing(1, status="active", customer_id="cus_1",
                                 subscription_id="sub_1")

    def test_the_subscription_is_cancelled_before_the_data_goes(self):
        order = []
        self.main.billing.cancel_subscription = lambda s: order.append(("cancel", s))
        real_delete = self.main.db.delete_user
        self.main.db.delete_user = lambda u: (order.append(("delete", u)),
                                              real_delete(u))[1]

        self.client.post("/account/delete", data={"confirm": "delete"},
                         follow_redirects=False)
        self.assertEqual([o[0] for o in order], ["cancel", "delete"])
        self.assertIsNone(self.main.db.get_user(1))

    def test_a_failed_cancellation_deletes_nothing(self):
        def boom(_sub):
            raise self.main.billing.BillingError("Stripe is down")
        self.main.billing.cancel_subscription = boom

        r = self.client.post("/account/delete", data={"confirm": "delete"})
        self.assertIn("could not be cancelled", r.text)
        self.assertIsNotNone(self.main.db.get_user(1),
                             "account was deleted despite a live subscription")


class TestPaymentLink(AppTestCase):
    """Taking money with a Stripe Payment Link and no secret key at all.

    The link is public and hosted by Stripe; the only thing that opens the
    app is still a verified webhook.
    """
    env = {"BILLING_ENABLED": "1", "DEV_MODE": "1",
           "STRIPE_SECRET_KEY": "", "STRIPE_PRICE_ID": "",
           "STRIPE_WEBHOOK_SECRET": "whsec_test",
           "STRIPE_PAYMENT_LINK": "https://buy.stripe.com/test_abc123"}

    def _sig(self, body: bytes) -> str:
        ts = int(time.time())
        mac = hmac.new(b"whsec_test", b"%d.%s" % (ts, body),
                       hashlib.sha256).hexdigest()
        return f"t={ts},v1={mac}"

    def test_checkout_redirects_to_the_link_stamped_with_the_user(self):
        self.sign_in()
        r = self.client.get("/billing/checkout", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"],
                         "https://buy.stripe.com/test_abc123?client_reference_id=1")

    def test_an_existing_query_string_is_appended_to_not_broken(self):
        self.assertEqual(
            self.main.config.payment_link_for(7),
            "https://buy.stripe.com/test_abc123?client_reference_id=7")
        self.main.config.STRIPE_PAYMENT_LINK = "https://buy.stripe.com/x?locale=en"
        self.assertEqual(self.main.config.payment_link_for(7),
                         "https://buy.stripe.com/x?locale=en&client_reference_id=7")

    def test_no_stripe_api_call_is_made(self):
        # Link mode must not need a secret key. If it reached the API this
        # would raise BillingError for the missing key.
        self.sign_in()
        r = self.client.get("/billing/checkout", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("buy.stripe.com", r.headers["location"])

    def test_a_subscription_payment_opens_the_app(self):
        self.sign_in()
        body = json.dumps({
            "type": "checkout.session.completed",
            "data": {"object": {"mode": "subscription", "customer": "cus_1",
                                "subscription": "sub_1",
                                "client_reference_id": "1"}},
        }).encode()
        r = self.client.post("/webhooks/stripe", content=body,
                             headers={"stripe-signature": self._sig(body)})
        self.assertIn("subscription", r.text)
        self.assertIn("Dashboard", self.client.get("/dashboard").text)

    def test_a_one_off_payment_does_not_grant_lifetime_access(self):
        # The expensive bug: one charge, access forever.
        self.sign_in()
        body = json.dumps({
            "type": "checkout.session.completed",
            "data": {"object": {"mode": "payment", "customer": "cus_1",
                                "client_reference_id": "1"}},
        }).encode()
        r = self.client.post("/webhooks/stripe", content=body,
                             headers={"stripe-signature": self._sig(body)})
        self.assertIn("one-off", r.text)

        user = self.main.db.get_user(1)
        self.assertIsNotNone(user["paid_until"], "one-off payment never expires")
        self.assertGreater(user["paid_until"], time.time())
        self.assertLess(user["paid_until"], time.time() + 31 * 86400)
        self.assertIn("Dashboard", self.client.get("/dashboard").text)

    def test_a_one_off_payment_expires(self):
        self.sign_in()
        self.main.db.set_billing(1, status="active",
                                 paid_until=int(time.time()) - 10)
        self.assertIn("Subscribe to start", self.client.get("/dashboard").text)

    def test_a_session_with_no_mode_and_no_subscription_is_treated_as_one_off(self):
        # Safer default: assume the payment was single unless Stripe says
        # otherwise. Guessing "subscription" would grant forever.
        self.sign_in()
        body = json.dumps({
            "type": "checkout.session.completed",
            "data": {"object": {"customer": "cus_1", "client_reference_id": "1"}},
        }).encode()
        self.client.post("/webhooks/stripe", content=body,
                         headers={"stripe-signature": self._sig(body)})
        self.assertIsNotNone(self.main.db.get_user(1)["paid_until"])

    def test_a_payment_with_no_user_stamped_on_it_is_not_honoured(self):
        # Without client_reference_id the payment belongs to nobody.
        self.sign_in()
        body = json.dumps({
            "type": "checkout.session.completed",
            "data": {"object": {"mode": "payment", "customer": "cus_unknown"}},
        }).encode()
        r = self.client.post("/webhooks/stripe", content=body,
                             headers={"stripe-signature": self._sig(body)})
        self.assertIn("no user could be identified", r.text)
        self.assertIn("Subscribe to start", self.client.get("/dashboard").text)

    def test_the_webhook_is_still_mandatory_in_link_mode(self):
        self.sign_in()
        body = b'{"type":"checkout.session.completed"}'
        self.assertEqual(
            self.client.post("/webhooks/stripe", content=body).status_code, 400)


class TestLinkModeConfig(unittest.TestCase):
    def test_link_mode_does_not_require_a_secret_key(self):
        os.environ.update({"DEV_MODE": "0", "BILLING_ENABLED": "1",
                           "SECRET_KEY": "x" * 40,
                           "STRIPE_PAYMENT_LINK": "https://buy.stripe.com/x",
                           "STRIPE_WEBHOOK_SECRET": "whsec_x"})
        for k in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID"):
            os.environ.pop(k, None)
        cfg = importlib.reload(importlib.import_module("app.config"))
        self.assertEqual(cfg.STRIPE_PAYMENT_LINK, "https://buy.stripe.com/x")

    def test_link_mode_still_requires_the_webhook_secret(self):
        os.environ.update({"DEV_MODE": "0", "BILLING_ENABLED": "1",
                           "SECRET_KEY": "x" * 40,
                           "STRIPE_PAYMENT_LINK": "https://buy.stripe.com/x"})
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        with self.assertRaises(RuntimeError) as ctx:
            importlib.reload(importlib.import_module("app.config"))
        self.assertIn("STRIPE_WEBHOOK_SECRET", str(ctx.exception))

    def tearDown(self):
        os.environ.update({"DEV_MODE": "1", "BILLING_ENABLED": "0",
                           "SECRET_KEY": "test-secret-key-not-for-production"})
        os.environ.pop("STRIPE_PAYMENT_LINK", None)
        importlib.reload(importlib.import_module("app.config"))


if __name__ == "__main__":
    unittest.main()
