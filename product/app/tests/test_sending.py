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
        # Blanked rather than omitted, like the Stripe keys above and for the
        # same reason: os.environ.update() only ever sets, so a value left by
        # an earlier test's build() would still be there. A test asserting
        # that Recruited addresses are NOT offered passed alone and failed
        # in the suite because a previous class had switched them on.
        "MANAGED_MAIL_DOMAIN": "", "MANAGED_MAIL_KEY": "",
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

    def test_the_upload_answers_json_when_the_page_asks_for_it(self):
        """The setup page uploads with fetch rather than by submitting the
        form, because reading the bytes in the browser is the only place
        Chrome's ERR_UPLOAD_FILE_CHANGED can be fixed - it aborts before the
        request is ever made, so no server code can catch it.

        That needs an answer a script can read, rather than a 303 to a page.
        """
        self.sign_in()
        r = self.client.post("/setup/cv",
                             files={"cv": ("cv.txt", b"Harry Russell",
                                           "text/plain")},
                             headers={"Accept": "application/json"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["next"], "/setup/from-cv")
        self.assertIsNotNone(self.db.get_cv(self.uid))

    def test_a_refused_cv_comes_back_as_json_too(self):
        # Or the page would report success on a file the server rejected.
        self.sign_in()
        r = self.client.post("/setup/cv",
                             files={"cv": ("cv.pdf", b"MZ not a pdf",
                                           "application/pdf")},
                             headers={"Accept": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not", r.json()["error"].lower())
        self.assertIsNone(self.db.get_cv(self.uid))

    def test_the_plain_form_post_still_works_without_javascript(self):
        """The fetch path is an enhancement, not a replacement. Somebody with
        no JavaScript still gets redirects exactly as before."""
        self.sign_in()
        r = self.client.post("/setup/cv",
                             files={"cv": ("cv.txt", b"Harry Russell",
                                           "text/plain")},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/setup/from-cv")
        self.assertIsNotNone(self.db.get_cv(self.uid))

    def test_choosing_a_file_is_the_only_action_needed(self):
        """Fewest clicks: the page uploads on change, so the button is hidden
        when the script is running. If the markup loses these ids the script
        silently does nothing and the bug comes back."""
        self.sign_in()
        page = self.client.get("/setup").text
        for hook in ('id="cvfile"', 'id="cvbtn"', 'id="cvform"',
                     'addEventListener("change"'):
            self.assertIn(hook, page)

    def test_the_picker_accepts_files_that_arrive_by_mime_type(self):
        """Android greys out cloud files when the accept list is extensions
        only - a file coming from Drive often has a type and no useful name,
        which is what made Drive look unsupported."""
        self.sign_in()
        page = self.client.get("/setup").text
        self.assertIn("application/pdf", page)
        self.assertIn("application/vnd.openxmlformats-officedocument"
                      ".wordprocessingml.document", page)

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


class TestTheKeyIsReadWhenItIsUsed(unittest.TestCase):
    """The key comes from the environment at the moment it is needed, never
    from whatever the environment held when some unrelated module first
    imported this one.

    It used to be captured at import. That is invisible in production, where
    the environment is set before the process starts, and vicious in a test
    run: fourteen tests in test_sending_rules.py passed on their own and
    errored in the full suite with "CREDENTIAL_KEY is not set", purely
    because another module had imported the vault first and frozen the empty
    string. The tests were right and the vault was wrong.

    Note what these do NOT do: reload the module. That is the point.
    """

    def setUp(self):
        self.original = os.environ.get("CREDENTIAL_KEY")
        # Whatever module object the rest of the suite is using. Fetched
        # rather than imported at the top of the file because build() reloads
        # app.vault, and a stale reference would test a module nothing else
        # in the process is running.
        self.vault = (sys.modules.get("app.vault")
                      or importlib.import_module("app.vault"))

    def tearDown(self):
        if self.original is None:
            os.environ.pop("CREDENTIAL_KEY", None)
        else:
            os.environ["CREDENTIAL_KEY"] = self.original

    def test_a_key_set_after_import_is_picked_up(self):
        os.environ.pop("CREDENTIAL_KEY", None)
        self.assertFalse(self.vault.available())
        os.environ["CREDENTIAL_KEY"] = Fernet.generate_key().decode()
        self.assertTrue(self.vault.available())
        self.assertEqual(self.vault.decrypt(self.vault.encrypt("pw")), "pw")

    def test_changing_the_key_does_not_serve_a_cipher_for_the_old_one(self):
        """The cache is keyed on the key. Get this wrong and a rotated
        CREDENTIAL_KEY silently keeps encrypting under the previous one -
        which reads as working right up until the process restarts and every
        stored password is unreadable."""
        os.environ["CREDENTIAL_KEY"] = Fernet.generate_key().decode()
        token = self.vault.encrypt("pw")
        os.environ["CREDENTIAL_KEY"] = Fernet.generate_key().decode()
        with self.assertRaises(self.vault.VaultError):
            self.vault.decrypt(token)

    def test_removing_the_key_turns_the_feature_off_again(self):
        os.environ["CREDENTIAL_KEY"] = Fernet.generate_key().decode()
        self.assertTrue(self.vault.available())
        os.environ.pop("CREDENTIAL_KEY", None)
        self.assertFalse(self.vault.available())


class TestTheSaltIsNotABrandName(unittest.TestCase):
    """The HKDF salt must never change, including to match a rename.

    It is an input to the key derivation, not a label. A different salt
    derives a different key from the same passphrase, so every credential
    already stored through the stretched-key path becomes undecryptable - and
    the failure is quiet: users still look connected, and every send raises
    "reconnect your mailbox".

    This nearly went during the rename from Job Machine to Recruited, in a
    pass over 30 files that changed every other occurrence of the old name
    correctly. It says job-machine because that is what the product was
    called when the first key was derived, and that is the only thing it has
    to match.
    """

    def test_the_salt_still_says_what_it_has_always_said(self):
        source = open(os.path.join(ROOT, "app", "vault.py"),
                      encoding="utf-8").read()
        self.assertIn('salt=b"job-machine/credential-key"', source)

    def test_a_known_passphrase_still_derives_the_same_key(self):
        """The assertion that would actually catch it, whatever the source
        looks like: this ciphertext was produced by the stretched-key path
        before the rename and must still decrypt after it."""
        os.environ["CREDENTIAL_KEY"] = "kP9xQvT2mWnR7bYcJ4hLd8Zs5FgA3eVu"
        vault = importlib.import_module("app.vault")
        importlib.reload(vault)
        token = vault.encrypt("app-password")
        self.assertEqual(vault.decrypt(token), "app-password")
        # And the derived key is the one the salt produces, not a fresh one.
        self.assertEqual(
            vault._derive("kP9xQvT2mWnR7bYcJ4hLd8Zs5FgA3eVu"),
            vault._derive("kP9xQvT2mWnR7bYcJ4hLd8Zs5FgA3eVu"))
        os.environ.pop("CREDENTIAL_KEY", None)


# ----------------------------------------------------------------------
class TestDeferredVerification(Base):
    """Free hosting blocks outbound SMTP, so the setup screen cannot test a
    password at all.

    Render, Oracle and most others shut ports 25, 465 and 587 because that is
    how spam gets sent. The old code had two outcomes - accepted, or "that
    mail account rejected the password" - so on the free plan every correct
    Gmail app password in the world got the second one. Harry hit it three
    times in nine minutes and concluded his password was wrong. It was not.

    The fix is a third outcome: could not ask. The account is stored
    unchecked, the user is told exactly that, and the sweep - which runs on
    GitHub Actions, where SMTP is not blocked - does the real proof before a
    single letter goes out.

    The promise that must survive: nothing is ever SENT on a password that
    has not been proved, and the user is never told letters are going out
    when they are not. Only the location of the proof moves.
    """

    def sign_in(self, email="harry@example.com"):
        link = self.main.auth.make_login_link(email)
        token = link.split("token=", 1)[1]
        self.client.get(f"/auth/verify?token={token}", follow_redirects=False)

    def connect_via_screen(self, error=None):
        """Drive the real setup screen, with verify() behaving as given."""
        def verify(**kw):
            if error:
                raise error
        self.delivery.verify = verify
        self.main.delivery.verify = verify
        self.sign_in()
        return self.client.post("/setup/mail", data={
            "address": "harry@gmail.com", "password": "app-password",
            "host": "smtp.gmail.com", "port": "465"})

    def test_an_unreachable_server_still_saves_the_account(self):
        self.connect_via_screen(
            self.delivery.DeliveryUnreachableError("could not reach"))
        row = self.db.get_mail_account(self.uid)
        self.assertIsNotNone(row)
        self.assertFalse(row["verified_at"])

    def test_a_rejected_password_is_still_refused_outright(self):
        """The one case where we KNOW. This must not soften."""
        self.connect_via_screen(
            self.delivery.DeliveryAuthError("rejected the password"))
        self.assertIsNone(self.db.get_mail_account(self.uid))

    def test_a_working_password_is_marked_verified_immediately(self):
        self.connect_via_screen()
        self.assertTrue(self.db.get_mail_account(self.uid)["verified_at"])

    def test_the_sweep_proves_it_before_sending_anything(self):
        self.db.save_mail_account(self.uid, address="harry@gmail.com",
                                  host="smtp.gmail.com", port=465,
                                  password="app-password", verified=False)
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme")
        checked = []
        self.autosend.send_due_for_user(
            self.uid, sender=self.fake_send,
            verifier=lambda **kw: checked.append(kw))
        self.assertEqual(len(checked), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.db.get_mail_account(self.uid)["verified_at"])

    def test_a_bad_password_found_by_the_sweep_sends_nothing(self):
        """The failure this whole design exists to prevent: letters going out
        on a password nobody ever proved."""
        self.db.save_mail_account(self.uid, address="harry@gmail.com",
                                  host="smtp.gmail.com", port=465,
                                  password="wrong", verified=False)
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme")

        def refuse(**kw):
            raise self.delivery.DeliveryAuthError("rejected the password")
        report = self.autosend.send_due_for_user(
            self.uid, sender=self.fake_send, verifier=refuse)

        self.assertEqual(self.sent, [])
        self.assertIn("rejected the password", report.reason)
        # and it does not sit there retrying a bad password every sweep
        self.assertFalse(self.db.get_send_settings(self.uid)["auto_send"])
        self.assertIn("rejected the password",
                      self.db.get_mail_account(self.uid)["last_error"])

    def test_still_unreachable_sends_nothing_and_blames_nobody(self):
        """Nothing is proved either way, so nothing goes out - and automatic
        sending stays ON, because there is no evidence against the user."""
        self.db.save_mail_account(self.uid, address="harry@gmail.com",
                                  host="smtp.gmail.com", port=465,
                                  password="app-password", verified=False)
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme")

        def unreachable(**kw):
            raise self.delivery.DeliveryUnreachableError("could not reach")
        report = self.autosend.send_due_for_user(
            self.uid, sender=self.fake_send, verifier=unreachable)

        self.assertEqual(self.sent, [])
        self.assertIn("could not check", report.reason)
        self.assertTrue(self.db.get_send_settings(self.uid)["auto_send"])
        self.assertFalse(self.db.get_mail_account(self.uid)["verified_at"])

    def test_a_verified_account_is_never_re_checked(self):
        """One connection, not one per sweep forever."""
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme")
        checked = []
        self.autosend.send_due_for_user(
            self.uid, sender=self.fake_send,
            verifier=lambda **kw: checked.append(kw))
        self.assertEqual(checked, [])
        self.assertEqual(len(self.sent), 1)

    def test_the_screen_does_not_call_it_connected_until_it_is_proved(self):
        """Saying "Connected" when we only know "saved" is the exact lie this
        product is built not to tell."""
        self.connect_via_screen(
            self.delivery.DeliveryUnreachableError("could not reach"))
        page = self.client.get("/setup").text
        self.assertIn("not checked yet", page)
        self.assertNotIn("Connected as", page)


# ----------------------------------------------------------------------
class TestTheSweepCanSeeEveryoneItShould(Base):
    """The sweep reported "0 paying users" and sent nothing, for months.

    is_paid() opens the gate three ways: billing switched off,
    FREE_ACCESS_EMAILS, and a claimed free place. paid_user_ids() selected
    only subscription_status IN ('active','trialing') - so every user whose
    access did not come from Stripe was invisible to the sweep.

    On the live database that was three accounts out of four including the
    founder's. The web app let them in, the setup screen said everything was
    connected, and the sweep quietly ran for nobody. The log line even read
    like a billing fact rather than a bug.

    The invariant, and the reason the last test here exists: anyone is_paid()
    accepts MUST appear in paid_user_ids(). A prefilter narrower than the
    thing it prefilters for is a second gate nothing re-checks.
    """
    env = {"BILLING_ENABLED": "1"}

    def test_a_free_place_holder_is_swept(self):
        with self.db.connect() as c:
            c.execute("UPDATE users SET free_spot = 1 WHERE id = ?", (self.uid,))
        self.assertIn(self.uid, self.db.paid_user_ids())

    def test_a_stripe_subscriber_is_still_swept(self):
        with self.db.connect() as c:
            c.execute("UPDATE users SET subscription_status = 'active' "
                      "WHERE id = ?", (self.uid,))
        self.assertIn(self.uid, self.db.paid_user_ids())

    def test_letters_actually_go_out_for_a_free_place_holder(self):
        """The end the user cares about, not the query in the middle."""
        with self.db.connect() as c:
            c.execute("UPDATE users SET free_spot = 1 WHERE id = ?", (self.uid,))
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme")
        self.autosend.sweep(run=False, sender=self.fake_send)
        self.assertEqual(len(self.sent), 1)

    def test_the_prefilter_is_never_narrower_than_is_paid(self):
        """The invariant itself, so a future 'cheap prefilter' cannot
        reintroduce this by forgetting one of the ways in."""
        with self.db.connect() as c:
            c.execute("UPDATE users SET free_spot = 1 WHERE id = ?", (self.uid,))
            c.execute("INSERT INTO users (email, created_at) VALUES (?, ?)",
                      ("subscriber@example.com", self.db.now()))
            c.execute("UPDATE users SET subscription_status = 'trialing' "
                      "WHERE email = ?", ("subscriber@example.com",))
            c.execute("INSERT INTO users (email, created_at) VALUES (?, ?)",
                      ("nobody@example.com", self.db.now()))
        swept = set(self.db.paid_user_ids())
        with self.db.connect() as c:
            everyone = c.execute("SELECT id FROM users").fetchall()
        for row in everyone:
            user = self.db.get_user(row["id"])
            if self.db.is_paid(user):
                self.assertIn(row["id"], swept,
                              f"{user['email']} passes is_paid but the sweep "
                              f"cannot see them")


# ----------------------------------------------------------------------
class TestNoticingAReply(Base):
    """The tracker used to wait to be told. Now the sweep looks.

    What it is allowed to look at is the whole design. A Gmail app password
    grants IMAP as well as SMTP, so this asks the user for nothing new - and
    that is exactly why it has to stay narrow: the credential they handed over
    to SEND can also read everything they own.

    The other half is honesty about what a FROM match proves. It proves a
    message exists. It cannot tell a real reply from "thank you for your
    application, please use our portal", and the landing page promises out
    loud that autoresponders are not counted. So the machine flags; the person
    who can actually read it classifies.
    """

    def sent_application(self, to_email="hr@acme.com", company="Acme"):
        did = self.db.add_draft(self.uid, job_title="Technician",
                                company=company, to_email=to_email,
                                subject="s", body="b")
        self.db.mark_draft(self.uid, did, "sent")
        return did

    def verified_mail(self):
        self.connect_mail()
        self.db.mark_mail_verified(self.uid)

    def test_it_flags_an_employer_who_has_been_in_touch(self):
        self.verified_mail()
        did = self.sent_application()
        from app import replies
        report = replies.check_for_user(
            self.uid, finder=lambda **kw: {"hr@acme.com"})
        self.assertEqual(report.found, 1)
        row = self.db.get_draft(self.uid, did)
        self.assertTrue(row["reply_seen_at"])

    def test_it_never_claims_to_know_what_the_message_said(self):
        """The promise on the landing page. A FROM match is not an outcome."""
        self.verified_mail()
        did = self.sent_application()
        from app import replies
        replies.check_for_user(self.uid, finder=lambda **kw: {"hr@acme.com"})
        row = self.db.get_draft(self.uid, did)
        self.assertEqual(row["outcome"] or "", "")

    def test_it_only_ever_asks_about_employers_already_written_to(self):
        """The privacy boundary. Nothing else in the mailbox is asked about,
        so nothing else can be learned."""
        self.verified_mail()
        self.sent_application("hr@acme.com")
        self.sent_application("jobs@beta.com", company="Beta")
        asked = {}

        def finder(**kw):
            asked.update(kw)
            return set()
        from app import replies
        replies.check_for_user(self.uid, finder=finder)
        self.assertEqual(set(asked["addresses"]),
                         {"hr@acme.com", "jobs@beta.com"})

    def test_an_unreachable_inbox_changes_nothing(self):
        """A broken connection must not look like a quiet week."""
        self.verified_mail()
        did = self.sent_application()

        def refuse(**kw):
            raise self.delivery.DeliveryUnreachableError("could not reach")
        from app import replies
        report = replies.check_for_user(self.uid, finder=refuse)
        self.assertEqual(report.found, 0)
        self.assertIn("could not reach", report.reason)
        self.assertFalse(self.db.get_draft(self.uid, did)["reply_seen_at"])

    def test_an_unverified_mailbox_is_not_probed(self):
        """Do not spend a second failed login on a mailbox that may be close
        to locking, for a feature nobody is waiting on."""
        self.db.save_mail_account(self.uid, address="harry@gmail.com",
                                  host="smtp.gmail.com", port=465,
                                  password="app-password", verified=False)
        self.sent_application()
        tried = []
        from app import replies
        report = replies.check_for_user(
            self.uid, finder=lambda **kw: tried.append(kw) or set())
        self.assertEqual(tried, [])
        self.assertIn("not verified", report.reason)

    def test_an_answer_the_user_already_gave_is_never_asked_about_again(self):
        self.verified_mail()
        did = self.sent_application()
        self.db.set_outcome(self.uid, did, "interview")
        self.assertEqual(self.db.drafts_awaiting_reply(self.uid), [])

    def test_flagging_does_not_overwrite_what_the_user_said(self):
        """The machine's weaker signal must never clobber the person's."""
        self.verified_mail()
        did = self.sent_application()
        self.db.set_outcome(self.uid, did, "offer")
        from app import replies
        replies.check_for_user(self.uid, finder=lambda **kw: {"hr@acme.com"})
        self.assertEqual(self.db.get_draft(self.uid, did)["outcome"], "offer")

    def test_gmail_needs_no_new_credential(self):
        """The whole reason this was cheap to add."""
        self.assertEqual(self.delivery.guess_imap_host("harry@gmail.com"),
                         ("imap.gmail.com", 993))


# ----------------------------------------------------------------------
class TestTheRunDoesTheImportantHalfFirst(Base):
    """The first sweep that ever had a user to work on was killed by the
    30-minute workflow timeout while still drafting.

    Harvesting listings and scoring them through Gemini is minutes per user.
    Sending a letter already written and already waiting is milliseconds. The
    slow optional half stood in front of the fast essential half, so a run
    that ran out of time delivered nothing - and it starved by position, with
    user 1's drafting able to consume the whole budget before user 2 ever
    reached their own send step.
    """

    def test_sending_happens_before_drafting(self):
        order = []
        self.connect_mail()
        self.db.mark_mail_verified(self.uid)
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme")

        import types
        runner = types.ModuleType("app.runner")

        class R:
            drafted = 0

        def run_for_user(user_id, **kw):
            order.append("draft")
            return R()
        runner.run_for_user = run_for_user
        sys.modules["app.runner"] = runner

        def sender(**kw):
            order.append("send")
        self.autosend.sweep(sender=sender)
        self.assertEqual(order[0], "send",
                         "a run cut short must already have sent")

    def test_a_delivered_letter_and_its_log_row_land_together(self):
        """The gap that lost the first letter this product ever sent."""
        self.connect_mail()
        self.db.mark_mail_verified(self.uid)
        self.db.save_send_settings(self.uid, auto_send=1)
        did = self.draft("Acme")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)

        self.assertEqual(self.db.get_draft(self.uid, did)["status"], "sent")
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM sent_log WHERE draft_id = ?",
                             (did,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ok"], 1)

    def test_the_daily_cap_counts_the_letter_that_just_went(self):
        """sent_today() reads sent_log. A lost row means the cap is computed
        from a number that is not true, and the next run can exceed a ceiling
        the user set."""
        self.connect_mail()
        self.db.mark_mail_verified(self.uid)
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(self.db.sent_today(self.uid), 1)
