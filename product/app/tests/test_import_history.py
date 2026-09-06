"""Tests for tools/import_history.py.

This tool runs once, by hand, immediately before a mailbox that has already
sent 102 applications gets connected to something that sends more. If it is
wrong, the first anybody hears is an employer receiving a second cold email
from a man who already wrote to them - and by then it has been sent.

So the tests are about the two ways it can be wrong quietly:

  - it imports fewer companies than it should, and the gap is invisible
  - it imports nothing at all and reports that cheerfully
"""

import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, PRODUCT)

from cryptography.fernet import Fernet  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.db_path)
                        and os.unlink(self.db_path))
        os.environ.update(DEV_MODE="1", BILLING_ENABLED="0", SECRET_KEY="t",
                          DATABASE_URL="", DB_PATH=self.db_path,
                          CREDENTIAL_KEY=Fernet.generate_key().decode())
        for mod in ("app.config", "app.store", "app.db"):
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
            else:
                importlib.import_module(mod)
        self.db = sys.modules["app.db"]
        self.db.init()
        self.uid = self.db.get_or_create_user("harry@example.com")["id"]

        self.tool = importlib.import_module("tools.import_history")
        importlib.reload(self.tool)

        self.tmp = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmp, "state.json")
        self.dnc_path = os.path.join(self.tmp, "do_not_contact.json")
        self.tool.STATE = self.state_path
        self.tool.DNC = self.dnc_path
        self.write_state({})
        self.write_dnc([{"name": "Hydro Group", "reason": "employer"}])

    def write_state(self, obj):
        with open(self.state_path, "w") as f:
            json.dump(obj, f)

    def write_dnc(self, blocked):
        with open(self.dnc_path, "w") as f:
            json.dump({"_README": ["notes"], "blocked": blocked}, f)

    def run_tool(self, *args):
        # The tool's whole job is printing a report a person reads before
        # letting it write, so it prints a lot. Captured rather than
        # silenced: a test that needs to assert on the report can, and the
        # suite output stays readable.
        self.output = io.StringIO()
        with contextlib.redirect_stdout(self.output), \
                contextlib.redirect_stderr(self.output):
            return self.tool.main(["--user", "harry@example.com", *args])

    def contacted(self, name):
        return self.db.already_contacted(self.uid, name)


class TestWhatGetsImported(Base):
    def state_with(self, **over):
        base = {
            "companies_contacted": {
                "cirrus logic": {"company": "Cirrus Logic",
                                 "at": "2026-08-11T08:26:05+00:00"},
                # The register is keyed twice - once by company, once by
                # domain - and both entries carry the same display name.
                "cirruslogic.com": {"company": "Cirrus Logic",
                                    "at": "2026-08-11T08:26:05+00:00"},
            },
            "jobs": {
                "1": {"company": "Matchtech", "status": "sent",
                      "sent_at": "2026-08-14T09:00:00+00:00"},
                "2": {"company": "UKRI", "status": "replied",
                      "sent_at": "2026-08-15T09:00:00+00:00"},
                "3": {"company": "Omega Resource Group",
                      "status": "spec_sent",
                      "sent_at": "2026-08-16T09:00:00+00:00"},
                "4": {"company": "Never Wrote To Them", "status": "no_email"},
                "5": {"company": "Nor Them", "status": "skipped"},
                "6": {"company": "Not Yet", "status": "ready"},
            },
        }
        base.update(over)
        return base

    def test_everybody_written_to_is_recorded(self):
        self.write_state(self.state_with())
        self.assertEqual(self.run_tool(), 0)
        for name in ("Cirrus Logic", "Matchtech", "UKRI",
                     "Omega Resource Group"):
            self.assertTrue(self.contacted(name), name)

    def test_a_company_never_written_to_stays_contactable(self):
        """The opposite failure, and just as bad: importing `no_email` or
        `skipped` would block employers who have never heard from him."""
        self.write_state(self.state_with())
        self.run_tool()
        for name in ("Never Wrote To Them", "Nor Them", "Not Yet"):
            self.assertFalse(self.contacted(name), name)

    def test_the_name_is_re_keyed_here_not_carried_across(self):
        """The whole reason this tool exists rather than a SQL copy.

        job_machine.py and jobseeker/names.py both have a company_key() and
        they do not agree, so the personal machine's keys are not the
        product's. Names travel; keys are made on arrival.
        """
        self.write_state(self.state_with())
        self.run_tool()
        for variant in ("Cirrus Logic Ltd", "CIRRUS LOGIC", "cirrus logic",
                        "Cirrus Logic Limited"):
            self.assertTrue(self.contacted(variant), variant)

    def test_the_real_date_is_kept_rather_than_today(self):
        """first_at means "when this employer was first written to". Stamping
        them all with the moment the script ran is a table that lies about
        the one thing it records."""
        self.write_state(self.state_with())
        self.run_tool()
        with self.db.connect() as c:
            row = c.execute(
                "SELECT first_at FROM contacted WHERE user_id = ? AND "
                "company_key = ?", (self.uid, self.db.company_key("Matchtech"))
            ).fetchone()
        # 2026-08-14, not now.
        self.assertLess(row["first_at"], self.db.now() - 60)

    def test_the_earliest_date_wins_when_a_name_appears_twice(self):
        state = self.state_with()
        state["jobs"]["7"] = {"company": "Cirrus Logic", "status": "sent",
                              "sent_at": "2026-09-01T09:00:00+00:00"}
        self.write_state(state)
        self.run_tool()
        with self.db.connect() as c:
            row = c.execute(
                "SELECT first_at FROM contacted WHERE user_id = ? AND "
                "company_key = ?",
                (self.uid, self.db.company_key("Cirrus Logic"))).fetchone()
        import datetime
        when = datetime.datetime.fromtimestamp(
            row["first_at"], datetime.timezone.utc).date()
        self.assertEqual(when, datetime.date(2026, 8, 11))

    def test_running_it_twice_changes_nothing(self):
        self.write_state(self.state_with())
        self.run_tool()
        with self.db.connect() as c:
            before = c.execute("SELECT company_key, first_at FROM contacted "
                               "WHERE user_id = ?", (self.uid,)).fetchall()
        self.run_tool()
        with self.db.connect() as c:
            after = c.execute("SELECT company_key, first_at FROM contacted "
                              "WHERE user_id = ?", (self.uid,)).fetchall()
        self.assertEqual(sorted(dict(r).items() for r in before),
                         sorted(dict(r).items() for r in after))

    def test_a_dry_run_writes_nothing(self):
        self.write_state(self.state_with())
        self.assertEqual(self.run_tool("--dry-run"), 0)
        self.assertFalse(self.contacted("Cirrus Logic"))


class TestTheBlockList(Base):
    def test_the_employer_is_blocked(self):
        self.write_state({"jobs": {"1": {"company": "Acme",
                                         "status": "sent"}}})
        self.run_tool()
        self.assertFalse(self.db.may_contact(self.uid, "Hydro Group"))
        self.assertFalse(self.db.may_contact(self.uid, "Hydro Group Ltd"))

    def test_a_register_that_yields_nothing_is_refused(self):
        """The failure this guard exists for looks exactly like success.

        The register's key is "blocked". Reading it as "entries" imported no
        blocks at all and printed "to block 0" as though the file were empty
        - and the thing not imported is Harry's own employer.
        """
        with open(self.dnc_path, "w") as f:
            json.dump({"_README": ["notes"], "entries": []}, f)
        self.write_state({"jobs": {"1": {"company": "Acme",
                                         "status": "sent"}}})
        self.assertEqual(self.run_tool(), 1)
        self.assertFalse(self.contacted("Acme"),
                         "it must not write anything after refusing")

    def test_an_empty_state_is_refused(self):
        """Importing nothing would leave the product free to write to all
        102, which is the exact outcome this tool exists to prevent."""
        self.write_state({"jobs": {}})
        self.assertEqual(self.run_tool(), 1)

    def test_no_register_file_at_all_is_allowed(self):
        """Absent is different from broken. Somebody running this on a fresh
        checkout with no register should not be stopped."""
        os.unlink(self.dnc_path)
        self.write_state({"jobs": {"1": {"company": "Acme",
                                         "status": "sent"}}})
        self.assertEqual(self.run_tool(), 0)
        self.assertTrue(self.contacted("Acme"))


class TestTheAccount(Base):
    def test_an_unknown_account_is_refused(self):
        self.write_state({"jobs": {"1": {"company": "Acme",
                                         "status": "sent"}}})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = self.tool.main(["--user", "nobody@example.com"])
        self.assertEqual(code, 1)
        self.assertIn("no account", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
