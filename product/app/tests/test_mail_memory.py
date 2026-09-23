"""One employer, one letter - remembered by WHERE it went, not only by name.

WHY THIS FILE EXISTS.

The `contacted` table remembers company names, and on 22 and 23 September
names were not enough. TMM Recruitment had already placed Harry, and was
written to three more times; Protea Recruitment replied at 09:07 and got a
fresh application at 13:10. Part of that was two machines with two memories,
and part of it was that a name is a weak handle - one agency trades under two
names, and an advert and a mailbox spell the same firm differently. The
address does not change with the spelling.

So these tests guard:

  1. THE KEY. A business domain, with subdomains folded in (careers.acme.co.uk
     is Acme), and a whole address at a shared provider - because gmail.com is
     a million strangers, not one employer.
  2. SENDING. A draft to a domain already written to is never sent, whatever
     name is on it, and two drafts to one domain in the same sweep send once.
  3. DRAFTING. A listing whose address turns out to be one already written to
     is set aside before a model is paid to write the letter.
  4. BY HAND. Marking a draft sent records where it went too.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_sending import Base  # noqa: E402


class TestTheKey(Base):
    def key(self, text):
        return self.db.mail_key(text)

    def test_an_address_is_remembered_by_its_domain(self):
        self.assertEqual(self.key("abarry@tmmrecruitment.com"),
                         "tmmrecruitment.com")

    def test_a_subdomain_is_the_same_employer(self):
        self.assertEqual(self.key("jobs@careers.acme.co.uk"), "acme.co.uk")
        self.assertEqual(self.key("hr@acme.co.uk"), "acme.co.uk")
        self.assertEqual(self.key("x@uk.bigfirm.com"), "bigfirm.com")

    def test_two_firms_under_co_uk_are_not_one(self):
        self.assertNotEqual(self.key("a@acme.co.uk"), self.key("a@other.co.uk"))

    def test_a_council_is_itself_not_all_of_gov_uk(self):
        self.assertEqual(self.key("jobs@bexley.gov.uk"), "bexley.gov.uk")

    def test_a_suffix_sold_as_a_tld_is_not_one_employer(self):
        """candidatesource.uk.com must not block every other *.uk.com."""
        self.assertEqual(self.key("hello@candidatesource.uk.com"),
                         "candidatesource.uk.com")
        self.assertEqual(self.key("jobs@scotland.police.uk"),
                         "scotland.police.uk")

    def test_a_shared_provider_is_remembered_by_the_whole_address(self):
        """Blocking gmail.com after one sole trader would block them all."""
        self.assertEqual(self.key("Bob.Plumber@Gmail.com"),
                         "bob.plumber@gmail.com")
        self.assertNotEqual(self.key("a@gmail.com"), self.key("b@gmail.com"))

    def test_nothing_usable_is_nothing(self):
        for text in ("", "not an address", "@", "localhost"):
            with self.subTest(text=text):
                self.assertEqual(self.key(text), "")

    def test_a_bare_domain_works_too(self):
        """The import brings domains the personal machine recorded, which
        have no local part."""
        self.assertEqual(self.key("www.ex-mil.co.uk"), "ex-mil.co.uk")


class TestSending(Base):
    def setUp(self):
        super().setUp()
        self.connect_mail()
        self.db.mark_mail_verified(self.uid)
        self.db.save_send_settings(self.uid, auto_send=1)

    def send(self):
        return self.autosend.send_due_for_user(self.uid, sender=self.fake_send)

    def test_a_second_name_for_an_agency_already_written_to_is_not_sent(self):
        self.db.record_mail_contacted(self.uid, "abarry@tmmrecruitment.com")
        self.draft("Thorpe Molloy McCulloch", email="ckeith@tmmrecruitment.com")
        report = self.send()
        self.assertEqual(self.sent, [])
        self.assertEqual(report.skipped, 1)

    def test_two_drafts_to_one_domain_in_one_sweep_send_once(self):
        self.draft("Protea", email="info@protearecruitment.com")
        self.draft("Protea Recruitment Ltd", email="matt@protearecruitment.com")
        self.send()
        self.assertEqual(len(self.sent), 1)

    def test_a_delivered_letter_is_remembered_by_domain(self):
        self.draft("Acme Ltd", email="hr@acme.co.uk")
        self.send()
        self.assertTrue(self.db.mail_contacted(self.uid, "jobs@careers.acme.co.uk"))

    def test_a_block_by_domain_holds(self):
        """The import carries Harry's employer in by domain as well as name."""
        self.db.record_mail_contacted(self.uid, "hydrogroup.plc.uk",
                                      reason="his employer")
        self.draft("Some Advert Name", email="hr@hydrogroup.plc.uk")
        self.send()
        self.assertEqual(self.sent, [])

    def test_different_employers_on_one_shared_provider_both_go(self):
        self.draft("Bob's Electrical", email="bob@gmail.com")
        self.draft("Ann's Controls", email="ann@gmail.com")
        self.send()
        self.assertEqual(len(self.sent), 2)


class TestMarkingItSentByHand(Base):
    def test_where_it_went_is_remembered(self):
        """Sending it yourself is sending: the next letter to anybody at
        that domain is just as much a second letter."""
        from app.tests.test_app import AppTestCase
        case = AppTestCase()
        case.client, case.main = self.client, self.main
        case.sign_in("harry@example.com")
        did = self.draft("Acme Ltd", email="hr@acme.co.uk")
        self.client.post(f"/drafts/{did}/sent", follow_redirects=False)
        self.assertTrue(self.db.mail_contacted(self.uid, "cv@acme.co.uk"))


class TestDrafting(unittest.TestCase):
    def setUp(self):
        from test_runner import RunnerTestCase
        self.case = RunnerTestCase("run_once")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_an_address_already_written_to_is_set_aside_before_any_letter(self):
        """Pennine Foods has never been written to by name, but its domain
        has, under another name."""
        db, uid = self.case.db, self.case.user["id"]
        db.record_mail_contacted(uid, "jobs@pennine.co.uk")
        calls = []

        def ai(prompt):
            calls.append(prompt)
            from test_runner import scripted_ai
            return scripted_ai()(prompt)
        report = self.case.run_once(ai=ai)
        self.assertEqual(report.drafted, 0)
        self.assertEqual(report.already_contacted, 1)
        # Scoring is one call; no letter was composed.
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
