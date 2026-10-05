"""Role-page builder tests.

The agent writes files, so each test uses a fresh temporary directory and
passes it as `out_dir`. Nothing here touches the real network, the real
clock, or the real `growth/out/`.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from growth import USER_AGENT  # noqa: E402
from growth.agents import page_builder  # noqa: E402

BASE = "https://example.test"


class FakeResp:
    def __init__(self, data=None, status=200, text=None):
        self.status_code = status
        self._data = data
        self.text = text if text is not None else json.dumps(data or {})

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class Fake:
    def __init__(self, resp=None, *, boom=False):
        self.resp = resp if resp is not None else FakeResp({"employers": []})
        self.boom = boom
        self.calls = []

    def __call__(self, url, headers=None, timeout=None):
        self.calls.append((url, (headers or {}).get("User-Agent", ""),
                           timeout))
        if self.boom:
            raise ConnectionError("refused")
        return self.resp


def tmp():
    d = tempfile.mkdtemp(prefix="pb-")
    return d


def company(i, roles, name=None):
    return {
        "slug": f"company-{i}",
        "name": name or f"Company {i}",
        "domain": f"c{i}.example",
        "hiring_email": f"jobs@c{i}.example",
        "general_email": None,
        "roles": roles,
        "checked_at": 0,
    }


def directory(employers):
    return FakeResp({"employers": employers})


class IdentityTests(unittest.TestCase):
    def test_user_agent_on_the_fetch(self):
        d = tmp()
        try:
            fake = Fake(directory([]))
            page_builder.run({}, base=BASE, get=fake,
                             now=lambda: 1_700_000_000, out_dir=d)
            self.assertEqual(fake.calls[0][1], USER_AGENT)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fetch_url_is_employers_json(self):
        d = tmp()
        try:
            fake = Fake(directory([]))
            page_builder.run({}, base=BASE, get=fake,
                             now=lambda: 1_700_000_000, out_dir=d)
            self.assertEqual(fake.calls[0][0], f"{BASE}/employers.json")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class FailClosedTests(unittest.TestCase):
    """A fetch that could not look must not blank the state. A run that saw
    an empty directory and a run that could not reach the server produce
    the same empty page count, and only one of them is true."""

    def test_connection_error_preserves_state(self):
        d = tmp()
        try:
            before = {"pages": {"software-engineer": {"hash": "abc"}}}
            after, result = page_builder.run(
                before, base=BASE, get=Fake(boom=True),
                now=lambda: 1_700_000_000, out_dir=d)
            self.assertFalse(result.ok)
            self.assertEqual(after, before)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_non_200_preserves_state(self):
        d = tmp()
        try:
            before = {"pages": {"software-engineer": {"hash": "abc"}}}
            after, result = page_builder.run(
                before, base=BASE,
                get=Fake(FakeResp({"employers": []}, status=503)),
                now=lambda: 1_700_000_000, out_dir=d)
            self.assertFalse(result.ok)
            self.assertEqual(after, before)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_unparsable_json_preserves_state(self):
        d = tmp()
        try:
            before = {"pages": {"x": {"hash": "y"}}}
            resp = FakeResp(None, text="not json")
            after, result = page_builder.run(
                before, base=BASE, get=Fake(resp),
                now=lambda: 1_700_000_000, out_dir=d)
            self.assertFalse(result.ok)
            self.assertEqual(after, before)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_empty_directory_is_not_a_failure(self):
        d = tmp()
        try:
            before = {"pages": {"x": {"hash": "y"}}}
            after, result = page_builder.run(
                before, base=BASE, get=Fake(directory([])),
                now=lambda: 1_700_000_000, out_dir=d)
            # ok=True (nothing broke), but state is kept rather than reset.
            self.assertTrue(result.ok)
            self.assertEqual(after, before)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ThresholdTests(unittest.TestCase):
    def test_below_min_employers_not_published(self):
        d = tmp()
        try:
            fake = Fake(directory([
                company(1, ["software engineer"]),
                company(2, ["software engineer"]),
            ]))
            state, _ = page_builder.run(
                {}, base=BASE, get=fake, now=lambda: 1_700_000_000,
                roles=["software engineer"], min_employers=3, out_dir=d)
            self.assertEqual(state.get("pages", {}), {})
            self.assertFalse(os.path.exists(
                os.path.join(d, "pages", "roles", "software-engineer.html")))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_at_min_employers_is_published(self):
        d = tmp()
        try:
            fake = Fake(directory([
                company(1, ["software engineer"]),
                company(2, ["software engineer"]),
                company(3, ["software engineer"]),
            ]))
            state, result = page_builder.run(
                {}, base=BASE, get=fake, now=lambda: 1_700_000_000,
                roles=["software engineer"], min_employers=3, out_dir=d)
            self.assertIn("software-engineer", state["pages"])
            self.assertTrue(os.path.exists(
                os.path.join(d, "pages", "roles", "software-engineer.html")))
            self.assertEqual(result.counts["written"], 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class PageShapeTests(unittest.TestCase):
    """The parts of the page that make it citable: canonical, meta
    description, JSON-LD, a figure in the opening paragraph, and every
    employer link pointing at the directory page the address came from."""

    def _build(self, d, n=4):
        fake = Fake(directory([
            company(i, ["software engineer"]) for i in range(1, n + 1)
        ]))
        page_builder.run({}, base=BASE, get=fake, now=lambda: 1_700_000_000,
                         roles=["software engineer"], min_employers=3,
                         out_dir=d)
        return (os.path.join(d, "pages", "roles", "software-engineer.html"))

    def test_canonical_and_description_present(self):
        d = tmp()
        try:
            with open(self._build(d), encoding="utf-8") as f:
                body = f.read()
            self.assertIn(
                f'<link rel="canonical" href="{BASE}/roles/software-engineer">',
                body)
            self.assertIn('<meta name="description"', body)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_json_ld_parses_and_counts_match(self):
        d = tmp()
        try:
            with open(self._build(d, n=4), encoding="utf-8") as f:
                body = f.read()
            m = re.search(
                r'<script type="application/ld\+json">(.*?)</script>',
                body, re.S)
            self.assertIsNotNone(m)
            parsed = json.loads(m.group(1))
            self.assertEqual(parsed["@type"], "ItemList")
            self.assertEqual(parsed["numberOfItems"], 4)
            self.assertEqual(len(parsed["itemListElement"]), 4)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_opening_paragraph_names_the_figure(self):
        d = tmp()
        try:
            with open(self._build(d, n=5), encoding="utf-8") as f:
                body = f.read()
            # The description quoted in the meta tag is lifted verbatim into
            # the opening paragraph on the page.
            self.assertIn("5 UK employers", body)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_employer_links_point_at_the_directory(self):
        d = tmp()
        try:
            with open(self._build(d, n=3), encoding="utf-8") as f:
                body = f.read()
            for i in range(1, 4):
                self.assertIn(f'{BASE}/employers/company-{i}', body)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class IdempotenceTests(unittest.TestCase):
    def test_running_twice_rewrites_nothing_when_input_unchanged(self):
        d = tmp()
        try:
            fake = Fake(directory([
                company(i, ["software engineer"]) for i in range(1, 5)]))
            s1, _ = page_builder.run(
                {}, base=BASE, get=fake, now=lambda: 1_700_000_000,
                roles=["software engineer"], min_employers=3, out_dir=d)
            path = os.path.join(d, "pages", "roles", "software-engineer.html")
            mtime1 = os.path.getmtime(path)
            s2, r2 = page_builder.run(
                s1, base=BASE, get=fake, now=lambda: 1_700_000_000,
                roles=["software engineer"], min_employers=3, out_dir=d)
            mtime2 = os.path.getmtime(path)
            self.assertEqual(mtime1, mtime2)
            self.assertEqual(r2.counts["written"], 0)
            self.assertEqual(r2.counts["unchanged"], 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_removes_pages_that_fell_below_threshold(self):
        d = tmp()
        try:
            fake1 = Fake(directory([
                company(i, ["software engineer"]) for i in range(1, 5)]))
            s1, _ = page_builder.run(
                {}, base=BASE, get=fake1, now=lambda: 1_700_000_000,
                roles=["software engineer"], min_employers=3, out_dir=d)
            path = os.path.join(d, "pages", "roles", "software-engineer.html")
            self.assertTrue(os.path.exists(path))

            # Second run: only one employer now has the role.
            fake2 = Fake(directory([
                company(1, ["software engineer"]),
                company(2, ["accountant"]),
            ]))
            s2, result = page_builder.run(
                s1, base=BASE, get=fake2, now=lambda: 1_700_000_000,
                roles=["software engineer"], min_employers=3, out_dir=d)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(result.counts["removed"], 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ManifestTests(unittest.TestCase):
    def test_manifest_lists_built_pages(self):
        d = tmp()
        try:
            fake = Fake(directory([
                company(i, ["software engineer", "data analyst"])
                for i in range(1, 5)]))
            page_builder.run(
                {}, base=BASE, get=fake, now=lambda: 1_700_000_000,
                roles=["software engineer", "data analyst"],
                min_employers=3, out_dir=d)
            manifest_path = os.path.join(
                d, "pages", "roles", "index.json")
            self.assertTrue(os.path.exists(manifest_path))
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            slugs = [p["slug"] for p in manifest["pages"]]
            self.assertIn("software-engineer", slugs)
            self.assertIn("data-analyst", slugs)
            # Sorted - a diff-stable manifest is reviewable.
            self.assertEqual(slugs, sorted(slugs))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class MatchingTests(unittest.TestCase):
    """Case-insensitive substring, so "Software engineer" and "Senior
    Software Engineer" both count toward "software engineer"."""

    def test_substring_match_is_case_insensitive(self):
        d = tmp()
        try:
            fake = Fake(directory([
                company(1, ["Software Engineer"]),
                company(2, ["senior software engineer"]),
                company(3, ["SOFTWARE ENGINEER - BACKEND"]),
                company(4, ["accountant"]),       # doesn't match
            ]))
            state, _ = page_builder.run(
                {}, base=BASE, get=fake, now=lambda: 1_700_000_000,
                roles=["software engineer"], min_employers=3, out_dir=d)
            self.assertEqual(
                state["pages"]["software-engineer"]["employer_count"], 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_roles_as_dicts_match_on_title(self):
        """The live directory stores roles as {title, location, posted_at}
        dicts. The matcher reads the title and ignores the rest."""
        d = tmp()
        try:
            fake = Fake(directory([
                company(1, [{"title": "Software Engineer", "location": "London",
                             "posted_at": ""}]),
                company(2, [{"title": "Senior software engineer",
                             "location": "Manchester", "posted_at": ""}]),
                company(3, [{"title": "Backend software engineer",
                             "location": "", "posted_at": ""}]),
            ]))
            state, _ = page_builder.run(
                {}, base=BASE, get=fake, now=lambda: 1_700_000_000,
                roles=["software engineer"], min_employers=3, out_dir=d)
            self.assertEqual(
                state["pages"]["software-engineer"]["employer_count"], 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
