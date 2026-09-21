"""The figures, served to machines.

The site's whole argument is that its numbers can be checked. A number that
can only be checked by a human reading HTML is one a machine will quote
without checking, and a machine quoting a figure is now the main way anybody
hears it at all.

So these tests are about the two ways that goes wrong. One is arithmetic: a
rate that no longer matches the counts under it, which nobody notices because
both look plausible. The other is worse and is the reason study.py exists -
the site publishes two different reply rates, 26% and 19%, measured
differently, and anything serving both has to say which is which or it has
quietly invented a third claim.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_sending import Base  # noqa: E402


class TestNumbersJson(Base):
    def get(self):
        r = self.client.get("/numbers.json")
        self.assertEqual(r.status_code, 200)
        return r.json()

    # -- reachable -----------------------------------------------------
    def test_it_needs_no_account(self):
        self.assertEqual(self.client.get("/numbers.json").status_code, 200)

    def test_it_is_json_not_a_page(self):
        r = self.client.get("/numbers.json")
        self.assertIn("application/json", r.headers["content-type"])

    # -- arithmetic ----------------------------------------------------
    def test_every_rate_matches_the_counts_under_it(self):
        """The failure this catches is a count corrected and the rate beside
        it left alone. Both look plausible on their own and the pair is
        wrong, which is exactly the kind of error that survives review."""
        from app import study
        data = self.get()["study"]
        for key in ("by_recipient", "by_source"):
            for row in data[key]:
                self.assertEqual(
                    row["reply_rate"], study.rate(row["replied"], row["sent"]),
                    f"{key}: {row['label']}")
                self.assertLessEqual(row["replied"], row["sent"], row["label"])

    def test_both_breakdowns_add_up_to_the_same_86(self):
        """They are two cuts of one set of emails. If one adds to 86 and the
        other to 95, at least one is describing a different study."""
        from app import study
        self.assertTrue(study.totals_reconcile())
        data = self.get()["study"]
        for key in ("by_recipient", "by_source"):
            self.assertEqual(sum(r["sent"] for r in data[key]), data["sent"], key)
            self.assertEqual(sum(r["replied"] for r in data[key]),
                             data["replied"], key)

    # -- honest --------------------------------------------------------
    def test_each_rate_carries_the_definition_it_was_measured_with(self):
        """26% and 19% are both true and are not the same measurement. A
        consumer lifting one of them has to be told which, or the site has
        published a contradiction with no way to resolve it."""
        data = self.get()
        self.assertIn("reply_definition", data["study"])
        if data.get("founder_live"):
            self.assertIn("reply_definition", data["founder_live"])
            self.assertNotEqual(data["study"]["reply_definition"],
                                data["founder_live"]["reply_definition"])

    def test_the_thin_rows_carry_their_caveats(self):
        """Whoever quotes this will quote the best-looking row, which is the
        one with ten emails in it."""
        caveats = " ".join(self.get()["study"]["caveats"]).lower()
        self.assertIn("ten emails", caveats)
        self.assertIn("nine emails", caveats)
        self.assertIn("not an interview", caveats)

    def test_the_refusals_are_published_too(self):
        """516 listings that produced no address. A product selling address
        lookup would rather not print that, which is the reason to."""
        self.assertGreater(
            self.get()["study"]["listings_with_no_address_found"], 0)

    def test_nothing_sent_is_absent_rather_than_zero(self):
        """A missing key is honest. A zero reads as a claim that a measurement
        was taken and came back empty."""
        data = self.get()
        if data.get("founder_live") is not None:
            self.assertGreater(data["founder_live"]["applications"], 0)

    # -- agrees with the prose -----------------------------------------
    def test_the_playbook_still_says_what_this_says(self):
        """study.py is the single copy of these counts, but the playbook was
        written first and is what a reader actually reads. If they disagree,
        one of them is lying to somebody."""
        playbook = open(os.path.join(ROOT, "PLAYBOOK.md"),
                        encoding="utf-8").read()
        data = self.get()["study"]
        for row in data["by_recipient"] + data["by_source"]:
            self.assertIn(f"| {row['sent']} | {row['replied']} |", playbook,
                          f"{row['label']} is not in the playbook as written")
        self.assertIn(str(data["listings_with_no_address_found"]), playbook)


if __name__ == "__main__":
    unittest.main()
