"""A notification when an employer writes back, and nothing else.

  1. DORMANT WITHOUT KEYS: no panel, no route, no send.
  2. A BROWSER SUBSCRIBES only while signed in, and only with a real push
     service's address - the server POSTs to it.
  3. A DETECTED REPLY PUSHES, naming only the employer.
  4. A GONE BROWSER is forgotten when the push service says so.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from test_sending import Base  # noqa: E402

KEYS = {"VAPID_PUBLIC_KEY": "BPublicKeyForTests", "VAPID_PRIVATE_KEY": "priv",
        "PUSH_ENABLED": "1"}
FCM = "https://fcm.googleapis.com/fcm/send/abc123"


class Signed(Base):
    def setUp(self):
        super().setUp()
        from app.tests.test_app import AppTestCase
        case = AppTestCase()
        case.client, case.main = self.client, self.main
        case.sign_in("harry@example.com")
        self.db.save_profile(self.uid, {"name": "Sam", "location": "Aberdeen"})
        from app import push
        self.push = push

    def subscribe(self, endpoint=FCM):
        return self.client.post("/push/subscribe", json={
            "endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}})


class Dormant(Signed):
    def test_no_keys_no_panel_and_no_subscribing(self):
        with patch.dict(os.environ, {"VAPID_PUBLIC_KEY": "",
                                     "VAPID_PRIVATE_KEY": ""}):
            self.assertNotIn("pushpanel", self.client.get("/dashboard").text)
            self.assertEqual(self.subscribe().status_code, 403)
            self.assertEqual(self.push.send(self.uid, {"title": "x"}), 0)

    def test_with_keys_the_panel_is_offered(self):
        with patch.dict(os.environ, KEYS):
            page = self.client.get("/dashboard").text
        self.assertIn("pushpanel", page)
        self.assertIn("BPublicKeyForTests", page)


class Subscribing(Signed):
    def test_a_real_push_service_is_saved(self):
        with patch.dict(os.environ, KEYS):
            self.assertEqual(self.subscribe().status_code, 200)
        self.assertEqual(len(self.db.push_subscriptions(self.uid)), 1)

    def test_anywhere_else_is_refused(self):
        with patch.dict(os.environ, KEYS):
            for bad in ("http://fcm.googleapis.com/x",
                        "https://evil.example/fcm.googleapis.com",
                        "https://fcm.googleapis.com.evil.example/x",
                        "https://169.254.169.254/latest"):
                self.assertEqual(self.subscribe(bad).status_code, 400, bad)
        self.assertEqual(self.db.push_subscriptions(self.uid), [])

    def test_signed_out_is_refused(self):
        self.client.cookies.clear()
        with patch.dict(os.environ, KEYS):
            self.assertEqual(self.subscribe().status_code, 403)

    def test_unsubscribe_forgets_it(self):
        with patch.dict(os.environ, KEYS):
            self.subscribe()
            self.client.post("/push/unsubscribe", json={"endpoint": FCM})
        self.assertEqual(self.db.push_subscriptions(self.uid), [])


class Pushing(Signed):
    def setUp(self):
        super().setUp()
        self.pushed = []
        with patch.dict(os.environ, KEYS):
            self.subscribe()

    def webpush(self, **kw):
        self.pushed.append(kw)

    def test_a_reply_names_the_employer(self):
        with patch.dict(os.environ, KEYS):
            n = self.push.tell_about_replies(self.uid, ["Acme Ltd"],
                                             webpush=self.webpush)
        self.assertEqual(n, 1)
        message = json.loads(self.pushed[0]["data"])
        self.assertEqual(message["title"], "Acme Ltd wrote back")
        self.assertTrue(message["url"].startswith("/"))
        self.assertEqual(self.pushed[0]["subscription_info"]["endpoint"], FCM)

    def test_a_gone_browser_is_forgotten(self):
        class Gone(Exception):
            response = type("R", (), {"status_code": 410})()

        def webpush(**kw):
            raise Gone()
        with patch.dict(os.environ, KEYS):
            self.assertEqual(self.push.send(self.uid, {"title": "x"},
                                            webpush=webpush), 0)
        self.assertEqual(self.db.push_subscriptions(self.uid), [])

    def test_the_reply_check_pushes(self):
        from app import replies
        pushed = []
        did = self.draft("Acme Ltd", email="hr@acme.example")
        self.db.record_delivered(self.uid, draft_id=did,
                                 to_email="hr@acme.example", company="Acme Ltd")
        self.connect_mail()
        self.db.mark_mail_verified(self.uid)
        replies.check_for_user(
            self.uid, finder=lambda **k: ["hr@acme.example"],
            notify=lambda **k: None,
            pusher=lambda uid, companies: pushed.append(companies))
        self.assertEqual(pushed, [["Acme Ltd"]])

    def test_the_service_worker_shows_it(self):
        sw = self.client.get("/sw.js").text
        self.assertIn('addEventListener("push"', sw)
        self.assertIn('addEventListener("notificationclick"', sw)


if __name__ == "__main__":
    unittest.main()
