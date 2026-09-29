"""Companies House: only a clear match counts, and names are only names.

  1. DORMANT without a key: nothing is asked.
  2. ONE EXACT MATCH OR NOTHING. Two "Smith Engineering"s is not an answer.
  3. A DISSOLVED OR LIQUIDATED employer is "gone"; administration is not.
  4. DIRECTORS only for a small firm, current ones only, as readable names.
  5. EVERY ANSWER IS CACHED, including "no clear match".
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker import companies_house as ch  # noqa: E402


class Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


class Api:
    def __init__(self, search, profile=None, officers=None):
        self.search, self.profile, self.officers = search, profile, officers
        self.asked = []

    def get(self, url, auth, timeout):
        self.asked.append(url)
        if "/search/companies" in url:
            return Resp(200, {"items": self.search})
        if url.endswith("/officers?items_per_page=50"):
            return Resp(200, {"items": self.officers or []})
        return Resp(200, self.profile or {})


def item(title, status="active", number="SC123456"):
    return {"title": title, "company_status": status, "company_number": number}


class Base(unittest.TestCase):
    def setUp(self):
        ch._memory.clear()
        self.addCleanup(ch._memory.clear)
        self.addCleanup(setattr, ch, "store", None)
        ch.store = None
        patcher = patch.dict(os.environ, {"COMPANIES_HOUSE_API_KEY": "k"})
        patcher.start()
        self.addCleanup(patcher.stop)


class Matching(Base):
    def test_dormant_without_a_key(self):
        api = Api([item("ACME LTD", "dissolved")])
        with patch.dict(os.environ, {"COMPANIES_HOUSE_API_KEY": ""}):
            self.assertEqual(ch.gone("Acme Ltd", get=api.get), "")
        self.assertEqual(api.asked, [])

    def test_one_exact_match(self):
        api = Api([item("ACME ENGINEERING LIMITED", "dissolved"),
                   item("ACME ENGINEERING SERVICES LTD")])
        self.assertEqual(ch.gone("Acme Engineering Ltd", get=api.get),
                         "dissolved")

    def test_two_matches_is_no_answer(self):
        api = Api([item("SMITH ENGINEERING LTD", "dissolved", "1"),
                   item("SMITH ENGINEERING LIMITED", "active", "2")])
        self.assertEqual(ch.gone("Smith Engineering", get=api.get), "")

    def test_administration_is_still_trading(self):
        api = Api([item("ACME LTD", "administration")])
        self.assertEqual(ch.gone("Acme Ltd", get=api.get), "")

    def test_a_failure_keeps_the_job(self):
        def down(url, auth, timeout):
            raise OSError("down")
        self.assertEqual(ch.gone("Acme Ltd", get=down), "")

    def test_cached_including_no_match(self):
        api = Api([])
        ch.gone("Nobody Ltd", get=api.get)
        ch.gone("Nobody Ltd", get=api.get)
        self.assertEqual(len(api.asked), 1)


class Directors(Base):
    def officers(self):
        return [{"name": "SMITH, Jane Anne", "officer_role": "director"},
                {"name": "BROWN, Tom", "officer_role": "director",
                 "resigned_on": "2020-01-01"},
                {"name": "JONES, Amy", "officer_role": "secretary"}]

    def test_current_directors_of_a_small_firm(self):
        api = Api([item("ACME LTD")],
                  {"accounts": {"last_accounts": {"type": "micro-entity"}}},
                  self.officers())
        self.assertEqual(ch.directors("Acme Ltd", get=api.get),
                         ["Jane Anne Smith"])

    def test_not_for_a_big_firm(self):
        api = Api([item("ACME LTD")],
                  {"accounts": {"last_accounts": {"type": "full"}}},
                  self.officers())
        self.assertEqual(ch.directors("Acme Ltd", get=api.get), [])


if __name__ == "__main__":
    unittest.main()
