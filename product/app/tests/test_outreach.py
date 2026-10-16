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
            {"/contact": "<p>Email info@aberdeen-employ.org</p>"}),
            send=True, sender=self.sender)
        self.assertEqual(done["sent"], 1)
        self.assertEqual(self.sent[0][0], "info@aberdeen-employ.org")

    def test_never_a_named_persons_address(self):
        """Letters go to the organisation, never to an employee's own
        address."""
        done = self.go(FakeSession(
            {"/contact": "<p>Email jane.mcleod@aberdeen-employ.org</p>"}),
            send=True, sender=self.sender)
        self.assertEqual(self.sent, [])
        self.assertEqual(done["no_address"], 1)

    def test_never_their_own_hiring_desk(self):
        """hr@ and careers@ are about hiring their own staff."""
        self.go(FakeSession(
            {"/contact": "hr@aberdeen-employ.org careers@aberdeen-employ.org"}),
            send=True, sender=self.sender)
        self.assertEqual(self.sent, [])

    def test_a_service_inbox_beats_the_front_door(self):
        self.go(FakeSession({"/contact": "info@aberdeen-employ.org "
                             "employability@aberdeen-employ.org"}),
                send=True, sender=self.sender)
        self.assertEqual(self.sent[0][0], "employability@aberdeen-employ.org")

    def test_the_registers_own_address_when_it_is_a_shared_inbox(self):
        org = dict(ORG, email="enquiries@aberdeen-employ.org")
        done = outreach.run([org], self.state, session=FakeSession({}),
                            send=True, sender=self.sender, delay=0,
                            scrape_delay=0, out=lambda *a: None)
        self.assertEqual(done["sent"], 1)
        self.assertEqual(self.sent[0][0], "enquiries@aberdeen-employ.org")

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
        done = self.go(FakeSession({"/contact": "info@aberdeen-employ.org"}))
        self.assertEqual(self.sent, [])
        self.assertEqual(done["would_send"], 1)

    def test_a_dry_run_does_not_mark_anybody_as_asked(self):
        """Otherwise the first rehearsal silently burns the whole list."""
        self.go(FakeSession({"/contact": "info@aberdeen-employ.org"}))
        self.assertFalse(outreach.already_asked(self.state, ORG))

    def test_sending_with_no_sender_is_refused_before_the_loop(self):
        with self.assertRaises(RuntimeError):
            self.go(FakeSession({}), send=True)

    def test_the_limit_counts_letters_not_organisations_looked_at(self):
        """A list where most have no address would otherwise blow straight
        past the cap while appearing to respect it."""
        orgs = [{"name": f"Org {n}", "website": f"https://o{n}.org"}
                for n in range(6)]
        session = FakeSession({"/contact": "info@{host}"})
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
            done = self.go(FakeSession({"/contact": "info@aberdeen-employ.org"}),
                           send=True, sender=self.sender)
        finally:
            outreach.compose_letter = original
        self.assertEqual(self.sent, [])
        self.assertEqual(done["refused"], 1)
        # Still a target once the letter is fixed - nothing went out.
        self.assertFalse(outreach.already_asked(self.state, ORG))

    def test_nothing_about_his_age_or_employer(self):
        _, body = self.letter()
        low = body.lower()
        for word in (" 22", "aged", "year-old", "year old", "hydro"):
            self.assertNotIn(word, low)

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
        self.assertIn("no reply is needed", joined)
        self.assertIn("won't write again", joined)


class Page:
    def __init__(self, text, status=200, url=None):
        self.text, self.status_code, self.url = text, status, url


class Site:
    """A site whose pages are named in full, redirects and all."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, **kwargs):
        self.asked.append(url)
        page = self.pages.get(url)
        if page is None:
            return Page("", 404, url)
        if isinstance(page, tuple):           # (where it ends up, text)
            return Page(page[1], 200, page[0])
        return Page(page, 200, url)


UNI = {"name": "University of Leeds", "website": "https://www.leeds.ac.uk",
       "kind": "careers"}
COUNCIL = {"name": "Fife Council", "website": "https://www.fife.gov.uk",
           "kind": "council"}


class TestTheRightDeskForEachKind(unittest.TestCase):
    """careers@ is the wrong desk at a charity (their own hiring) and the
    right one at a university (the careers service). A council's front door
    is a contact centre, so only its employment team's inbox will do."""

    def test_a_charitys_careers_inbox_is_its_own_hiring(self):
        self.assertFalse(outreach.desk_inbox("careers@club.org", "charity"))
        self.assertTrue(outreach.desk_inbox("info@club.org", "charity"))

    def test_a_universitys_careers_inbox_is_the_careers_service(self):
        for good in ("careers@leeds.ac.uk", "careers.service@leeds.ac.uk",
                     "employability@leeds.ac.uk", "placements@leeds.ac.uk"):
            self.assertTrue(outreach.desk_inbox(good, "careers"), good)
        for bad in ("info@leeds.ac.uk", "jobs@leeds.ac.uk", "hr@leeds.ac.uk",
                    "recruitment@leeds.ac.uk", "j.smith@leeds.ac.uk",
                    "careers.complaints@leeds.ac.uk"):
            self.assertFalse(outreach.desk_inbox(bad, "careers"), bad)

    def test_a_councils_employment_team_and_never_its_front_door(self):
        for good in ("employability@fife.gov.uk",
                     "employmentsupport@fife.gov.uk",
                     "skills.team@fife.gov.uk"):
            self.assertTrue(outreach.desk_inbox(good, "council"), good)
        for bad in ("enquiries@fife.gov.uk", "contact@fife.gov.uk",
                    "employee.relations@fife.gov.uk", "jobs@fife.gov.uk",
                    "careers@fife.gov.uk", "hr.employment@fife.gov.uk"):
            self.assertFalse(outreach.desk_inbox(bad, "council"), bad)

    def test_the_site_end_takes_any_kinds_desk_and_never_a_person(self):
        self.assertTrue(outreach.any_desk("careers@leeds.ac.uk"))
        self.assertTrue(outreach.any_desk("info@club.org"))
        self.assertFalse(outreach.any_desk("jane.smith@leeds.ac.uk"))


class TestFindingTheDesk(unittest.TestCase):
    def find(self, org, pages):
        site = Site(pages)
        return outreach.find_address(org, session=site, scrape_delay=0), site

    def test_the_homepages_own_link_to_the_careers_service(self):
        found, site = self.find(UNI, {
            "https://www.leeds.ac.uk/": '<a href="/info">Contact</a>'
                '<a href="/students/careers-centre">Careers Centre</a>',
            "https://www.leeds.ac.uk/students/careers-centre":
                '<a href="/students/careers-centre/contact">Contact us</a>',
            "https://www.leeds.ac.uk/students/careers-centre/contact":
                "Email careers@leeds.ac.uk or the switchboard info@leeds.ac.uk",
        })
        self.assertEqual(found["email"], "careers@leeds.ac.uk")
        self.assertEqual(found["tier_name"], "their careers service")
        # The site's own link was followed before any guessed path.
        self.assertEqual(site.asked[1],
                         "https://www.leeds.ac.uk/students/careers-centre")

    def test_a_careers_subdomain(self):
        found, _ = self.find(UNI, {
            "https://careers.leeds.ac.uk/":
                '<a href="https://careers.leeds.ac.uk/contact">Contact</a>',
            "https://careers.leeds.ac.uk/contact":
                "<a href='mailto:careerscentre@leeds.ac.uk'>Email us</a>",
        })
        self.assertEqual(found["email"], "careerscentre@leeds.ac.uk")

    def test_nothing_printed_means_nothing_found(self):
        found, site = self.find(UNI, {
            "https://www.leeds.ac.uk/": '<a href="/careers">Careers</a>',
            "https://www.leeds.ac.uk/careers": "Book an appointment online.",
        })
        self.assertIsNone(found)
        self.assertLessEqual(len(site.asked), outreach.DESK_PAGES)

    def test_an_address_at_another_domain_is_not_theirs(self):
        found, _ = self.find(UNI, {
            "https://www.leeds.ac.uk/careers":
                "Powered by careers@targetconnect-host.com",
        })
        self.assertIsNone(found)

    def test_a_councils_employment_support_page(self):
        found, _ = self.find(COUNCIL, {
            "https://www.fife.gov.uk/": (
                "https://www.fife.gov.uk/kb/home",
                '<a href="/kb/docs/articles/jobs/employability">'
                'Employability support</a>'),
            "https://www.fife.gov.uk/kb/docs/articles/jobs/employability":
                "Contact enquiries@fife.gov.uk or "
                "opportunities.fife@fife.gov.uk, employability@fife.gov.uk",
        })
        self.assertEqual(found["email"], "employability@fife.gov.uk")

    def test_never_a_named_adviser(self):
        found, _ = self.find(UNI, {
            "https://www.leeds.ac.uk/careers":
                "Your adviser: jane.mcleod@leeds.ac.uk",
        })
        self.assertIsNone(found)


class TestTheLetterForEachKind(unittest.TestCase):
    def body(self, org):
        return outreach.compose_letter(org, {"email": "x@y.org"})[1]

    def test_each_kind_is_spoken_to_in_its_own_terms(self):
        self.assertIn("students and graduates", self.body(UNI))
        self.assertIn("employment support team", self.body(COUNCIL))
        self.assertIn("Aberdeen Employability Trust", self.body(ORG))

    def test_each_kind_is_counted_on_its_own(self):
        self.assertIn("/find?c=careers", self.body(UNI))
        self.assertIn("/find?c=council", self.body(COUNCIL))
        self.assertIn("/find?c=outreach", self.body(ORG))

    def test_every_kind_passes_the_checks(self):
        for org in (UNI, COUNCIL, ORG):
            self.assertEqual(outreach.check(*outreach.compose_letter(
                org, {"email": "x@y.org"})), [])

    def test_a_rehearsal_writes_nobody_off(self):
        """A rehearsal of a new kind must not record the ones it could not
        read as done: the finder may be better by the time it sends."""
        state = {}
        outreach.run([UNI], state, session=Site({}), delay=0,
                     scrape_delay=0, out=lambda *a: None)
        self.assertFalse(outreach.already_asked(state, UNI))


if __name__ == "__main__":
    unittest.main()
