"""The Search Console report: the checks, the signed token, the weekly run,
and the admin page that shows it."""

import base64
import datetime
import json
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

from app import search_console as gsc  # noqa: E402


def row(query, page="/", clicks=0, impressions=0, position=1.0, ctr=None):
    if ctr is None:
        ctr = clicks / impressions if impressions else 0.0
    return {"query": query, "page": page, "clicks": clicks,
            "impressions": impressions, "ctr": ctr, "position": position}


class TheChecks(unittest.TestCase):
    def test_nearly_there_is_positions_8_to_20_with_enough_impressions(self):
        rows = [row("email hiring manager", "/answers/email-hiring-manager-directly",
                    impressions=120, position=11.2),
                row("rare search", impressions=3, position=12),
                row("already top", impressions=200, clicks=60, position=1.4),
                row("buried", impressions=90, position=34)]
        out = gsc.analyse(rows)
        self.assertEqual([r["query"] for r in out["nearly_there"]],
                         ["email hiring manager"])

    def test_seen_not_clicked_is_well_under_the_usual_rate(self):
        rows = [row("job email subject", impressions=400, clicks=2,
                    position=3.1),          # 0.5% against about 10%
                row("cover letter", impressions=400, clicks=40,
                    position=3.0)]          # 10%: fine
        out = gsc.analyse(rows)
        self.assertEqual([r["query"] for r in out["seen_not_clicked"]],
                         ["job email subject"])
        self.assertEqual(out["seen_not_clicked"][0]["typical_ctr"], 0.10)

    def test_a_search_no_answer_page_covers_is_listed(self):
        rows = [row("how to ask for a pay rise", impressions=50, position=40),
                row("why do i never hear back from job applications",
                    "/answers/why-no-reply", impressions=80, position=9)]
        out = gsc.analyse(rows)
        self.assertEqual([q["query"] for q in out["unanswered"]],
                         ["how to ask for a pay rise"])

    def test_one_search_on_two_pages_is_flagged(self):
        rows = [row("find hiring manager email", "/find", impressions=30),
                row("find hiring manager email", "/answers/no-contact-details",
                    impressions=25)]
        out = gsc.analyse(rows)
        self.assertEqual(out["split"][0]["pages"],
                         ["/answers/no-contact-details", "/find"])

    def test_movers_compare_with_the_window_before(self):
        now = [row("a", "/find", clicks=12), row("b", "/", clicks=1)]
        before = [row("a", "/find", clicks=2), row("b", "/", clicks=5)]
        out = gsc.analyse(now, before)
        self.assertEqual(out["movers"][0], {"page": "/find", "clicks": 12,
                                            "before": 2, "change": 10})
        self.assertEqual(out["movers"][1]["change"], -4)

    def test_nothing_in_nothing_out(self):
        out = gsc.analyse([], [])
        self.assertEqual((out["clicks"], out["impressions"], out["queries"]),
                         (0, 0, 0))
        self.assertEqual(out["nearly_there"], [])

    def test_full_urls_become_paths(self):
        self.assertEqual(gsc._path("https://recruited.org.uk/find?x=1"), "/find")
        self.assertEqual(gsc._path("https://recruited.org.uk/"), "/")
        self.assertEqual(gsc._path("https://recruited.org.uk"), "/")


def service_account():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return key, {"client_email": "reader@proj.iam.gserviceaccount.com",
                 "private_key": pem,
                 "token_uri": "https://oauth2.googleapis.com/token"}


class Reply:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class TalkingToGoogle(unittest.TestCase):
    def test_the_token_request_is_a_correctly_signed_jwt(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        key, account = service_account()
        jwt = gsc._assertion(account, 1_700_000_000)
        head, body, sig = jwt.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad(body)))
        self.assertEqual(claims["scope"],
                         "https://www.googleapis.com/auth/webmasters.readonly")
        self.assertEqual(claims["iss"], account["client_email"])
        self.assertEqual(claims["exp"] - claims["iat"], 3600)
        key.public_key().verify(base64.urlsafe_b64decode(pad(sig)),
                                f"{head}.{body}".encode(),
                                padding.PKCS1v15(), hashes.SHA256())

    def test_fetch_asks_for_the_verified_property_and_reads_rows(self):
        _, account = service_account()
        calls = []

        def post(url, **kw):
            calls.append((url, kw))
            if "oauth2" in url:
                return Reply({"access_token": "tok"})
            return Reply({"rows": [{"keys": ["email hr directly",
                                             "https://recruited.org.uk/find"],
                                    "clicks": 3, "impressions": 40,
                                    "ctr": 0.075, "position": 6.2}]})
        with patch.dict(os.environ, {
                "GSC_SERVICE_ACCOUNT_JSON": json.dumps(account),
                "GSC_SITE_URL": "https://recruited.org.uk/"}):
            rows = gsc.fetch(datetime.date(2026, 9, 1),
                             datetime.date(2026, 9, 28), post=post)
        self.assertIn("/sites/https%3A%2F%2Frecruited.org.uk%2F/searchAnalytics",
                      calls[1][0])
        self.assertEqual(calls[1][1]["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(calls[1][1]["json"]["dimensions"], ["query", "page"])
        self.assertEqual(rows, [{"query": "email hr directly", "page": "/find",
                                 "clicks": 3, "impressions": 40,
                                 "ctr": 0.075, "position": 6.2}])


class TheWeeklyRun(AppTestCase):
    def test_off_without_a_key(self):
        with patch.dict(os.environ, {"GSC_SERVICE_ACCOUNT_JSON": ""}):
            self.assertEqual(gsc.run(), {"reason": "off"})
        self.assertIsNone(gsc.latest())

    def test_two_28_day_windows_three_days_behind_and_stored(self):
        windows = []

        def fetch_rows(start, end):
            windows.append((start.isoformat(), end.isoformat()))
            return [row("email hiring manager", "/find", clicks=4,
                        impressions=60, position=9.0)]
        with patch.dict(os.environ, {"GSC_SERVICE_ACCOUNT_JSON": "{}"}):
            gsc.run(today=datetime.date(2026, 10, 31), fetch_rows=fetch_rows)
        self.assertEqual(windows, [("2026-10-01", "2026-10-28"),
                                   ("2026-09-03", "2026-09-30")])
        saved = gsc.latest()
        self.assertEqual(saved["clicks"], 4)
        self.assertEqual(saved["nearly_there"][0]["query"],
                         "email hiring manager")


class TheAdminPage(AppTestCase):
    env = {"ADMIN_EMAILS": "boss@example.com"}

    def test_says_what_is_missing_before_the_first_report(self):
        self.sign_in("boss@example.com")
        page = self.client.get("/admin").text
        self.assertIn("No Search Console report yet", page)

    def test_shows_the_report(self):
        from app import db
        report = gsc.analyse([row("email hiring manager", "/find", clicks=1,
                                  impressions=60, position=12.0),
                              row("ask for a pay rise", impressions=30,
                                  position=40)], [])
        report.update({"from": "2026-10-01", "to": "2026-10-28"})
        db.set_meta(gsc.META_KEY, json.dumps(report))
        self.sign_in("boss@example.com")
        page = self.client.get("/admin").text
        self.assertIn("Nearly there", page)
        self.assertIn("email hiring manager", page)
        self.assertIn("Questions without a page", page)
        self.assertIn("ask for a pay rise", page)


if __name__ == "__main__":
    unittest.main()
