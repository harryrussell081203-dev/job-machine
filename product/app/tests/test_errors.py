"""Sentry: nothing without a DSN, and nothing private with one.

  1. NO DSN, NOTHING STARTS and the privacy page does not mention it.
  2. WITH ONE, it starts with no personal data, no request bodies and no
     tracing, and the privacy page names it.
  3. EVERY EVENT IS SCRUBBED of bodies, cookies, headers and the user.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import AppTestCase  # noqa: E402


class Dormant(AppTestCase):
    def test_no_dsn_nothing_starts(self):
        from app import errors
        with patch.dict(os.environ, {"SENTRY_DSN": ""}), \
                patch.object(errors, "_started", False):
            self.assertFalse(errors.start("web"))

    def test_the_privacy_page_names_it_only_when_on(self):
        with patch.dict(os.environ, {"SENTRY_DSN": ""}):
            self.assertNotIn("Sentry", self.client.get("/privacy").text)
        with patch.dict(os.environ, {"SENTRY_DSN": "https://k@o0.ingest.sentry.io/1"}):
            self.assertIn("Sentry", self.client.get("/privacy").text)


class WhenOn(unittest.TestCase):
    def test_it_starts_with_nothing_private(self):
        from app import errors
        seen = {}
        import sentry_sdk
        with patch.dict(os.environ, {"SENTRY_DSN": "https://k@o0.ingest.sentry.io/1"}), \
                patch.object(errors, "_started", False), \
                patch.object(sentry_sdk, "init",
                             side_effect=lambda **kw: seen.update(kw)), \
                patch.object(sentry_sdk, "set_tag"):
            self.assertTrue(errors.start("sweep"))
        self.assertFalse(seen["send_default_pii"])
        self.assertEqual(seen["max_request_body_size"], "never")
        self.assertEqual(seen["traces_sample_rate"], 0.0)

    def test_events_are_scrubbed(self):
        from app import errors
        event = {"request": {"url": "/setup/cv", "data": "CV TEXT",
                             "cookies": {"s": "x"}, "headers": {"a": "b"},
                             "query_string": "token=x"},
                 "user": {"email": "a@b.example"}}
        out = errors._scrub(event, {})
        self.assertEqual(out["request"], {"url": "/setup/cv"})
        self.assertNotIn("user", out)


if __name__ == "__main__":
    unittest.main()
