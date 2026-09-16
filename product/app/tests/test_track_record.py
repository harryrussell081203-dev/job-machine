"""The founder's own numbers, read from where the personal machine puts them.

This is the one place the product touches anything belonging to the personal
job machine, so the tests are mostly about the boundary rather than the
arithmetic: it reads, it never writes, and it never brings the site down or
prints a mangled figure when the file is missing or wrong.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_app import AppTestCase  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        import app.track_record as tr
        self.tr = tr
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.real_path, tr.PATH = tr.PATH, self.path
        self.real_cache, tr._cache = tr._cache, None

        def restore():
            tr.PATH = self.real_path
            tr._cache = self.real_cache
            if os.path.exists(self.path):
                os.unlink(self.path)
        self.addCleanup(restore)

    def write(self, payload):
        with open(self.path, "w") as f:
            json.dump(payload, f)
        self.tr._cache = None

    def good(self, **over):
        record = {"applications": 116, "replies": 30, "reply_rate": 26,
                  "employers": 94, "updated_at": "2026-09-15T07:59:25+00:00"}
        record.update(over)
        return record


class TestReadingIt(Base):
    def test_it_reads_what_the_machine_published(self):
        self.write(self.good())
        self.assertEqual(self.tr.read()["applications"], 116)
        self.assertEqual(self.tr.read()["reply_rate"], 26)

    def test_a_missing_file_is_not_an_error(self):
        """The product has to run without the personal machine's data - and
        the landing page has to render without its headline figures rather
        than 500 because a marketing number is absent."""
        self.assertIsNone(self.tr.read())

    def test_nonsense_in_the_file_is_not_trusted(self):
        for junk in ("{not json", '"a string"', "[1, 2, 3]", "null"):
            with open(self.path, "w") as f:
                f.write(junk)
            self.tr._cache = None
            self.assertIsNone(self.tr.read(), junk)

    def test_a_number_that_is_not_a_number_is_refused(self):
        """Written by a different program. A string here would reach the
        template and render something like "26%%" on the front page."""
        self.write(self.good(reply_rate="twenty six"))
        self.assertIsNone(self.tr.read())

    def test_a_missing_field_is_refused_rather_than_half_rendered(self):
        record = self.good()
        del record["replies"]
        self.write(record)
        self.assertIsNone(self.tr.read())

    def test_a_negative_count_is_refused(self):
        self.write(self.good(replies=-3))
        self.assertIsNone(self.tr.read())

    def test_booleans_are_not_integers_here(self):
        """True == 1 in Python, so isinstance(x, int) passes for it. A
        published `true` would silently become "1 application"."""
        self.write(self.good(applications=True))
        self.assertIsNone(self.tr.read())

    def test_nothing_sent_makes_no_claim(self):
        """Zero is a true number and a terrible advertisement. "0
        applications, 0 replies" on a sales page reads as broken."""
        self.write(self.good(applications=0, replies=0, reply_rate=0))
        self.assertIsNone(self.tr.read())

    def test_it_is_cached_on_the_files_timestamp(self):
        """Read on every page view of the busiest page on the site, so it must
        not re-open the file each time - but a timed cache would go on serving
        yesterday's figures after a deploy for no reason. The timestamp is
        both."""
        self.write(self.good())
        self.assertEqual(self.tr.read()["applications"], 116)
        stamp = os.path.getmtime(self.path)

        # New contents, deliberately restored to the OLD timestamp: a reader
        # that re-parsed every call would see 200 here. Seeing 116 is the
        # proof that it did not.
        with open(self.path, "w") as f:
            json.dump(self.good(applications=200), f)
        os.utime(self.path, (stamp, stamp))
        self.assertEqual(self.tr.read()["applications"], 116)

        # A real publication moves the timestamp, and then it is picked up.
        os.utime(self.path, (stamp + 10, stamp + 10))
        self.assertEqual(self.tr.read()["applications"], 200)

    def test_it_never_writes(self):
        """The whole basis for letting the product read the job hunt's data.
        One direction, and nothing the website does can reach back."""
        self.write(self.good())
        before = os.stat(self.path)
        self.tr._cache = None
        self.tr.read()
        after = os.stat(self.path)
        self.assertEqual(before.st_mtime, after.st_mtime)
        self.assertEqual(before.st_size, after.st_size)


class TestOnThePage(AppTestCase):
    def test_the_landing_page_quotes_it_rather_than_a_typed_number(self):
        import app.track_record as tr
        record = tr.read()
        page = self.client.get("/").text
        if record:
            self.assertIn(str(record["applications"]), page)
            self.assertIn(f"{record['reply_rate']}%", page)
        # The figures that used to be typed into the template by hand.
        self.assertNotIn("A fortnight in August 2026", page)

    def test_the_page_still_renders_with_nothing_published(self):
        import app.track_record as tr
        real, tr.PATH = tr.PATH, "/nonexistent/track_record.json"
        cache, tr._cache = tr._cache, None
        try:
            r = self.client.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Get your CV in front of a human", r.text)
            # No claim at all, rather than a stale or empty one.
            self.assertNotIn("emails sent", r.text)
        finally:
            tr.PATH, tr._cache = real, cache



class TestTheCardEveryShareShows(unittest.TestCase):
    """Post the site anywhere and og.png is what appears. It is seen by
    everybody shown the link and clicked by only some of them, which makes it
    the highest-traffic surface the product has and the one nobody looks at
    twice.

    Which is how it came to be wrong. It was drawn once by hand and the script
    was thrown away, so when the figures moved it could not be redrawn: for
    six days the card said 105 applications and 25% while the page it linked
    to said 125 and 17%. Somebody comparing the two concludes the numbers are
    decorative, and they are the entire argument.
    """

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))) + "/product")
        from tools import make_og_card
        self.card = make_og_card
        self.dir = tempfile.mkdtemp()
        self.out = os.path.join(self.dir, "og.png")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def record(self, **over):
        r = {"applications": 125, "replies": 21, "reply_rate": 17,
             "employers": 96, "updated_at": ""}
        r.update(over)
        return r

    def test_it_draws_a_real_png_at_the_size_every_platform_wants(self):
        self.card.draw(self.record(), self.out)
        with open(self.out, "rb") as f:
            head = f.read(24)
        self.assertTrue(head.startswith(b"\x89PNG"))
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        self.assertEqual((width, height),
                         (self.card.WIDTH * self.card.SCALE,
                          self.card.HEIGHT * self.card.SCALE))

    def test_it_writes_down_what_it_drew(self):
        """A PNG cannot be asked what it says without OCR, so it says so
        alongside. This is what makes staleness checkable at all."""
        self.card.draw(self.record(applications=200, replies=40,
                                   reply_rate=20), self.out)
        self.assertEqual(self.card.drawn_figures(self.out),
                         {"applications": 200, "replies": 40,
                          "reply_rate": 20})

    def test_a_card_that_matches_the_figures_is_not_stale(self):
        self.card.draw(self.record(), self.out)
        was = self.card.drawn_figures(self.out)
        now = {k: self.record()[k] for k in was}
        self.assertEqual(was, now)

    def test_a_card_drawn_with_different_figures_is_stale(self):
        """The check that would have caught the six days."""
        self.card.draw(self.record(applications=105, replies=26,
                                   reply_rate=25), self.out)
        was = self.card.drawn_figures(self.out)
        now = {k: self.record()[k] for k in was}
        self.assertNotEqual(was, now)

    def test_staleness_is_figures_and_not_timestamps(self):
        """The personal machine rewrites track_record.json at the end of every
        run whether or not anything changed, so an mtime comparison reports a
        perfectly accurate card as stale several times a day. A warning that
        cries wolf is ignored on the day it is right - the same lesson as the
        heartbeat alarm that fired daily on a healthy machine."""
        self.card.draw(self.record(), self.out)
        os.utime(self.card.track_record.PATH, None)      # touched, unchanged
        was = self.card.drawn_figures(self.out)
        now = {k: self.record()[k] for k in was}
        self.assertEqual(was, now, "a touched file must not mean a stale card")

    def test_the_numbers_come_from_the_same_place_the_page_reads(self):
        """Not typed into the generator. One fact, one source."""
        import inspect
        source = inspect.getsource(self.card)
        self.assertIn("track_record.read()", source)
        for typed in ("105", "125", "26%", "25%"):
            self.assertNotIn(f">{typed}<", source,
                             "a figure is hard-coded into the card")


if __name__ == "__main__":
    unittest.main()
