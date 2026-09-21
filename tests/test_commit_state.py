"""
Tests for .github/workflows/commit-state.sh.

This script had no test coverage at all, and the bug that hid there was the
worst kind: it committed a revert as though it were fresh output, silently,
from an automated account nobody reads the diffs of.

It copied the whole of data/ aside, reset to the target branch, then put
every differing file back - which cannot tell a file the run PRODUCED from
one it merely had CHECKED OUT at an older commit. So a run whose checkout
predated a hand edit reverted that edit. Commit 39c0381 did exactly that:
data/answers.json went back to a months-old copy carrying the false claim
"DV (Developed Vetting)" into a public repo, and data/targets.json lost 50
entries in the same commit.

These tests drive the real script in a throwaway git repo.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import personal  # noqa: E402

SCRIPT = os.path.join(ROOT, ".github", "workflows", "commit-state.sh")


def git(*args, cwd, **kw):
    return subprocess.run(("git",) + args, cwd=cwd, check=True,
                          capture_output=True, text=True, **kw)


class TestCommitStateDoesNotRevertOtherPeoplesEdits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        # A bare "remote", and a clone standing in for the runner's checkout.
        self.remote = os.path.join(self.tmp, "remote.git")
        git("init", "--bare", "-b", "main", self.remote, cwd=self.tmp)
        self.work = os.path.join(self.tmp, "work")
        git("clone", self.remote, self.work, cwd=self.tmp)
        git("config", "user.email", "t@t", cwd=self.work)
        git("config", "user.name", "t", cwd=self.work)

        os.makedirs(os.path.join(self.work, "data"))
        os.makedirs(os.path.join(self.work, "tools"))
        # The real merge script, since the script under test copies and runs it.
        shutil.copy(os.path.join(ROOT, "tools", "merge_state.py"),
                    os.path.join(self.work, "tools", "merge_state.py"))
        # And personal.py, for the same reason: the script copies that aside
        # too, and merge_state.py imports it to unseal both sides before
        # comparing them. A fixture without it does not model this repository,
        # and the merge fails into its fallback - which silently drops the
        # other run's jobs, so the symptom looks like a merge bug rather than
        # a missing file.
        shutil.copy(os.path.join(ROOT, "personal.py"),
                    os.path.join(self.work, "personal.py"))
        # companies_contacted included because merge_state.py always emits
        # it - a real state file has been through the merge many times, so
        # a base without it is not a state a no-op run could ever see.
        self.write("data/state.json",
                   {"companies_contacted": {},
                    "jobs": {"a": {"status": "sent"}}}, normalised=True)
        self.write("data/answers.json", {"security_clearance": "none held"})
        git("add", "-A", cwd=self.work)
        git("commit", "-m", "base", cwd=self.work)
        git("push", "origin", "main", cwd=self.work)

    def write(self, rel, obj, normalised=False):
        """normalised=True writes the exact formatting merge_state.py emits.

        Without it a byte-identical no-op still shows as a change, because
        the merge rewrites state.json with indent=1 and sorted keys - which
        is real behaviour, just not what a 'nothing happened' test wants to
        be measuring."""
        with open(os.path.join(self.work, rel), "w") as f:
            if normalised:
                json.dump(obj, f, indent=1, sort_keys=True)
            else:
                json.dump(obj, f)

    def read_remote(self, rel):
        out = subprocess.run(["git", "show", f"main:{rel}"], cwd=self.remote,
                             check=True, capture_output=True, text=True).stdout
        return json.loads(out)

    def land_on_main(self, rel, obj):
        """Somebody else edits a file and pushes it, after this run started."""
        other = os.path.join(self.tmp, "other")
        git("clone", self.remote, other, cwd=self.tmp)
        git("config", "user.email", "o@o", cwd=other)
        git("config", "user.name", "o", cwd=other)
        with open(os.path.join(other, rel), "w") as f:
            json.dump(obj, f)
        git("add", "-A", cwd=other)
        git("commit", "-m", "hand edit", cwd=other)
        git("push", "origin", "main", cwd=other)

    def run_script(self, key=None):
        env = dict(os.environ)
        env.pop(personal.KEY_ENV, None)
        if key:
            env[personal.KEY_ENV] = key
        return subprocess.run(["bash", SCRIPT, "main", "state"], cwd=self.work,
                              capture_output=True, text=True, env=env)

    def test_an_edit_this_run_never_touched_survives(self):
        """The actual bug. The run has a stale answers.json in its checkout
        and never writes to it; a newer one lands on main meanwhile. The
        newer one must win."""
        self.land_on_main("data/answers.json",
                          {"security_clearance": "none held - lapsed, can be vetted"})
        # this run only ever writes state.json
        self.write("data/state.json", {"jobs": {"a": {"status": "sent"},
                                                "b": {"status": "sent"}}})
        self.run_script()
        self.assertEqual(self.read_remote("data/answers.json"),
                         {"security_clearance": "none held - lapsed, can be vetted"})

    def test_a_file_this_run_really_wrote_is_still_carried_forward(self):
        """The behaviour the old code was protecting, which must not regress:
        the Covenant list is built by a tool straight into data/ and went
        missing this way once."""
        self.write("data/veteran_employers.json", {"employers": ["Babcock"]})
        self.run_script()
        self.assertEqual(self.read_remote("data/veteran_employers.json"),
                         {"employers": ["Babcock"]})

    def test_state_is_merged_rather_than_overwritten(self):
        """Two runs writing state at once is the normal case this exists for."""
        self.land_on_main("data/state.json", {"jobs": {"z": {"status": "sent"}}})
        self.write("data/state.json", {"jobs": {"a": {"status": "sent"},
                                                "b": {"status": "sent"}}})
        self.run_script()
        jobs = self.read_remote("data/state.json")["jobs"]
        self.assertEqual(set(jobs), {"a", "b", "z"})

    def test_a_run_that_changed_nothing_commits_nothing(self):
        result = self.run_script()
        self.assertIn("no state changes", result.stdout)


class TestWhatGetsCommittedIsSealed(unittest.TestCase):
    """The end of the wiring, driven through the real script.

    The machine seals state.json on save, and this script then merges it
    against whatever is on the branch. Every rule in merge_state.py prefers
    the branch's copy when the two sides tie, and a sealed record ties with
    its plaintext twin on all of them - so the merge unsealed the whole file
    on every commit. Measured on the real 18MB file: 570 of 579 addresses
    came back in the clear and not one enc.v1: marker survived.

    merge_state.py has its own tests for that. This one exists because the
    bug was in the WIRING as much as the logic: the key has to reach the
    commit step, personal.py has to be copied beside the merge script, and
    the merge script has to find that copy rather than the one `git reset`
    restored. Any of those three missing brings the bug back with every unit
    test still green.
    """

    def setUp(self):
        self.key = personal.make_key()

    def test_the_commit_that_lands_on_main_carries_no_addresses(self):
        case = TestCommitStateDoesNotRevertOtherPeoplesEdits("run")
        case.setUp()
        self.addCleanup(shutil.rmtree, case.tmp, True)

        # What is on the branch: plaintext, as it is today.
        case.land_on_main("data/state.json", {"jobs": {"z": {
            "status": "sent", "contact_email": "zoe@elsewhere.example"}}})
        # What this run wrote: the same shape, sealed on the way out.
        case.write("data/state.json", personal.seal({"jobs": {"a": {
            "status": "sent", "contact_email": "anna@kestrel.example"}}},
            self.key))

        case.run_script(key=self.key)
        blob = subprocess.run(["git", "show", "main:data/state.json"],
                              cwd=case.remote, check=True,
                              capture_output=True, text=True).stdout

        self.assertNotIn("anna@kestrel.example", blob)
        self.assertNotIn("zoe@elsewhere.example", blob)
        self.assertIn(personal.MARKER, blob)

        # and both runs' work is still there underneath
        state = personal.unseal(json.loads(blob), self.key)
        self.assertEqual(set(state["jobs"]), {"a", "z"})
        self.assertEqual(state["jobs"]["z"]["contact_email"],
                         "zoe@elsewhere.example")

    def test_without_a_key_it_commits_exactly_what_it_always_did(self):
        """Nothing about sealing may change a checkout with no secret set."""
        case = TestCommitStateDoesNotRevertOtherPeoplesEdits("run")
        case.setUp()
        self.addCleanup(shutil.rmtree, case.tmp, True)

        case.land_on_main("data/state.json", {"jobs": {"z": {"status": "sent"}}})
        case.write("data/state.json", {"jobs": {"a": {"status": "sent"}}})
        case.run_script()

        state = case.read_remote("data/state.json")
        self.assertEqual(set(state["jobs"]), {"a", "z"})
        self.assertNotIn(personal.MARKER, json.dumps(state))


if __name__ == "__main__":
    unittest.main(verbosity=2)
