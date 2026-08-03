"""
Tests for harvesting jobs out of Harry's own inbox.

All offline. No inbox is opened and no advert is fetched.

The point of this route: Adzuna and Reed have APIs and yield a median of about
four in-trade Aberdeen listings a day. CV-Library, Totaljobs, s1jobs and Oil
and Gas Job Search carry most of the rest of this market and have no free API
at all - but every one of them will email a daily alert, and reading your own
email is not automating anybody's website.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alert_harvest as ah  # noqa: E402
import job_machine as jm  # noqa: E402


class TestSpottingAnAlert(unittest.TestCase):
    def test_the_boards_that_matter_are_recognised(self):
        for sender in ("Indeed <noreply@indeed.com>",
                       "CV-Library <jobs@cv-library.co.uk>",
                       "alerts@totaljobs.com", "no-reply@s1jobs.com",
                       "Oil and Gas Job Search <alerts@oilandgasjobsearch.com>",
                       "LinkedIn Job Alerts <jobs-noreply@linkedin.com>"):
            with self.subTest(sender=sender):
                self.assertTrue(ah.is_alert(sender))

    def test_ordinary_mail_is_left_alone(self):
        for sender in ("jane.smith@hydrasun.com", "mum@hotmail.com",
                       "billing@octopus.energy", ""):
            with self.subTest(sender=sender):
                self.assertFalse(ah.is_alert(sender))


class TestPullingAdvertsOutOfAnAlert(unittest.TestCase):
    def test_advert_links_are_found(self):
        body = """
        <a href="https://www.cv-library.co.uk/job/222333/instrument-tech">View</a>
        <a href="https://uk.indeed.com/viewjob?jk=abc123&from=alert">Apply</a>
        """
        links = ah.job_links(body)
        self.assertEqual(len(links), 2)
        self.assertTrue(any("cv-library" in u for u in links))

    def test_unsubscribe_and_account_links_are_not_jobs(self):
        body = """
        <a href="https://www.totaljobs.com/unsubscribe?jobs=1">Unsubscribe</a>
        <a href="https://www.totaljobs.com/account/settings/jobs">Settings</a>
        <a href="https://www.totaljobs.com/job/12345/technician">The job</a>
        """
        self.assertEqual(ah.job_links(body),
                         ["https://www.totaljobs.com/job/12345/technician"])

    def test_the_same_advert_twice_in_one_email_counts_once(self):
        body = ('<a href="https://x.com/job/1?utm_source=email">a</a>'
                '<a href="https://x.com/job/1?utm_source=footer">b</a>')
        self.assertEqual(len(ah.job_links(body)), 1)

    def test_the_same_advert_tomorrow_gets_the_same_id(self):
        """Daily alerts repeat yesterday's jobs. Tracking parameters differ."""
        self.assertEqual(ah.alert_id("https://x.com/job/1?utm_source=mon"),
                         ah.alert_id("https://x.com/job/1?utm_source=tue"))
        self.assertNotEqual(ah.alert_id("https://x.com/job/1"),
                            ah.alert_id("https://x.com/job/2"))

    def test_an_empty_alert_yields_nothing(self):
        self.assertEqual(ah.job_links(""), [])
        self.assertEqual(ah.job_links(None), [])


class TestReadingTheAdvert(unittest.TestCase):
    """An alert gives a link and little else, so the title and employer come
    off the advert's own markup - the tags that make a link preview work."""

    def page(self, title, og_title=None, description="Offshore calibration."):
        og = f'<meta property="og:title" content="{og_title}">' if og_title else ""
        return (f"<html><head><title>{title}</title>{og}"
                f'<meta property="og:description" content="{description}">'
                f"</head><body>advert</body></html>")

    def test_the_role_and_employer_are_read_off_the_page(self):
        facts = ah.page_facts(self.page(
            "Instrumentation Technician - Aberdeen | Acme Subsea | CV-Library"))
        title, company = ah.split_title(facts["title"])
        self.assertEqual(title, "Instrumentation Technician")
        self.assertEqual(company, "Acme Subsea")

    def test_the_job_board_is_never_taken_for_the_employer(self):
        """Putting 'Totaljobs' on an email as the employer is the same mistake
        that has already sent two of these to the wrong company."""
        for raw in ("Technician - Aberdeen | Totaljobs",
                    "Field Engineer | CV-Library",
                    "Workshop Technician at Indeed"):
            with self.subTest(raw=raw):
                self.assertEqual(ah.split_title(raw)[1], "")

    def test_a_location_is_not_an_employer(self):
        self.assertEqual(
            ah.split_title("Instrumentation Technician - Aberdeen")[1], "")

    def test_the_open_graph_title_is_preferred(self):
        facts = ah.page_facts(self.page("Some Board - Search Results",
                                        og_title="Test Engineer at Sonardyne"))
        self.assertEqual(ah.split_title(facts["title"]),
                         ("Test Engineer", "Sonardyne"))

    def test_meta_tags_in_either_attribute_order_are_read(self):
        html = ('<meta content="Rope Access Technician at Acme" '
                'property="og:title">')
        self.assertEqual(ah.page_facts(html)["title"],
                         "Rope Access Technician at Acme")


class TestEnriching(unittest.TestCase):
    def state(self, **over):
        job = {"external_id": "alert-1", "url": "https://x.com/job/1",
               "source": "alert:x.com", "status": "new", "title": "",
               "company": "", "description": ""}
        job.update(over)
        return {"jobs": {job["external_id"]: job}}

    def response(self, html, ok=True):
        return mock.Mock(ok=ok, text=html, status_code=200 if ok else 500)

    def test_an_advert_that_names_the_role_and_firm_is_kept(self):
        state = self.state()
        html = ('<title>Instrumentation Technician - Aberdeen | Acme Subsea</title>'
                '<meta property="og:description" content="Calibration offshore.">')
        with mock.patch.object(jm.requests, "get", return_value=self.response(html)):
            ah.enrich(state)
        job = state["jobs"]["alert-1"]
        self.assertEqual(job["title"], "Instrumentation Technician")
        self.assertEqual(job["company"], "Acme Subsea")
        self.assertEqual(job["status"], "new")

    def test_an_advert_that_names_no_employer_is_dropped_not_guessed(self):
        """A listing with no company cannot be emailed to anyone, and inventing
        one is how an application reaches the wrong firm."""
        state = self.state()
        with mock.patch.object(jm.requests, "get",
                               return_value=self.response("<title>Jobs</title>")):
            ah.enrich(state)
        self.assertEqual(state["jobs"]["alert-1"]["status"], "skipped")

    def test_an_unreachable_advert_is_dropped(self):
        state = self.state()
        with mock.patch.object(jm.requests, "get", side_effect=OSError("down")):
            ah.enrich(state)
        self.assertEqual(state["jobs"]["alert-1"]["status"], "skipped")

    def test_listings_from_the_apis_are_left_alone(self):
        state = self.state(source="adzuna", title="Already Known",
                           company="Known Ltd")
        with mock.patch.object(jm.requests, "get") as get:
            ah.enrich(state)
        get.assert_not_called()


class TestHarvesting(unittest.TestCase):
    def test_adverts_from_alerts_join_the_ordinary_queue(self):
        alerts = [("alerts@cv-library.co.uk", "Your jobs",
                   '<a href="https://www.cv-library.co.uk/job/999/tech">go</a>',
                   None)]
        state = {"jobs": {}}
        with mock.patch.object(ah, "fetch_alerts", return_value=alerts), \
             mock.patch.object(ah.imaplib, "IMAP4_SSL"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"):
            added = ah.harvest_alerts(state)
        self.assertEqual(added, 1)
        job = next(iter(state["jobs"].values()))
        self.assertEqual(job["status"], "new")
        self.assertEqual(job["source"], "alert:cv-library.co.uk")

    def test_yesterdays_advert_is_not_added_twice(self):
        alerts = [("alerts@cv-library.co.uk", "Your jobs",
                   '<a href="https://www.cv-library.co.uk/job/999/tech">go</a>',
                   None)]
        state = {"jobs": {}}
        with mock.patch.object(ah, "fetch_alerts", return_value=alerts), \
             mock.patch.object(ah.imaplib, "IMAP4_SSL"), \
             mock.patch.object(jm, "GMAIL_ADDRESS", "h@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"):
            ah.harvest_alerts(state)
            ah.harvest_alerts(state)
        self.assertEqual(len(state["jobs"]), 1)

    def test_without_credentials_it_does_nothing_rather_than_crashing(self):
        with mock.patch.object(jm, "GMAIL_ADDRESS", ""), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", ""):
            self.assertEqual(ah.harvest_alerts({"jobs": {}}), 0)

    def test_an_unreachable_inbox_does_not_stop_the_run(self):
        with mock.patch.object(jm, "GMAIL_ADDRESS", "h@gmail.com"), \
             mock.patch.object(jm, "GMAIL_APP_PASSWORD", "pw"), \
             mock.patch.object(ah.imaplib, "IMAP4_SSL",
                               side_effect=OSError("no route")):
            self.assertEqual(ah.harvest_alerts({"jobs": {}}), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
