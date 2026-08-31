"""
Tests for reading the real advert behind a board's link.

Offline. Nothing is fetched.

THE BUG BEING FIXED, AND WHY IT WAS INVISIBLE
---------------------------------------------
269 listings scored 70 or better; 185 of them sat at no_email, judged worth
going for and never written to. The cause was one line in the harvester storing
Adzuna's tracking link as the advert's URL, so the address hunt, the ATS
detector and the portal agent were all reading Adzuna rather than the employer.
Every one of those stages ran, succeeded, and found nothing - which is exactly
what they would look like if the employer genuinely had no published address.

THE FAILURE MODE THIS CODE COULD INTRODUCE, WHICH IS WORSE
----------------------------------------------------------
A redirect can land on a cookie wall, a bot check, an expired-advert page or a
404, and all four return 200 with a page full of words. Overwriting a true
500-character blurb with a false 2,000-character one would degrade every score
downstream while looking, in the logs, like an improvement. Most of what is
tested here is the refusal to do that.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_machine as jm  # noqa: E402
import listings  # noqa: E402

ADVERT = ("We are seeking an Electrical Technician for offshore work on a "
          "two weeks on, two weeks off rotation from Aberdeen. " * 12)


def job(**over):
    base = {"external_id": "adzuna_1", "source": "adzuna", "company": "BES Group",
            "title": "Engineer Surveyor (Electrical)", "score": 75,
            "status": "no_email",
            "url": "https://www.adzuna.co.uk/jobs/details/5819685259",
            "description": "We are seeking an Engineer Surveyor to join our…"}
    base.update(over)
    return base


class TestWhatIsWorthFetching(unittest.TestCase):
    def test_a_board_link_is_always_worth_following(self):
        """Even when the blurb looks complete. The URL itself is the thing
        every downstream stage is misreading."""
        self.assertTrue(listings.needs_resolving(
            job(description="x" * 3000)))

    def test_a_truncated_description_is_worth_following(self):
        self.assertTrue(listings.needs_resolving(
            job(url="https://acme.com/careers/1",
                description="We are seeking a technician…")))

    def test_a_real_advert_on_a_real_site_is_left_alone(self):
        self.assertFalse(listings.needs_resolving(
            job(url="https://acme.com/careers/1", description="x" * 3000)))

    def test_nothing_already_written_to_is_touched(self):
        """The description on a sent job is the advert the letter was written
        about. Rewriting it would falsify the record of what was actually
        applied for."""
        for status in ("sent", "spec_sent", "replied"):
            with self.subTest(status=status):
                self.assertFalse(listings.needs_resolving(job(status=status)))

    def test_it_is_only_read_once(self):
        self.assertFalse(listings.needs_resolving(job(resolved_at=jm.now())))

    def test_a_dead_link_is_not_retried_every_run(self):
        self.assertFalse(listings.needs_resolving(
            job(resolve_failed_at=jm.now())))

    def test_but_a_bad_afternoon_is_forgiven_eventually(self):
        old = (datetime.now(timezone.utc)
               - timedelta(days=listings.RETRY_AFTER_DAYS + 1)).isoformat()
        self.assertTrue(listings.needs_resolving(job(resolve_failed_at=old)))

    def test_the_best_scoring_go_first(self):
        """A run that hits its cap should have spent it on the listings most
        worth having."""
        state = {"jobs": {"a": job(external_id="a", score=70),
                          "b": job(external_id="b", score=95)}}
        self.assertEqual([j["score"] for j in listings.candidates(state)],
                         [95, 70])

    def test_the_backlog_mode_takes_only_the_ones_that_died_of_no_address(self):
        state = {"jobs": {"a": job(external_id="a", status="no_email"),
                          "b": job(external_id="b", status="new")}}
        got = listings.candidates(state, backlog=True)
        self.assertEqual([j["external_id"] for j in got], ["a"])


class TestRefusingToMakeItWorse(unittest.TestCase):
    """A page full of words is not the same thing as an advert."""

    def test_a_bot_check_never_replaces_the_advert(self):
        for page in ("Please verify you are a human before continuing. " * 40,
                     "Checking your browser before accessing. " * 40,
                     "Access denied. You do not have permission. " * 40):
            with self.subTest(page=page[:30]):
                j = job()
                self.assertFalse(listings.keep_the_better_text(j, page))
                self.assertTrue(j["description"].endswith("…"))

    def test_an_expired_advert_page_never_replaces_it_either(self):
        page = "This job has been filled and is no longer available. " * 40
        j = job()
        self.assertFalse(listings.keep_the_better_text(j, page))

    def test_a_short_page_is_not_an_advert(self):
        j = job()
        self.assertFalse(listings.keep_the_better_text(j, "Apply here."))

    def test_a_page_no_better_than_the_stub_is_not_kept(self):
        """Swapping one truncated blurb for another gains nothing and costs a
        fetch."""
        j = job(description="x" * 2000)
        self.assertFalse(listings.keep_the_better_text(j, "y" * 2100))

    def test_the_real_advert_is_kept(self):
        j = job()
        self.assertTrue(listings.keep_the_better_text(j, ADVERT))
        self.assertIn("two weeks on, two weeks off", j["description"])

    def test_a_cookie_line_in_the_footer_does_not_condemn_a_real_advert(self):
        """Rejecting every page that mentions cookies somewhere would throw
        away most of the internet."""
        j = job()
        self.assertTrue(listings.keep_the_better_text(
            j, ADVERT + " We use cookies to improve your experience."))


class TestFollowingTheLink(unittest.TestCase):
    def resolve(self, j, final, text):
        with mock.patch.object(listings, "fetch", return_value=(final, text)):
            return listings.resolve_one(j)

    def test_the_real_destination_replaces_the_board_link(self):
        j = job()
        self.resolve(j, "https://www.besgroup.com/careers/1234", ADVERT)
        self.assertEqual(j["url"], "https://www.besgroup.com/careers/1234")

    def test_the_board_link_is_kept_rather_than_thrown_away(self):
        """It is how the listing was found, and the board's page is sometimes
        the only thing still standing once an employer takes theirs down."""
        j = job()
        self.resolve(j, "https://www.besgroup.com/careers/1234", ADVERT)
        self.assertIn("adzuna.co.uk", j[listings.BOARD_URL])

    def test_a_failure_is_recorded_and_nothing_is_changed(self):
        j = job()
        before = dict(j)
        self.assertFalse(self.resolve(j, None, ""))
        self.assertTrue(j[listings.FAILED])
        self.assertEqual(j["url"], before["url"])
        self.assertEqual(j["description"], before["description"])

    def test_a_success_clears_an_earlier_failure(self):
        j = job(resolve_failed_at=jm.now())
        self.resolve(j, "https://www.besgroup.com/careers/1", ADVERT)
        self.assertNotIn(listings.FAILED, j)
        self.assertTrue(j[listings.RESOLVED])

    def test_a_link_that_goes_nowhere_new_still_counts_as_read(self):
        """Otherwise it is fetched again on every single run forever."""
        j = job()
        self.resolve(j, j["url"], "")
        self.assertTrue(j[listings.RESOLVED])


class TestTheBacklog(unittest.TestCase):
    """The 185. Judged worth going for, never written to, because the address
    hunt was run against an Adzuna page."""

    def test_a_rescued_listing_goes_back_in_the_queue(self):
        state = {"jobs": {"a": job()}}
        with mock.patch.object(listings, "fetch",
                               return_value=("https://besgroup.com/j/1", ADVERT)):
            listings.run(state, backlog=True)
        self.assertEqual(state["jobs"]["a"]["status"], "scored")

    def test_one_that_learned_nothing_is_left_where_it_was(self):
        """Re-queueing it would send it round the discovery loop again against
        the same page that already found nothing."""
        state = {"jobs": {"a": job()}}
        with mock.patch.object(listings, "fetch", return_value=(None, "")):
            listings.run(state, backlog=True)
        self.assertEqual(state["jobs"]["a"]["status"], "no_email")

    def test_a_dry_run_fetches_nothing_and_changes_nothing(self):
        state = {"jobs": {"a": job()}}
        with mock.patch.object(listings, "fetch") as fetched:
            listings.run(state, dry_run=True)
        fetched.assert_not_called()
        self.assertNotIn(listings.RESOLVED, state["jobs"]["a"])


class TestItRunsBeforeTheScorer(unittest.TestCase):
    def test_the_pipeline_reads_the_advert_before_judging_it(self):
        """The whole point of the second half of this fix. Scoring the board's
        first paragraph is how a 3/3 offshore electrical job gets binned for
        looking like a workshop role."""
        import inspect
        source = inspect.getsource(jm.main)
        self.assertLess(source.index('stage("adverts"'),
                        source.index('stage("score"'))

    def test_it_can_never_take_the_pipeline_down(self):
        """It is a network stage in the middle of a run that sends letters."""
        import inspect
        self.assertIn("stage(", inspect.getsource(jm.main))
        self.assertIn("try", inspect.getsource(jm.stage))


if __name__ == "__main__":
    unittest.main(verbosity=2)
