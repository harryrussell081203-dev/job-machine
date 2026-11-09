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

    def __init__(self, status=200, boom=False, text=""):
        self.status, self.boom, self.text, self.calls = status, boom, text, []

    def __call__(self, url, json=None, timeout=None, headers=None):
        if self.boom:
            raise RuntimeError("the network is having a moment")
        self.calls.append((url, json))
        self.headers = headers or {}
        return type("R", (), {"status_code": self.status, "text": self.text})()


class KeyFile:
    """Stands in for requests.get on our own key file, as the engine sees
    it from outside."""

    def __init__(self, status=200, text=KEY, boom=False):
        self.status, self.text, self.boom, self.calls = status, text, boom, []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if self.boom:
            raise TimeoutError("asleep")
        return type("R", (), {"status_code": self.status, "text": self.text})()


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


class TestTheDefaultKey(unittest.TestCase):
    """On the live site the key was never set, and every boot logged
    "[indexnow] off" for a month. The key is public by design, so there is
    now one in the code; an explicit empty setting still switches it off."""

    def load(self, **env):
        import importlib
        from unittest.mock import patch
        from app import config
        with patch.dict(os.environ, env):
            if "INDEXNOW_KEY" not in env:
                os.environ.pop("INDEXNOW_KEY", None)
            importlib.reload(config)
            key = config.INDEXNOW_KEY
        importlib.reload(config)
        return key

    def test_unset_means_the_default(self):
        from app import config
        self.assertEqual(self.load(), config.INDEXNOW_DEFAULT_KEY)
        self.assertRegex(config.INDEXNOW_DEFAULT_KEY, r"^[0-9a-f]{32}$")

    def test_empty_means_off_and_a_value_wins(self):
        self.assertEqual(self.load(INDEXNOW_KEY=""), "")
        self.assertEqual(self.load(INDEXNOW_KEY=KEY), KEY)


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

    def go(self, post, urls=("https://recruited.org.uk/find",), get=None,
           now=None):
        return self.indexnow.submit_if_changed(
            list(urls),
            get_meta=lambda k: self.store.get(k, ""),
            set_meta=lambda k, v: self.store.__setitem__(k, v),
            post=post, get=get or KeyFile(), now=now)

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
        self.assertTrue(self.go(bad, now=1000).startswith("failed"))
        self.assertNotIn(self.indexnow.MEMORY_KEY, self.store)
        good = Recorder()
        later = 1000 + self.indexnow.RETRY_AFTER + 1
        self.assertEqual(self.go(good, now=later), "sent")

    def test_a_network_failure_is_swallowed(self):
        """A search engine not hearing about a page today is worth nothing
        next to an app that will not boot."""
        said = self.go(Recorder(boom=True))
        self.assertTrue(said.startswith("failed: no answer"), said)

    # -- saying why (16 October to 9 November 2026: "failed", every time) --
    def test_a_refusal_says_what_the_engine_said(self):
        said = self.go(Recorder(status=403, text="  Key not\nvalid "))
        self.assertIn("403", said)
        self.assertIn("key was not accepted", said)
        self.assertIn("Key not valid", said)

    def test_the_last_try_is_kept_for_the_weekly_check(self):
        """The hosting logs are often unreadable; site_meta is not."""
        self.go(Recorder(status=429), now=5000)
        last = self.indexnow.last_try(lambda k: self.store.get(k, ""))
        self.assertEqual(last["at"], 5000)
        self.assertFalse(last["ok"])
        self.assertIn("429 too many submissions", last["said"])
        self.assertEqual(last["urls"], 1)
        self.go(Recorder(), now=5000 + self.indexnow.RETRY_AFTER)
        self.assertTrue(
            self.indexnow.last_try(lambda k: self.store.get(k, ""))["ok"])

    # -- not retrying a refusal on every wake ---------------------------
    def test_a_refusal_waits_a_day(self):
        """Every cold start sent the whole list again, several times a
        night, for three weeks."""
        post = Recorder(status=403)
        self.go(post, now=1000)
        said = self.go(post, now=1000 + 3600)
        self.assertTrue(said.startswith("waiting"), said)
        self.assertIn("403", said)
        self.go(post, now=1000 + 7200)
        self.assertEqual(len(post.calls), 1)
        self.go(post, now=1000 + self.indexnow.RETRY_AFTER)
        self.assertEqual(len(post.calls), 2)

    def test_the_wait_is_per_list(self):
        """The directory run and the web app keep their own records, so a
        refusal of one does not hold back the other."""
        self.go(Recorder(status=403), now=1000)
        post = Recorder()
        said = self.indexnow.submit_if_changed(
            ["https://recruited.org.uk/employers"],
            get_meta=lambda k: self.store.get(k, ""),
            set_meta=lambda k, v: self.store.__setitem__(k, v),
            post=post, get=KeyFile(), memory_key="indexnow_directory",
            now=1001)
        self.assertEqual(said, "sent")

    # -- the key file, read from outside first --------------------------
    def test_the_key_file_is_read_where_the_engine_reads_it(self):
        keyfile = KeyFile()
        self.go(Recorder(), get=keyfile)
        self.assertEqual(keyfile.calls,
                         [f"https://recruited.org.uk/{KEY}.txt"])

    def test_nothing_is_sent_when_the_key_file_does_not_answer(self):
        """Sending a key the engine cannot read is how a key gets
        rejected."""
        for keyfile, why in ((KeyFile(status=404), "answered 404"),
                             (KeyFile(text="something else"), "does not hold"),
                             (KeyFile(boom=True), "did not answer")):
            self.store.clear()
            post = Recorder()
            said = self.go(post, get=keyfile)
            self.assertTrue(said.startswith("failed"), said)
            self.assertIn(why, said)
            self.assertEqual(post.calls, [])

    def test_202_counts_as_accepted(self):
        """The protocol returns 202 while a new key is still being verified,
        which is most of the first day."""
        self.assertEqual(self.go(Recorder(status=202)), "sent")

    def test_sent_as_the_protocol_asks(self):
        post = Recorder()
        self.go(post)
        self.assertEqual(post.headers["Content-Type"],
                         "application/json; charset=utf-8")


class TestTheAppSendsOnceItIsServing(Base):
    """The engine checks the key by fetching it from this site. Sending from
    inside startup meant asking it to read a file from a site that could
    not answer yet."""

    def test_startup_hands_it_to_a_thread(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from app import indexnow, main
        started, sent = [], []
        with patch.object(indexnow, "available", return_value=True), \
                patch.object(indexnow, "submit_if_changed",
                             side_effect=lambda *a, **k: sent.append(1)), \
                patch.object(main, "start_announcing",
                             lambda: started.append(1)), \
                TestClient(main.app) as client:
            self.assertEqual(client.get("/privacy").status_code, 200)
        self.assertEqual(started, [1])
        self.assertEqual(sent, [], "sent from inside startup")

    def test_the_thread_is_a_background_one(self):
        from unittest.mock import patch

        from app import main
        made = []

        class Thread:
            def __init__(self, target=None, daemon=None, name=None):
                made.append((target, daemon))

            def start(self):
                made.append("started")

        with patch.object(main.threading, "Thread", Thread):
            main.start_announcing()
        self.assertEqual(made, [(main.announce_pages, True), "started"])

    def test_the_thread_reports_and_never_raises(self):
        from unittest.mock import patch
        from app import indexnow, main
        with patch.object(indexnow, "submit_if_changed",
                          return_value="failed: 403 x"):
            self.assertEqual(main.announce_pages(delay=0), "failed: 403 x")
        with patch.object(indexnow, "submit_if_changed",
                          side_effect=RuntimeError("db gone")):
            self.assertEqual(main.announce_pages(delay=0),
                             "failed: RuntimeError")


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
