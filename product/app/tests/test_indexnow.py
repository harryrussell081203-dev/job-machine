"""Telling the search engines a page exists, without telling them twice.

IndexNow is free, needs no account, and gets a page into Bing within hours
instead of weeks. The whole thing is one POST, which makes it the cheapest
indexing lever available and also the easiest to get subtly wrong in the two
ways that stop it working:

  - submitting the same list over and over, which is what gets a key
    ignored. This runs on an instance that sleeps after fifteen minutes and
    cold-starts on the next request, so "submit at startup" means submitting
    several times a day unless something remembers.
  - recording a submission that never succeeded, which means it is never
    retried and nothing ever looks wrong.

Nothing here touches the network. The POST is injected.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_sending import Base  # noqa: E402

KEY = "0123456789abcdef0123456789abcdef"


class Recorder:
    """Stands in for requests.post."""

    def __init__(self, status=200, boom=False):
        self.status, self.boom, self.calls = status, boom, []

    def __call__(self, url, json=None, timeout=None):
        if self.boom:
            raise RuntimeError("the network is having a moment")
        self.calls.append((url, json))
        return type("R", (), {"status_code": self.status})()


class TestOff(Base):
    """No key configured, and so no feature at all."""

    def test_nothing_is_submitted_without_a_key(self):
        from app import indexnow
        self.assertFalse(indexnow.available())
        post = Recorder()
        self.assertEqual(
            indexnow.submit_if_changed(["https://x.example/"],
                                       get_meta=lambda k: "",
                                       set_meta=lambda k, v: None, post=post),
            "off")
        self.assertEqual(post.calls, [])

    def test_the_key_file_is_not_served_when_there_is_no_key(self):
        """A 404 on the key file plus a submission carrying that key is
        exactly how a key gets rejected."""
        self.assertEqual(self.client.get(f"/{KEY}.txt").status_code, 404)


class TestOn(Base):
    # DEV_MODE off, because the feature refuses to submit from a development
    # instance and that is the point of the guard - see TestDevNeverSubmits.
    env = {"INDEXNOW_KEY": KEY, "BASE_URL": "https://recruited.org.uk",
           "DEV_MODE": "0"}

    def setUp(self):
        super().setUp()
        from app import indexnow
        self.indexnow = indexnow
        self.store = {}

    def go(self, post, urls=("https://recruited.org.uk/find",)):
        return self.indexnow.submit_if_changed(
            list(urls),
            get_meta=lambda k: self.store.get(k, ""),
            set_meta=lambda k, v: self.store.__setitem__(k, v),
            post=post)

    # -- the key file --------------------------------------------------
    def test_the_key_is_served_at_the_root_as_the_protocol_wants(self):
        r = self.client.get(f"/{KEY}.txt")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text.strip(), KEY)

    # -- submitting ----------------------------------------------------
    def test_a_first_run_submits(self):
        post = Recorder()
        self.assertEqual(self.go(post), "sent")
        url, body = post.calls[0]
        self.assertEqual(url, self.indexnow.ENDPOINT)
        self.assertEqual(body["host"], "recruited.org.uk")
        self.assertEqual(body["key"], KEY)
        self.assertIn("https://recruited.org.uk/find", body["urlList"])

    def test_the_key_location_points_at_a_file_that_exists(self):
        """The engine fetches this to confirm we own the domain."""
        post = Recorder()
        self.go(post)
        location = post.calls[0][1]["keyLocation"]
        path = location.split("recruited.org.uk", 1)[1]
        self.assertEqual(self.client.get(path).status_code, 200)

    def test_the_same_list_is_not_submitted_twice(self):
        """The failure this exists to prevent. A free instance wakes from
        sleep several times a day and every wake runs startup."""
        post = Recorder()
        self.assertEqual(self.go(post), "sent")
        self.assertEqual(self.go(post), "unchanged")
        self.assertEqual(self.go(post), "unchanged")
        self.assertEqual(len(post.calls), 1)

    def test_a_new_page_is_submitted(self):
        post = Recorder()
        self.go(post)
        self.assertEqual(
            self.go(post, urls=("https://recruited.org.uk/find",
                                "https://recruited.org.uk/answers/new-one")),
            "sent")
        self.assertEqual(len(post.calls), 2)

    def test_the_order_of_the_list_is_not_a_change(self):
        """Sorted before fingerprinting. A page list that comes back in a
        different order is the same page list, and resubmitting it because a
        dict iterated differently is exactly the noise that gets a key
        ignored."""
        post = Recorder()
        pages = ["https://recruited.org.uk/a", "https://recruited.org.uk/b"]
        self.assertEqual(self.go(post, urls=pages), "sent")
        self.assertEqual(self.go(post, urls=list(reversed(pages))), "unchanged")

    # -- failing safely ------------------------------------------------
    def test_a_rejected_submission_is_not_recorded_as_done(self):
        """Recording a failure would mean it is never retried, and nothing
        would ever look wrong."""
        bad = Recorder(status=403)
        self.assertEqual(self.go(bad), "failed")
        self.assertEqual(self.store, {})
        good = Recorder()
        self.assertEqual(self.go(good), "sent")

    def test_a_network_failure_is_swallowed(self):
        """This runs inside startup. A search engine not hearing about a page
        today is worth nothing next to an app that will not boot."""
        self.assertEqual(self.go(Recorder(boom=True)), "failed")

    def test_202_counts_as_accepted(self):
        """The protocol returns 202 while a new key is still being verified,
        which is most of the first day."""
        self.assertEqual(self.go(Recorder(status=202)), "sent")


class TestDevNeverSubmits(Base):
    """The guard that keeps the suite self-contained.

    These tests configure a key and a real-looking BASE_URL. Without the DEV
    check the startup hook would POST those URLs to IndexNow from CI, with a
    key that does not resolve - rude, and the fastest way to get the real key
    distrusted."""

    env = {"INDEXNOW_KEY": KEY, "BASE_URL": "https://recruited.org.uk",
           "DEV_MODE": "1"}

    def test_a_development_instance_submits_nothing(self):
        from app import indexnow
        self.assertFalse(indexnow.available())
        post = Recorder()
        self.assertEqual(
            indexnow.submit_if_changed(["https://recruited.org.uk/find"],
                                       get_meta=lambda k: "",
                                       set_meta=lambda k, v: None, post=post),
            "off")
        self.assertEqual(post.calls, [])


class TestRobotsNamesTheAssistants(Base):
    def test_the_agents_that_do_the_quoting_are_allowed_by_name(self):
        """`*` already allows them. Naming them means a Disallow added later
        for one crawler cannot silently widen to all of them."""
        robots = self.client.get("/robots.txt").text
        for agent in ("OAI-SearchBot", "Claude-SearchBot", "PerplexityBot",
                      "GPTBot", "ClaudeBot", "CCBot"):
            self.assertIn(f"User-agent: {agent}", robots)

    def test_naming_them_did_not_stop_allowing_them(self):
        """A named group with no Allow, or with the site's private prefixes
        copied into it, would be worse than the wildcard it replaced."""
        blocks = self.client.get("/robots.txt").text.split("User-agent: ")
        for block in blocks[1:]:
            name = block.splitlines()[0].strip()
            if name == "*":
                continue
            self.assertIn("Allow: /", block, name)
            self.assertNotIn("Disallow:", block, name)


if __name__ == "__main__":
    unittest.main()
