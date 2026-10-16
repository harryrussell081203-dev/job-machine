"""The weekly organisation outreach run: rehearsal until it is configured,
once per organisation ever, a week's quota per week, from the site's own
mail route with replies to a person."""

import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")

from test_app import AppTestCase  # noqa: E402
from test_outreach import FakeSession  # noqa: E402


def orgs(n):
    return [{"name": f"Job Club {i}", "website": f"https://club{i}.org"}
            for i in range(n)]


LIVE = {"OUTREACH_REPLY_TO": "harry@recruited.org.uk",
        "BREVO_API_KEY": "k", "APP_SMTP_ADDRESS": "hello@recruited.org.uk",
        "OUTREACH_PER_RUN": "3"}


class TheWeeklyRun(AppTestCase):
    def setUp(self):
        super().setUp()
        from app import org_outreach
        self.o = org_outreach
        self.sent = []
        self.session = FakeSession({"/contact": "info@{host}"})

    def go(self, n=5):
        return self.o.run(orgs(n), session=self.session,
                          send_mail=lambda *a, **k: self.sent.append((a, k)),
                          delay=0, scrape_delay=0, out=lambda *a: None)

    def test_a_rehearsal_until_a_reply_address_is_set(self):
        with patch.dict(os.environ, dict(LIVE, OUTREACH_REPLY_TO="",
                                         OUTREACH_PER_RUN="20")), \
                patch("app.config.mail_route", lambda: "brevo"):
            done = self.go()
        self.assertEqual(self.sent, [])
        self.assertEqual(done["would_send"], 5)
        self.assertNotIn("club0.org", self.o.LogState())

    def test_live_sends_with_replies_to_a_person(self):
        with patch.dict(os.environ, LIVE), \
                patch("app.config.mail_route", lambda: "brevo"):
            done = self.go()
        self.assertEqual(done["sent"], 3)
        (to, subject, body), kwargs = self.sent[0]
        self.assertEqual(to, "info@club0.org")
        self.assertEqual(kwargs["reply_to"], "harry@recruited.org.uk")
        self.assertEqual(kwargs["sender_name"], "Harry Russell")
        self.assertIn("club0.org", self.o.LogState())

    def test_once_ever_and_a_weeks_quota_per_week(self):
        with patch.dict(os.environ, LIVE), \
                patch("app.config.mail_route", lambda: "brevo"):
            self.go()
            again = self.go()
        self.assertEqual(again, {"sent": 0, "reason": "quota"})
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(len({a[0] for a, _ in self.sent}), 3)

    def test_the_reply_address_can_live_in_the_database(self):
        """So a personal address is never in a public repository's
        settings or logs."""
        from app import db
        db.set_meta("outreach_reply_to", "owner@example.com")
        with patch.dict(os.environ, dict(LIVE, OUTREACH_REPLY_TO="")), \
                patch("app.config.mail_route", lambda: "brevo"):
            done = self.go()
        self.assertEqual(done["sent"], 3)
        self.assertEqual(self.sent[0][1]["reply_to"], "owner@example.com")

    def test_the_setting_wins_over_the_database(self):
        from app import db
        db.set_meta("outreach_reply_to", "owner@example.com")
        with patch.dict(os.environ, LIVE):
            self.assertEqual(self.o.reply_to_address(), "harry@recruited.org.uk")

    def test_a_days_letters_are_a_day_s(self):
        """Every weekday now, so a day's share of the week, not all of it on
        Monday."""
        with patch.dict(os.environ, dict(LIVE, OUTREACH_PER_RUN="150",
                                         OUTREACH_PER_DAY="2")), \
                patch("app.config.mail_route", lambda: "brevo"):
            first = self.go()
            again = self.go()
        self.assertEqual(first["sent"], 2)
        self.assertEqual(again, {"sent": 0, "reason": "quota"})

    def test_yesterdays_letters_are_not_on_todays_count(self):
        import time
        from app import db
        with db.connect() as c:
            for i in range(5):
                c.execute("INSERT INTO outreach_log (org_key, name, address, "
                          "outcome, at) VALUES (?, '', '', 'sent', ?)",
                          (f"old{i}.org", int(time.time()) - 22 * 3600))
        self.assertEqual(self.o.sent_today(), 0)
        self.assertEqual(self.o.sent_this_week(), 5)

    def test_the_lists_are_taken_in_turn_and_each_site_once(self):
        a = [{"name": "A1", "website": "https://a1.org"},
             {"name": "A2", "website": "https://a2.org"},
             {"name": "A3", "website": "https://a3.org"}]
        b = [{"name": "B1", "website": "https://www.a1.org", "kind": "careers"},
             {"name": "B2", "website": "https://b2.ac.uk", "kind": "careers"}]
        got = [o["name"] for o in self.o.in_turn([a, b, []])]
        self.assertEqual(got, ["A1", "A2", "B2", "A3"])

    def test_one_kind_on_its_own_from_several_files(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            one, two = os.path.join(d, "1.json"), os.path.join(d, "2.json")
            with open(one, "w") as f:
                json.dump(orgs(2), f)
            with open(two, "w") as f:
                json.dump([{"name": "Uni", "website": "https://u.ac.uk",
                            "kind": "careers"}], f)
            seen = []
            with patch.object(self.o, "run", lambda o: seen.append(o) or {}):
                self.o.main(["--orgs", one, "--orgs", two,
                             "--orgs", os.path.join(d, "missing.json"),
                             "--only", "careers"])
        self.assertEqual([o["name"] for o in seen[0]], ["Uni"])

    def test_switched_off(self):
        with patch.dict(os.environ, dict(LIVE, OUTREACH_ENABLED="0")), \
                patch("app.config.mail_route", lambda: "brevo"):
            self.go()
        self.assertEqual(self.sent, [])


LETTER = ("A free tool for the jobseekers you work with",
          "Hello,\n\nA free tool that finds the address a company publishes: "
          "https://recruited.org.uk/find\n\nHarry Russell")


class ThroughTheSite(AppTestCase):
    """Brevo refused the first live run from a GitHub runner's address
    (16 October). Letters now go through the website, which must not become
    a way for anybody else to send mail."""

    def setUp(self):
        super().setUp()
        from app import auth, db, org_outreach
        self.o = org_outreach
        db.set_meta("outreach_reply_to", "owner@example.com")
        self.sent = []
        real = auth.send_app_email
        auth.send_app_email = lambda *a, **k: self.sent.append((a, k))
        self.addCleanup(setattr, auth, "send_app_email", real)

    def post(self, to="info@club.org", token=None, subject=None, body=None):
        return self.client.post(
            "/internal/outreach/send",
            json={"to": to, "subject": subject or LETTER[0],
                  "body": body or LETTER[1]},
            headers={"Authorization": "Bearer " + (token or self.o.site_token())})

    def test_only_with_the_sites_own_secret(self):
        self.assertEqual(self.post(token="nope").status_code, 403)
        self.assertEqual(self.sent, [])

    def test_sent_once_as_harry_with_replies_to_the_owner(self):
        self.assertEqual(self.post().json(), {"ok": True})
        self.assertEqual(self.post().json(), {"ok": True, "already": True})
        self.assertEqual(len(self.sent), 1)
        (to, subject, body), kw = self.sent[0]
        self.assertEqual(to, "info@club.org")
        self.assertEqual(kw, {"reply_to": "owner@example.com",
                              "sender_name": "Harry Russell"})

    def test_a_careers_service_and_an_employment_team(self):
        self.assertEqual(self.post(to="careers@leeds.ac.uk").status_code, 200)
        self.assertEqual(self.post(to="employability@fife.gov.uk").status_code, 200)
        self.assertEqual(self.post(to="hr@leeds.ac.uk").status_code, 422)
        self.assertEqual(len(self.sent), 2)

    def test_never_a_person_and_never_a_banned_letter(self):
        self.assertEqual(self.post(to="jane.smith@club.org").status_code, 422)
        self.assertEqual(self.post(body="I am excited to leverage synergies "
                                        "as a passionate team player.").status_code, 422)
        self.assertEqual(self.sent, [])

    def test_the_job_hands_letters_to_the_site_and_retries_a_sleeping_one(self):
        calls = []

        class R:
            def __init__(self, status):
                self.status_code, self.text = status, ""

        answers = [R(502), R(200)]

        def post(url, json=None, headers=None, timeout=None):
            calls.append((url, json, headers))
            return answers.pop(0)
        self.o.send_via_site("info@club.org", *LETTER, post=post, pause=0)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][0].endswith("/internal/outreach/send"))
        self.assertEqual(calls[0][2]["Authorization"],
                         "Bearer " + self.o.site_token())

    def test_a_refusal_is_not_retried(self):
        calls = []

        def post(url, **kw):
            calls.append(url)
            return type("R", (), {"status_code": 422, "text": "no"})()
        with self.assertRaises(RuntimeError):
            self.o.send_via_site("info@club.org", *LETTER, post=post, pause=0)
        self.assertEqual(len(calls), 1)


class TheCharityCommissionFile(unittest.TestCase):
    def test_tab_separated_with_status_and_email(self):
        from tools import find_orgs
        text = ("charity_name\tcharity_registration_status\tcharity_contact_web"
                "\tcharity_contact_email\tcharity_contact_postcode"
                "\tcharity_activities\n"
                "Leeds Job Club\tRegistered\twww.leedsjobclub.org\t"
                "info@leedsjobclub.org\tLS1 1AA\tEmployability support and "
                "job search help for unemployed people\n"
                "Old Job Club\tRemoved\twww.oldjobclub.org\t\t\tjob club\n"
                "Leeds Choir\tRegistered\twww.leedschoir.org\t\t\tSinging\n")
        out = find_orgs.select(find_orgs.rows_from_csv(text))
        self.assertEqual([o["name"] for o in out], ["Leeds Job Club"])
        self.assertEqual(out[0]["email"], "info@leedsjobclub.org")
        self.assertEqual(out[0]["website"], "https://www.leedsjobclub.org")

    def test_writing_the_list_to_a_bare_file_name(self):
        """The workflow writes orgs.json in the working directory, which has
        no directory part; the first run on the full register died there."""
        import json
        import tempfile
        from tools import find_orgs
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "reg.txt")
            with open(src, "w") as f:
                f.write("charity_name\tcharity_contact_web\tcharity_activities"
                        "\nLeeds Job Club\twww.leedsjobclub.org\tjob club\n")
            here = os.getcwd()
            os.chdir(d)
            try:
                find_orgs.main(["--from-file", src, "--out", "orgs.json"])
                with open("orgs.json") as f:
                    self.assertEqual(json.load(f)[0]["name"], "Leeds Job Club")
            finally:
                os.chdir(here)

    def test_a_very_long_field_does_not_stop_the_run(self):
        from tools import find_orgs
        text = ("charity_name\tcharity_contact_web\tcharity_activities\n"
                "Big Words\twww.bigwords.org\t" + "word " * 60000 + "\n"
                "Leeds Job Club\twww.leedsjobclub.org\tjob club\n")
        out = find_orgs.select(find_orgs.rows_from_csv(text))
        self.assertEqual([o["name"] for o in out], ["Leeds Job Club"])


if __name__ == "__main__":
    unittest.main()
