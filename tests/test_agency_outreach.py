"""
Tests for getting Harry onto the recruitment agencies' databases.

Offline. No agency is contacted, nothing is scraped, no email leaves.

The thing worth guarding here is the exception this module represents. Every
other route in this repository writes to a company once, ever, because a second
unsolicited email reads as pestering. This one is deliberately allowed to write
again - so the cooldown, the cap and the fact that the second letter is a
DIFFERENT letter are the safety rails, and they are what these tests are for.
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agency_outreach as ao  # noqa: E402
import job_machine as jm  # noqa: E402


def ts(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def blank_state():
    return {"jobs": {}, "companies_contacted": {}, "agency_registered": {},
            "support_asked": {}, "send_counts": {}}


AGENCY = {"name": "Cammach", "site": "wearecammach.com", "group": "energy",
          "desk": "you recruit engineering staff across the Aberdeen energy market"}


class TestWhoIsDue(unittest.TestCase):
    def test_an_agency_never_written_to_is_due_as_approach_one(self):
        ok, approach, _ = ao.due(blank_state(), AGENCY)
        self.assertTrue(ok)
        self.assertEqual(approach, 1)

    def test_an_agency_written_to_yesterday_is_not_due(self):
        state = blank_state()
        state["agency_registered"]["cammach"] = {"at": ts(1), "count": 1}
        ok, _, reason = ao.due(state, AGENCY)
        self.assertFalse(ok)
        self.assertIn("gap is", reason)

    def test_the_gap_is_what_makes_this_a_refresh_and_not_a_nudge(self):
        """A month, not a week. Any shorter and the second letter is chasing,
        which is the thing the rest of the project is careful never to do."""
        self.assertGreaterEqual(ao.REGISTER_GAP_DAYS, 21)

    def test_an_agency_past_the_gap_is_due_as_the_next_approach(self):
        state = blank_state()
        state["agency_registered"]["cammach"] = {
            "at": ts(ao.REGISTER_GAP_DAYS + 1), "count": 1}
        ok, approach, _ = ao.due(state, AGENCY)
        self.assertTrue(ok)
        self.assertEqual(approach, 2)

    def test_it_stops_at_the_cap_however_long_it_has_been(self):
        """Silence from a desk after six letters is an answer."""
        state = blank_state()
        state["agency_registered"]["cammach"] = {
            "at": ts(400), "count": ao.REGISTER_MAX}
        ok, _, reason = ao.due(state, AGENCY)
        self.assertFalse(ok)
        self.assertIn("cap", reason)

    def test_a_missing_timestamp_does_not_unlock_the_gap(self):
        state = blank_state()
        state["agency_registered"]["cammach"] = {"count": 1}
        ok, approach, _ = ao.due(state, AGENCY)
        self.assertTrue(ok)
        self.assertEqual(approach, 2)


class TestItDoesNotTalkOverThePipeline(unittest.TestCase):
    """Both routes reach the same consultant's inbox. A note about a live
    vacancy is the more useful of the two, so it wins and registration waits."""

    def test_an_agency_just_pitched_about_a_vacancy_is_left_alone(self):
        state = blank_state()
        state["companies_contacted"][jm.company_key("Cammach")] = {
            "at": ts(1), "company": "Cammach"}
        self.assertEqual(ao.recently_pitched(state, AGENCY), 1)

    def test_an_old_pitch_does_not_block_registration(self):
        state = blank_state()
        state["companies_contacted"][jm.company_key("Cammach")] = {
            "at": ts(jm.AGENCY_GAP_DAYS + 5), "company": "Cammach"}
        self.assertIsNone(ao.recently_pitched(state, AGENCY))

    def test_an_agency_the_charity_route_already_wrote_to_is_left_alone(self):
        """Ten recruiters were put in support_orgs.json the same hour this
        module was written, and both routes fired three minutes apart. Three
        consultants got two different letters from Harry inside two minutes."""
        state = blank_state()
        state["support_asked"][jm.company_key("Cammach")] = {"at": ts(0)}
        self.assertTrue(ao.written_to_by_the_support_route(state, AGENCY))
        with mock.patch.object(ao, "load_agencies", return_value=[AGENCY]), \
             mock.patch.object(ao, "find_address",
                               return_value=("a@b.example", None)), \
             mock.patch.object(jm, "send_email") as send:
            self.assertEqual(ao.run(state, send=True), 0)
        send.assert_not_called()

    def test_a_run_skips_it_rather_than_writing(self):
        state = blank_state()
        state["companies_contacted"][jm.company_key("Cammach")] = {"at": ts(0)}
        with mock.patch.object(ao, "load_agencies", return_value=[AGENCY]), \
             mock.patch.object(ao, "find_address",
                               return_value=("jobs@wearecammach.com", None)), \
             mock.patch.object(jm, "send_email") as send:
            self.assertEqual(ao.run(state, send=True), 0)
        send.assert_not_called()


class TestTheFirstLetter(unittest.TestCase):
    def letter(self, agency=None):
        return ao.compose(agency or AGENCY, 1)

    def test_it_asks_for_the_thing_it_is_for(self):
        _, body = self.letter()
        self.assertIn("on your database", body)

    def test_it_says_why_this_agency_rather_than_any_agency(self):
        _, body = self.letter()
        self.assertIn(AGENCY["desk"], body)

    def test_an_agency_with_no_desk_line_still_writes_a_sound_letter(self):
        _, body = self.letter({"name": "X", "site": "x.example"})
        self.assertNotIn("because .", body)
        self.assertNotIn("because,", body)
        self.assertIn("My CV is attached", body)

    def test_it_gives_the_facts_a_consultant_matches_on(self):
        _, body = self.letter()
        for fact in ("Sonardyne", "Royal Navy", "DV cleared",
                     "Available immediately", "Aberdeen", "35k"):
            with self.subTest(fact=fact):
                self.assertIn(fact, body)

    def test_it_volunteers_the_awkward_facts_too(self):
        """A consultant who finds these out at the point of submission has
        wasted a placement and Harry's shot at it."""
        _, body = self.letter()
        self.assertIn("I do not drive", body)
        self.assertIn("do not hold BOSIET or MIST yet", body)

    def test_it_claims_nothing_he_does_not_hold(self):
        _, body = self.letter()
        low = body.lower()
        for false_claim in ("bosiet certified", "mist certified", "i drive",
                            "full uk driving licence", "chartered", "degree in",
                            "beng (hons) in instrumentation, measurement and "
                            "control awarded"):
            with self.subTest(claim=false_claim):
                self.assertNotIn(false_claim, low)

    def test_it_ends_with_a_question_and_his_number(self):
        _, body = self.letter()
        self.assertIn("?", body)
        self.assertIn(jm.PHONE, body)

    def test_a_veteran_specialist_agency_is_told_he_is_a_veteran_first(self):
        subject, body = self.letter(
            {"name": "Ex-Mil", "site": "ex-mil.co.uk", "group": "veteran",
             "desk": "you work exclusively with ex-military candidates"})
        self.assertIn("Royal Navy veteran", subject)
        self.assertLess(body.index("Royal Navy veteran"), body.index("Sonardyne"))

    def test_the_greeting_uses_a_first_name_when_discovery_found_one(self):
        _, body = ao.compose(AGENCY, 1, None, "Sarah")
        self.assertTrue(body.startswith("Hi Sarah,"))

    def test_it_greets_plainly_when_no_name_is_known(self):
        _, body = self.letter()
        self.assertTrue(body.startswith("Hello,"))


class TestTheSecondLetterIsADifferentLetter(unittest.TestCase):
    """The same pitch sent twice is a mail merge and reads like one."""

    def refresh(self, entry=None):
        return ao.compose(AGENCY, 2, entry or {"at": ts(35), "count": 1})

    def test_it_is_not_the_first_letter_again(self):
        _, first = ao.compose(AGENCY, 1)
        _, again = self.refresh()
        self.assertNotEqual(first, again)
        self.assertNotIn("I would like to be on your database", again)

    def test_it_says_it_is_a_refresh_and_why(self):
        subject, body = self.refresh()
        self.assertIn("refresh", subject.lower())
        self.assertIn("current CV attached", body)

    def test_it_owns_up_to_having_written_before(self):
        """Pretending the first email never happened is the thing that makes a
        second one feel like spam."""
        month = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%B")
        _, body = self.refresh()
        self.assertIn(f"I last wrote in {month}", body)

    def test_it_reads_properly_when_the_date_is_missing(self):
        _, body = self.refresh({"count": 1})
        self.assertIn("Nothing has changed on the facts", body)
        self.assertNotIn("I last wrote in ,", body)

    def test_it_still_carries_the_facts_and_the_constraints(self):
        _, body = self.refresh()
        self.assertIn("Sonardyne", body)
        self.assertIn("I do not drive", body)

    def test_it_asks_about_live_roles_rather_than_repeating_the_ask(self):
        _, body = self.refresh()
        self.assertIn("Anything live at the moment", body)


class TestAddressesAreNeverGuessed(unittest.TestCase):
    def test_no_site_means_no_email(self):
        self.assertEqual(ao.find_address({"name": "X"}), (None, None))

    def test_a_site_publishing_nothing_real_is_refused(self):
        with mock.patch.object(jm, "scrape_site", return_value=[]):
            self.assertEqual(ao.find_address(AGENCY), (None, None))

    def test_an_address_whose_own_domain_cannot_receive_mail_is_refused(self):
        with mock.patch.object(jm, "scrape_site",
                               return_value=["jobs@orionjobs.com"]), \
             mock.patch.object(jm, "has_mx", return_value=False):
            self.assertEqual(ao.find_address(AGENCY), (None, None))

    def test_the_mx_check_is_on_the_address_not_on_the_website(self):
        """Agencies routinely run the careers site on one domain and their mail
        on another, so requiring the scraped domain to carry an MX record - as
        the support-org route does - would drop real agencies on the floor."""
        calls = []

        def fake_mx(domain):
            calls.append(domain)
            return True

        with mock.patch.object(jm, "scrape_site",
                               return_value=["aberdeen@oriongroup.co.uk"]), \
             mock.patch.object(jm, "has_mx", side_effect=fake_mx):
            address, _ = ao.find_address({"name": "Orion Group",
                                          "site": "orionjobs.com"})
        self.assertEqual(address, "aberdeen@oriongroup.co.uk")
        self.assertEqual(calls, ["oriongroup.co.uk"])

    def test_a_verified_published_address_is_used_when_the_site_is_unreadable(self):
        """Eight of twenty-one published nothing a scraper could read on the
        first live run. A published address transcribed by hand is not a
        guessed one - but only if the file says where it was read."""
        agency = dict(AGENCY, email="info@example.com",
                      email_source="their own contact page")
        with mock.patch.object(jm, "scrape_site", return_value=[]), \
             mock.patch.object(jm, "has_mx", return_value=True):
            self.assertEqual(ao.find_address(agency),
                             ("info@example.com", None))

    def test_an_address_with_no_stated_source_is_refused(self):
        agency = dict(AGENCY, email="info@example.com")
        with mock.patch.object(jm, "scrape_site", return_value=[]), \
             mock.patch.object(jm, "has_mx", return_value=True):
            self.assertEqual(ao.find_address(agency), (None, None))

    def test_the_scraped_address_still_wins(self):
        agency = dict(AGENCY, email="info@example.com",
                      email_source="their own contact page")
        with mock.patch.object(jm, "scrape_site",
                               return_value=["jobs@wearecammach.com"]), \
             mock.patch.object(jm, "has_mx", return_value=True):
            self.assertEqual(ao.find_address(agency)[0],
                             "jobs@wearecammach.com")

    def test_an_agency_with_no_findable_address_is_skipped_not_invented(self):
        state = blank_state()
        with mock.patch.object(ao, "load_agencies", return_value=[AGENCY]), \
             mock.patch.object(jm, "scrape_site", return_value=[]), \
             mock.patch.object(jm, "send_email") as send:
            ao.run(state, send=True)
        send.assert_not_called()
        self.assertEqual(state["agency_registered"], {})


class TestSending(unittest.TestCase):
    def run_one(self, state, send=True, test_mode=False):
        with mock.patch.object(ao, "load_agencies", return_value=[AGENCY]), \
             mock.patch.object(ao, "find_address",
                               return_value=("jobs@wearecammach.com", None)), \
             mock.patch.object(ao, "cv_file", return_value=None), \
             mock.patch.object(ao, "REGISTER_INTERVAL_SECONDS", 0), \
             mock.patch.object(jm, "TEST_MODE", test_mode), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "harry@example.com"), \
             mock.patch.object(jm, "save"), \
             mock.patch.object(jm, "send_email") as send_email:
            written = ao.run(state, send=send)
        return written, send_email

    def test_a_dry_run_sends_nothing_and_records_nothing(self):
        state = blank_state()
        written, send_email = self.run_one(state, send=False)
        self.assertEqual(written, 1)
        send_email.assert_not_called()
        self.assertEqual(state["agency_registered"], {})

    def test_a_real_send_is_recorded_so_the_cooldown_starts(self):
        state = blank_state()
        _, send_email = self.run_one(state)
        send_email.assert_called_once()
        entry = state["agency_registered"]["cammach"]
        self.assertEqual(entry["count"], 1)
        self.assertEqual(entry["email"], "jobs@wearecammach.com")
        self.assertFalse(ao.due(state, AGENCY)[0])

    def test_the_cv_is_always_attached(self):
        _, send_email = self.run_one(blank_state())
        self.assertTrue(send_email.call_args.kwargs["attach_cv"])

    def test_test_mode_routes_it_to_harry_and_records_nothing(self):
        """Otherwise a rehearsal would burn the agency's cooldown for real."""
        state = blank_state()
        _, send_email = self.run_one(state, test_mode=True)
        to_addr = send_email.call_args.args[0]
        subject = send_email.call_args.args[1]
        self.assertEqual(to_addr, "harry@example.com")
        self.assertIn("jobs@wearecammach.com", subject)
        self.assertEqual(state["agency_registered"], {})

    def test_the_per_run_cap_is_obeyed(self):
        many = [dict(AGENCY, name=f"Agency {i}") for i in range(5)]
        state = blank_state()
        with mock.patch.object(ao, "load_agencies", return_value=many), \
             mock.patch.object(ao, "find_address",
                               return_value=("a@b.example", None)), \
             mock.patch.object(ao, "cv_file", return_value=None), \
             mock.patch.object(ao, "REGISTER_INTERVAL_SECONDS", 0), \
             mock.patch.object(jm, "save"), \
             mock.patch.object(jm, "send_email") as send_email:
            ao.run(state, send=True, limit=2)
        self.assertEqual(send_email.call_count, 2)

    def test_one_failure_does_not_stop_the_rest(self):
        many = [dict(AGENCY, name=f"Agency {i}") for i in range(3)]
        state = blank_state()
        with mock.patch.object(ao, "load_agencies", return_value=many), \
             mock.patch.object(ao, "find_address",
                               return_value=("a@b.example", None)), \
             mock.patch.object(ao, "cv_file", return_value=None), \
             mock.patch.object(ao, "REGISTER_INTERVAL_SECONDS", 0), \
             mock.patch.object(jm, "save"), \
             mock.patch.object(jm, "send_email",
                               side_effect=[OSError("smtp"), None, None]) as send:
            ao.run(state, send=True)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(len(state["agency_registered"]), 2)


class TestTheShippedList(unittest.TestCase):
    def setUp(self):
        with open(ao.AGENCIES_PATH) as f:
            self.data = json.load(f)

    def test_every_agency_has_what_the_letter_needs(self):
        for agency in self.data["agencies"]:
            with self.subTest(agency=agency.get("name")):
                self.assertTrue(agency.get("name"))
                self.assertTrue(agency.get("site"))
                self.assertTrue(agency.get("desk"))
                self.assertIn(agency.get("group"),
                              ("energy", "technical", "defence", "veteran"))

    def test_no_agency_is_listed_twice(self):
        keys = [jm.company_key(a["name"]) for a in self.data["agencies"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_shipped_address_says_where_it_came_from(self):
        """Discovery first; a typed-in address only where the file records the
        page it was read from, so nothing here can be a guessed pattern."""
        for agency in self.data["agencies"]:
            if agency.get("email"):
                with self.subTest(agency=agency["name"]):
                    self.assertTrue(agency.get("email_source"))
                    self.assertIn("@", agency["email"])

    def test_the_four_harry_named_are_on_it(self):
        names = " ".join(a["name"] for a in self.data["agencies"]).lower()
        for agency in ("orion", "cammach", "texo", "tmm"):
            with self.subTest(agency=agency):
                self.assertIn(agency, names)

    def test_the_list_is_the_recruiters_the_pipeline_already_knows(self):
        """Anything here must read as an agency to job_machine.is_agency, or
        the two routes will disagree about who is an employer."""
        for agency in self.data["agencies"]:
            with self.subTest(agency=agency["name"]):
                self.assertTrue(jm.is_agency({"company": agency["name"]}),
                                f"{agency['name']} does not look like an agency")


class TestThePipelineStage(unittest.TestCase):
    def test_the_pipeline_paces_itself_more_tightly_than_a_manual_fire(self):
        self.assertLess(jm.AGENCY_PIPELINE_PER_RUN, ao.REGISTER_PER_RUN)

    def test_the_stage_sends_and_respects_its_limit(self):
        state = blank_state()
        with mock.patch.object(ao, "run") as run:
            jm.agency_refresh(state)
        run.assert_called_once_with(state, send=True,
                                    limit=jm.AGENCY_PIPELINE_PER_RUN)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTheOffshoreTicketsQuestion(unittest.TestCase):
    """OPITO BOSIET and MIST are about a thousand pounds and effectively
    mandatory offshore, which is the market this whole pipeline searches. The
    charities are already being asked whether they will fund them; this is the
    commercial version of the same question, to a firm that has a reason to
    front the cost a charity does not."""

    FLAGGED = dict(AGENCY, ask_tickets=True,
                   tickets_source="Harry was told they have funded tickets")

    def test_only_a_flagged_agency_is_asked(self):
        ok, reason = ao.tickets_due(blank_state(), AGENCY)
        self.assertFalse(ok)
        self.assertIn("not flagged", reason)

    def test_a_flagged_agency_never_written_to_is_due(self):
        self.assertTrue(ao.tickets_due(blank_state(), self.FLAGGED)[0])

    def test_it_waits_after_a_cold_letter_they_have_not_answered(self):
        """A second email tomorrow asking them to spend a thousand pounds is
        how a candidate becomes a nuisance."""
        state = blank_state()
        state["agency_registered"]["cammach"] = {"at": ts(1), "count": 1}
        ok, reason = ao.tickets_due(state, self.FLAGGED)
        self.assertFalse(ok)
        self.assertIn("waiting", reason)

    def test_it_goes_once_the_ordinary_agency_gap_has_passed(self):
        state = blank_state()
        state["agency_registered"]["cammach"] = {
            "at": ts(jm.AGENCY_GAP_DAYS + 1), "count": 1}
        self.assertTrue(ao.tickets_due(state, self.FLAGGED)[0])

    def test_a_live_conversation_does_not_have_to_wait(self):
        """A question added to a thread already running is not a cold email."""
        state = blank_state()
        state["agency_registered"]["cammach"] = {"at": ts(0), "count": 1}
        self.assertTrue(ao.tickets_due(
            state, dict(self.FLAGGED, thread_open=True))[0])

    def test_nobody_is_asked_twice(self):
        state = blank_state()
        ao.record_tickets_ask(state, self.FLAGGED, "a@b.example")
        ok, reason = ao.tickets_due(state, self.FLAGGED)
        self.assertFalse(ok)
        self.assertIn("already asked", reason)

    def test_it_never_tells_a_firm_what_its_own_policy_is(self):
        """He was told this by somebody else and nothing published confirms
        it. The letter has to say told, and ask."""
        _, body = ao.tickets_letter(self.FLAGGED)
        self.assertIn("I have been told", body)
        self.assertIn("Is that something you would ever consider", body)
        for assertion in ("you fund", "you sponsor", "your policy is",
                          "as you know", "you have funded mine"):
            with self.subTest(assertion=assertion):
                self.assertNotIn(assertion, body.lower())

    def test_an_unflagged_source_asks_without_the_hearsay(self):
        _, body = ao.tickets_letter(dict(AGENCY, ask_tickets=True))
        self.assertNotIn("I have been told", body)
        self.assertIn("in case it is something you do", body)

    def test_it_offers_to_pay_it_back_rather_than_asking_for_charity(self):
        _, body = ao.tickets_letter(self.FLAGGED)
        self.assertIn("not asking for a favour", body.lower())
        self.assertIn("come out of what I earn", body)

    def test_it_gives_them_an_easy_no(self):
        _, body = ao.tickets_letter(self.FLAGGED)
        self.assertIn("no problem at all", body)

    def test_the_register_is_its_own_and_does_not_eat_a_refresh_slot(self):
        state = blank_state()
        ao.record_tickets_ask(state, self.FLAGGED, "a@b.example")
        self.assertIn("cammach", state[ao.TICKETS_ASKED])
        self.assertEqual(state["agency_registered"], {})

    def test_a_run_sends_one_and_records_it(self):
        state = blank_state()
        with mock.patch.object(ao, "load_agencies", return_value=[self.FLAGGED]), \
             mock.patch.object(ao, "find_address",
                               return_value=("jobs@wearecammach.com", None)), \
             mock.patch.object(ao, "cv_file", return_value=None), \
             mock.patch.object(ao, "REGISTER_INTERVAL_SECONDS", 0), \
             mock.patch.object(jm, "save"), \
             mock.patch.object(jm, "send_email") as send:
            self.assertEqual(ao.run_tickets(state, send=True), 1)
        send.assert_called_once()
        self.assertIn("cammach", state[ao.TICKETS_ASKED])

    def test_the_shipped_flag_records_where_the_claim_came_from(self):
        with open(ao.AGENCIES_PATH) as f:
            data = json.load(f)
        flagged = [a for a in data["agencies"] if a.get("ask_tickets")]
        self.assertTrue(flagged)
        for agency in flagged:
            with self.subTest(agency=agency["name"]):
                if "I have been told" in ao.tickets_letter(agency)[1]:
                    self.assertTrue(agency.get("tickets_source"))
