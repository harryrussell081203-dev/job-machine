"""What a job pays, as the advert states it, for the card.

  1. A FIGURE IN THE TEXT is read: ranges, hourly and day rates, "up to £40k".
  2. A BONUS OR AN ALLOWANCE IS NOT PAY, and a bare £ figure with no salary
     word next to it is not read at all.
  3. AN ADZUNA ESTIMATE IS NOT "LISTED": a predicted salary is dropped at
     harvest, so it neither shows on the card nor trips the pay floor.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.pipeline import harvest, pay  # noqa: E402


def read(text):
    found = pay.from_text(text)
    return pay.describe(*found) if found else None


class ReadsTheText(unittest.TestCase):
    def test_a_range_per_annum(self):
        self.assertEqual(read("Salary: £32,000 - £36,000 per annum"),
                         "£32,000 to £36,000 a year")

    def test_an_hourly_rate(self):
        self.assertEqual(read("Paying £18.50 per hour, days"), "£18.50 an hour")

    def test_k_shorthand_on_both_ends(self):
        self.assertEqual(read("£30 - 35k DOE"), "£30,000 to £35,000 a year")

    def test_up_to(self):
        self.assertEqual(read("up to £40k"), "£40,000 a year")

    def test_a_day_rate(self):
        self.assertEqual(read("£250 a day inside IR35"), "£250 a day")


class LeavesAlone(unittest.TestCase):
    def test_bonuses_and_allowances(self):
        for text in ("£1,000 welcome bonus", "£500 relocation allowance",
                     "£2,000 sign-on bonus paid after probation"):
            self.assertIsNone(read(text), text)

    def test_a_bare_figure_with_no_salary_word(self):
        self.assertIsNone(read("We won a £45,000 contract last year"))

    def test_nonsense_sizes(self):
        self.assertIsNone(read("Company turnover £30,000,000"))

    def test_switched_off(self):
        with patch.dict(os.environ, {"SALARY_FROM_TEXT": "0"}):
            self.assertIsNone(read("Salary £32,000 per annum"))


class AdzunaEstimates(unittest.TestCase):
    def test_a_predicted_salary_is_not_stated(self):
        job = {"salary_min": 21000, "salary_max": 21000,
               "salary_is_predicted": "1"}
        self.assertIsNone(harvest._stated(job, "salary_min"))

    def test_a_real_one_is(self):
        job = {"salary_min": 40000, "salary_is_predicted": "0"}
        self.assertEqual(harvest._stated(job, "salary_min"), 40000)


if __name__ == "__main__":
    unittest.main()
