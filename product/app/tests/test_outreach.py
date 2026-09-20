"""The marketing machine, held to the product's own rules.

This is the one piece of the system that writes to strangers on the product's
behalf, so the tests are not really about outreach working. They are about the
three ways it could quietly stop obeying the rules the site publishes:

  - writing to an address nobody published, which is the single claim every
    answer page rests on;
  - writing to the same organisation twice, which is how a helpful note
    becomes a nuisance;
  - sending at all when nobody decided it should, from whichever mailbox
    happened to be configured.

Nothing here touches the network or a mail server. The fetcher and the sender
are both injected, and a test that sends is a test where the sender is a list.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import outreach  # noqa: E402


class FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class FakeSession:
    """A website. Any path that is not named answers 404, because most of the
    paths the scraper tries do not exist on a small charity's site."""

    def __init__(self, pages=None):
        self.pages = pages or {}
        self.asked = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        host = url.split("//", 1)[-1].split("/", 1)[0]
        for fragment, body in self.pages.items():
            if url.endswith(fragment):
                # "{host}" lets a page carry an address at whatever domain it
                # is being served from, which is what a real site does and
                # what clean_emails checks. Without it a fixture address at
                # example.org is correctly discarded on every other domain,
                # and the test reads as a bug in the code it is testing.
                return FakeResponse(body.replace("{host}", host))
        return FakeResponse("", 404)


ORG = {"name": "Aberdeen Employability Trust",
       "website": "https://www.aberdeen-employ.org"}


class Base(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.state = {}

    def sender(self, to, subject, body):
        self.sent.append((to, subject, body))

    def go(self, session, **kw):
        kw.setdefault("delay", 0)
        kw.setdefault("scrape_delay", 0)
        kw.setdefault("out", lambda *a: None)
        return outreach.run([ORG], self.state, session=session, **kw)


class TestNoAddressIsEverInvented(Base):
    def test_a_site_with_no_address_gets_no_letter(self):
        """The most common outcome and a correct one. A product that sells
        'we never guess' cannot market itself with a guess."""
        done = self.go(FakeSession({"/contact": "<p>Call us on 01224 123456</p>"}),
                       send=True, sender=self.sender)
        self.assertEqual(self.sent, [])
        self.assertEqual(done["no_address"], 1)

    def test_a_published_address_is_used(self):
        done = self.go(FakeSession(
            {"/contact": "<p>Email jane.mcleod@aberdeen-employ.org</p>"}),
            send=True, sender=self.sender)
        self.assertEqual(done["sent"], 1)
        self.assertEqual(self.sent[0][0], "jane.mcleod@aberdeen-employ.org")

    def test_an_address_at_some_other_domain_is_not_taken(self):
        """Charity sites carry funders, partners and web developers. Writing
        to the agency that built the site is not writing to the charity."""
        self.go(FakeSession(
            {"/contact": "<p>Site by hello@somewebagency.co.uk</p>"}),
            send=True, sender=self.sender)
        self.assertEqual(self.sent, [])

    def test_a_no_address_result_is_recorded_so_it_is_not_retried(self):
        self.go(FakeSession({}), send=True, sender=self.sender)
        self.assertTrue(outreach.already_asked(self.state, ORG))


class TestOnceAndOnlyOnce(Base):
    def test_an_organisation_already_written_to_is_skipped(self):
        outreach.record(self.state, ORG, "sent", "a@b.org")
        done = self.go(FakeSession({"/contact": "x@aberdeen-employ.org"}),
                       send=True, sender=self.sender)
        self.assertEqual(self.sent, [])
        self.assertEqual(done["skipped"], 1)

    def test_the_same_body_under_two_names_is_one_organisation(self):
        """Registers carry the same charity twice. Keyed by name, both get a
        letter and both land in the same inbox."""
        outreach.record(self.state, ORG, "sent", "a@b.org")
        twin = {"name": "Aberdeen Employability Trust (Scotland)",
                "website": "http://aberdeen-employ.org/about"}
        self.assertTrue(outreach.already_asked(self.state, twin))


class TestItDoesNotSendByAccident(Base):
    def test_the_default_is_a_dry_run(self):
        done = self.go(FakeSession({"/contact": "jane@aberdeen-employ.org"}))
        self.assertEqual(self.sent, [])
        self.assertEqual(done["would_send"], 1)

    def test_a_dry_run_does_not_mark_anybody_as_asked(self):
        """Otherwise the first rehearsal silently burns the whole list."""
        self.go(FakeSession({"/contact": "jane@aberdeen-employ.org"}))
        self.assertFalse(outreach.already_asked(self.state, ORG))

    def test_sending_with_no_sender_is_refused_before_the_loop(self):
        with self.assertRaises(RuntimeError):
            self.go(FakeSession({}), send=True)

    def test_the_limit_counts_letters_not_organisations_looked_at(self):
        """A list where most have no address would otherwise blow straight
        past the cap while appearing to respect it."""
        orgs = [{"name": f"Org {n}", "website": f"https://o{n}.org"}
                for n in range(6)]
        session = FakeSession({"/contact": "careers@{host}"})
        done = outreach.run(orgs, {}, session=session, limit=2, delay=0,
                            scrape_delay=0, out=lambda *a: None)
        self.assertEqual(done["would_send"], 2)


class TestTheLetterItself(Base):
    def letter(self):
        return outreach.compose_letter(ORG, {"email": "j@x.org", "name": "Jane",
                                             "tier": 3,
                                             "tier_name": "named person"})

    def test_it_passes_the_same_checks_a_job_application_does(self):
        """A letter that reads as generated is as unwelcome to a careers
        adviser as it is to an employer, and the list is already written."""
        subject, body = self.letter()
        self.assertEqual(outreach.check(subject, body), [])

    def test_a_letter_that_fails_the_check_is_not_sent_and_not_recorded(self):
        original = outreach.compose_letter
        outreach.compose_letter = lambda org, c: ("Hi", "I am writing to you!")
        try:
            done = self.go(FakeSession({"/contact": "jane@aberdeen-employ.org"}),
                           send=True, sender=self.sender)
        finally:
            outreach.compose_letter = original
        self.assertEqual(self.sent, [])
        self.assertEqual(done["refused"], 1)
        # Still a target once the letter is fixed - nothing went out.
        self.assertFalse(outreach.already_asked(self.state, ORG))

    def test_it_names_the_organisation_and_the_free_thing(self):
        _, body = self.letter()
        self.assertIn("Aberdeen Employability Trust", body)
        self.assertIn("/find", body)

    def test_every_rate_in_it_carries_its_count(self):
        """The rule the whole site runs on. This letter is the first thing a
        stranger reads, so a bare percentage here is the worst place for one."""
        _, body = self.letter()
        for figure in ("13 times out of\n34", "4 times out of 42"):
            self.assertIn(figure.replace("\n", " ").replace("  ", " "),
                          " ".join(body.split()), body)

    def test_it_asks_for_nothing_and_says_it_will_not_write_again(self):
        _, body = self.letter()
        joined = " ".join(body.split()).lower()
        self.assertIn("no reply needed", joined)
        self.assertIn("will not write again", joined)


if __name__ == "__main__":
    unittest.main()
