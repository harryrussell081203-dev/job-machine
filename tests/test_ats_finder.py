"""
Tests for finding an employer's application form without a job board.

All offline. Nothing here touches a real ATS or a real employer's site.

Several of these are regressions for false positives that reached production:
every one of these platforms answers 200 for a company that is not on it, and
believing those 200s put fourteen unrelated companies on Workable, matched
'Morson Edge' to an AECOM advert, and sent 'Future Engineering' to recruitee's
marketing homepage.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ats_finder as af  # noqa: E402
import job_machine as jm  # noqa: E402


def response(status=200, payload=None, text=None, url="https://example.com"):
    """A requests-like response. payload=None means .json() raises, which is
    what an HTML bot check or a redirect to a marketing page really does."""
    body = text if text is not None else (json.dumps(payload)
                                          if payload is not None else "<html>hi")
    r = mock.Mock(status_code=status, text=body, url=url)
    if payload is None:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = payload
    r.raise_for_status = lambda: None
    return r


def api(routes, default=None):
    """side_effect that answers only the URLs given, 404 for everything else."""
    default = default or response(status=404, text="Not Found")

    def get(url, **kw):
        for fragment, resp in routes.items():
            if fragment in url:
                return resp
        return default
    return get


GREENHOUSE = "boards-api.greenhouse.io/v1/boards/{}/jobs"
SMARTRECRUITERS = "api.smartrecruiters.com/v1/companies/{}/postings"


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

    def test_an_abbreviation_is_never_asked_about(self):
        """A probe run asked greenhouse about 'future', from 'Future
        Engineering Recruitment Ltd', and got five real postings belonging to
        an unrelated AI company. Abbreviations found one wrong board and no
        right ones across fourteen companies, so they are not asked about."""
        plan = dict(af.slug_plan("Speedy Services Hire"))
        self.assertIn("speedyhire", plan)
        self.assertNotIn("speedy", plan)
        self.assertNotIn("future", dict(af.slug_plan("Future Engineering Ltd")))

    def test_a_noise_word_is_not_what_makes_a_name_whole(self):
        """'Survitec Group' is just Survitec - dropping 'Group' loses nothing,
        so the remaining slug is still the whole name."""
        self.assertEqual(af.slug_plan("Survitec Group")[0], ("survitec", True))

    def test_a_single_word_company_is_its_own_whole_name(self):
        self.assertEqual(af.slug_plan("Hydrasun")[0], ("hydrasun", True))


class TestBoardApis(unittest.TestCase):
    def test_a_real_greenhouse_board_is_found(self):
        get = api({GREENHOUSE.format("hydrasun"): response(payload={"jobs": [
            {"title": "Mobile Service Van Technician",
             "absolute_url": "https://boards.greenhouse.io/hydrasun/jobs/7"}]})})
        with mock.patch.object(af.requests, "get", side_effect=get):
            board = af.find_board("Hydrasun")
        self.assertEqual(board["ats"], "greenhouse")
        self.assertEqual(len(board["jobs"]), 1)

    def test_a_company_on_no_platform_at_all_gets_nothing(self):
        with mock.patch.object(af.requests, "get", side_effect=api({})):
            self.assertIsNone(af.find_board("Nonexistent Engineering"))

    def test_a_board_belonging_to_someone_else_is_refused(self):
        """The regression: careers.smartrecruiters.com/morsonedge served
        AECOM's adverts, and the HTML probe called it a match."""
        get = api({SMARTRECRUITERS.format("morsonedge"): response(payload={
            "content": [{"id": "744000141127109", "name": "QA & QC Engineer",
                         "company": {"identifier": "AECOM2", "name": "AECOM"}}]})})
        with mock.patch.object(af.requests, "get", side_effect=get):
            self.assertIsNone(af.find_board("Morson Edge"))

    def test_a_bot_check_served_at_the_api_url_is_not_a_board(self):
        """apply.workable.com answers 200 for any slug. It is not JSON, so it
        cannot be mistaken for a board any more."""
        botcheck = response(text="<div class='g-recaptcha'>Checking your browser")
        with mock.patch.object(af.requests, "get", return_value=botcheck):
            self.assertIsNone(af.find_board("ScottishPower"))

    def test_a_redirect_to_the_platforms_marketing_site_is_not_a_board(self):
        """futureengineering.recruitee.com redirected to recruitee.com's own
        homepage, which the HTML probe scored as a hit."""
        marketing = response(text="<html><body>Recruitee: hiring software</body>",
                             url="https://recruitee.com/")
        with mock.patch.object(af.requests, "get", return_value=marketing):
            self.assertIsNone(af.find_board("Future Engineering Recruitment Ltd"))

    def test_a_board_with_no_open_roles_is_no_use_to_us(self):
        get = api({GREENHOUSE.format("hydrasun"): response(payload={"jobs": []})})
        with mock.patch.object(af.requests, "get", side_effect=get):
            self.assertIsNone(af.find_board("Hydrasun"))

    def test_an_html_page_is_never_a_board_whatever_it_says(self):
        """These three used to be separate tests of looks_like_a_board, which
        tried to judge a board from its HTML. It cannot be done: the platforms
        answer 200 for any slug. Prose that merely mentions the company - even
        the right company - is not evidence of a board."""
        for page in ("<div class='g-recaptcha'>Checking your browser</div>",
                     "Open roles at Totally Different Corp",
                     "Careers at Hydrasun"):
            with self.subTest(page=page[:30]):
                with mock.patch.object(af.requests, "get",
                                       return_value=response(text=page + "x" * 900)):
                    self.assertIsNone(af.probe_ats("Hydrasun"))

    def test_network_failure_is_survived(self):
        with mock.patch.object(af.requests, "get", side_effect=OSError("down")):
            self.assertIsNone(af.find_board("Acme"))

    def test_lever_returns_a_bare_list_not_an_object(self):
        get = api({"api.lever.co/v0/postings/acme": response(payload=[
            {"text": "Field Service Engineer",
             "applyUrl": "https://jobs.lever.co/acme/1/apply"}])})
        with mock.patch.object(af.requests, "get", side_effect=get):
            board = af.find_board("Acme")
        self.assertEqual(board["ats"], "lever")
        self.assertEqual(board["jobs"][0]["url"],
                         "https://jobs.lever.co/acme/1/apply")

    def test_smartrecruiters_is_asked_in_its_own_capitalisation(self):
        """Their identifier is case-sensitive - their posting URLs read
        jobs.smartrecruiters.com/AECOM2/... - so a lowercase slug gets a polite
        200 with zero postings even for a company that really is on there.
        Oceaneering was missed exactly this way."""
        asked = []

        def get(url, **kw):
            asked.append(url)
            if SMARTRECRUITERS.format("BakerHughes") in url:
                return response(payload={"content": [
                    {"id": "9", "name": "Field Engineer",
                     "company": {"identifier": "BakerHughes",
                                 "name": "Baker Hughes"}}]})
            return response(payload={"content": []})

        with mock.patch.object(af.requests, "get", side_effect=get):
            board = af.find_board("Baker Hughes")
        self.assertEqual(board["slug"], "BakerHughes")
        self.assertTrue(any("bakerhughes/postings" in u for u in asked),
                        "the plain lowercase spelling should still be tried first")

    def test_only_smartrecruiters_gets_the_capitalisation_treatment(self):
        self.assertEqual(af.slug_forms("greenhouse", "baker-hughes"),
                         ["baker-hughes"])
        self.assertIn("BakerHughes", af.slug_forms("smartrecruiters",
                                                   "baker-hughes"))

    def test_a_rate_limited_platform_is_retried_rather_than_written_off(self):
        """Workable started 429ing partway through a fourteen-company probe."""
        replies = [response(status=429, text="slow down"),
                   response(payload={"name": "Acme", "jobs": [
                       {"title": "Technician",
                        "url": "https://apply.workable.com/acme/j/1/"}]})]

        def get(url, **kw):
            if "apply.workable.com" not in url:
                return response(status=404, text="Not Found")
            return replies.pop(0) if replies else replies_last
        replies_last = response(status=404, text="Not Found")
        with mock.patch.object(af.requests, "get", side_effect=get), \
             mock.patch.object(af.time, "sleep"):
            board = af.find_board("Acme")
        self.assertIsNotNone(board)
        self.assertEqual(board["ats"], "workable")

    def test_smartrecruiters_postings_become_real_apply_links(self):
        get = api({SMARTRECRUITERS.format("oceaneering"): response(payload={
            "content": [{"id": "744000123", "name": "Workshop Technician",
                         "company": {"identifier": "Oceaneering",
                                     "name": "Oceaneering"}}]})})
        with mock.patch.object(af.requests, "get", side_effect=get):
            board = af.find_board("Oceaneering")
        self.assertEqual(board["jobs"][0]["url"],
                         "https://jobs.smartrecruiters.com/Oceaneering/744000123")


class TestCareersPage(unittest.TestCase):
    def test_ats_link_on_a_careers_page_is_found(self):
        html = ('<a href="https://jobs.lever.co/acme/123">See our roles</a>'
                + "x" * 500)
        with mock.patch.object(af.requests, "get",
                               return_value=response(text=html)):
            ats, url = af.careers_page_ats("acme.com")
        self.assertEqual(ats, "lever")
        self.assertTrue(url.startswith("https://jobs.lever.co/acme"))

    def test_a_portal_we_cannot_automate_is_still_worth_the_link(self):
        """Workday needs a human, but a direct link beats handing Harry an
        Adzuna advert to hunt through."""
        html = '<a href="https://acme.wd3.myworkdayjobs.com/careers">Apply</a>'
        with mock.patch.object(af.requests, "get",
                               return_value=response(text=html)):
            ats, url = af.careers_page_ats("acme.com")
        self.assertEqual(ats, "workday")

    def test_a_firm_with_no_hosted_ats_hands_back_their_own_page(self):
        """Not a failure. Of fourteen Aberdeen employers probed, one used a
        hosted ATS - the rest take applications on a form of their own, and a
        form is a form."""
        with mock.patch.object(af.requests, "get",
                               return_value=response(
                                   text="<p>About us</p>",
                                   url="https://acme.com/careers")):
            ats, url = af.careers_page_ats("acme.com")
        self.assertIsNone(ats)
        self.assertEqual(url, "https://acme.com/careers")

    def test_a_site_that_cannot_be_read_at_all_gives_nothing(self):
        """Distinct from 'they have no ATS' - a Cloudflare block used to look
        identical to a careers page with nothing on it."""
        with mock.patch.object(af.requests, "get",
                               return_value=response(status=403, text="denied")):
            self.assertIsNone(af.careers_page_ats("acme.com"))

    def test_no_domain_means_no_lookup(self):
        with mock.patch.object(af.requests, "get") as get:
            self.assertIsNone(af.careers_page_ats(None))
        get.assert_not_called()


class TestSlugFromUrl(unittest.TestCase):
    def test_the_company_identifier_is_read_back_out_of_a_board_link(self):
        self.assertEqual(
            af.slug_from_board_url("https://boards.greenhouse.io/hydrasun/jobs/7"),
            "hydrasun")
        self.assertEqual(
            af.slug_from_board_url("https://acme.recruitee.com/o/technician"),
            "acme")
        self.assertEqual(
            af.slug_from_board_url("https://apply.workable.com/dron-dickson/"),
            "dron-dickson")

    def test_a_link_with_no_identifier_gives_nothing(self):
        self.assertIsNone(af.slug_from_board_url("https://acme.com/careers"))


class TestMatchingTheAdvert(unittest.TestCase):
    def jobs(self, *titles):
        return [{"title": t, "url": f"https://x/{i}"} for i, t in enumerate(titles)]

    def test_the_right_advert_is_picked_off_a_board(self):
        url = af.match_posting(
            self.jobs("Senior Software Developer",
                      "Instrumentation Technician - Aberdeen", "Finance Manager"),
            "Instrumentation Technician")
        self.assertEqual(url, "https://x/1")

    def test_a_board_with_nothing_similar_returns_none(self):
        """Applying to the wrong vacancy is worse than not applying."""
        self.assertIsNone(af.match_posting(
            self.jobs("Head of Marketing", "Legal Counsel"),
            "Instrumentation Technician"))

    def test_a_partial_match_below_the_threshold_is_refused(self):
        one = self.jobs("Technician")
        self.assertEqual(af.match_posting(one, "Instrumentation Technician"),
                         "https://x/0")
        self.assertIsNone(af.match_posting(
            one, "Instrumentation Calibration Control Technician",
            min_overlap=0.6))

    def test_an_unreadable_browser_board_does_not_crash(self):
        page = mock.MagicMock()
        page.goto.side_effect = OSError("timeout")
        self.assertIsNone(af.match_job_on_board(page, "https://x", "Technician"))


class TestEndToEndDiscovery(unittest.TestCase):
    def test_board_then_match_gives_the_application_url(self):
        job = {"company": "Hydrasun", "title": "Instrumentation Technician"}
        board = {"ats": "greenhouse", "whole_name": True,
                 "jobs": [{"title": "Instrumentation Technician",
                           "url": "https://boards.greenhouse.io/hydrasun/jobs/9"}]}
        with mock.patch.object(af, "find_board", return_value=board):
            url, ats = af.find_application_url(None, job)
        self.assertEqual(url, "https://boards.greenhouse.io/hydrasun/jobs/9")
        self.assertEqual(ats, "greenhouse")

    def test_a_real_board_without_this_advert_reports_the_ats_and_no_url(self):
        """An agency posted it; the employer's board does not carry it. That is
        a different answer from 'we found nothing', and run() says so."""
        job = {"company": "Hydrasun", "title": "Mobile Service Van Technician"}
        board = {"ats": "greenhouse", "whole_name": True,
                 "jobs": [{"title": "Finance Manager", "url": "https://x/1"}]}
        with mock.patch.object(af, "find_board", return_value=board):
            url, ats = af.find_application_url(None, job)
        self.assertIsNone(url)
        self.assertEqual(ats, "greenhouse")

    def test_a_board_found_by_an_unproven_slug_needs_a_much_closer_match(self):
        """Nothing generates these any more, but the careers-page route can
        still hand us a slug we did not derive, so the guard stays."""
        job = {"company": "Speedy Services", "title": "Test and Run Technician"}
        board = {"ats": "lever", "whole_name": False,
                 "jobs": [{"title": "Technician", "url": "https://x/1"}]}
        with mock.patch.object(af, "find_board", return_value=board), \
             mock.patch.object(jm, "find_domain", return_value=None):
            url, ats = af.find_application_url(None, job)
        self.assertIsNone(url)

    def test_the_careers_page_route_lists_the_board_and_matches_the_advert(self):
        job = {"company": "Acme Engineering", "title": "Field Service Engineer",
               "company_domain": "acme.com"}
        listing = {"jobs": [{"title": "Field Service Engineer",
                             "url": "https://jobs.lever.co/acme/1/apply"}]}
        with mock.patch.object(af, "find_board", return_value=None), \
             mock.patch.object(af, "careers_page_ats",
                               return_value=("lever", "https://jobs.lever.co/acme")), \
             mock.patch.object(af, "api_board", return_value=listing):
            url, ats = af.find_application_url(None, job)
        self.assertEqual(url, "https://jobs.lever.co/acme/1/apply")
        self.assertEqual(ats, "lever")

    def test_no_ats_anywhere_means_no_url_rather_than_a_guess(self):
        job = {"company": "Some Local Firm", "title": "Technician"}
        with mock.patch.object(af, "find_board", return_value=None), \
             mock.patch.object(af, "careers_page_ats", return_value=None), \
             mock.patch.object(jm, "find_domain", return_value=None):
            url, ats = af.find_application_url(mock.MagicMock(), job)
        self.assertIsNone(url)

    def test_a_company_with_no_name_is_skipped(self):
        url, ats = af.find_application_url(mock.MagicMock(), {"company": ""})
        self.assertIsNone(url)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestWalkingAnEmployersOwnSite(unittest.TestCase):
    """Most Aberdeen employers run no hosted ATS. Their careers page is an
    index, so the vacancy is one click in and the form often one more."""
    def page(self, links, apply_links=None, final="https://acme.com/apply/9"):
        page = mock.MagicMock()
        page.evaluate.side_effect = [links, apply_links if apply_links is not None
                                     else {"offsite": [], "onsite": []}]
        page.url = final
        return page

    def test_the_vacancy_is_found_and_its_apply_link_followed(self):
        page = self.page(
            [{"href": "https://acme.com/jobs/9", "text": "Instrumentation Technician"},
             {"href": "https://acme.com/jobs/3", "text": "Head of Finance"}],
            {"offsite": [], "onsite": [{"href": "https://acme.com/apply/9",
                                        "text": "Apply now"}]})
        url = af.own_site_application(page, "https://acme.com/careers",
                                      "Instrumentation Technician")
        self.assertEqual(url, "https://acme.com/apply/9")

    def test_the_advert_itself_is_used_when_it_carries_the_form(self):
        page = self.page(
            [{"href": "https://acme.com/jobs/9", "text": "Instrumentation Technician"}],
            {"offsite": [], "onsite": []})
        url = af.own_site_application(page, "https://acme.com/careers",
                                      "Instrumentation Technician")
        self.assertEqual(url, "https://acme.com/jobs/9")

    def test_a_careers_page_without_this_vacancy_gives_nothing(self):
        page = self.page([{"href": "https://acme.com/jobs/3",
                           "text": "Head of Finance"}])
        self.assertIsNone(af.own_site_application(page, "https://acme.com/careers",
                                                  "Instrumentation Technician"))

    def test_no_browser_means_no_walk(self):
        self.assertIsNone(af.own_site_application(None, "https://acme.com/careers",
                                                  "Technician"))

    def test_the_route_reaches_it_when_no_hosted_ats_exists(self):
        job = {"company": "Acme Engineering", "title": "Instrumentation Technician",
               "company_domain": "acme.com"}
        with mock.patch.object(af, "find_board", return_value=None), \
             mock.patch.object(af, "careers_page_ats",
                               return_value=(None, "https://acme.com/careers")), \
             mock.patch.object(af, "own_site_application",
                               return_value="https://acme.com/apply/9") as walk:
            url, ats = af.find_application_url(mock.MagicMock(), job)
        self.assertEqual(url, "https://acme.com/apply/9")
        self.assertIsNone(ats)
        walk.assert_called_once()


class TestGivingUpOnAHungServer(unittest.TestCase):
    """requests' timeout bounds each socket read, not the whole request. A
    diagnose run sat on a dribbling server past its sixty-minute workflow
    timeout and printed nothing at all."""
    def test_a_hung_read_is_abandoned_rather_than_waited_out(self):
        import time as clock

        def hangs(url, **kw):
            clock.sleep(10)
            return response()

        started = clock.monotonic()
        out = af.fetch(hangs, "https://slow.example.com", hard=0.3)
        self.assertIsNone(out)
        self.assertLess(clock.monotonic() - started, 3,
                        "fetch waited for the hung read instead of giving up")

    def test_a_healthy_fetch_still_comes_back(self):
        self.assertEqual(af.fetch(lambda url, **kw: "fine", "https://x", hard=5),
                         "fine")

    def test_a_failing_fetch_is_just_a_miss(self):
        def boom(url, **kw):
            raise OSError("refused")
        self.assertIsNone(af.fetch(boom, "https://x", hard=5))

    def test_the_careers_route_survives_a_hung_site(self):
        def hangs(url, **kw):
            import time as clock
            clock.sleep(10)
        with mock.patch.object(af.requests, "get", side_effect=hangs), \
             mock.patch.object(af, "HARD_TIMEOUT", 0.2):
            self.assertIsNone(af.careers_page_ats("slow.example.com"))


class TestAskingAgainWithTheBrowser(unittest.TestCase):
    """'nlb.org.uk could not be read at all', then '0 form fields found' -
    four times in one run, on Northern Lighthouse Board, Speedy Hire, Innserve
    and KBM. Every one is a real employer with a real careers page. They were
    unreadable because careers_page_ats asks with requests, and a corporate
    site behind a WAF answers a bare Python user-agent with 403 while serving
    a browser perfectly well. There is a browser open and idle at that moment."""

    def page(self, html, url="https://x.example.com/careers"):
        return mock.Mock(goto=mock.Mock(), wait_for_timeout=mock.Mock(),
                         content=mock.Mock(return_value=html), url=url)

    def test_the_browser_finds_the_ats_requests_could_not_see(self):
        page = self.page(
            '<a href="https://apply.workable.com/nlb/">Our vacancies</a>')
        self.assertEqual(
            af.careers_page_in_browser(page, "nlb.org.uk"),
            ("workable", "https://apply.workable.com/nlb/"))

    def test_their_own_careers_page_beats_the_job_boards_interstitial(self):
        page = self.page("<h1>Work for us</h1><p>No vacancies listed.</p>")
        ats, url = af.careers_page_in_browser(page, "speedyhire.co.uk")
        self.assertIsNone(ats)
        self.assertTrue(url)

    def test_a_site_that_will_not_load_at_all_is_still_a_miss(self):
        page = mock.Mock(goto=mock.Mock(side_effect=RuntimeError("dead")),
                         wait_for_timeout=mock.Mock())
        self.assertIsNone(af.careers_page_in_browser(page, "gone.example.com"))

    def test_it_needs_a_browser_and_a_domain(self):
        self.assertIsNone(af.careers_page_in_browser(None, "x.com"))
        self.assertIsNone(af.careers_page_in_browser(self.page("<p>x</p>"), ""))

    def test_the_cheap_question_is_still_asked_first(self):
        """A page load costs seconds where a GET costs milliseconds, and most
        sites answer the cheap one fine."""
        job = {"company": "Acme Energy", "title": "Technician",
               "company_domain": "acme.example.com"}
        with mock.patch.object(af, "find_board", return_value=None), \
             mock.patch.object(af, "careers_page_ats",
                               return_value=("greenhouse",
                                             "https://boards.greenhouse.io/a")), \
             mock.patch.object(af, "careers_page_in_browser") as browser, \
             mock.patch.object(af, "slug_from_board_url", return_value=None):
            af.find_application_url(mock.Mock(), job)
        browser.assert_not_called()

    def test_and_the_browser_is_asked_only_when_it_fails(self):
        job = {"company": "Northern Lighthouse Board", "title": "Technician",
               "company_domain": "nlb.org.uk"}
        with mock.patch.object(af, "find_board", return_value=None), \
             mock.patch.object(af, "careers_page_ats", return_value=None), \
             mock.patch.object(af, "careers_page_in_browser",
                               return_value=(None, "https://nlb.org.uk/careers")), \
             mock.patch.object(af, "own_site_application", return_value=None):
            url, ats = af.find_application_url(mock.Mock(), job)
        self.assertEqual(url, "https://nlb.org.uk/careers")
