"""Composition: the rules that turn 1-5% into 26%, enforced rather than asked.

The never_claim tests matter most. Every other failure here costs one
application; a false credential claim is found at vetting and follows the
person afterwards.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.pipeline import compose as c  # noqa: E402
from jobseeker.pipeline.harvest import Listing  # noqa: E402
from jobseeker.profile import Profile, Role  # noqa: E402


def profile(**over):
    base = dict(
        name="Sam Doherty", location="Sheffield", phone="07700 900123",
        situation="employed", current_salary=32000, min_salary_annual=38000,
        priorities=["money"], target_roles=["maintenance technician"],
        locations=["Sheffield"],
        qualifications=["Level 3 NVQ in Engineering Maintenance"],
        never_claim=["that they hold a current 17th Edition certificate",
                     "that they have completed their HNC"],
        history=[Role(title="Maintenance Technician", org="Brightwater",
                      detail="planned and reactive maintenance on packaging lines"),
                 Role(title="Mechanical Fitter", org="Kestrel",
                      detail="stripped and rebuilt pumps and gearboxes")],
    )
    base.update(over)
    return Profile(**base)


def listing(**over):
    base = dict(external_id="a1", source="adzuna",
                title="Multi-Skilled Maintenance Engineer",
                company="Pennine Foods", location="Rotherham", url="",
                description="Packaging lines, four-on-four-off, Rotherham site.")
    base.update(over)
    return Listing(**base)


CONTACT = {"email": "claire@pennine.example", "name": "Claire", "tier": 3}

GOOD_BODY = (
    "Saw your multi-skilled maintenance engineer role covering the packaging "
    "lines at Rotherham. That is the work I do now.\n\n"
    "1. Four years of planned and reactive maintenance on high-speed packaging "
    "lines, fault finding on PLC-controlled conveyors and fillers there.\n"
    "2. Cut unplanned downtime on our primary line by about a third across two "
    "years of shift work on that same site.\n\n"
    "Would it help if I sent over my availability for a call this week?")


def ai_returning(subject, body):
    return lambda prompt: json.dumps({"subject": subject, "body": body})


class TestNeverClaim(unittest.TestCase):
    def test_terms_are_pulled_out_of_a_free_text_claim(self):
        self.assertIn("17th Edition",
                      c.credential_terms("that they hold a current 17th Edition certificate"))
        self.assertIn("HNC", c.credential_terms("that they have completed their HNC"))
        self.assertIn("SC", c.credential_terms("that they are SC cleared"))

    def test_a_draft_mentioning_a_forbidden_credential_is_blocked(self):
        hits = c.never_claim_violations(
            "I hold the 17th Edition and can start Monday.", profile())
        self.assertTrue(hits)
        self.assertIn("17th Edition", hits[0])

    def test_an_acronym_is_caught(self):
        self.assertTrue(c.never_claim_violations("I finished my HNC.", profile()))

    def test_a_clean_draft_passes(self):
        self.assertEqual(c.never_claim_violations(GOOD_BODY, profile()), [])

    def test_the_check_is_blunt_in_the_safe_direction(self):
        # It also blocks an honest mention. That is the intended trade: a
        # blocked draft costs one application, a false claim costs the person.
        hits = c.never_claim_violations(
            "The advert asks for the 17th Edition, which I would need to sit.",
            profile())
        self.assertTrue(hits, "honest mentions are blocked too, on purpose")

    def test_nothing_is_blocked_when_the_list_is_empty(self):
        p = profile(never_claim=[])
        self.assertEqual(
            c.never_claim_violations("I hold the 17th Edition and an HNC.", p), [])

    def test_a_forbidden_claim_fails_the_whole_letter(self):
        out = c.problems("Maintenance engineer role",
                         "I hold the 17th Edition.\n\n1. a\n2. b\n\nOk?",
                         listing(), profile())
        self.assertTrue(any("never to claim" in p for p in out))


class TestRules(unittest.TestCase):
    def test_a_good_letter_has_no_problems(self):
        self.assertEqual(
            c.problems("Maintenance engineer, packaging lines", GOOD_BODY,
                       listing(), profile()), [])

    def test_body_length_is_enforced_both_ways(self):
        short = "Maintenance engineer here.\n\n1. a\n2. b\n\nShall we talk?"
        out = c.problems("Maintenance engineer role", short, listing(), profile())
        self.assertTrue(any("must be 60-90" in p for p in out))

    def test_a_long_subject_is_rejected(self):
        out = c.problems("this subject line is far too long for any reasonable use",
                         GOOD_BODY, listing(), profile())
        self.assertTrue(any("max is 8" in p for p in out))

    def test_application_for_is_banned_in_the_subject(self):
        out = c.problems("Application for maintenance engineer", GOOD_BODY,
                         listing(), profile())
        self.assertTrue(any("Application for" in p for p in out))

    def test_the_subject_must_name_the_role(self):
        out = c.problems("Hello there", GOOD_BODY, listing(), profile())
        self.assertTrue(any("must name the role" in p for p in out))

    def test_shouting_is_rejected(self):
        out = c.problems("MAINTENANCE ENGINEER ROLE", GOOD_BODY, listing(), profile())
        self.assertTrue(any("ALL CAPS" in p for p in out))

    def test_banned_phrases_are_caught(self):
        body = GOOD_BODY.replace("Saw your", "I hope this email finds you well. Saw your")
        out = c.problems("Maintenance engineer role", body, listing(), profile())
        self.assertTrue(any("banned phrases" in p for p in out))

    def test_exactly_one_question_is_required(self):
        two = GOOD_BODY + " And when do you interview?"
        out = c.problems("Maintenance engineer role", two, listing(), profile())
        self.assertTrue(any("exactly one question" in p for p in out))

    def test_proof_points_must_be_numbered_and_between_two_and_three(self):
        one = GOOD_BODY.replace("2. Cut unplanned", "Cut unplanned")
        out = c.problems("Maintenance engineer role", one, listing(), profile())
        self.assertTrue(any("2 or 3 numbered" in p for p in out))

    def test_the_first_line_must_name_the_role(self):
        body = "Hello, I would like to apply.\n\n" + GOOD_BODY.split("\n\n", 1)[1]
        out = c.problems("Maintenance engineer role", body, listing(), profile())
        self.assertTrue(any("first line must name" in p for p in out))


class TestHygiene(unittest.TestCase):
    def test_markdown_and_shouting_are_stripped(self):
        self.assertEqual(c.normalise("**bold** and _italic_!!"), "bold and italic.")

    def test_em_dashes_become_hyphens(self):
        self.assertIn(" - ", c.normalise("one—two"))

    def test_smart_quotes_are_flattened(self):
        self.assertEqual(c.normalise("it’s"), "it's")

    def test_the_models_signoff_is_removed(self):
        body = "Real content here.\n\nKind regards,\nSam\n07700 900123"
        self.assertEqual(c.strip_signoff(body, profile()), "Real content here.")

    def test_a_bare_first_name_signoff_is_removed(self):
        self.assertEqual(
            c.strip_signoff("Real content.\n\nSam", profile()), "Real content.")

    def test_assemble_forces_the_real_greeting_and_signoff(self):
        full, core = c.assemble("Dear Sir,\n\nBody text.\n\nRegards,\nSam",
                                "Hi Claire,", profile())
        self.assertTrue(full.startswith("Hi Claire,"))
        self.assertTrue(full.endswith("Sam Doherty / 07700 900123 / CV attached"))
        self.assertEqual(core, "Body text.")
        self.assertNotIn("Dear Sir", full)


class TestCompose(unittest.TestCase):
    def test_a_compliant_letter_is_returned(self):
        out = c.compose(listing(), CONTACT, profile(),
                        ai_returning("Maintenance engineer, packaging lines", GOOD_BODY))
        self.assertIsNotNone(out)
        self.assertTrue(out["body"].startswith("Hi Claire,"))
        self.assertEqual(out["to_email"], "claire@pennine.example")
        self.assertEqual(out["contact_tier"], 3)

    def test_a_letter_that_never_complies_is_refused(self):
        # A letter breaking the rules is worse than no letter.
        out = c.compose(listing(), CONTACT, profile(),
                        ai_returning("Application for the role", "Too short."))
        self.assertIsNone(out)

    def test_rejections_are_fed_back_into_the_retry(self):
        prompts = []

        def flaky(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                return json.dumps({"subject": "Application for it",
                                   "body": "Too short."})
            return json.dumps({"subject": "Maintenance engineer, packaging lines",
                               "body": GOOD_BODY})

        out = c.compose(listing(), CONTACT, profile(), flaky)
        self.assertIsNotNone(out)
        self.assertIn("rejected for these reasons", prompts[1])
        self.assertIn("Application for", prompts[1])

    def test_no_greeting_when_there_is_no_name(self):
        out = c.compose(listing(), {"email": "careers@x.example", "tier": 2},
                        profile(),
                        ai_returning("Maintenance engineer, packaging lines", GOOD_BODY))
        self.assertTrue(out["body"].startswith("Hi,\n"))

    def test_invalid_json_is_survived_and_retried(self):
        calls = []

        def bad_then_good(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return "not json at all"
            return json.dumps({"subject": "Maintenance engineer, packaging lines",
                               "body": GOOD_BODY})

        self.assertIsNotNone(c.compose(listing(), CONTACT, profile(), bad_then_good))

    def test_an_ai_failure_returns_nothing_rather_than_raising(self):
        def boom(_p):
            raise RuntimeError("quota gone")
        self.assertIsNone(c.compose(listing(), CONTACT, profile(), boom))

    def test_the_prompt_carries_the_never_claim_list(self):
        seen = []
        c.compose(listing(), CONTACT, profile(),
                  lambda p: (seen.append(p), json.dumps(
                      {"subject": "Maintenance engineer, packaging lines",
                       "body": GOOD_BODY}))[1])
        self.assertIn("NEVER claim", seen[0])
        self.assertIn("17th Edition", seen[0])


class TestPlainFallback(unittest.TestCase):
    """An AI quota is a daily ceiling. Hitting it must not stop a real
    application to a real verified address."""

    def test_it_produces_a_sendable_letter_with_no_model(self):
        out = c.plain_letter(listing(), CONTACT, profile())
        self.assertTrue(out["body"].startswith("Hi Claire,"))
        self.assertIn("Multi-Skilled Maintenance Engineer", out["body"])
        self.assertTrue(out["body"].endswith(
            "Sam Doherty / 07700 900123 / CV attached"))
        self.assertTrue(out["fallback"])

    def test_it_uses_the_persons_own_history(self):
        body = c.plain_letter(listing(), CONTACT, profile())["body"]
        self.assertIn("Brightwater", body)
        self.assertIn("packaging lines", body)

    def test_it_never_breaches_never_claim(self):
        out = c.plain_letter(listing(), CONTACT, profile())
        self.assertEqual(
            c.never_claim_violations(out["subject"] + " " + out["body"], profile()),
            [])

    def test_it_asks_exactly_one_question(self):
        self.assertEqual(c.plain_letter(listing(), CONTACT, profile())["body"].count("?"), 1)

    def test_it_works_without_a_contact_name(self):
        out = c.plain_letter(listing(), {"email": "info@x.example", "tier": 1},
                             profile())
        self.assertTrue(out["body"].startswith("Hi,\n"))

    def test_the_subject_respects_the_same_eight_word_cap(self):
        # Without this the fallback quietly breaks the one rule the composed
        # path would have caught.
        long_title = Listing(
            external_id="x", source="adzuna",
            title="Senior Instrumentation and Control Systems Technician",
            company="Northern Industrial Services Group", location="Leeds",
            url="", description="d")
        out = c.plain_letter(long_title, CONTACT, profile())
        self.assertLessEqual(len(out["subject"].split()), c.MAX_SUBJECT_WORDS)
        self.assertIn("Instrumentation", out["subject"])

    def test_the_company_is_dropped_before_the_role_is(self):
        # A reader scanning an inbox needs the role far more than the name of
        # their own employer.
        subj = c.fallback_subject(
            "Senior Instrumentation and Control Systems Technician",
            "Northern Industrial Services Group")
        self.assertNotIn("Northern", subj)
        self.assertIn("Senior Instrumentation", subj)

    def test_it_satisfies_every_rule_except_the_word_floor(self):
        """The exemption is deliberate: padding to reach 60 words would add
        the exact filler the floor exists to prevent."""
        l = listing()
        out = c.plain_letter(l, CONTACT, profile())
        _, core = c.assemble(out["body"], "Hi Claire,", profile())
        found = c.problems(out["subject"], core, l, profile())
        # the only complaint allowed is the length floor
        self.assertTrue(all("words, must be" in p for p in found),
                        f"fallback broke a rule other than length: {found}")


if __name__ == "__main__":
    unittest.main()
