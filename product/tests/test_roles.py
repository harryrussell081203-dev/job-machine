"""Working out what to search for from the person, not just their words.

The first sign-up from ChatGPT (22 October) typed "shop keeper restraunt
waiter" and had been a landscaper. The model's answer is checked against the
rules whatever it says, and the rules stand in when it cannot be reached.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker import roles  # noqa: E402
from jobseeker.profile import Profile, Role  # noqa: E402


def person(**over):
    base = dict(name="Sam", location="Leicester", phone="07700 900123",
                min_rate_hourly=7, target_roles=["shop keeper restraunt waiter"],
                locations=["Leicester"],
                history=[Role(title="Landscaper", org="Lawn Care Ltd")])
    base.update(over)
    return Profile(**base)


def answer(asked, from_history=()):
    return lambda prompt: json.dumps({"asked": list(asked),
                                      "from_history": list(from_history)})


class TheRules(unittest.TestCase):
    def test_two_jobs_in_one_box_are_two_jobs(self):
        self.assertEqual(roles.split_roles(["shop keeper restraunt waiter"]),
                         ["shop keeper", "waiter"])

    def test_separators_split_too(self):
        self.assertEqual(roles.split_roles(["Retail Assistant, Barista"]),
                         ["retail assistant", "barista"])

    def test_a_title_nobody_listed_is_kept_as_typed(self):
        self.assertEqual(roles.split_roles(["data analyst"]), ["data analyst"])
        self.assertEqual(roles.split_roles(["maintenance technician"]),
                         ["maintenance technician"])


class TheModelsAnswer(unittest.TestCase):
    def test_what_the_person_asked_for_and_their_history_both_go_in(self):
        prompt = roles.prompt_for(person(), "Mowed lawns for two years.")
        self.assertIn("shop keeper restraunt waiter", prompt)
        self.assertIn("Landscaper at Lawn Care Ltd", prompt)
        self.assertIn("Mowed lawns", prompt)

    def test_a_good_answer_is_used(self):
        got = roles.plan(person(), answer(
            ["Retail Assistant", "Sales Assistant", "Waiter", "Waiting Staff"],
            ["Landscape Gardener"]))
        self.assertEqual(got["by"], "model")
        self.assertEqual(got["asked"], ["retail assistant", "sales assistant",
                                        "waiter", "waiting staff"])
        self.assertEqual(got["from_history"], ["landscape gardener"])

    def test_the_kind_of_work_is_kept_for_the_scorer(self):
        ai = lambda prompt: json.dumps({
            "asked": ["retail assistant"], "from_history": [],
            "work": "Customer-facing shop and restaurant work at entry level."})
        p = person()
        got = roles.plan(p, ai)
        self.assertEqual(got["work"], "Customer-facing shop and restaurant "
                                      "work at entry level.")
        p.search_titles, p.wanted_work = got["asked"], got["work"]
        text = roles.describe(p)
        self.assertIn("The work they want: Customer-facing shop and "
                      "restaurant work at entry level. Judge an advert by "
                      "the work it describes, not by its exact title", text)

    def test_a_work_line_that_is_not_a_sentence_is_dropped(self):
        ai = lambda prompt: json.dumps({"asked": ["waiter"],
                                        "work": "<script>x</script>"})
        self.assertEqual(roles.plan(person(), ai)["work"], "")

    def test_no_titles_above_the_level_their_history_shows(self):
        got = roles.plan(person(), answer(
            ["Retail Assistant", "Store Manager", "Senior Waiter"],
            ["Head Gardener", "Gardener"]))
        self.assertEqual(got["asked"], ["retail assistant"])
        self.assertEqual(got["from_history"], ["gardener"])

    def test_junk_is_dropped_and_the_list_is_capped(self):
        got = roles.plan(person(), answer(
            ["retail assistant", "waiter in Leicester £11.44/hr",
             "x" * 60, "a b c d e f", *[f"title {c}" for c in "abcdefgh"]]))
        self.assertEqual(got["asked"][0], "retail assistant")
        self.assertLessEqual(len(got["asked"]), roles.MAX_TITLES)
        self.assertTrue(all(len(t.split()) <= 4 and "£" not in t
                            for t in got["asked"]))

    def test_no_history_means_no_second_choice(self):
        nobody = person()
        nobody.history = []      # a profile always has one; the rule holds anyway
        got = roles.plan(nobody, answer(["waiter"], ["gardener"]))
        self.assertEqual(got["from_history"], [])

    def test_the_rules_stand_in_when_the_model_fails(self):
        def busy(prompt):
            raise RuntimeError("HTTP 503")
        for ai in (busy, lambda p: "I think retail.", answer([])):
            got = roles.plan(person(), ai)
            self.assertEqual(got["by"], "rules")
            self.assertIn("retail assistant", got["asked"])
            self.assertIn("waiter", got["asked"])

    def test_the_fingerprint_changes_only_with_the_person(self):
        a = roles.fingerprint(person())
        self.assertEqual(a, roles.fingerprint(person()))
        self.assertNotEqual(a, roles.fingerprint(person(target_roles=["chef"])))
        self.assertNotEqual(a, roles.fingerprint(person(), "a new CV"))


if __name__ == "__main__":
    unittest.main()
