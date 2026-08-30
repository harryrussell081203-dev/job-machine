"""Scoring: is this better than what they already have?

No network — the AI is a callable, so these assert on judgement rather than
on whether Google is up.

The pay-unit tests are the ones that earn their keep. Reading an hourly rate
as an annual salary turns the best-paid job in the queue into the worst.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.pipeline import scoring as s  # noqa: E402
from jobseeker.pipeline.harvest import Listing  # noqa: E402
from jobseeker.profile import Profile, Role  # noqa: E402


def profile(**over):
    base = dict(
        name="Sam Doherty", location="Sheffield", phone="07700 900123",
        situation="employed", current_salary=32000,
        min_salary_annual=38000, min_rate_hourly=20,
        priorities=["money"], target_roles=["maintenance technician"],
        locations=["Sheffield", "Rotherham"],
        history=[Role(title="Tech", org="Acme", detail="fixed things")],
    )
    base.update(over)
    return Profile(**base)


def listing(**over):
    base = dict(external_id="a1", source="adzuna", title="Maintenance Technician",
                company="Pennine Foods", location="Rotherham", url="",
                description="Maintain packaging lines.")
    base.update(over)
    return Listing(**base)


def canned(payload):
    """An AI that always answers with this."""
    return lambda prompt: json.dumps(payload)


class TestPayUnits(unittest.TestCase):
    def test_small_numbers_read_as_hourly(self):
        self.assertEqual(s.stated_pay(listing(salary_max=30.81)), (30.81, "hour"))

    def test_middling_numbers_read_as_a_day_rate(self):
        self.assertEqual(s.stated_pay(listing(salary_max=350)), (350.0, "day"))

    def test_large_numbers_read_as_a_salary(self):
        self.assertEqual(s.stated_pay(listing(salary_max=41000)), (41000.0, "year"))

    def test_the_top_of_a_range_is_used(self):
        # Reject only when even the best case is too little.
        self.assertEqual(s.stated_pay(listing(salary_min=30000, salary_max=45000)),
                         (45000.0, "year"))

    def test_no_salary_at_all(self):
        self.assertEqual(s.stated_pay(listing()), (None, None))

    def test_an_hourly_rate_is_not_read_as_an_annual_salary(self):
        # The bug this guards: £30.81/hr read as £30.81/yr would reject the
        # best-paid listing in the queue as the worst.
        self.assertTrue(s.pays_enough(listing(salary_max=30.81), profile()))


class TestPayFloor(unittest.TestCase):
    def test_a_salary_below_the_floor_is_rejected(self):
        self.assertFalse(s.pays_enough(listing(salary_max=31000), profile()))

    def test_a_salary_above_the_floor_passes(self):
        self.assertTrue(s.pays_enough(listing(salary_max=41000), profile()))

    def test_silence_always_passes(self):
        # Most adverts print no figure and the contract market quotes on
        # application. Treating unstated as too little deletes the best-paid
        # half of the market.
        self.assertTrue(s.pays_enough(listing(), profile()))

    def test_a_day_rate_is_measured_against_eight_hours(self):
        p = profile(min_rate_hourly=20)          # so £160/day is the floor
        self.assertFalse(s.pays_enough(listing(salary_max=120), p))
        self.assertTrue(s.pays_enough(listing(salary_max=350), p))

    def test_an_hourly_rate_is_measured_against_the_hourly_floor(self):
        p = profile(min_rate_hourly=20)
        self.assertFalse(s.pays_enough(listing(salary_max=15.50), p))
        self.assertTrue(s.pays_enough(listing(salary_max=24.00), p))


class TestScoreGuide(unittest.TestCase):
    def test_an_employed_person_is_told_the_bar_is_their_current_salary(self):
        guide = s.score_guide(profile())
        self.assertIn("IN WORK on GBP 32,000", guide)
        self.assertIn("better than what they have", guide)

    def test_an_unemployed_person_gets_a_different_bar(self):
        guide = s.score_guide(profile(situation="unemployed", current_salary=0))
        self.assertNotIn("better than what they have", guide)
        self.assertIn("looking for work", guide)

    def test_their_own_towns_are_neutral_not_a_bonus(self):
        # A local job is not better for being local. Rewarding it buries the
        # better-paid one an hour away.
        guide = s.score_guide(profile())
        self.assertIn("NEUTRAL", guide)
        self.assertIn("Sheffield, Rotherham", guide)

    def test_travel_and_contract_only_appear_when_wanted(self):
        plain = s.score_guide(profile())
        self.assertNotIn("rotational", plain)
        wanted = s.score_guide(profile(priorities=["money", "travel", "contract"],
                                       wants_travel=True, wants_contract=True))
        self.assertIn("rotational", wanted)
        self.assertIn("day rate", wanted)

    def test_the_floor_is_stated_in_the_rubric(self):
        self.assertIn("38,000", s.score_guide(profile()))

    def test_seniority_is_never_penalised(self):
        self.assertIn("Do NOT penalise senior", s.score_guide(profile()))


class TestParsing(unittest.TestCase):
    def test_a_plain_array(self):
        got = s.parse_scores('[{"listing":0,"score":88,"reason":"good"}]', 1)
        self.assertEqual(got, {0: (88, "good")})

    def test_an_object_wrapping_an_array(self):
        got = s.parse_scores({"results": [{"listing": 0, "score": 70}]}, 1)
        self.assertEqual(got[0][0], 70)

    def test_a_lone_object(self):
        self.assertEqual(s.parse_scores({"listing": 0, "score": 55}, 1)[0][0], 55)

    def test_scores_are_clamped_to_the_scale(self):
        got = s.parse_scores([{"listing": 0, "score": 250},
                              {"listing": 1, "score": -8}], 2)
        self.assertEqual(got[0][0], 100)
        self.assertEqual(got[1][0], 0)

    def test_rubbish_yields_nothing_rather_than_a_guess(self):
        # An unscored listing gets another go; a made-up score is acted on.
        for junk in ("not json", "", [1, 2, 3], None, 42):
            self.assertEqual(s.parse_scores(junk, 2), {})

    def test_an_index_outside_the_batch_is_ignored(self):
        self.assertEqual(s.parse_scores([{"listing": 99, "score": 90}], 2), {})


class TestScoring(unittest.TestCase):
    def test_a_high_score_passes_and_a_low_one_does_not(self):
        out = s.score([listing(external_id="a"), listing(external_id="b")],
                      profile(),
                      canned([{"listing": 0, "score": 88, "reason": "strong"},
                              {"listing": 1, "score": 30, "reason": "weak"}]))
        self.assertEqual([l.external_id for l in out["passed"]], ["a"])
        self.assertEqual([l.external_id for l in out["rejected"]], ["b"])
        self.assertEqual(out["passed"][0].score, 88)
        self.assertEqual(out["passed"][0].score_reason, "strong")

    def test_the_underpaid_never_reach_the_model(self):
        seen = []

        def spy(prompt):
            seen.append(prompt)
            return "[]"

        out = s.score([listing(salary_max=25000)], profile(), spy)
        self.assertEqual(seen, [], "an AI call was spent on an underpaid listing")
        self.assertEqual(len(out["rejected"]), 1)
        self.assertIn("below your floor", out["rejected"][0].skipped)

    def test_a_rejection_always_carries_a_reason(self):
        out = s.score([listing()], profile(),
                      canned([{"listing": 0, "score": 20, "reason": "wrong trade"}]))
        self.assertIn("wrong trade", out["rejected"][0].skipped)
        self.assertEqual(out["rejected"][0].score, 20)

    def test_a_failed_batch_leaves_listings_unscored_rather_than_rejected(self):
        # A network blip must not quietly bin good jobs.
        def broken(_prompt):
            raise RuntimeError("gemini exploded")

        out = s.score([listing()], profile(), broken)
        self.assertEqual(out["passed"], [])
        self.assertEqual(out["rejected"], [])

    def test_best_first(self):
        out = s.score([listing(external_id="a"), listing(external_id="b")],
                      profile(),
                      canned([{"listing": 0, "score": 75},
                              {"listing": 1, "score": 95}]))
        self.assertEqual([l.external_id for l in out["passed"]], ["b", "a"])

    def test_batching_keeps_the_call_count_down(self):
        calls = []
        many = [listing(external_id=str(i)) for i in range(25)]
        out = s.score(many, profile(),
                      lambda p: (calls.append(p), "[]")[1], batch_size=12)
        self.assertEqual(len(calls), 3, "expected 25 listings in 3 batches")
        self.assertEqual(out["passed"], [])

    def test_the_threshold_is_configurable(self):
        scored = canned([{"listing": 0, "score": 60}])
        self.assertEqual(len(s.score([listing()], profile(), scored)["passed"]), 0)
        self.assertEqual(
            len(s.score([listing()], profile(), scored, threshold=50)["passed"]), 1)

    def test_the_prompt_carries_the_candidate_and_the_rubric(self):
        seen = []
        s.score([listing()], profile(), lambda p: (seen.append(p), "[]")[1])
        self.assertIn("Sam Doherty", seen[0])
        self.assertIn("SCORE GUIDE", seen[0])
        self.assertIn("Maintenance Technician", seen[0])


if __name__ == "__main__":
    unittest.main()
