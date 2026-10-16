"""The browser extension: what it may read, where it may run, what it sends.

It sits on job boards a person is logged in to, so the promises in its
README are held here: it runs only on the listed boards, every file it
names exists, and the script makes no network request of its own - the
only thing that leaves the page is the employer's name, in a link the
person taps."""

import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "extension")


class TheManifest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(EXT, "manifest.json")) as f:
            self.m = json.load(f)

    def test_manifest_v3_and_every_file_it_names_exists(self):
        self.assertEqual(self.m["manifest_version"], 3)
        names = list(self.m["icons"].values())
        names += list(self.m["action"]["default_icon"].values())
        names.append(self.m["action"]["default_popup"])
        for cs in self.m["content_scripts"]:
            names += cs["js"] + cs.get("css", [])
        for name in names:
            self.assertTrue(os.path.exists(os.path.join(EXT, name)), name)

    def test_only_job_boards_over_https_and_no_extra_permissions(self):
        self.assertNotIn("permissions", self.m)
        self.assertNotIn("host_permissions", self.m)
        for cs in self.m["content_scripts"]:
            for match in cs["matches"]:
                self.assertTrue(match.startswith("https://"), match)
                self.assertNotIn("<all_urls>", match)
                self.assertNotEqual(match, "https://*/*")

    def test_store_limits(self):
        self.assertLessEqual(len(self.m["name"]), 45)
        self.assertLessEqual(len(self.m["description"]), 132)


class TheScript(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(EXT, "content.js")) as f:
            self.js = f.read()

    def test_it_sends_nothing_itself(self):
        for call in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket",
                     "chrome.runtime.sendMessage", "localStorage", "cookie"):
            self.assertNotIn(call, self.js, call)

    def test_it_only_links_to_recruited(self):
        hosts = set(re.findall(r"https?://([a-z0-9.-]+)", self.js))
        self.assertEqual(hosts, {"recruited.org.uk"})
        self.assertIn("c=extension", self.js)


if __name__ == "__main__":
    unittest.main()
