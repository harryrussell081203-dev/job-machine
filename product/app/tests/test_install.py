"""Getting the app onto a phone.

THE BUG THIS FILE EXISTS FOR.

The install banner was revealed from inside the `beforeinstallprompt`
handler. Chrome and Edge fire that event; Safari never has and never will.
So every iPhone user was shown nothing at all - no banner, no button, no hint
that an app existed - on a product whose audience checks for replies on a
phone. The comment in the template claimed the banner covered that case.

Nothing caught it because nothing tested it: the markup was present, the
route worked, and the one thing that was wrong lived in a branch of
JavaScript that only runs on hardware CI does not have.

So these tests assert the things that CAN be asserted from here - that the
instructions exist in the page, that they are reachable without a prompt,
and that the route is public and in the sitemap - and the ones that cannot
are named in a docstring rather than left implied.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_app import AppTestCase  # noqa: E402


class TestTheInstallPageIsReachable(AppTestCase):
    def test_it_is_public(self):
        """Somebody should be able to read what installing involves before
        they have an account. Behind the login, the first time anybody sees
        it is a moment they are already busy."""
        r = self.client.get("/app")
        self.assertEqual(r.status_code, 200)

    def test_it_is_in_the_sitemap(self):
        r = self.client.get("/sitemap.xml")
        self.assertIn("/app", r.text)

    def test_robots_does_not_block_it(self):
        r = self.client.get("/robots.txt")
        for line in r.text.splitlines():
            if line.startswith("Disallow:"):
                self.assertNotEqual(line.split(":", 1)[1].strip(), "/app")


class TestIPhoneUsersAreToldWhatToDo(AppTestCase):
    """The case that was silently broken for every iPhone user."""

    def page(self):
        return self.client.get("/app").text

    def test_the_ios_steps_are_in_the_page(self):
        """Written into the HTML rather than fetched, so they are there
        whether or not any script runs."""
        text = self.page()
        self.assertIn("Add to Home Screen", text)
        self.assertIn("Share", text)

    def test_it_says_safari_specifically(self):
        """Chrome on iOS cannot add anything to the home screen, and somebody
        following these steps in Chrome would find no such menu item and
        conclude the app does not exist."""
        self.assertIn("Safari", self.page())

    def test_the_android_steps_are_there_too(self):
        """The prompt is the fast path, but it does not always fire - a
        browser that has decided the site is not eligible, or a user who
        dismissed it once - and the menu route always works."""
        text = self.page()
        self.assertIn("Install app", text)

    def test_the_install_button_starts_hidden(self):
        """It does nothing without a prompt the browser has offered. A button
        that does nothing is worse than no button."""
        text = self.page()
        self.assertRegex(text, r'id="installcard"[^>]*hidden')


class TestTheOptionIsFindableAfterSigningUp(AppTestCase):
    def test_signed_in_pages_carry_a_link_to_it(self):
        """The banner can be dismissed once and never returns - the flag is
        written and never cleared - so the nav is what makes this findable
        the week after."""
        self.sign_in("ada@example.com")
        r = self.client.get("/dashboard")
        self.assertIn('href="/app"', r.text)

    def test_the_setup_screen_offers_it(self):
        """The screen where nine of the first ten accounts stopped. Most of
        them arrived on a phone from a link, and a tab is something you close
        and never find again."""
        self.sign_in("bea@example.com")
        r = self.client.get("/setup")
        self.assertIn("/app", r.text)
        self.assertIn("home screen", r.text)

    def test_the_banner_offers_a_route_that_needs_no_prompt(self):
        """Both controls are in the markup, hidden, and the script reveals
        exactly one. Before this there was only the button, which on iOS
        could never be revealed at all."""
        r = self.client.get("/")
        self.assertIn('id="installbtn"', r.text)
        self.assertIn('id="installhow"', r.text)
        self.assertIn('href="/app"', r.text)


class TestRecordingAnInstall(AppTestCase):
    def test_a_signed_in_install_is_recorded(self):
        self.sign_in("cy@example.com")
        user = self.main.db.get_user_by_email("cy@example.com")
        r = self.client.post("/app/installed")
        self.assertEqual(r.status_code, 204)
        self.assertIsNotNone(
            self.main.db.first_event_at(user["id"], "installed"))

    def test_installing_twice_records_once(self):
        """The browser can fire appinstalled more than once across devices,
        and a second row would make the count disagree with the number of
        people."""
        self.sign_in("dev@example.com")
        user = self.main.db.get_user_by_email("dev@example.com")
        self.client.post("/app/installed")
        self.client.post("/app/installed")
        self.assertEqual(self.main.db.event_counts().get("installed"), 1)

    def test_an_anonymous_install_is_accepted_and_counted_as_nothing(self):
        """The page is public, so this can be reached without a session.
        Refusing it would be an error in somebody's console over a
        statistic."""
        r = self.client.post("/app/installed")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.main.db.event_counts().get("installed", 0), 0)

    def test_installing_is_not_a_funnel_stage(self):
        """Somebody who never installs is not stuck. Putting it in FUNNEL
        would make it look like a step people fail to reach."""
        self.assertNotIn("installed", self.main.db.FUNNEL)
        self.assertIn("installed", self.main.db.EVENT_KINDS)


class TestWhatCannotBeTestedHere(unittest.TestCase):
    """Named rather than left implied, because these are exactly the branches
    the original bug lived in.

    A browser is what decides whether `beforeinstallprompt` fires, whether
    `display-mode: standalone` matches, and whether `navigator.standalone` is
    true. None of that exists in a test client, so the following are checked
    by hand on a real phone and are written down so the gap is visible:

      - the banner appears on an iPhone in Safari, with "How" and not
        "Install"
      - nothing about installing is shown inside the installed app
      - Chrome's own prompt appears on the button, once
      - /app hides the Android menu steps when the prompt is available, so
        two routes are not offered at once

    The iPadOS detection is the part most likely to rot: iPadOS has reported
    itself as a Mac since iPadOS 13, so the user-agent alone cannot tell them
    apart and the code falls back to maxTouchPoints. If Apple changes that,
    iPads go quiet in exactly the way iPhones did.
    """

    def test_this_file_documents_the_manual_checks(self):
        self.assertIn("by hand on a real phone", self.__doc__)


if __name__ == "__main__":
    unittest.main()
