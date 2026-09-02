"""Tests for the parts that act without a human watching.

Automatic sending is the feature with the most expensive failure modes in
this product, because every one of them lands in a stranger's inbox with the
user's name on it. So these concentrate on the promises rather than the
plumbing:

  - a mail password is never stored where a database dump can read it
  - one employer is written to once, even when two drafts arrive together
  - a letter can be stopped after it is written and before it goes
  - a daily ceiling holds
  - a rejected password stops, rather than retrying until the account locks
  - an uploaded file is not trusted because of what it is called
"""

import importlib
import io
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from cryptography.fernet import Fernet  # noqa: E402


def build(**env):
    """A fresh app, database and credential key per test."""
    defaults = {
        "DEV_MODE": "1",
        "SECRET_KEY": "test-secret-key-not-for-production",
        "BILLING_ENABLED": "0",
        "STRIPE_SECRET_KEY": "", "STRIPE_PRICE_ID": "",
        "STRIPE_WEBHOOK_SECRET": "", "STRIPE_PAYMENT_LINK": "",
        "BASE_URL": "http://testserver",
        "CREDENTIAL_KEY": Fernet.generate_key().decode(),
    }
    defaults.update(env)
    os.environ.update(defaults)

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path
    for mod in ("app.config", "app.vault", "app.store", "app.db", "app.auth",
                "app.billing", "app.delivery", "app.cv", "app.autosend",
                "app.runner", "app.main"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
        else:
            importlib.import_module(mod)
    return sys.modules["app.main"], path


class Base(unittest.TestCase):
    env: dict = {}

    def setUp(self):
        from fastapi.testclient import TestClient
        self.main, self.db_path = build(**self.env)
        self.addCleanup(lambda: os.path.exists(self.db_path)
                        and os.unlink(self.db_path))
        self.db = sys.modules["app.db"]
        self.autosend = sys.modules["app.autosend"]
        self.delivery = sys.modules["app.delivery"]
        self.client = TestClient(self.main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.uid = self.db.get_or_create_user("harry@example.com")["id"]
        self.sent = []

    def fake_send(self, **kw):
        self.sent.append(kw)

    def draft(self, company, email="a@example.com", age_seconds=7200):
        did = self.db.add_draft(self.uid, job_title="Scaffolder",
                                company=company, to_email=email,
                                subject="s", body="b")
        with self.db.connect() as c:
            c.execute("UPDATE drafts SET created_at = ? WHERE id = ?",
                      (self.db.now() - age_seconds, did))
        return did

    def connect_mail(self, password="app-password"):
        self.db.save_mail_account(self.uid, address="harry@gmail.com",
                                  host="smtp.gmail.com", port=465,
                                  password=password)


# ----------------------------------------------------------------------
class TestCredentialsAtRest(Base):
    def test_the_password_is_not_readable_from_the_row(self):
        self.connect_mail("s3cr3t-app-password")
        row = self.db.get_mail_account(self.uid)
        self.assertNotIn("s3cr3t-app-password", str(dict(row)))

    def test_it_still_decrypts_for_sending(self):
        self.connect_mail("s3cr3t-app-password")
        _, _, _, password = self.db.mail_login(self.uid)
        self.assertEqual(password, "s3cr3t-app-password")

    def test_a_rotated_key_asks_the_user_to_reconnect(self):
        self.connect_mail()
        vault = sys.modules["app.vault"]
        # Simulate the operator rotating CREDENTIAL_KEY.
        vault._cached = Fernet(Fernet.generate_key())
        with self.assertRaises(vault.VaultError) as caught:
            self.db.mail_login(self.uid)
        self.assertIn("reconnect", str(caught.exception))

    def test_sending_reports_it_rather_than_claiming_success(self):
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme Ltd")
        vault = sys.modules["app.vault"]
        vault._cached = Fernet(Fernet.generate_key())
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 0)
        self.assertIn("reconnect", report.reason)
        self.assertEqual(self.sent, [])


class TestVaultUnavailable(Base):
    env = {"CREDENTIAL_KEY": ""}

    def test_no_key_means_the_feature_is_off_not_insecure(self):
        vault = sys.modules["app.vault"]
        self.assertFalse(vault.available())
        with self.assertRaises(vault.VaultError):
            vault.encrypt("anything")

    def test_the_app_still_boots_and_serves(self):
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})


# ----------------------------------------------------------------------
class TestAutomaticSending(Base):
    def test_off_unless_switched_on(self):
        self.connect_mail()
        self.draft("Acme Ltd")
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 0)
        self.assertEqual(self.sent, [])

    def test_on_with_a_mail_account_it_sends(self):
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme Ltd")
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 1)
        self.assertEqual(len(self.sent), 1)

    def test_on_without_a_mail_account_sends_nothing(self):
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme Ltd")
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 0)
        self.assertIn("no mail account", report.reason)

    def test_a_draft_inside_the_holding_window_waits(self):
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1, hold_minutes=60)
        self.draft("Acme Ltd", age_seconds=0)
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 0)
        self.assertEqual(self.sent, [])

    def test_a_zero_hold_sends_immediately(self):
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1, hold_minutes=0)
        self.draft("Acme Ltd", age_seconds=0)
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 1)

    def test_the_cv_is_attached_and_the_name_is_on_the_from_line(self):
        self.connect_mail()
        self.db.save_profile(self.uid, {"name": "Harry Russell"})
        self.db.save_cv(self.uid, filename="cv.pdf",
                        content_type="application/pdf", blob=b"%PDF-1.4 x")
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(self.sent[0]["attachment"][0], "cv.pdf")
        self.assertEqual(self.sent[0]["display_name"], "Harry Russell")


class TestOneEmployerOneLetter(Base):
    def setUp(self):
        super().setUp()
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)

    def test_an_employer_already_written_to_is_skipped(self):
        self.db.record_contacted(self.uid, "Acme Ltd")
        self.draft("Acme Limited")
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 0)
        self.assertEqual(report.skipped, 1)

    def test_two_drafts_for_one_employer_in_one_sweep(self):
        # The dangerous case: neither is recorded as contacted when the sweep
        # begins, so only an in-run guard stops the second going out.
        self.draft("Acme Ltd", email="a@acme.com")
        self.draft("Acme Group Services", email="b@acme.com")
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 1)
        self.assertEqual(report.skipped, 1)

    def test_a_blocked_employer_is_never_written_to(self):
        self.db.block_company(self.uid, "Acme Ltd", "asked to stop")
        self.draft("Acme Ltd")
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 0)
        self.assertEqual(self.sent, [])

    def test_a_draft_with_no_address_is_never_guessed_at(self):
        self.draft("Acme Ltd", email="")
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(report.sent, 0)
        self.assertEqual(self.sent, [])


class TestLimits(Base):
    def setUp(self):
        super().setUp()
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1, daily_cap=3)

    def test_the_daily_cap_holds(self):
        for i in range(8):
            self.draft(f"Company {i} Ltd", email=f"c{i}@example.com")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.db.sent_today(self.uid), 3)

    def test_a_second_sweep_does_not_reset_it(self):
        for i in range(8):
            self.draft(f"Company {i} Ltd", email=f"c{i}@example.com")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        report = self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(len(self.sent), 3)
        self.assertIn("daily limit", report.reason)

    def test_failed_sends_do_not_eat_the_allowance(self):
        def boom(**kw):
            raise self.delivery.DeliveryError("temporary server problem")
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=boom)
        self.assertEqual(self.db.sent_today(self.uid), 0)


class TestFailureHandling(Base):
    def setUp(self):
        super().setUp()
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)

    def test_a_rejected_password_switches_automatic_sending_off(self):
        def rejected(**kw):
            raise self.delivery.DeliveryError(
                "that mail account rejected the password. If this is Gmail "
                "or Outlook you need an app password")
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=rejected)
        self.assertEqual(self.db.get_send_settings(self.uid)["auto_send"], 0)

    def test_and_tells_the_user_why(self):
        def rejected(**kw):
            raise self.delivery.DeliveryError(
                "that mail account rejected the password")
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=rejected)
        row = self.db.get_mail_account(self.uid)
        self.assertIn("rejected", row["last_error"])

    def test_a_transient_failure_does_not_switch_it_off(self):
        def flaky(**kw):
            raise self.delivery.DeliveryError("could not reach smtp: timeout")
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=flaky)
        self.assertEqual(self.db.get_send_settings(self.uid)["auto_send"], 1)

    def test_it_gives_up_after_a_few_failures_in_a_row(self):
        calls = []

        def flaky(**kw):
            calls.append(1)
            raise self.delivery.DeliveryError("could not reach smtp: timeout")
        for i in range(20):
            self.draft(f"Company {i} Ltd", email=f"c{i}@example.com")
        self.autosend.send_due_for_user(self.uid, sender=flaky)
        self.assertLessEqual(len(calls), self.autosend.MAX_CONSECUTIVE_FAILURES)

    def test_a_sent_draft_is_not_sent_again(self):
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(len(self.sent), 1)


class TestSweep(Base):
    def test_it_only_runs_for_people_who_are_paid(self):
        # Billing is off in these tests, so everyone counts - which is exactly
        # the configuration where a status-only query would find nobody.
        ids = self.db.paid_user_ids()
        self.assertIn(self.uid, ids)

    def test_one_broken_user_does_not_stop_the_others(self):
        other = self.db.get_or_create_user("sam@example.com")["id"]
        for uid in (self.uid, other):
            self.db.save_mail_account(uid, address="a@gmail.com",
                                      host="smtp.gmail.com", port=465,
                                      password="pw")
            self.db.save_send_settings(uid, auto_send=1)
        self.draft("Acme Ltd")
        did = self.db.add_draft(other, job_title="x", company="Beta Ltd",
                                to_email="b@example.com", subject="s", body="b")
        with self.db.connect() as c:
            c.execute("UPDATE drafts SET created_at = ? WHERE id = ?",
                      (self.db.now() - 7200, did))

        seen = []

        def sender(**kw):
            seen.append(kw["to_email"])
            if kw["to_email"] == "a@example.com":
                raise self.delivery.DeliveryError("this one is broken")
        totals = self.autosend.sweep(run=False, sender=sender)
        self.assertEqual(totals["sent"], 1)
        self.assertEqual(totals["failed"], 1)


# ----------------------------------------------------------------------
class TestCVIsNotTrusted(Base):
    def setUp(self):
        super().setUp()
        self.cv = sys.modules["app.cv"]

    def test_a_renamed_executable_is_refused(self):
        with self.assertRaises(self.cv.CVError):
            self.cv.check("cv.pdf", b"MZ\x90\x00 an executable")

    def test_a_file_over_the_limit_is_refused(self):
        with self.assertRaises(self.cv.CVError):
            self.cv.check("cv.pdf", b"%PDF-" + b"x" * self.cv.MAX_BYTES)

    def test_an_unknown_format_is_refused(self):
        with self.assertRaises(self.cv.CVError):
            self.cv.check("cv.exe", b"anything")

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(self.cv.CVError):
            self.cv.check("cv.pdf", b"")

    def test_a_real_docx_is_accepted_and_read(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml",
                       '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
                       '<w:p><w:r><w:t>Jane Smith</w:t></w:r></w:p>'
                       '<w:p><w:r><w:t>Welder &amp; Fabricator</w:t></w:r></w:p>'
                       '</w:body></w:document>')
        blob = buf.getvalue()
        self.cv.check("cv.docx", blob)
        text = self.cv.extract_text("cv.docx", blob)
        self.assertIn("Jane Smith", text)
        self.assertIn("Welder & Fabricator", text)

    def test_unreadable_contents_never_raise(self):
        self.assertEqual(self.cv.extract_text("cv.pdf", b"%PDF-broken"), "")


class TestCVSuggestions(Base):
    def setUp(self):
        super().setUp()
        self.cv = sys.modules["app.cv"]

    def test_the_model_cannot_set_the_pay_floor(self):
        out = self.cv.parse_suggestion(
            '{"name":"Harry","min_salary_annual":55000,"min_rate_hourly":30}')
        self.assertEqual(out.get("name"), "Harry")
        self.assertNotIn("min_salary_annual", out)
        self.assertNotIn("min_rate_hourly", out)

    def test_the_model_cannot_set_the_never_claim_list(self):
        out = self.cv.parse_suggestion('{"never_claim":[],"name":"Harry"}')
        self.assertNotIn("never_claim", out)

    def test_rubbish_returns_nothing_rather_than_raising(self):
        for raw in ("", "sorry, I cannot", "[1,2,3]", '{"a":,}', None):
            self.assertEqual(self.cv.parse_suggestion(raw), {})

    def test_a_fenced_reply_is_still_read(self):
        out = self.cv.parse_suggestion('```json\n{"name":"Harry"}\n```')
        self.assertEqual(out["name"], "Harry")

    def test_lists_and_strings_are_capped(self):
        out = self.cv.parse_suggestion(
            '{"name":"' + "A" * 5000 + '","target_roles":'
            + str(["role"] * 50).replace("'", '"') + '}')
        self.assertLessEqual(len(out["name"]), 120)
        self.assertLessEqual(len(out["target_roles"]), 6)


# ----------------------------------------------------------------------
class TestSetupScreens(Base):
    def sign_in(self, email="harry@example.com"):
        link = self.main.auth.make_login_link(email)
        token = link.split("token=", 1)[1]
        self.client.get(f"/auth/verify?token={token}", follow_redirects=False)

    def test_setup_needs_a_sign_in(self):
        r = self.client.get("/setup", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/login")

    def test_setup_renders_the_four_steps(self):
        self.sign_in()
        r = self.client.get("/setup")
        self.assertEqual(r.status_code, 200)
        for heading in ("Your CV", "Your search", "How letters go out",
                        "Sending rules"):
            self.assertIn(heading, r.text)

    def test_a_bad_cv_is_refused_with_a_reason(self):
        self.sign_in()
        r = self.client.post("/setup/cv",
                             files={"cv": ("cv.pdf", b"MZ not a pdf",
                                           "application/pdf")})
        self.assertIn("not", r.text.lower())
        self.assertIsNone(self.db.get_cv(self.uid))

    def test_a_good_cv_is_stored_and_can_be_downloaded(self):
        self.sign_in()
        self.client.post("/setup/cv",
                         files={"cv": ("cv.txt", b"Harry Russell, scaffolder",
                                       "text/plain")},
                         follow_redirects=False)
        stored = self.db.get_cv(self.uid)
        self.assertIsNotNone(stored)
        r = self.client.get("/cv")
        self.assertEqual(r.content, b"Harry Russell, scaffolder")

    def test_automatic_sending_cannot_be_turned_on_without_mail(self):
        self.sign_in()
        r = self.client.post("/setup/sending", data={"auto_send": "1"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("/setup/mail", r.headers["location"])
        self.assertEqual(self.db.get_send_settings(self.uid)["auto_send"], 0)

    def test_the_daily_cap_is_clamped_to_something_defensible(self):
        self.sign_in()
        self.client.post("/setup/sending",
                         data={"daily_cap": "5000", "hold_minutes": "99999"},
                         follow_redirects=False)
        settings = self.db.get_send_settings(self.uid)
        self.assertLessEqual(settings["daily_cap"], 50)
        self.assertLessEqual(settings["hold_minutes"], 1440)

    def test_disconnecting_mail_also_turns_automatic_sending_off(self):
        self.sign_in()
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)
        self.client.post("/setup/mail/forget", follow_redirects=False)
        self.assertIsNone(self.db.get_mail_account(self.uid))
        self.assertEqual(self.db.get_send_settings(self.uid)["auto_send"], 0)

    def test_deleting_the_account_takes_the_cv_and_credentials_with_it(self):
        self.sign_in()
        self.connect_mail()
        self.db.save_cv(self.uid, filename="cv.txt", content_type="text/plain",
                        blob=b"x")
        self.db.delete_user(self.uid)
        self.assertIsNone(self.db.get_cv(self.uid))
        self.assertIsNone(self.db.get_mail_account(self.uid))


class TestInstallable(Base):
    def test_the_worker_is_served_from_the_root_with_the_right_scope(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["service-worker-allowed"], "/")

    def test_the_worker_caches_no_pages(self):
        # A cached page would serve one person's job search to the next user
        # of a shared device. Assert the rule in the source itself.
        source = self.client.get("/sw.js").text
        self.assertIn("/static/", source)
        self.assertNotIn('"/dashboard"', source)
        self.assertNotIn('"/drafts"', source)

    def test_the_manifest_is_valid_and_installable(self):
        import json
        r = self.client.get("/manifest.webmanifest")
        data = json.loads(r.text)
        self.assertEqual(data["display"], "standalone")
        self.assertTrue(any(i["sizes"] == "512x512" for i in data["icons"]))
        self.assertTrue(any(i.get("purpose") == "maskable"
                            for i in data["icons"]))

    def test_there_is_a_favicon(self):
        self.assertEqual(self.client.get("/favicon.ico").status_code, 200)


if __name__ == "__main__":
    unittest.main()


class TestStatusPage(unittest.TestCase):
    """The page that answers "did I deploy this right".

    It has to be readable signed out, because the failures it reports are the
    ones that stop you signing in - and it must never print a value, because
    anybody can read it.
    """

    def build(self, **env):
        from fastapi.testclient import TestClient
        main, path = build(**env)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        client = TestClient(main.app)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def test_it_is_readable_without_signing_in(self):
        self.assertEqual(self.build().get("/status").status_code, 200)

    def test_a_misconfigured_deployment_is_reported_as_not_ok(self):
        data = self.build().get("/status").json()
        self.assertFalse(data["ok"])
        self.assertTrue(data["problems"])

    def test_it_catches_an_ephemeral_database(self):
        data = self.build().get("/status").json()
        self.assertEqual(data["storage"], "sqlite (not persistent)")
        self.assertTrue(any("deleted by the next deploy" in p
                            for p in data["problems"]))

    def test_it_catches_an_open_paywall(self):
        data = self.build(BILLING_ENABLED="0").get("/status").json()
        self.assertEqual(data["billing"], "disabled")
        self.assertTrue(any("Nobody has to pay you" in p
                            for p in data["problems"]))

    def test_it_catches_a_missing_webhook_secret(self):
        data = self.build(BILLING_ENABLED="1", DEV_MODE="1",
                          STRIPE_PAYMENT_LINK="https://buy.stripe.com/x",
                          STRIPE_WEBHOOK_SECRET="").get("/status").json()
        self.assertFalse(data["webhook_secret_set"])
        self.assertTrue(any("locked out" in p for p in data["problems"]))

    def test_it_catches_a_base_url_pointing_somewhere_else(self):
        data = self.build(BASE_URL="https://not-this-app.example.com"
                          ).get("/status").json()
        self.assertFalse(data["base_url_matches_this_page"])
        self.assertTrue(any("point somewhere else" in p
                            for p in data["problems"]))

    def test_a_correct_base_url_is_accepted(self):
        data = self.build(BASE_URL="http://testserver").get("/status").json()
        self.assertTrue(data["base_url_matches_this_page"])

    def test_it_catches_dev_mode_left_on(self):
        data = self.build(DEV_MODE="1").get("/status").json()
        self.assertTrue(data["dev_mode"])
        self.assertTrue(any("DEV_MODE" in p for p in data["problems"]))

    def test_a_missing_credential_key_is_a_warning_not_a_failure(self):
        # Automatic sending is a feature, not the product. Its absence must
        # not read as a broken deployment.
        data = self.build(CREDENTIAL_KEY="").get("/status").json()
        self.assertEqual(data["automatic_sending"], "unavailable")
        self.assertTrue(any("CREDENTIAL_KEY" in w for w in data["warnings"]))
        self.assertFalse(any("CREDENTIAL_KEY" in p for p in data["problems"]))

    def test_it_leaks_no_values(self):
        secrets = {
            "SECRET_KEY": "sk-secret-value-here",
            "STRIPE_PAYMENT_LINK": "https://buy.stripe.com/leak-me",
            "STRIPE_WEBHOOK_SECRET": "whsec_leak_me",
            "APP_SMTP_ADDRESS": "operator@example.com",
            "APP_SMTP_PASSWORD": "smtp-leak-me",
            "ADZUNA_APP_KEY": "adzuna-leak-me",
            "GEMINI_API_KEY": "gemini-leak-me",
        }
        body = self.build(**secrets).get("/status").text
        for name, value in secrets.items():
            self.assertNotIn(value, body, f"{name} leaked into /status")


class TestCredentialKeyForms(unittest.TestCase):
    """A key has to be settable by somebody on a phone.

    Telling a user to run Python to generate a Fernet key is a step they may
    have no way to take, so a host's own "generate a random value" button has
    to work. What must NOT work is a short guessable passphrase, because that
    looks like encryption without being any.
    """

    def vault_with(self, key):
        os.environ.update(DEV_MODE="1", BILLING_ENABLED="0", SECRET_KEY="t",
                          CREDENTIAL_KEY=key)
        for mod in ("app.config", "app.vault"):
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
            else:
                importlib.import_module(mod)
        return sys.modules["app.vault"]

    def test_a_real_fernet_key_is_used_as_is(self):
        v = self.vault_with(Fernet.generate_key().decode())
        self.assertTrue(v.available())
        self.assertEqual(v.decrypt(v.encrypt("pw")), "pw")

    def test_a_long_random_string_is_stretched_into_one(self):
        v = self.vault_with("kP9xQvT2mWnR7bYcJ4hLd8Zs5FgA3eVu")
        self.assertTrue(v.available())
        self.assertEqual(v.decrypt(v.encrypt("pw")), "pw")

    def test_the_same_string_always_gives_the_same_key(self):
        # Or every restart would make stored credentials unreadable.
        a = self.vault_with("kP9xQvT2mWnR7bYcJ4hLd8Zs5FgA3eVu")
        token = a.encrypt("pw")
        b = self.vault_with("kP9xQvT2mWnR7bYcJ4hLd8Zs5FgA3eVu")
        self.assertEqual(b.decrypt(token), "pw")

    def test_two_different_strings_give_different_keys(self):
        a = self.vault_with("kP9xQvT2mWnR7bYcJ4hLd8Zs5FgA3eVu")
        token = a.encrypt("pw")
        b = self.vault_with("DIFFERENT2mWnR7bYcJ4hLd8Zs5FgA3e")
        with self.assertRaises(b.VaultError):
            b.decrypt(token)

    def test_a_short_passphrase_is_refused(self):
        for weak in ("aB3dE5", "password", "letmein123"):
            v = self.vault_with(weak)
            self.assertFalse(v.available(), f"{weak!r} was accepted")

    def tearDown(self):
        os.environ.pop("CREDENTIAL_KEY", None)
