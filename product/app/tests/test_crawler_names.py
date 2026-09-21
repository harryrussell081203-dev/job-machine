"""Which crawler read which page.

The whole retrieval strategy on this site - fifteen answer pages, a JSON
endpoint, llms.txt, every rate carrying its count - is aimed at being quoted
accurately by machines. Until now the only thing measured about them was that
they were machines: 258 visits a week collapsed into one number with an empty
source, because a crawler sends no referer.

The distinction that matters is not crawler-versus-person, it is which JOB the
crawler is doing. A search-and-answer bot fetching a page means an assistant
is citing it to somebody right now. A training crawler fetching the same page
means it might matter in a year. Those are different signals and they were
being added together.

These tests are mostly about the ways the naming goes quietly wrong: one token
swallowing another that contains it, and a stream of unnamed agents filling a
table that has no scheduled prune.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.tests.test_sending import Base  # noqa: E402


class TestNamingTheCrawler(Base):
    def setUp(self):
        super().setUp()
        from app import views
        self.views = views

    def name(self, agent):
        return self.views.crawler_name(agent)

    # -- the ones this site exists for ---------------------------------
    def test_a_bot_answering_a_question_now_is_told_from_one_training(self):
        """The reason for the role prefix. Both are Anthropic, both are
        worth having, and one of them means a person is being given this
        page as an answer this minute."""
        self.assertEqual(self.name("Mozilla/5.0 (compatible; Claude-SearchBot/1.0)"),
                         "ai-answer:anthropic")
        self.assertEqual(self.name("Mozilla/5.0 (compatible; ClaudeBot/1.0)"),
                         "ai-train:anthropic")

    def test_the_search_and_answer_agents_are_recognised(self):
        for agent, expected in (
                ("Mozilla/5.0 (compatible; OAI-SearchBot/1.0)", "ai-answer:openai"),
                ("Mozilla/5.0 ChatGPT-User/1.0", "ai-answer:openai"),
                ("Mozilla/5.0 (compatible; PerplexityBot/1.0)", "ai-answer:perplexity"),
                ("Perplexity-User/1.0", "ai-answer:perplexity"),
                ("Mozilla/5.0 (compatible; DuckAssistBot/1.0)", "ai-answer:duckduckgo")):
            self.assertEqual(self.name(agent), expected, agent)

    def test_the_training_crawlers_are_recognised(self):
        for agent, expected in (
                ("Mozilla/5.0 (compatible; GPTBot/1.2)", "ai-train:openai"),
                ("CCBot/2.0 (https://commoncrawl.org/faq/)", "ai-train:commoncrawl"),
                ("Mozilla/5.0 (compatible; Google-Extended)", "ai-train:google"),
                ("meta-externalagent/1.1", "ai-train:meta"),
                ("Mozilla/5.0 (compatible; Bytespider)", "ai-train:bytedance")):
            self.assertEqual(self.name(agent), expected, agent)

    # -- the way this breaks silently ----------------------------------
    def test_a_longer_token_is_not_swallowed_by_a_shorter_one(self):
        """"claudebot" is a substring of nothing, but "applebot" IS a
        substring of "applebot-extended", and "googlebot" sits next to
        "googleother". Matched shortest-first, Apple's training crawler would
        be filed as ordinary search indexing and nobody would ever notice."""
        self.assertEqual(self.name("Mozilla/5.0 (compatible; Applebot-Extended/1.0)"),
                         "ai-train:apple")
        self.assertEqual(self.name("Mozilla/5.0 (compatible; Applebot/0.1)"),
                         "search:apple")

    def test_an_unknown_crawler_is_one_bucket_not_its_whole_user_agent(self):
        """User-agent strings are unbounded, high-cardinality, and sometimes
        carry a URL. Recording them raw would grow a table that has no
        scheduled prune, one row per variant per hour."""
        for agent in ("Mozilla/5.0 (compatible; SomeNewBot/9.9; +https://x.example/bot)",
                      "curl/8.4.0",
                      "python-requests/2.31.0"):
            self.assertEqual(self.name(agent), self.views.UNKNOWN_CRAWLER, agent)

    def test_a_name_always_fits_the_column(self):
        for name in list(self.views.CRAWLERS.values()) + [self.views.UNKNOWN_CRAWLER]:
            self.assertLessEqual(len(name), self.views.MAX_SOURCE, name)

    def test_every_name_carries_a_role(self):
        """The prefix is what makes the number readable. A name without one
        would be counted and never interpreted."""
        roles = {"ai-answer", "ai-train", "search", "other"}
        for name in list(self.views.CRAWLERS.values()) + [self.views.UNKNOWN_CRAWLER]:
            self.assertIn(name.split(":")[0], roles, name)

    # -- and it reaches the table --------------------------------------
    def test_a_crawler_visit_is_stored_under_its_name(self):
        self.client.get("/find", headers={
            "user-agent": "Mozilla/5.0 (compatible; ClaudeBot/1.0)"})
        rows = self.views.totals(since=0)
        self.assertIn("ai-train:anthropic", rows["crawlers"])
        self.assertIn("/find", rows["crawler_pages"]["ai-train:anthropic"])

    def test_a_person_is_still_recorded_by_where_they_came_from(self):
        """The source column does two jobs now. The one it had must survive."""
        self.client.get("/find", headers={
            "user-agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko)"),
            "referer": "https://www.reddit.com/r/UKJobs/"})
        rows = self.views.totals(since=0)
        self.assertIn("reddit.com", rows["sources"])

    def test_an_in_app_browser_is_still_a_person(self):
        """The rule that cost a day of Snapchat traffic. A real person tapping
        a link from the app carrying the launch must not be filed as a bot
        just because the naming table got longer."""
        self.client.get("/find", headers={
            "user-agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                           "Snapchat/12.63.0.44")})
        rows = self.views.totals(since=0)
        self.assertTrue(rows["people"], "an in-app browser was counted as a robot")


if __name__ == "__main__":
    unittest.main()
