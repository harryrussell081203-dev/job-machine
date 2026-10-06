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

    def test_switched_off(self):
        with patch.dict(os.environ, dict(LIVE, OUTREACH_ENABLED="0")), \
                patch("app.config.mail_route", lambda: "brevo"):
            self.go()
        self.assertEqual(self.sent, [])


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
