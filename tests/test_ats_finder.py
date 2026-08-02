"""
Tests for finding an employer's application form without a job board.

All offline. Nothing here touches a real ATS or a real employer's site.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ats_finder as af  # noqa: E402
import job_machine as jm  # noqa: E402


def response(status=200, text="x" * 1000, url="https://example.com"):
    r = mock.Mock(status_code=status, text=text, url=url)
    r.raise_for_status = lambda: None
    return r


class TestSlugs(unittest.TestCase):
    def test_company_names_become_plausible_slugs(self):
        self.assertEqual(af.slug_candidates("Hydrasun")[0], "hydrasun")
        self.assertIn("dronanddickson", af.slug_candidates("Dron & Dickson Ltd"))
        self.assertIn("bakerhughes", af.slug_candidates("Baker Hughes"))
        self.assertIn("baker-hughes", af.slug_candidates("Baker Hughes"))

    def test_corporate_noise_is_dropped(self):
        slugs = af.slug_candidates("Orion Electrotech Limited UK Group")
        self.assertIn("orionelectrotech", slugs)
        self.assertFalse(any("limited" in s for s in slugs))

    def test_a_nameless_company_yields_nothing(self):
        self.assertEqual(af.slug_candidates(""), [])
        self.assertEqual(af.slug_candidates("Ltd"), [])

    def test_first_word_is_tried_for_multiword_firms(self):
        self.assertIn("survitec", af.slug_candidates("Survitec Group"))


class TestProbing(unittest.TestCase):
    def test_finds_a_real_greenhouse_board(self):
        def get(url, **kw):
            if url == "https://boards.greenhouse.io/hydrasun":
                return response(text="Open roles at Hydrasun " + "x" * 900,
                                url=url)
            return response(status=404, text="Page not found")
        with mock.patch.object(af.requests, "get", side_effect=get):
            self.assertEqual(af.probe_ats("Hydrasun"),
                             ("greenhouse", "https://boards.greenhouse.io/hydrasun"))

    def test_a_friendly_404_is_not_mistaken_for_a_board(self):
        """These platforms return 200 with a 'page not found' body."""
        with mock.patch.object(af.requests, "get",
                               return_value=response(text="Page not found " + "x" * 900)):
            self.assertIsNone(af.probe_ats("Nonexistent Engineering"))

    def test_a_thin_page_is_not_a_board(self):
        with mock.patch.object(af.requests, "get", return_value=response(text="hi")):
            self.assertIsNone(af.probe_ats("Tiny Co"))

    def test_network_failure_is_survived(self):
        with mock.patch.object(af.requests, "get", side_effect=OSError("down")):
            self.assertIsNone(af.probe_ats("Acme"))


class TestCareersPage(unittest.TestCase):
    def test_ats_link_on_a_careers_page_is_found(self):
        html = ('<a href="https://jobs.lever.co/acme/123">See our roles</a>'
                + "x" * 500)
        with mock.patch.object(af.requests, "get",
                               return_value=response(text=html)):
            ats, url = af.careers_page_ats("acme.com")
        self.assertEqual(ats, "lever")
        self.assertTrue(url.startswith("https://jobs.lever.co/acme"))

    def test_a_site_with_no_ats_returns_nothing(self):
        with mock.patch.object(af.requests, "get",
                               return_value=response(text="<p>About us</p>")):
            self.assertIsNone(af.careers_page_ats("acme.com"))

    def test_no_domain_means_no_lookup(self):
        with mock.patch.object(af.requests, "get") as get:
            self.assertIsNone(af.careers_page_ats(None))
        get.assert_not_called()


class TestMatchingTheAdvert(unittest.TestCase):
    def board(self, links):
        page = mock.MagicMock()
        page.evaluate.return_value = links
        return page

    def test_the_right_advert_is_picked_off_a_board(self):
        page = self.board([
            {"href": "https://boards.greenhouse.io/acme/jobs/1",
             "text": "Senior Software Developer"},
            {"href": "https://boards.greenhouse.io/acme/jobs/2",
             "text": "Instrumentation Technician - Aberdeen"},
            {"href": "https://boards.greenhouse.io/acme/jobs/3",
             "text": "Finance Manager"}])
        url = af.match_job_on_board(page, "https://boards.greenhouse.io/acme",
                                    "Instrumentation Technician")
        self.assertEqual(url, "https://boards.greenhouse.io/acme/jobs/2")

    def test_a_board_with_nothing_similar_returns_none(self):
        """Applying to the wrong vacancy is worse than not applying."""
        page = self.board([
            {"href": "https://boards.greenhouse.io/acme/jobs/1",
             "text": "Head of Marketing"},
            {"href": "https://boards.greenhouse.io/acme/jobs/2",
             "text": "Legal Counsel"}])
        self.assertIsNone(af.match_job_on_board(
            page, "https://boards.greenhouse.io/acme", "Instrumentation Technician"))

    def test_a_partial_match_below_the_threshold_is_refused(self):
        page = self.board([{"href": "https://x/1", "text": "Technician"}])
        # only 1 of 2 significant words - below the 50% bar? it is exactly 50%
        self.assertEqual(
            af.match_job_on_board(page, "https://x", "Instrumentation Technician"),
            "https://x/1")
        self.assertIsNone(af.match_job_on_board(
            page, "https://x", "Instrumentation Calibration Control Technician",
            min_overlap=0.6))

    def test_an_unreadable_board_does_not_crash(self):
        page = mock.MagicMock()
        page.goto.side_effect = OSError("timeout")
        self.assertIsNone(af.match_job_on_board(page, "https://x", "Technician"))


class TestEndToEndDiscovery(unittest.TestCase):
    def test_probe_then_match_gives_the_application_url(self):
        job = {"company": "Hydrasun", "title": "Instrumentation Technician"}
        page = mock.MagicMock()
        page.evaluate.return_value = [
            {"href": "https://boards.greenhouse.io/hydrasun/jobs/9",
             "text": "Instrumentation Technician"}]
        with mock.patch.object(af, "probe_ats",
                               return_value=("greenhouse",
                                             "https://boards.greenhouse.io/hydrasun")):
            url, ats = af.find_application_url(page, job)
        self.assertEqual(url, "https://boards.greenhouse.io/hydrasun/jobs/9")
        self.assertEqual(ats, "greenhouse")

    def test_falls_back_to_the_board_url_when_the_advert_is_not_listed(self):
        job = {"company": "Hydrasun", "title": "Instrumentation Technician"}
        page = mock.MagicMock()
        page.evaluate.return_value = []
        with mock.patch.object(af, "probe_ats",
                               return_value=("lever", "https://jobs.lever.co/hydrasun")):
            url, ats = af.find_application_url(page, job)
        self.assertEqual(url, "https://jobs.lever.co/hydrasun")

    def test_no_ats_anywhere_means_no_url_rather_than_a_guess(self):
        job = {"company": "Some Local Firm", "title": "Technician"}
        with mock.patch.object(af, "probe_ats", return_value=None), \
             mock.patch.object(af, "careers_page_ats", return_value=None), \
             mock.patch.object(jm, "find_domain", return_value=None):
            url, ats = af.find_application_url(mock.MagicMock(), job)
        self.assertIsNone(url)

    def test_a_company_with_no_name_is_skipped(self):
        url, ats = af.find_application_url(mock.MagicMock(), {"company": ""})
        self.assertIsNone(url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
