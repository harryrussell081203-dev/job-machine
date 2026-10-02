"""The Telegram bot: answers whoever writes to it, and nobody else.

  1. DORMANT without a token: no route, no registration.
  2. ONLY TELEGRAM GETS IN: the path and a header, both derived from the
     token, must match, or it is a 404.
  3. A COMPANY NAME or AN ADVERT gets the real address back; nothing found
     says so and guesses nothing.
  4. NEVER NOISE: in a group it answers only /find, and it is rate-limited
     per chat.
  5. REGISTERED ONCE per token and address.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("BILLING_ENABLED", "0")

from app.tests.test_app import AppTestCase  # noqa: E402

TOKEN = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_BOT_USERNAME": "RecruitedBot",
         "TELEGRAM_ENABLED": "1"}


def message(text, chat_type="private", chat_id=42):
    return {"message": {"chat": {"id": chat_id, "type": chat_type},
                        "text": text}}


class Replies(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)
        from app import telegram
        self.tg = telegram

    def test_start_explains_itself(self):
        out = self.tg.reply_to(message("/start"))
        self.assertIn("never guess", out["text"])
        self.assertEqual(out["method"], "sendMessage")
        self.assertEqual(out["chat_id"], 42)

    def test_an_advert_gets_its_address(self):
        out = self.tg.reply_to(message(
            "Fitter wanted. Send your CV to fiona.menzies@kestrelfoods.co.uk"))
        self.assertIn("fiona.menzies@kestrelfoods.co.uk", out["text"])

    def test_a_company_name_is_looked_up(self):
        look = lambda name, **kw: {"domain": "kestrelfoods.co.uk",
                                   "emails": ["careers@kestrelfoods.co.uk"]}
        out = self.tg.reply_to(message("Kestrel Foods"), lookup=look)
        self.assertIn("careers@kestrelfoods.co.uk", out["text"])
        self.assertIn("on kestrelfoods.co.uk", out["text"])

    def test_no_confident_site_says_so(self):
        out = self.tg.reply_to(message("Smith Ltd"),
                               lookup=lambda n, **k: {"domain": "", "emails": []})
        self.assertIn("couldn't be sure", out["text"])

    def test_each_message_reports_how_it_ended(self):
        seen = []
        self.tg.reply_to(message("/start"), tally=seen.append)
        self.tg.reply_to(message("CV to fiona.menzies@kestrelfoods.co.uk"),
                         tally=seen.append)
        self.tg.reply_to(message("Kestrel Foods"), tally=seen.append,
                         lookup=lambda n, **k: {"domain": "kestrelfoods.co.uk",
                                                "emails": []})
        self.tg.reply_to(message("Smith Ltd"), tally=seen.append,
                         lookup=lambda n, **k: {"domain": "", "emails": []})
        self.assertEqual(seen, ["start", "advert:found", "company:none",
                                "company:no-site"])

    def test_a_broken_tally_never_stops_the_reply(self):
        def broken(name):
            raise RuntimeError("database gone")
        out = self.tg.reply_to(message("/start"), tally=broken)
        self.assertIn("never guess", out["text"])

    def test_a_group_hears_only_its_command(self):
        self.assertIsNone(self.tg.reply_to(message("hello all", "group")))
        out = self.tg.reply_to(message(
            "/find send CV to hr@acme.co.uk", "group"))
        self.assertIn("hr@acme.co.uk", out["text"])

    def test_rate_limited_per_chat(self):
        out = self.tg.reply_to(message("Kestrel Foods"),
                               allow=lambda chat: False)
        self.assertIn("a lot of lookups", out["text"])

    def test_share_button_points_at_the_bot(self):
        out = self.tg.reply_to(message("/start"))
        url = out["reply_markup"]["inline_keyboard"][0][0]["url"]
        self.assertIn("t.me%2FRecruitedBot", url)

    def test_registered_once(self):
        sent, kept = [], {}

        class R:
            status_code = 200

            def json(self):
                return {"ok": True}

        def post(url, json, timeout):
            sent.append((url, json))
            return R()
        self.assertEqual(self.tg.register(post=post, get_meta=kept.get,
                                          set_meta=kept.__setitem__),
                         "registered")
        self.assertEqual(self.tg.register(post=post, get_meta=kept.get,
                                          set_meta=kept.__setitem__),
                         "already registered")
        methods = [url.rsplit("/", 1)[1] for url, _ in sent]
        self.assertEqual(methods, ["setWebhook", "setMyDescription",
                                   "setMyShortDescription", "setMyCommands"])
        self.assertLessEqual(len(self.tg.DESCRIPTION), 512)
        self.assertLessEqual(len(self.tg.SHORT_DESCRIPTION), 120)
        self.assertEqual(sent[0][1]["secret_token"], self.tg.header_secret())
        self.assertEqual(sent[0][1]["allowed_updates"], ["message"])


class TheWebhook(AppTestCase):
    def call(self, path=None, header=None, body=None):
        from app import telegram
        return self.client.post(
            f"/telegram/{path or telegram.path_secret()}",
            json=body or message("/start"),
            headers={"X-Telegram-Bot-Api-Secret-Token":
                     header if header is not None else telegram.header_secret()})

    def test_dormant_without_a_token(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}):
            self.assertEqual(self.call(path="x", header="y").status_code, 404)

    def test_only_telegram_gets_in(self):
        with patch.dict(os.environ, TOKEN):
            self.assertEqual(self.call(header="wrong").status_code, 404)
            self.assertEqual(self.call(path="wrong").status_code, 404)
            r = self.call()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["method"], "sendMessage")

    def test_find_mentions_the_bot_when_it_exists(self):
        with patch.dict(os.environ, TOKEN):
            self.assertIn("t.me/RecruitedBot", self.client.get("/find").text)
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}):
            self.assertNotIn("t.me/", self.client.get("/find").text)


if __name__ == "__main__":
    unittest.main()
