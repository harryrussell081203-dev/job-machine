"""One sentence in, the setup boxes filled in for the person to check."""

import json
import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")

from test_app import AppTestCase  # noqa: E402

from app import describe  # noqa: E402


def model(answer):
    return lambda prompt: json.dumps(answer)


class Reading(unittest.TestCase):
    def test_fills_what_was_said(self):
        out = describe.read("forklift at Tesco in Leeds, want warehouse work, "
                            "12.50 an hour", model({
                                "target_roles": "warehouse operative, forklift driver",
                                "location": "Leeds", "last_title": "Forklift driver",
                                "last_org": "Tesco", "min_pay": "£12.50"}))
        self.assertEqual(out, {"target_roles": "warehouse operative, forklift driver",
                               "location": "Leeds",
                               "last_title": "Forklift driver",
                               "last_org": "Tesco", "min_pay": "12.50"})

    def test_empty_stays_empty_and_vague_is_dropped(self):
        out = describe.read("anything really", model({
            "target_roles": "anything", "location": "", "last_title": "",
            "last_org": "", "min_pay": "decent"}))
        self.assertEqual(out, {})

    def test_k_means_thousands(self):
        out = describe.read("x", model({"min_pay": "24k", "target_roles": "chef"}))
        self.assertEqual(out["min_pay"], "24000")

    def test_never_takes_a_name_or_phone(self):
        out = describe.read("x", model({"target_roles": "chef",
                                        "name": "Sam", "phone": "0770"}))
        self.assertEqual(set(out), {"target_roles"})

    def test_the_prompt_says_not_to_guess(self):
        self.assertIn("Never guess", describe.prompt("hello"))


class ThePage(AppTestCase):
    env = {"BILLING_ENABLED": "0"}

    def setUp(self):
        super().setUp()
        self.sign_in()

    def test_the_box_is_on_the_setup_screen(self):
        self.assertIn('action="/setup/describe', self.client.get("/setup").text)

    def test_it_fills_the_boxes_and_saves_nothing(self):
        with patch("app.ai.gemini_now", model({
                "target_roles": "chef", "location": "York",
                "last_title": "Line cook", "last_org": "Bettys",
                "min_pay": "13"})):
            r = self.client.post("/setup/describe",
                                 data={"about": "cook in York at Bettys"})
        self.assertIn("Filled in from what you wrote", r.text)
        for value in ('value="chef"', 'value="York"', 'value="Bettys"',
                      'value="13"'):
            self.assertIn(value, r.text)
        me = self.main.db.get_user_by_email("sam@example.com")
        self.assertIsNone(self.main.db.load_profile(me["id"]))

    def test_a_model_failure_says_to_use_the_boxes(self):
        def broken(prompt):
            raise RuntimeError("quota")
        with patch("app.ai.gemini_now", broken):
            r = self.client.post("/setup/describe", data={"about": "hello"})
        self.assertIn("Fill in the boxes below instead", r.text)


if __name__ == "__main__":
    unittest.main()
