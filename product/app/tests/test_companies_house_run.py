"""A dissolved employer is set aside before any model call, in a real run."""

import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from jobseeker import companies_house  # noqa: E402
from app.tests.test_runner import Resp, RunnerTestCase, Session  # noqa: E402


class CHSession(Session):
    def __init__(self, status):
        super().__init__()
        self.status = status

    def get(self, url, **kw):
        if "company-information.service.gov.uk" in url:
            self.urls.append(url)
            if "/officers" in url:
                return Resp(payload={"items": [
                    {"name": "SMITH, Jane", "officer_role": "director"}]})
            if "/company/" in url:
                return Resp(payload={"accounts": {"last_accounts": {
                    "type": "small"}}})
            return Resp(payload={"items": [{
                "title": "PENNINE FOODS LIMITED", "company_number": "01234567",
                "company_status": self.status}]})
        return super().get(url, **kw)


class Dissolved(RunnerTestCase):
    def setUp(self):
        super().setUp()
        companies_house._memory.clear()
        self.addCleanup(companies_house._memory.clear)
        patcher = patch.dict(os.environ, {"COMPANIES_HOUSE_API_KEY": "k"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_model_is_paid_to_read_it(self):
        prompts = []
        report = self.run_once(session=CHSession("dissolved"),
                               ai=lambda p: (prompts.append(p), "[]")[1])
        self.assertEqual(report.dissolved, 1)
        self.assertEqual(prompts, [])

    def test_an_active_one_goes_ahead(self):
        report = self.run_once(session=CHSession("active"))
        self.assertEqual(report.dissolved, 0)
        self.assertEqual(report.drafted, 1, report.errors)
        d = self.db.list_drafts(self.user["id"])[0]
        self.assertEqual(d["directors"], "Jane Smith")


if __name__ == "__main__":
    unittest.main()
