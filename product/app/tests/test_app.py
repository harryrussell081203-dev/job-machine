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
        self.assertIn("Get your CV in front of a human", r.text)

    def test_the_claim_matches_what_is_actually_counted(self):
        """The claim went away and came back, and which is correct depends
        entirely on how the number underneath it is worked out.

        It was true originally: seven replies curated by hand, with one "apply
        through our portal" deliberately left out. It stopped being true when
        the figures started publishing themselves, because nobody was in the
        middle any more - so the sentence came down with the curation.

        It is true again, and for a better reason than either: the machine
        classifies what comes back, because it reads it. Excluding
        autoresponders and rejections took the headline from 26% to 17%, which
        is the direction that tells you the exclusion is real rather than
        decorative.

        The rule this test exists to hold is not the wording. It is that the
        sentence and the sum must agree - so if the published rate ever goes
        back to counting everything that arrived, this claim comes down again.
        """
        page = self.client.get("/").text
        self.assertNotIn("replies from a person", page)
        # Asserted on the claim, not the sentence: the wording around it
        # gets edited, and a test that breaks on a comma teaches people to
        # change the test rather than think about it.
        self.assertIn("update themselves", page)

        import app.track_record as tr
        record = tr.read()
        if record:
            self.assertIn("not counted in that", page)
        else:
            # No numbers, no claim about them.
            self.assertNotIn("not counted in that", page)

    def test_playbook_is_free_and_needs_no_account(self):
        r = self.client.get("/playbook")
        self.assertEqual(r.status_code, 200)
        self.assertIn("banned", r.text.lower())

    def test_there_is_something_to_click_before_the_evidence(self):
        """The landing page had no call to action until its foot - 1,600px
        down on a desktop, 6,000 on a phone. A reader convinced by the first
        two sentences had to scroll past four sections to act.

        The assertion is on ORDER, not presence: the buttons at the bottom
        were always there, and it is being above the evidence that matters.
        """
        body = self.client.get("/").text
        cta = body.find('class="hero-cta"')
        stats = body.find('class="stats"')
        self.assertNotEqual(cta, -1, "no call to action in the hero")
        self.assertLess(cta, stats,
                        "the call to action is below the evidence again")

    def test_the_closing_call_to_action_is_still_there(self):
        """Two calls to action for two readers: one already sold by the
        headline, one who needed the numbers first. Adding the hero one must
        not have replaced the other."""
        body = self.client.get("/").text
        self.assertGreater(body.count('href="/login"'), 1)

    def test_health(self):
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})

    def test_robots_points_at_the_sitemap_and_hides_the_private_screens(self):
        body = self.client.get("/robots.txt").text
        self.assertIn("Sitemap: http://testserver/sitemap.xml", body)
        # The ones that would put somebody's drafts, CV or account in Google.
        for private in ("/dashboard", "/drafts", "/setup", "/account",
                        "/admin", "/cv", "/auth"):
            self.assertIn(f"Disallow: {private}", body)

    def test_the_sitemap_lists_the_public_pages_and_only_those(self):
        xml = self.client.get("/sitemap.xml").text
        for public in ("/find", "/playbook", "/terms", "/privacy"):
            self.assertIn(f"http://testserver{public}<", xml)
        for private in ("/dashboard", "/drafts", "/account", "/admin"):
            self.assertNotIn(f"http://testserver{private}<", xml)

    def test_the_sitemap_is_served_as_xml(self):
        # Served as text/html it is ignored, and the failure is invisible.
        r = self.client.get("/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("xml", r.headers["content-type"])

    def test_a_shared_link_has_a_title_a_description_and_a_picture(self):
        """Without these a link to the site is a bare grey URL everywhere it
        is posted - Reddit, WhatsApp, a TikTok bio. The launch is made of
        shares, so this is the first impression on nearly every visitor."""
        page = self.client.get("/").text
        for tag in ('property="og:title"', 'property="og:description"',
                    'property="og:image"', 'name="description"',
                    'name="twitter:card"'):
            self.assertIn(tag, page)

    def test_the_preview_image_is_an_absolute_url(self):
        # A relative og:image is ignored by every scraper there is, and the
        # failure looks like no image rather than a broken one.
        page = self.client.get("/").text
        self.assertIn('content="http://testserver/static/og.png"', page)

    def test_the_preview_card_actually_exists(self):
        r = self.client.get("/static/og.png")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"\x89PNG"))

    def test_the_description_does_not_leak_into_the_page(self):
        """The obvious way to do per-page titles is a Jinja block, and a
        declared block prints its body where it is declared - which puts the
        marketing description as loose text at the top of every screen."""
        body = self.client.get("/").text.split("<body>", 1)[1]
        self.assertNotIn("A real email address at every company", body)


class TestSignIn(AppTestCase):
    def test_a_bad_address_is_rejected(self):
        r = self.client.post("/login", data={"email": "not-an-email"})
        self.assertIn("does not look like an email address", r.text)
        # Says what to do about it, not just that it is wrong.
        self.assertIn("@", r.text)

    def test_the_reply_does_not_reveal_who_has_an_account(self):
        """Same response either way, or the form is a customer-list oracle.

        Asserted as an exact match rather than by looking for a phrase. The
        wording here changed once already - it used to hedge, "if that address
        has an account, a link is on its way", which protected nothing (a link
        goes to any valid address; tapping it makes the account) and cost the
        new arrival, who reads it, knows they have no account, and concludes
        nothing was sent. A phrase match would have let the next rewrite
        introduce a real difference while still passing.
        """
        a = self.client.post("/login", data={"email": "stranger@example.com"})
        self.sign_in("known@example.com")
        # Signing in to create the account also leaves a session cookie on the
        # client, which renders a different navbar. That is a difference in
        # who is ASKING, not in which address was asked about, so comparing
        # with it still attached would fail for the wrong reason - and worse,
        # would keep passing if a real leak appeared.
        self.client.cookies.clear()
        b = self.client.post("/login", data={"email": "known@example.com"})
        self.assertEqual(a.text.replace("stranger@", "known@"), b.text,
                         "the response differs for an address that has an "
                         "account, which makes the form a customer list")

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
        self.assertIn("already been used", second.text)

    def test_tapping_the_same_link_again_while_signed_in_just_goes_in(self):
        # The real complaint behind "an email link every time". Somebody goes
        # back to their inbox and taps the link a second time. It is single
        # use, so it is refused - and they were shown a login screen and told
        # to request another link, while holding a session good for a month.
        link = self.main.auth.make_login_link("sam@example.com")
        token = link.split("token=", 1)[1]
        first = self.client.get(f"/auth/verify?token={token}",
                                follow_redirects=False)
        self.assertEqual(first.status_code, 303)

        # Same token, same browser, cookies kept this time.
        second = self.client.get(f"/auth/verify?token={token}",
                                 follow_redirects=False)
        self.assertEqual(second.status_code, 303)
        self.assertIn(second.headers["location"], ("/dashboard", "/setup"))
        self.assertNotIn("expired or was already used", second.text)

    def test_a_forged_token_while_signed_in_still_does_not_sign_anyone_new_in(self):
        # The session decides where an authenticated person LANDS. It must
        # never be what authenticates them, or a junk token plus somebody
        # else's cookie would read as a successful sign-in for that token's
        # owner. Signed in as sam, the forged token must not change who you
        # are - it should simply drop you back where sam belongs.
        self.sign_in("sam@example.com")
        r = self.client.get("/auth/verify?token=made.up.token",
                            follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        who = self.client.get("/account")
        self.assertIn("sam@example.com", who.text)

    def test_a_forged_token_is_refused(self):
        r = self.client.get("/auth/verify?token=made.up.token",
                            follow_redirects=False)
        self.assertIn("already been used", r.text)

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

    def test_a_description_in_the_location_box_is_refused_in_the_ui(self):
        """The one that cost a hundred and eighteen listings.

        Saved silently, went to the job board as a literal search term, and
        looked like there were no jobs. Nothing to fix afterwards, because
        nothing had gone wrong as far as the software knew.
        """
        r = self.client.post("/profile", data=self._good_form(
            locations="United Kingdom europe oil hotspots"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("reads like a sentence", r.text)
        self.assertIsNone(self.main.db.load_profile(1))

    def test_the_refusal_says_what_to_type_instead(self):
        r = self.client.post("/profile", data=self._good_form(
            locations="anywhere I can get to on the train"))
        self.assertIn("One place per line", r.text)
        self.assertIsNone(self.main.db.load_profile(1))

    def test_commas_separate_places_as_well_as_lines(self):
        """Nobody types four towns on four lines when a comma is right there,
        and 'Aberdeen, Edinburgh' as ONE location searched for a place of that
        name and found nothing."""
        r = self.client.post("/profile", data=self._good_form(
            locations="Aberdeen, Edinburgh\nGlasgow"),
            follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.main.db.load_profile(1)["locations"],
                         ["Aberdeen", "Edinburgh", "Glasgow"])

    def test_commas_separate_job_titles_too(self):
        r = self.client.post("/profile", data=self._good_form(
            target_roles="field service engineer, maintenance technician"),
            follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.main.db.load_profile(1)["target_roles"],
                         ["field service engineer", "maintenance technician"])

    def test_a_comma_inside_a_never_claim_rule_does_not_split_it(self):
        """never_claim entries are prose, so a comma there is punctuation.
        Splitting on it would turn one rule into two half-rules, and a
        half-rule about what you must never claim is worse than none."""
        rule = "a degree, which is paused rather than finished"
        r = self.client.post("/profile", data=self._good_form(never_claim=rule),
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.main.db.load_profile(1)["never_claim"], [rule])


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
        # Asserted through check_billing_config() rather than through the
        # reload, because the guard is no longer a side effect of importing
        # config. It belongs to the website - the half that takes money - and
        # main.py calls it at import, so a misconfigured site still dies at
        # boot. The scheduled sweep imports config too, sells nothing, and
        # must not need a Stripe key to start. See test_billing_guard.py.
        os.environ["SECRET_KEY"] = "x" * 40
        os.environ["DEV_MODE"] = "0"
        os.environ["BILLING_ENABLED"] = "1"
        for k in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(k, None)
        cfg = importlib.reload(importlib.import_module("app.config"))
        with self.assertRaises(RuntimeError) as ctx:
            cfg.check_billing_config()
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
            self.assertIn("Check your email", r.text)
        self.assertEqual(len(sent), 5)

        # Sixth is silently dropped - and looks identical, so an abuser
        # learns nothing and a real user is not told their address exists.
        r = self.client.post("/login", data={"email": "victim@example.com"})
        self.assertIn("Check your email", r.text)
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
        # Through check_billing_config() for the reason given above: importing
        # config no longer decides whether a payment route is honourable.
        cfg = importlib.reload(importlib.import_module("app.config"))
        with self.assertRaises(RuntimeError) as ctx:
            cfg.check_billing_config()
        self.assertIn("STRIPE_WEBHOOK_SECRET", str(ctx.exception))

    def tearDown(self):
        os.environ.update({"DEV_MODE": "1", "BILLING_ENABLED": "0",
                           "SECRET_KEY": "test-secret-key-not-for-production"})
        os.environ.pop("STRIPE_PAYMENT_LINK", None)
        importlib.reload(importlib.import_module("app.config"))


if __name__ == "__main__":
    unittest.main()


class TestTheScoreShowsItsWorking(AppTestCase):
    """Every competitor in this category shows a match score with a breakdown
    of the factors behind it, and it is the feature their users single out.

    This machine has always computed the reasoning - scoring.py sets
    `score_reason` and its own docstring says it is "so a user can" see it -
    and the drafts table had nowhere to put it, so the screen showed a bare
    "scored 78" and threw the rest away.

    It matters more here than for them. They apply to everything; the number
    is decoration. This applies to a fraction of what it sees, so the
    reasoning is the evidence that it chose.
    """

    def draft_with(self, **over):
        uid = self.main.db.get_or_create_user("sam@example.com")["id"]
        fields = dict(job_title="Subsea Technician", company="Acme",
                      to_email="a@acme.com", subject="s", body="b",
                      score=78, score_reason="Matches subsea cable testing; "
                                             "pays above your floor")
        fields.update(over)
        self.main.db.add_draft(uid, **fields)
        return uid

    def test_the_reasoning_is_stored_and_shown(self):
        self.draft_with()
        self.sign_in("sam@example.com")
        body = self.client.get("/drafts").text
        self.assertIn("78", body)
        self.assertIn("Matches subsea cable testing", body)

    def test_a_draft_without_reasoning_still_renders(self):
        """Every draft written before this column existed has none, and an
        old draft must not blank the screen."""
        self.draft_with(score_reason="")
        self.sign_in("sam@example.com")
        r = self.client.get("/drafts")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Subsea Technician", r.text)

    def test_the_score_is_shown_out_of_a_hundred(self):
        """"78" alone could be anything. "78/100" is a judgement."""
        self.draft_with()
        self.sign_in("sam@example.com")
        self.assertIn("78/100", self.client.get("/drafts").text)


class TestThePublicNumbers(AppTestCase):
    """The pitch rests on a number, and a number nobody can check is a claim.

    This page is what makes it checkable - including when the answer is zero,
    which is what it says today.
    """

    def test_it_is_public(self):
        r = self.client.get("/numbers")
        self.assertEqual(r.status_code, 200)

    def test_with_nothing_sent_it_says_so_plainly(self):
        # A page that only appears once the numbers flatter you is an advert.
        page = self.client.get("/numbers").text
        self.assertIn("No letters have gone out yet", page)

    def test_the_benchmarks_are_on_the_page_with_their_source(self):
        page = self.client.get("/numbers").text
        for figure in ("2&ndash;3%", "9.25%", "2.58%", "0.5%", "Huntr"):
            self.assertIn(figure, page)

    def test_it_keeps_reply_rate_and_interview_rate_apart(self):
        """The benchmarks are INTERVIEW rates; Harry's 27% is a REPLY rate.
        Presenting them as one number would be the exact arithmetic this page
        exists to prevent."""
        page = self.client.get("/numbers").text
        self.assertIn("different thing", page)
        landing = self.client.get("/").text
        self.assertIn("Not the same measurement as ours", landing)


class TestThePublicNumbersCountHonestly(AppTestCase):
    def _send(self, n, outcome=None):
        """n letters actually out of the door, optionally with an outcome."""
        uid = self.main.db.get_or_create_user("sam@example.com")["id"]
        for i in range(n):
            did = self.main.db.add_draft(
                uid, job_title="Technician", company=f"Co {i}",
                to_email=f"a{i}@example.com", subject="s", body="b")
            self.main.db.mark_draft(uid, did, "sent")
            self.main.db.record_sent(uid, draft_id=did,
                                     to_email=f"a{i}@example.com",
                                     company=f"Co {i}")
            if outcome:
                self.main.db.set_outcome(uid, did, outcome)
        return uid

    def test_a_failed_send_is_not_a_letter(self):
        """It reached nobody. Counting attempts would be the first lie on a
        page whose only job is being checkable."""
        uid = self.main.db.get_or_create_user("sam@example.com")["id"]
        self.main.db.record_sent(uid, draft_id=None, to_email="a@b.com",
                                 company="Co", ok=False, error="refused")
        self.assertEqual(self.main.db.public_stats()["letters"], 0)

    def test_the_total_does_not_fall_when_a_reply_arrives(self):
        """The trap Harry found in the personal machine: a total built on
        drafts.status goes DOWN as answers come in."""
        self._send(3)
        before = self.main.db.public_stats()["letters"]
        uid = self.main.db.get_or_create_user("sam@example.com")["id"]
        rows = self.main.db.applications(uid)
        self.main.db.set_outcome(uid, rows[0]["id"], "replied")
        self.assertEqual(self.main.db.public_stats()["letters"], before)

    def test_a_rate_is_withheld_until_there_are_enough_people(self):
        # With one user the percentage is one person's diary, not a statistic.
        self._send(4, outcome="replied")
        stats = self.main.db.public_stats()
        self.assertIsNone(stats["reply_rate"])
        self.assertTrue(stats["rate_withheld"])
        # The template wraps, so assert on a phrase that survives the line
        # break rather than one that only looks contiguous in the source.
        page = self.client.get("/numbers").text
        self.assertIn("diary rather than a statistic", page)
        self.assertIn("No reply rate yet", page)

    def test_the_raw_counts_are_never_hidden(self):
        # Withholding those too would look like there is something to hide.
        self._send(4)
        self.assertEqual(self.main.db.public_stats()["letters"], 4)

    def test_a_rejection_is_heard_back_but_is_not_a_reply(self):
        """Or the headline improves as things go worse."""
        self._send(2, outcome="rejected")
        stats = self.main.db.public_stats()
        self.assertEqual(stats["marked"], 2)
        self.assertEqual(stats["heard_back"], 0)


class TestThePressedButtonSaysSomething(AppTestCase):
    """Connecting a mailbox took thirty seconds, and for all of it the screen
    was identical to before the tap. Harry pressed it three times in nine
    minutes, which is the correct reading of a button that looks inert.

    Every form here posts and waits on a server, so the fix belongs in the
    base template rather than on one button.
    """

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.page = self.client.get("/dashboard").text

    def test_a_pressed_button_is_marked_busy(self):
        self.assertIn('setAttribute("aria-busy", "true")', self.page)

    def test_the_spinner_is_styled_for_it(self):
        css = self.client.get("/static/style.css").text
        self.assertIn('button[aria-busy="true"]::before', css)
        self.assertIn("jm-spin", css)

    def test_the_disable_is_deferred_past_the_submit_event(self):
        """The whole feature is a foot-gun without this line.

        A submit button disabled INSIDE its own submit handler is dropped
        from the form data by the browser, so the server stops seeing which
        button was pressed - a change that looks purely cosmetic silently
        breaks the post underneath it. The disable therefore has to happen on
        a later tick, and a future edit that "tidies" the setTimeout away
        would reintroduce it with no visible symptom until somebody's letter
        failed to send.
        """
        self.assertIn("setTimeout(function () { button.disabled = true; }, 0)",
                      self.page)
        # And it is genuinely deferred, not merely written with a timeout
        # somewhere else on the page: nothing disables the button inline.
        self.assertNotIn("button.disabled = true;\n", self.page)

    def test_a_cancelled_submit_does_not_leave_it_spinning(self):
        self.assertIn("if (e.defaultPrevented) return;", self.page)

    def test_going_back_does_not_hand_over_a_dead_form(self):
        """bfcache restores the page mid-submit, disabled button and all."""
        self.assertIn('"pageshow"', self.page)

    def test_reduced_motion_still_gets_the_signal(self):
        """The animation goes; the meaning must not go with it."""
        css = self.client.get("/static/style.css").text
        reduced = css.split("prefers-reduced-motion")[1]
        self.assertIn('aria-busy="true"', reduced)


class TestTheDashboardDoesNotListWhatItSkipped(AppTestCase):
    """"Looked at, not written to" listed every rejected listing with its
    reason. Harry asked for it gone: it was the longest thing on the page,
    it grew every day whatever happened, and not one line of it was something
    he could act on.

    The reasons are still recorded - seen_listings keeps them, and that is
    what stops the same listing being scored twice. They are simply not the
    dashboard's job.
    """

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.main.db.mark_seen(1, "job-1", "no real email address could be "
                                           "found; nothing is guessed")

    def test_the_section_is_gone(self):
        page = self.client.get("/dashboard").text
        self.assertNotIn("Looked at, not written to", page)
        self.assertNotIn("nothing is guessed", page)

    def test_but_the_reason_is_still_recorded(self):
        rows = self.main.db.recent_outcomes(1)
        self.assertEqual(len(rows), 1)
        self.assertIn("nothing is guessed", rows[0]["outcome"])


class TestThePageIsNotNarrowerThanTheProduct(AppTestCase):
    """The evidence on the landing page is one person's, and saying whose is
    what makes it checkable. But for a while his trade was the ONLY thing on
    the page describing who it was for, which quietly told everybody in a
    different line of work that this was not for them.

    They would have been wrong. scoring.py's own docstring is explicit that
    the rubric is derived from the profile rather than written about one man,
    "so the same code scores a Sheffield fitter and a Bristol lab technician
    correctly without either of them editing a prompt". It reads the advert it
    is given and has never known what the job is.

    Harry made the point himself: the audience is anyone out of work.
    """

    def test_it_says_the_method_is_not_built_around_a_trade(self):
        page = self.client.get("/").text
        self.assertIn("Nothing in it is built around a trade", page)

    def test_it_names_somebody_other_than_an_engineer(self):
        """One concrete non-technical example does more than any amount of
        "for everybody", which reads as marketing and persuades nobody."""
        page = self.client.get("/").text
        self.assertTrue(
            any(job in page for job in ("carer", "driver", "bookkeeper")),
            "the only worked example is still a trade")

    def test_it_does_not_promise_only_your_trade(self):
        """'your trade' reads as skilled manual work, and a receptionist does
        not see themselves in it."""
        for path in ("/", "/find"):
            self.assertNotIn("your trade", self.client.get(path).text, path)

    def test_the_evidence_still_says_whose_it_is(self):
        """Widening must not become hiding. The numbers are one person's and
        the page has to keep saying so, or it stops being checkable - which is
        the only reason to print them."""
        page = self.client.get("/").text
        self.assertIn("the founder's own", page)


class TestGettingIn(AppTestCase):
    """The two screens and one email between tapping a link and being inside.

    This is where the funnel actually leaked. Five accounts existed and four
    of them had never got past it, while 500 people were shown the link and
    one signed up. Nothing here is a guess about taste - each test names the
    specific thing that stopped somebody.
    """

    env = {"FREE_SPOTS": "25"}

    def test_it_does_not_ask_a_newcomer_to_sign_in_to_an_account_they_lack(self):
        """Nearly everybody who sees this page has never been here - they just
        tapped "Take a free place". Heading it "Sign in" tells them they need
        something they know they do not have, and there is no such thing here:
        the same box makes the account."""
        page = self.client.get("/login").text
        self.assertNotIn("<h1>Sign in</h1>", page)
        self.assertIn("free place", page.lower())

    def test_it_says_the_same_box_works_either_way(self):
        """The hesitation this removes is "am I in the right place" - there is
        no separate sign-up page to be in the wrong one of."""
        page = self.client.get("/login").text
        self.assertIn("New here or coming back", page)

    def test_the_check_your_email_screen_says_what_to_look_for(self):
        """Somebody staring at this screen has one job - find the email - and
        the old version gave them nothing to search for."""
        page = self.client.post("/login", data={"email": "new@example.com"}).text
        self.assertIn("Check your email", page)
        self.assertIn("Recruited", page)
        self.assertIn("Your sign-in link", page, "the subject is not quoted")

    def test_it_warns_about_spam_before_they_give_up(self):
        """A young sending domain lands in spam constantly, and somebody who
        does not know to look there concludes it is broken."""
        page = self.client.post("/login", data={"email": "new@example.com"}).text
        self.assertIn("spam", page.lower())

    def test_it_does_not_hedge_about_whether_anything_was_sent(self):
        """The wording that cost the most: "if that address has an account, a
        link is on its way", read by somebody who knows they have no account.
        A link goes to any valid address, so the hedge described a rule that
        does not exist."""
        page = self.client.post("/login", data={"email": "new@example.com"}).text
        self.assertNotIn("has an account", page)

    def test_a_typo_is_told_what_a_valid_address_looks_like(self):
        page = self.client.post("/login", data={"email": "harry"}).text
        self.assertIn("@", page)
        self.assertNotIn("<h1>Check your email</h1>", page)

    def test_the_email_says_what_it_is_not_just_here_is_your_link(self):
        """It arrives from a name the reader has seen once, two minutes ago.
        A spam complaint on a young domain costs every other customer's link
        as well as this one."""
        body = self.main.auth._body("http://testserver/auth/verify?token=x")
        self.assertIn("Recruited", body)
        self.assertIn("no password", body.lower())
        # What the product actually does, for somebody who is not sure.
        self.assertIn("real person", body.lower())

    def test_the_email_still_says_how_to_ignore_it(self):
        """Somebody else's address can be typed into that box. They have to be
        told they are not required to do anything."""
        body = self.main.auth._body("http://testserver/auth/verify?token=x")
        self.assertIn("did not ask for this", body)


class TestSetupDoesNotReadAsAWall(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()

    def page(self):
        return self.client.get("/setup").text

    def test_it_leads_with_what_is_needed_not_how_long_it_takes(self):
        """"Four things, about ten minutes" counts the work before saying what
        any of it is for - and two of the four are not needed at all to get
        letters written."""
        page = self.page()
        self.assertNotIn("Four things", page)
        self.assertIn("Two things", page)

    def test_the_optional_steps_say_they_are_optional(self):
        """Without this the screen reads as four compulsory chores, and the
        two that matter are buried among them."""
        self.assertEqual(self.page().count("optional"), 2)

    def test_it_says_which_single_thing_to_do_first(self):
        page = self.page()
        self.assertIn("Start with your CV", page)

    def test_it_does_not_call_it_your_trade(self):
        """Same correction as the landing page and /find, which this screen
        was left out of."""
        self.assertNotIn("Your trade", self.page())


class TestThePrivacyNoticeMatchesWhatIsDone(AppTestCase):
    def test_the_view_counter_is_described(self):
        """The notice said "there is no analytics", and then a page-view
        counter shipped. It holds nothing about anybody and sets no cookie,
        which made it tempting to leave the sentence alone - but a privacy
        notice that is nearly true is the wrong kind, on the one page where
        somebody decides whether to hand over a CV."""
        page = self.client.get("/privacy").text
        self.assertNotIn("no analytics", page)
        self.assertIn("Counting visits", page)
        # The two facts that make it harmless, both stated rather than implied.
        self.assertIn("No cookie is set for it", page)
        self.assertIn("not stored", page)

    def test_it_says_how_long_the_count_is_kept(self):
        page = self.client.get("/privacy").text
        self.assertIn("60 days", page)

    def test_the_retention_claim_matches_the_code(self):
        """A number typed into a notice drifts away from the one that runs.
        Asserted against views.KEEP_FOR so the page cannot quietly become a
        false statement about our own retention."""
        import app.views as views
        self.assertEqual(views.KEEP_FOR // 86400, 60)

    def test_no_tracking_pixel_claim_survives(self):
        page = self.client.get("/privacy").text
        self.assertIn("no tracking pixel", page)
        self.assertIn("third-party script", page)
