"""Tests for Job Machine sending addresses.

The user picks: send from their own mailbox, or from an address we issue so
their own is never used and cannot be damaged.

Both halves need protecting, and they fail in opposite directions:

  - a managed account must never quietly become the default, because that
    would send existing customers' letters from a domain they never agreed to
  - two people with the same name must never share an address, because the
    second one would receive the first one's replies
  - the shared provider allowance is across everybody, so counting it
    per-user would let ten people each stay under their own limit and blow
    the shared one between them
"""

import importlib
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from cryptography.fernet import Fernet  # noqa: E402

from test_sending import Base, build  # noqa: E402

MANAGED = {
    "MANAGED_MAIL_DOMAIN": "mail.jobmachine.co.uk",
    "MANAGED_MAIL_KEY": "re_test_key",
}


class TestTheAddressWeIssue(unittest.TestCase):
    def setUp(self):
        self.main, self.db_path = build(**MANAGED)
        self.addCleanup(lambda: os.path.exists(self.db_path)
                        and os.unlink(self.db_path))
        self.db = sys.modules["app.db"]
        self.delivery = sys.modules["app.delivery"]
        # build() reloads the modules but the schema is created by the app's
        # lifespan, and this class never starts a client because it is testing
        # the address logic rather than the screens.
        self.db.init()

    def uid(self, email):
        return self.db.get_or_create_user(email)["id"]

    def test_it_reads_as_a_person(self):
        """user4821@ announces a tool before the subject line is read, and
        the whole reason these letters get answered is that they do not."""
        uid = self.uid("harry@example.com")
        self.assertEqual(
            self.db.issue_managed_address(uid, "Harry Russell"),
            "harry.russell@mail.jobmachine.co.uk")

    def test_two_people_with_one_name_do_not_share_an_address(self):
        """The second person would receive the first person's replies."""
        a = self.db.issue_managed_address(self.uid("a@example.com"),
                                          "Harry Russell")
        self.db.save_mail_account(self.uid("a@example.com"), address=a,
                                  host="smtp.resend.com", port=465,
                                  password="k", kind="managed",
                                  reply_to="a@example.com")
        b = self.db.issue_managed_address(self.uid("b@example.com"),
                                          "Harry Russell")
        self.assertNotEqual(a, b)
        self.assertEqual(b, "harry.russell2@mail.jobmachine.co.uk")

    def test_asking_twice_returns_the_same_address(self):
        """Reissuing a different one would orphan every reply in flight."""
        uid = self.uid("harry@example.com")
        first = self.db.issue_managed_address(uid, "Harry Russell")
        self.db.save_mail_account(uid, address=first, host="smtp.resend.com",
                                  port=465, password="k", kind="managed",
                                  reply_to="harry@example.com")
        self.assertEqual(self.db.issue_managed_address(uid, "Harry Russell"),
                         first)

    def test_a_name_it_cannot_use_still_gets_an_address(self):
        uid = self.uid("bjorn@example.com")
        self.assertEqual(self.delivery.local_part_for("Björn Ståhl"),
                         "bjorn.stahl")
        self.assertEqual(self.delivery.local_part_for("Siobhán O'Brien"),
                         "siobhan.obrien")
        self.assertTrue(
            self.db.issue_managed_address(uid, "", "bjorn@example.com")
            .startswith("bjorn@"))
        self.assertTrue(self.delivery.local_part_for("").endswith("applicant"))


class TestItIsNeverTheDefault(Base):
    """A managed account has to be chosen. Every row that existed before the
    `kind` column did is somebody's own mailbox, and a default of 'managed'
    would have rerouted their letters through a domain they never agreed to
    send from."""

    def test_an_ordinary_connection_is_still_their_own(self):
        self.connect_mail()
        self.assertEqual(self.db.get_mail_account(self.uid)["kind"], "own")

    def test_letters_still_come_from_their_own_address(self):
        self.connect_mail()
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        sent = self.sent[0]
        self.assertEqual(sent["username"], "harry@gmail.com")
        self.assertEqual(sent.get("from_address", ""), "")
        self.assertEqual(sent.get("reply_to", ""), "")


class TestSendingFromAnIssuedAddress(Base):
    env = dict(MANAGED)

    def connect_managed(self, reply_to="harry@gmail.com"):
        address = self.db.issue_managed_address(self.uid, "Harry Russell")
        self.db.save_mail_account(
            self.uid, address=address, host="smtp.resend.com", port=465,
            password="re_test_key", kind="managed", reply_to=reply_to)
        return address

    def test_the_from_line_is_the_issued_address_not_the_smtp_username(self):
        """Authenticating to Resend uses the literal username "resend".
        Putting that on the From line would be both wrong and faintly comic."""
        address = self.connect_managed()
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        sent = self.sent[0]
        self.assertEqual(sent["username"], "resend")
        self.assertEqual(sent["from_address"], address)

    def test_the_employer_replies_to_the_user_not_to_us(self):
        """The point of the whole feature: their address is never used to
        send, but an answer still reaches them directly and we never see it."""
        self.connect_managed(reply_to="harry@gmail.com")
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(self.sent[0]["reply_to"], "harry@gmail.com")

    def test_the_shared_allowance_is_counted_across_everybody(self):
        """Counting per-user would let ten people each stay under their own
        limit and blow the provider's between them."""
        self.connect_managed()
        other = self.db.get_or_create_user("someone@example.com")["id"]
        for _ in range(3):
            self.db.record_managed_send(other)
        self.assertEqual(self.db.managed_sent_today(), 3)

    def test_it_stops_at_the_shared_cap_rather_than_being_rejected(self):
        """Going over does not degrade politely - the provider rejects, and a
        rejection would be recorded as a failed send and shown to the user as
        though their letter had bounced."""
        self.connect_managed()
        self.db.save_send_settings(self.uid, auto_send=1)
        other = self.db.get_or_create_user("someone@example.com")["id"]
        for _ in range(sys.modules["app.config"].MANAGED_MAIL_DAILY_CAP):
            self.db.record_managed_send(other)
        self.draft("Acme Ltd")
        report = self.autosend.send_due_for_user(self.uid,
                                                 sender=self.fake_send)
        self.assertEqual(report.sent, 0)
        self.assertIn("tomorrow", report.reason)
        self.assertEqual(self.sent, [])

    def test_a_send_that_failed_does_not_spend_the_allowance(self):
        self.connect_managed()
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme Ltd")

        def boom(**kw):
            raise self.delivery_error("nope")
        self.autosend.send_due_for_user(self.uid, sender=boom)
        self.assertEqual(self.db.managed_sent_today(), 0)

    def test_a_successful_send_does(self):
        self.connect_managed()
        self.db.save_send_settings(self.uid, auto_send=1)
        self.draft("Acme Ltd")
        self.autosend.send_due_for_user(self.uid, sender=self.fake_send)
        self.assertEqual(self.db.managed_sent_today(), 1)

    @property
    def delivery_error(self):
        return sys.modules["app.delivery"].DeliveryError


class SignedIn(Base):
    """Base plus a signed-in session. Base itself has no sign_in - that helper
    lives further down test_sending.py on a class we are not inheriting."""

    def sign_in(self, email="harry@example.com"):
        link = self.main.auth.make_login_link(email)
        token = link.split("token=", 1)[1]
        self.client.get(f"/auth/verify?token={token}", follow_redirects=False)


class TestTheOptionIsHiddenUnlessItWorks(SignedIn):
    """Unset, the choice is simply not offered - the same way automatic
    sending hides itself when there is no CREDENTIAL_KEY, rather than
    presenting a choice that fails later."""

    def test_without_a_domain_or_key_it_is_not_offered(self):
        self.assertFalse(sys.modules["app.config"].managed_mail_available())
        self.sign_in("harry@example.com")
        body = self.client.get("/setup/mail").text
        self.assertNotIn("Give me", body)

    def test_the_route_refuses_rather_than_issuing_a_broken_address(self):
        self.sign_in("harry@example.com")
        r = self.client.post("/setup/mail/managed", follow_redirects=False)
        self.assertNotEqual(r.status_code, 303)


class TestTheOptionAppearsWhenItDoes(SignedIn):
    env = dict(MANAGED)

    def test_it_is_offered_with_the_real_address_shown(self):
        self.sign_in("harry@example.com")
        uid = self.db.get_or_create_user("harry@example.com")["id"]
        self.db.save_profile(uid, {"name": "Harry Russell"})
        body = self.client.get("/setup/mail").text
        self.assertIn("harry.russell@mail.jobmachine.co.uk", body)

    def test_taking_it_connects_without_a_password(self):
        self.sign_in("harry@example.com")
        uid = self.db.get_or_create_user("harry@example.com")["id"]
        self.db.save_profile(uid, {"name": "Harry Russell"})
        self.client.post("/setup/mail/managed", follow_redirects=False)
        row = self.db.get_mail_account(uid)
        self.assertEqual(row["kind"], "managed")
        self.assertEqual(row["address"], "harry.russell@mail.jobmachine.co.uk")
        self.assertEqual(row["reply_to"], "harry@example.com")

    def test_it_survives_the_password_rejected_screen(self):
        """Somebody just told their app password was refused is exactly the
        person who wants the route that needs no password."""
        self.sign_in("harry@example.com")
        body = self.client.post("/setup/mail",
                                data={"address": "not-an-email",
                                      "password": "x"}).text
        self.assertIn("mail.jobmachine.co.uk", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
