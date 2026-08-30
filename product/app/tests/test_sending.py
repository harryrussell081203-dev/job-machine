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
