"""What was borrowed from AI Apply, and the lines it had to stay inside.

Its users praise two things: seeing the whole pipeline at a glance, and help
getting ready for the interview a letter earned. They complain about paying
per application in credits and about applications going to the wrong places.
So these tests hold:

  1. THE TRACKER'S TABS count every letter into exactly the tab it belongs
     in, and an interview counts as hearing back.
  2. INTERVIEW PREP never hands somebody an invented past. An outline that
     claims a job they did not do, or anything on their never-claim list, is
     thrown away rather than shown.
  3. "NEVER WRITE TO" blocks by name and by domain, and unblocking a domain
     that was really written to does not forget the letter.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_sending import Base  # noqa: E402

PROFILE = {
    "name": "Sam Doherty", "phone": "07700 900123",
    "history": [{"title": "Maintenance Technician", "org": "Brightwater",
                 "detail": "reactive maintenance on packaging lines"}],
    "qualifications": ["Level 3 NVQ"],
    "never_claim": ["security clearance of any kind. None held now."],
}


class Signed(Base):
    def setUp(self):
        super().setUp()
        from app.tests.test_app import AppTestCase
        case = AppTestCase()
        case.client, case.main = self.client, self.main
        case.sign_in("harry@example.com")
        self.db.save_profile(self.uid, PROFILE)

    def letter(self, company, outcome="", seen=False):
        did = self.draft(company)
        self.db.record_delivered(self.uid, draft_id=did, to_email="x@y.example",
                                 company=company)
        if outcome:
            self.db.set_outcome(self.uid, did, outcome)
        if seen:
            self.db.mark_reply_seen(self.uid, did)
        return did


class TestTheTabs(Signed):
    def test_each_letter_lands_in_its_tab(self):
        self.letter("Quiet Ltd")
        self.letter("Seen Ltd", seen=True)
        self.letter("Talking Ltd", outcome="interview")
        self.letter("No Ltd", outcome="rejected")
        _, counts = self.db.filter_applications(
            self.db.applications(self.uid), "all")
        self.assertEqual(counts, {"all": 4, "waiting": 1, "heard": 2,
                                  "interviews": 1, "closed": 1})

    def test_a_tab_shows_only_its_letters(self):
        self.letter("Quiet Ltd")
        self.letter("Talking Ltd", outcome="interview")
        page = self.client.get("/applications?show=interviews").text
        self.assertIn("Talking Ltd", page)
        self.assertNotIn("Quiet Ltd", page)

    def test_nonsense_in_the_address_is_all(self):
        self.letter("Quiet Ltd")
        page = self.client.get("/applications?show=<script>").text
        self.assertIn("Quiet Ltd", page)

    def test_the_dashboard_shows_what_came_of_it(self):
        self.letter("Talking Ltd", outcome="interview")
        page = self.client.get("/dashboard").text
        self.assertIn("heard back", page)
        self.assertIn("/applications?show=interviews", page)


class TestInterviewPrep(Signed):
    def prep(self):
        import importlib
        return importlib.import_module("app.prep")

    def answer(self, *items, ask=("What does a first month look like?",)):
        return json.dumps({"questions": list(items), "ask_them": list(ask)})

    def test_an_outline_from_their_own_job_is_kept(self):
        raw = self.answer({"q": "Tell me about a breakdown you fixed.",
                           "why_they_ask": "fault finding",
                           "draws_on": "Maintenance Technician at Brightwater",
                           "outline": "S: line down. T: restart. A: traced a "
                                      "sensor. R: back in an hour."})
        made = self.prep().parse(raw, PROFILE)
        self.assertEqual(len(made["questions"]), 1)

    def test_an_invented_employer_is_thrown_away(self):
        raw = self.answer({"q": "Leading a team?", "draws_on":
                           "Shift Lead at BP", "outline": "S: ..."})
        self.assertIsNone(self.prep().parse(raw, PROFILE))

    def test_their_never_claim_list_is_obeyed(self):
        raw = self.answer({"q": "Do you have clearance?", "draws_on": "",
                           "outline": "Say you hold security clearance of "
                                      "any kind from the Navy."})
        self.assertIsNone(self.prep().parse(raw, PROFILE))

    def test_an_honest_gap_is_allowed(self):
        raw = self.answer({"q": "Used SAP?", "draws_on": "",
                           "outline": "Say you have not, and how you learn "
                                      "systems quickly."})
        self.assertEqual(len(self.prep().parse(raw, PROFILE)["questions"]), 1)

    def test_the_prompt_carries_the_never_claim_list(self):
        did = self.letter("Talking Ltd", outcome="interview")
        prompt = self.prep().build_prompt(self.db.get_draft(self.uid, did),
                                          PROFILE)
        self.assertIn("security clearance", prompt)
        self.assertIn("Brightwater", prompt)

    def test_built_once_and_kept(self):
        did = self.letter("Talking Ltd", outcome="interview")
        self.db.save_interview_prep(self.uid, did, {
            "questions": [{"q": "Why us?", "why": "", "draws_on": "",
                           "outline": "..."}], "ask_them": []})
        page = self.client.get(f"/applications/{did}/prep").text
        self.assertIn("Why us?", page)
        listing = self.client.get("/applications").text
        self.assertIn("Your interview prep", listing)

    def test_a_letter_still_waiting_does_not_offer_it(self):
        self.letter("Quiet Ltd")
        self.assertNotIn("Prepare for the interview",
                         self.client.get("/applications").text)


class TestNeverWriteTo(Signed):
    def test_a_name_and_a_domain(self):
        self.client.post("/setup/never",
                         data={"names": "Hydro Group\nhttps://hydrogroup-uk.com/about"})
        self.assertTrue(self.db.is_blocked(self.uid, "Hydro Group"))
        self.assertTrue(self.db.mail_blocked(self.uid, "hr@hydrogroup-uk.com"))
        self.assertIn("hydrogroup-uk.com", self.client.get("/setup").text)

    def test_unblocking_a_domain_never_written_to_forgets_it(self):
        self.db.add_user_block(self.uid, "acme.co.uk")
        self.db.remove_user_block(self.uid, "mail", "acme.co.uk")
        self.assertFalse(self.db.mail_contacted(self.uid, "hr@acme.co.uk"))

    def test_unblocking_a_domain_that_was_written_to_keeps_the_memory(self):
        self.db.record_mail_contacted(self.uid, "hr@acme.co.uk")
        self.db.add_user_block(self.uid, "acme.co.uk")
        self.db.remove_user_block(self.uid, "mail", "acme.co.uk")
        self.assertTrue(self.db.mail_contacted(self.uid, "cv@acme.co.uk"))
        self.assertFalse(self.db.mail_blocked(self.uid, "cv@acme.co.uk"))


class TestThePrice(Signed):
    def test_no_credits(self):
        """AI Apply's most common complaint is a subscription that turns out
        not to include applying. This one is the whole thing."""
        self.client.cookies.clear()
        self.assertIn("No credits", self.client.get("/").text)


if __name__ == "__main__":
    unittest.main()
