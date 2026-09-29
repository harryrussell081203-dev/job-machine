"""British spelling on every letter, and nothing more than that.

  1. A MISSPELLING WITH ONE SUGGESTION is fixed; with several it is left.
  2. NAMES ARE NEVER TOUCHED - anything with a capital or a digit.
  3. GRAMMAR AND STYLE are reported, never applied.
  4. NO SERVER, OR A BROKEN ONE, leaves the letter exactly as written.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker.pipeline import proofread  # noqa: E402


class Resp:
    def __init__(self, matches, status=200):
        self.status_code, self._m = status, matches

    def json(self):
        return {"matches": self._m}


def spelling(text, word, *fixes):
    return {"offset": text.index(word), "length": len(word),
            "message": "Possible spelling mistake",
            "replacements": [{"value": f} for f in fixes],
            "rule": {"id": "MORFOLOGIK_RULE_EN_GB", "issueType": "misspelling"}}


def grammar(text, phrase):
    return {"offset": text.index(phrase), "length": len(phrase),
            "message": "Consider a shorter phrase",
            "replacements": [{"value": "x"}],
            "rule": {"id": "WORDINESS", "issueType": "style"}}


class Base(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {"LANGUAGETOOL_URL": "http://lt:8010"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sent = []

    def server(self, matches, status=200):
        def post(url, data, timeout):
            self.sent.append((url, data))
            return Resp(matches, status)
        return post


class WhatItFixes(Base):
    def test_one_suggestion_is_applied(self):
        text = "I would recieve the color charts."
        out, notes = proofread.proofread(text, post=self.server([
            spelling(text, "recieve", "receive"),
            spelling(text, "color", "colour")]))
        self.assertEqual(out, "I would receive the colour charts.")
        self.assertEqual(len([n for n in notes if n.startswith("fixed")]), 2)

    def test_it_asks_for_british_english(self):
        proofread.proofread("text", post=self.server([]))
        self.assertEqual(self.sent[0][1]["language"], "en-GB")
        self.assertTrue(self.sent[0][0].endswith("/v2/check"))

    def test_several_suggestions_is_a_judgement_call_and_left(self):
        text = "I fixd the line."
        out, _ = proofread.proofread(text, post=self.server([
            spelling(text, "fixd", "fixed", "fix")]))
        self.assertEqual(out, text)

    def test_a_name_is_never_changed(self):
        text = "Hi Siobhan, the Kincorth site and the 17th Edition kit."
        out, _ = proofread.proofread(text, post=self.server([
            spelling(text, "Siobhan", "Sobbing"),
            spelling(text, "Kincorth", "Kincaid")]))
        self.assertEqual(out, text)

    def test_grammar_is_reported_not_applied(self):
        text = "In order to help, I can start."
        out, notes = proofread.proofread(text, post=self.server([
            grammar(text, "In order to")]))
        self.assertEqual(out, text)
        self.assertTrue(any("WORDINESS" in n for n in notes))


class WhenItCannot(Base):
    def test_no_server_configured_means_no_call(self):
        with patch.dict(os.environ, {"LANGUAGETOOL_URL": ""}):
            letter = {"subject": "s", "body": "recieve"}
            self.assertEqual(proofread.letter(letter, post=self.server([])),
                             letter)
        self.assertEqual(self.sent, [])

    def test_a_server_error_leaves_the_letter(self):
        text = "recieve"
        out, _ = proofread.proofread(text, post=self.server(
            [spelling(text, "recieve", "receive")], status=500))
        self.assertEqual(out, text)

    def test_an_unreachable_server_leaves_the_letter(self):
        def down(*a, **k):
            raise OSError("connection refused")
        self.assertEqual(proofread.proofread("recieve", post=down)[0],
                         "recieve")

    def test_subject_and_body_are_both_checked(self):
        def post(url, data, timeout):
            text = data["text"]
            return Resp([spelling(text, "recieve", "receive")]
                        if "recieve" in text else [])
        out = proofread.letter({"subject": "recieve it", "body": "recieve",
                                "to_email": "a@b.example"}, post=post)
        self.assertEqual((out["subject"], out["body"]),
                         ("receive it", "receive"))
        self.assertEqual(out["to_email"], "a@b.example")


if __name__ == "__main__":
    unittest.main()
