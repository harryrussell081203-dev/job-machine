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

    def test_a_published_team_inbox_is_used(self):
        done = self.go(FakeSession(
            {"/contact": "<p>Email employability@aberdeen-employ.org</p>"}),
            send=True, sender=self.sender)
        self.assertEqual(done["sent"], 1)
        self.assertEqual(self.sent[0][0], "employability@aberdeen-employ.org")

    def test_a_named_person_is_never_written_to_even_when_published(self):
        """A note asking a service to pass something on belongs in the
        service's inbox. And a named employee's address is personal data in
        a repository whose Actions logs are public."""
        done = self.go(FakeSession(
            {"/contact": "<p>Email jane.mcleod@aberdeen-employ.org</p>"}),
            send=True, sender=self.sender)
        self.assertEqual(self.sent, [])
        self.assertEqual(done["no_address"], 1)

    def test_the_team_inbox_is_chosen_over_a_person_on_the_same_page(self):
        self.go(FakeSession({"/contact": (
            "<p>jane.mcleod@aberdeen-employ.org or "
            "employability@aberdeen-employ.org</p>")}),
            send=True, sender=self.sender)
        self.assertEqual(self.sent[0][0], "employability@aberdeen-employ.org")

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
        done = self.go(FakeSession({"/contact": "employability@aberdeen-employ.org"}))
        self.assertEqual(self.sent, [])
        self.assertEqual(done["would_send"], 1)

    def test_a_dry_run_does_not_mark_anybody_as_asked(self):
        """Otherwise the first rehearsal silently burns the whole list."""
        self.go(FakeSession({"/contact": "employability@aberdeen-employ.org"}))
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
            done = self.go(FakeSession({"/contact": "employability@aberdeen-employ.org"}),
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


class TestOnlyTeamInboxes(unittest.TestCase):
    """The employer classifier read these services exactly wrong - the
    perfect inbox as unusable, a team as a person - so organisations have
    their own rule. Each case below is a real address or a real shape."""

    CASES = {
        # the service's own inbox
        "abzworks@aberdeencity.gov.uk": 2,
        "employability@nescol.ac.uk": 2,
        "employmentsupportteam@aberdeenshire.gov.uk": 2,
        "partnership@discoverworkdundee.co.uk": 2,
        "support.team@example.org": 2,
        # the front door
        "contact@discoverworkdundee.co.uk": 1,
        "info@example.org": 1,
        "info-desk@example.org": 1,
        # people, never
        "jane.smith@jbg.org.uk": 0,
        "j.smith@jbg.org.uk": 0,
        "jsmith@jbg.org.uk": 0,
        "a.workman@example.org": 0,
        "jane.workman@example.org": 0,
        # the desks nobody should be sent a pitch
        "complaints@example.org": 0,
    }

    def test_each_case(self):
        for address, want in self.CASES.items():
            with self.subTest(address=address):
                self.assertEqual(outreach.team_inbox(address), want)

    def test_the_service_inbox_beats_the_front_door(self):
        got = outreach.best_team_inbox(["contact@d.example",
                                        "partnership@d.example"])
        self.assertEqual(got["email"], "partnership@d.example")

    def test_the_greeting_never_names_anybody(self):
        """"Hello Abzworks," is what the employer classifier produced."""
        _, body = outreach.compose_letter(
            ORG, {"email": "abzworks@x.org", "name": "Abzworks", "tier": 2})
        self.assertTrue(body.startswith("Hello,"))


class TestTheJobHuntsContactsAreNeverWrittenToTwice(Base):
    """The personal machine has already written to several of these bodies
    about Harry's own job hunt. A second letter from the product would break
    one-approach-ever and make two machines that must not interfere land in
    the same inbox."""

    def test_an_organisation_the_job_hunt_knows_is_skipped(self):
        done = self.go(FakeSession({"/contact": "employability@aberdeen-employ.org"}),
                       excluded={"aberdeen-employ.org"})
        self.assertEqual(done["excluded"], 1)
        self.assertEqual(done["would_send"], 0)

    def test_it_is_not_recorded_as_asked_by_the_product(self):
        """The product never wrote to them. The state should not say it did."""
        self.go(FakeSession({}), excluded={"aberdeen-employ.org"})
        self.assertFalse(outreach.already_asked(self.state, ORG))

    def test_a_subdomain_counts_as_the_same_organisation(self):
        org = {"name": "X", "website": "https://careers.aberdeen-employ.org"}
        self.assertTrue(outreach.is_excluded(org, {"aberdeen-employ.org"}))

    def test_the_real_registers_are_read_and_are_not_empty(self):
        """If this ever reads nothing, the guard has silently switched off."""
        hosts = outreach.excluded_hosts()
        self.assertGreater(len(hosts), 20)

    def test_an_unreadable_register_stops_everything(self):
        """Fail closed. An empty exclusion list from a missing file would
        look exactly like a list with nobody on it."""
        with self.assertRaises(RuntimeError):
            outreach.excluded_hosts(paths=("/nonexistent/register.json",))

    def test_nobody_on_the_shipped_list_is_someone_the_job_hunt_wrote_to(self):
        """Or if they are, they are caught. NESCol is on both lists - the job
        hunt asked it about the paused BEng - and is the live proof this
        works."""
        orgs = outreach.load(outreach.ORGS, [])
        hosts = outreach.excluded_hosts()
        caught = [o["name"] for o in orgs if outreach.is_excluded(o, hosts)]
        self.assertIn("North East Scotland College", caught)


class TestTheShippedTargetList(unittest.TestCase):
    def orgs(self):
        return outreach.load(outreach.ORGS, [])

    def test_there_is_one(self):
        self.assertGreater(len(self.orgs()), 0)

    def test_every_entry_says_where_it_was_confirmed(self):
        """The scraper takes addresses from whatever domain it is given, so a
        wrong website is a letter to the wrong organisation. Each entry
        carries the page it was checked against."""
        for org in self.orgs():
            with self.subTest(org=org.get("name")):
                self.assertTrue(org.get("website", "").startswith("https://"))
                self.assertTrue(org.get("source", "").startswith("https://"))

    def test_no_address_is_typed_into_the_file(self):
        """Addresses come from reading their site on the day, never from a
        list someone wrote. This is the never-guessed rule applied to us."""
        import json
        blob = json.dumps(self.orgs())
        self.assertNotIn("@", blob)

    def test_each_organisation_appears_once(self):
        keys = [outreach.key_for(o) for o in self.orgs()]
        self.assertEqual(len(keys), len(set(keys)))


class TestTheLetterIsHonest(Base):
    def letter(self):
        return outreach.compose_letter(ORG, {"email": "j@x.org", "name": "",
                                             "tier": 2, "tier_name": "x"})

    def test_the_figures_come_from_the_study_not_from_this_file(self):
        """One home for the counts. Change them there and the letter follows."""
        from app import study
        original = study.BY_RECIPIENT
        study.BY_RECIPIENT = (("A named human", 50, 20),
                              ("A generic inbox", 60, 3))
        try:
            _, body = self.letter()
        finally:
            study.BY_RECIPIENT = original
        joined = " ".join(body.split())
        self.assertIn("20 times out of 50", joined)
        self.assertIn("3 times out of 60", joined)

    def test_it_does_not_volunteer_his_age(self):
        """The whole job machine is built never to. An earlier draft of this
        letter opened with it."""
        _, body = self.letter()
        self.assertNotIn("22", body.replace(str(outreach.study.REPLIED), ""))
        self.assertNotIn("years old", body)
        self.assertNotIn("I am 2", body)

    def test_every_link_is_tagged(self):
        """Untagged, a signup from one of these letters counts as 'direct',
        and the one channel that can be run from here is invisible in the
        report meant to judge channels."""
        import re
        _, body = self.letter()
        links = re.findall(r"https?://\S+", body)
        self.assertTrue(links)
        for link in links:
            with self.subTest(link=link):
                self.assertIn("utm_source=orgs", link)

    def test_no_paragraph_is_broken_mid_sentence(self):
        """Hard breaks at 78 columns arrive ragged on a phone, worst of all
        around the long tagged links. One line per paragraph; the mail
        client wraps."""
        _, body = self.letter()
        paragraphs = body.split("\n\n")
        for para in paragraphs[:-1]:
            with self.subTest(para=para[:40]):
                self.assertNotIn("\n", para)

    def test_it_uses_the_studys_own_word_for_a_reply(self):
        """'Came back', because the study counted every message that arrived,
        autoresponders included. /numbers counts differently and says so."""
        _, body = self.letter()
        self.assertIn("came back", body)


class TestTheSendingMailbox(unittest.TestCase):
    ENV = ("OUTREACH_FROM", "OUTREACH_SMTP_USER", "OUTREACH_SMTP_PASSWORD",
           "GMAIL_ADDRESS")

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in self.ENV}
        self.saved_from = outreach.FROM_ADDRESS
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        outreach.FROM_ADDRESS = self.saved_from

    def configure(self, **env):
        for k in self.ENV:
            os.environ.pop(k, None)
        os.environ.update(env)
        outreach.FROM_ADDRESS = env.get("OUTREACH_FROM", "")

    def test_nothing_configured_is_refused(self):
        self.configure()
        with self.assertRaises(RuntimeError):
            outreach._smtp_sender()

    def test_the_job_hunts_mailbox_is_refused_as_the_login(self):
        """By comparison, not by trust. A copied secret must not be enough to
        put a marketing campaign behind the job applications' reputation."""
        self.configure(OUTREACH_FROM="hello@recruited.example",
                       OUTREACH_SMTP_USER="harry@gmail.example",
                       OUTREACH_SMTP_PASSWORD="x",
                       GMAIL_ADDRESS="harry@gmail.example")
        with self.assertRaises(RuntimeError):
            outreach._smtp_sender()

    def test_the_job_hunts_mailbox_is_refused_as_the_from_address(self):
        self.configure(OUTREACH_FROM="Harry@Gmail.example",
                       OUTREACH_SMTP_USER="other@gmail.example",
                       OUTREACH_SMTP_PASSWORD="x",
                       GMAIL_ADDRESS="harry@gmail.example")
        with self.assertRaises(RuntimeError):
            outreach._smtp_sender()

    def test_a_separate_mailbox_is_accepted(self):
        self.configure(OUTREACH_FROM="recruited.hello@gmail.example",
                       OUTREACH_SMTP_USER="recruited.hello@gmail.example",
                       OUTREACH_SMTP_PASSWORD="x",
                       GMAIL_ADDRESS="harry@gmail.example")
        os.environ.setdefault("SECRET_KEY", "test")
        self.assertTrue(callable(outreach._smtp_sender()))

    def test_it_no_longer_goes_through_resend(self):
        """Resend's rules forbid unsolicited email, and the product's users
        send through that account."""
        import inspect
        source = inspect.getsource(outreach._smtp_sender)
        self.assertNotIn("MANAGED_MAIL_KEY", source)


class TestAPreviewChangesNothing(unittest.TestCase):
    def test_a_preview_run_does_not_save_state(self):
        """The scheduled preview runs every weekday. If it saved, every
        'no address' would be retired on a rehearsal."""
        import json
        import tempfile
        work = tempfile.mkdtemp()
        orgs = os.path.join(work, "orgs.json")
        state = os.path.join(work, "state.json")
        with open(orgs, "w") as f:
            json.dump([{"name": "Nobody", "website": "https://nobody.invalid",
                        "source": "https://nobody.invalid"}], f)
        original = outreach.find_address
        outreach.find_address = lambda org, **kw: None
        try:
            outreach.main(["--orgs", orgs, "--state", state, "--summary", ""])
        finally:
            outreach.find_address = original
        self.assertFalse(os.path.exists(state))

    def test_the_preview_shows_each_letter_in_full(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "summary.md")
        subject, body = outreach.compose_letter(ORG, {"name": "", "email": "a@b",
                                                      "tier": 2})
        outreach.preview({"sent": 0, "would_send": 1, "no_address": 0,
                          "excluded": 0, "skipped": 0,
                          "letters": [{"org": ORG["name"], "to": "a@b",
                                       "tier": 2, "subject": subject,
                                       "body": body}]}, path)
        with open(path) as f:
            text = f.read()
        self.assertIn(ORG["name"], text)
        self.assertIn(body.splitlines()[0], text)


if __name__ == "__main__":
    unittest.main()
