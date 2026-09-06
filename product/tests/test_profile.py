"""The profile is the only thing standing between a stranger and a wrong letter.

These tests are mostly about refusal. A profile that loads but is subtly wrong
does not crash - it sends confident, inaccurate mail to real employers - so the
interesting cases here are all the ones that should fail.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.profile import Profile, ProfileError, Role  # noqa: E402


def good(**overrides):
    base = dict(
        name="Sam Doherty",
        location="Sheffield",
        phone="07700 900123",
        situation="employed",
        current_salary=32000,
        min_salary_annual=38000,
        priorities=["money", "progression"],
        target_roles=["maintenance technician"],
        locations=["Sheffield"],
        history=[Role(title="Maintenance Technician", org="Brightwater",
                      detail="fault finding on PLC-controlled lines")],
    )
    base.update(overrides)
    return base


class TestValidation(unittest.TestCase):
    def test_a_complete_profile_loads(self):
        p = Profile(**good())
        self.assertEqual(p.name, "Sam Doherty")

    def test_floor_below_current_salary_is_refused(self):
        # The expensive typo: it quietly opens the gate to every pay cut going.
        with self.assertRaises(ProfileError) as ctx:
            Profile(**good(min_salary_annual=28000))
        self.assertIn("pay cut", str(ctx.exception))

    def test_employed_without_a_salary_is_refused(self):
        with self.assertRaises(ProfileError) as ctx:
            Profile(**good(current_salary=0))
        self.assertIn("current_salary", str(ctx.exception))

    def test_no_floor_at_all_is_refused(self):
        with self.assertRaises(ProfileError) as ctx:
            Profile(**good(min_salary_annual=0, min_rate_hourly=0))
        self.assertIn("floor", str(ctx.exception))

    def test_bad_phone_is_refused(self):
        with self.assertRaises(ProfileError):
            Profile(**good(phone="ask me"))

    def test_unknown_priority_is_refused(self):
        with self.assertRaises(ProfileError) as ctx:
            Profile(**good(priorities=["money", "vibes"]))
        self.assertIn("vibes", str(ctx.exception))

    def test_empty_history_is_refused(self):
        with self.assertRaises(ProfileError) as ctx:
            Profile(**good(history=[]))
        self.assertIn("proof points", str(ctx.exception))

    def test_all_problems_are_reported_at_once(self):
        # A buyer who has to fix one field per run gives up and asks for a
        # refund, so validation collects rather than short-circuits.
        with self.assertRaises(ProfileError) as ctx:
            Profile(**good(phone="", target_roles=[], locations=[]))
        msg = str(ctx.exception)
        self.assertIn("phone", msg)
        self.assertIn("target_roles", msg)
        self.assertIn("locations", msg)


class TestRendering(unittest.TestCase):
    def test_signoff_matches_the_engines_shape(self):
        p = Profile(**good())
        self.assertEqual(p.signoff(), "Sam Doherty / 07700 900123 / CV attached")
        self.assertEqual(p.signoff(cv_attached=False), "Sam Doherty / 07700 900123")

    def test_employed_profile_warns_the_model_off_naming_the_employer(self):
        block = Profile(**good()).prompt_block()
        self.assertIn("NEVER NAME THEIR CURRENT EMPLOYER", block)
        self.assertIn("looking for a BETTER one", block)

    def test_employer_may_be_named_when_explicitly_allowed(self):
        block = Profile(**good(may_name_employer=True)).prompt_block()
        self.assertNotIn("NEVER NAME THEIR CURRENT EMPLOYER", block)

    def test_unemployed_profile_says_available_now(self):
        block = Profile(**good(situation="unemployed", current_salary=0)).prompt_block()
        self.assertIn("Available to start now", block)

    def test_never_claim_becomes_an_explicit_prohibition(self):
        p = Profile(**good(never_claim=["that they hold a current 17th Edition"]))
        block = p.prompt_block()
        self.assertIn("NEVER STATE, IMPLY OR HINT", block)
        self.assertIn("17th Edition", block)

    def test_the_money_floor_reaches_the_prompt(self):
        block = Profile(**good(min_rate_hourly=20)).prompt_block()
        self.assertIn("38,000", block)
        self.assertIn("20 an hour", block)

    def test_priorities_are_numbered_in_the_users_order(self):
        block = Profile(**good(priorities=["travel", "money"])).prompt_block()
        self.assertLess(block.index("TRAVEL"), block.index("MONEY"))


class TestLoading(unittest.TestCase):
    def _write(self, payload, suffix=".json"):
        fh = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                         encoding="utf-8")
        json.dump(payload, fh)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_round_trip_through_json(self):
        raw = good()
        raw["history"] = [{"title": "Maintenance Technician", "org": "Brightwater",
                           "detail": "fault finding"}]
        p = Profile.load(self._write(raw))
        self.assertEqual(p.name, "Sam Doherty")
        self.assertEqual(p.history[0].org, "Brightwater")

    def test_a_typo_in_a_field_name_is_refused_not_ignored(self):
        raw = good()
        raw["history"] = []
        raw["min_salery_annual"] = 38000  # the silent-failure case
        with self.assertRaises(ProfileError) as ctx:
            Profile.load(self._write(raw))
        self.assertIn("min_salery_annual", str(ctx.exception))

    def test_missing_file_points_at_the_wizard(self):
        with self.assertRaises(ProfileError) as ctx:
            Profile.load("/nonexistent/profile.yaml")
        self.assertIn("wizard", str(ctx.exception))

    def test_history_entry_missing_org_is_refused(self):
        raw = good()
        raw["history"] = [{"title": "Technician"}]
        with self.assertRaises(ProfileError) as ctx:
            Profile.load(self._write(raw))
        self.assertIn("title and org", str(ctx.exception))


class TestShippedExample(unittest.TestCase):
    def test_the_example_profile_is_valid(self):
        # If the file we ship as the starting point does not load, every buyer
        # hits the error before they have written a line of their own.
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "profile.example.yaml")
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        p = Profile.load(path)
        self.assertTrue(p.never_claim, "the example must demonstrate never_claim")
        self.assertIn("Sheffield", p.prompt_block())




class TestWizardRoundTrip(unittest.TestCase):
    """A wizard that writes YAML the loader rejects is worse than no wizard."""

    def test_yaml_the_wizard_writes_loads_back_identically(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        from jobseeker.wizard import to_yaml

        original = Profile(**good(
            email="sam@example.com",
            min_rate_hourly=20,
            qualifications=["Level 3 NVQ", "18th Edition"],
            never_claim=["that they hold a current 17th Edition certificate"],
            priorities=["money", "travel", "training"],
            locations=["Sheffield", "Rotherham"],
            target_roles=["maintenance technician", "shift engineer"],
        ))

        fh = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                         encoding="utf-8")
        fh.write(to_yaml(original))
        fh.close()
        self.addCleanup(os.unlink, fh.name)

        reloaded = Profile.load(fh.name)
        self.assertEqual(reloaded.name, original.name)
        self.assertEqual(reloaded.phone, original.phone)
        self.assertEqual(reloaded.priorities, original.priorities)
        self.assertEqual(reloaded.never_claim, original.never_claim)
        self.assertEqual(reloaded.qualifications, original.qualifications)
        self.assertEqual(reloaded.locations, original.locations)
        self.assertEqual(reloaded.target_roles, original.target_roles)
        self.assertEqual(reloaded.current_salary, original.current_salary)
        self.assertEqual(len(reloaded.history), len(original.history))
        self.assertEqual(reloaded.history[0].org, original.history[0].org)
        # and the thing the engine actually consumes must survive the trip
        self.assertEqual(reloaded.prompt_block(), original.prompt_block())
        self.assertEqual(reloaded.signoff(), original.signoff())

    def test_a_quote_in_a_name_does_not_break_the_yaml(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        from jobseeker.wizard import to_yaml

        p = Profile(**good(name="Sam O'Doherty"))
        fh = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                         encoding="utf-8")
        fh.write(to_yaml(p))
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        self.assertEqual(Profile.load(fh.name).name, "Sam O'Doherty")


if __name__ == "__main__":
    unittest.main()
