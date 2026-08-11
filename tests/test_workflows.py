"""
Tests for the workflow files themselves.

Nothing here runs a workflow. What it checks is the handful of things that
have gone wrong in the YAML often enough to be worth pinning down, and both of
them share a shape: they are invisible. A workflow with either mistake runs
green, prints a plausible log and quietly does the wrong thing.

  THE STALE STATE. Every fire-* branch carries whatever data/state.json looked
  like when the branch was cut. The modules read that file for everything -
  who has been written to, what is waiting on a CAPTCHA, which mail has been
  read - so a forced run on a branch works off history. It has cost real work:
  a burn run that recorded nothing, and a morning list that told him nothing
  was waiting on a CAPTCHA while five really were.

  THE SHARED CONCURRENCY GROUP. A group holds exactly ONE pending run, and a
  second run queueing behind an in-progress one CANCELS the pending one rather
  than queueing after it. On one afternoon that silently deleted every fire-*
  branch pushed, several before they executed a line.

Both were found by noticing an odd number in a log rather than by anything
failing. That is what these are for.
"""
import glob
import os
import unittest

import yaml

WORKFLOWS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".github", "workflows", "*.yml")


def workflows():
    for path in sorted(glob.glob(WORKFLOWS)):
        with open(path) as f:
            # PyYAML reads the key `on:` as the boolean True, which is the
            # YAML 1.1 spec doing exactly what it says and catching everyone.
            yield os.path.basename(path), yaml.safe_load(f)


def triggers(config):
    return (config or {}).get(True) or (config or {}).get("on") or {}


def fires_on_a_branch(config):
    branches = (triggers(config).get("push") or {}).get("branches") or []
    return [b for b in branches if str(b).startswith("fire-")]


def steps(config):
    out = []
    for job in (config.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


class TestEveryForcedRunWorksOnTheLiveQueue(unittest.TestCase):
    """A workflow that can be fired from a branch must read main's state.

    This is the bug that keeps coming back, and it never announces itself. The
    most recent one: a forced morning run emailed a CAPTCHA list built from a
    branch whose state.json was two thousand jobs and several weeks behind, so
    it said nothing was waiting while five were - and the log read as a
    perfectly ordinary success."""

    def test_they_all_start_from_the_live_state(self):
        checked = 0
        for name, config in workflows():
            if not fires_on_a_branch(config):
                continue
            checked += 1
            names = [str(s.get("name") or "") for s in steps(config)]
            with self.subTest(workflow=name):
                self.assertTrue(
                    any("live state" in n for n in names),
                    f"{name} can be fired from a fire-* branch but never "
                    f"checks out main's data/state.json, so a forced run "
                    f"works off whatever the branch was cut with")
        self.assertGreater(checked, 0, "no fire-* workflows found at all")

    def test_the_guard_only_applies_to_a_branch(self):
        """On main it must not run: main IS the live state, and fetching it
        over itself mid-run is a way to lose the run's own work."""
        for name, config in workflows():
            if not fires_on_a_branch(config):
                continue
            for step in steps(config):
                if "live state" in str(step.get("name") or ""):
                    with self.subTest(workflow=name):
                        self.assertIn("fire-", str(step.get("if") or ""))

    def test_it_only_takes_the_state_file(self):
        """Everything else in data/ is configuration a branch is allowed to
        change - the goals, the agency list, the people to ask. Checking out
        the whole directory would undo the very change being tested."""
        for name, config in workflows():
            for step in steps(config):
                if "live state" not in str(step.get("name") or ""):
                    continue
                with self.subTest(workflow=name):
                    self.assertIn("data/state.json", step.get("run") or "")
                    self.assertNotIn("-- data\n", step.get("run") or "")


class TestNoWorkflowCanCancelAnother(unittest.TestCase):
    """A concurrency group holds ONE pending run, and the next push into the
    group deletes it rather than queueing behind it. Runs here sit pending for
    twenty minutes waiting for a hosted runner, so a group shared across refs
    means a forced branch can be destroyed by an unrelated scheduled run - and
    there is nothing in any log to say it happened."""

    def test_every_group_is_keyed_on_the_ref(self):
        checked = 0
        for name, config in workflows():
            group = (config.get("concurrency") or {}).get("group")
            if not group:
                continue
            checked += 1
            with self.subTest(workflow=name):
                self.assertIn(
                    "github.ref", str(group),
                    f"{name} shares one concurrency group across every "
                    f"branch, so a fire-* push can cancel a pending run")
        self.assertGreater(checked, 0)


class TestTheStateAlwaysGetsHome(unittest.TestCase):
    def test_a_run_that_fails_still_commits_what_it_learned(self):
        """Half a run's work is worth keeping. A timeout after nine of eleven
        applications should not throw the nine away."""
        for name, config in workflows():
            for step in steps(config):
                if "commit-state.sh" not in str(step.get("run") or ""):
                    continue
                with self.subTest(workflow=name):
                    self.assertEqual(step.get("if"), "always()")

    def test_a_branch_run_pushes_its_state_to_main(self):
        """The state file is one shared record. A fire-* branch that committed
        to itself would leave main's copy - the one every scheduled run reads -
        without the work."""
        for name, config in workflows():
            if not fires_on_a_branch(config):
                continue
            for step in steps(config):
                if "commit-state.sh" not in str(step.get("run") or ""):
                    continue
                target = (step.get("env") or {}).get("TARGET") or ""
                with self.subTest(workflow=name):
                    self.assertIn("default_branch", str(target))


if __name__ == "__main__":
    unittest.main(verbosity=2)
