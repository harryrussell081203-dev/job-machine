"""Count how many people land on a page, so the funnel starts before sign-up.

The admin funnel begins at "Signed up", which means the most important
question this product currently has is unanswerable: 500 people were shown
the link and one account exists. Did nobody tap it, or did they tap it, land,
and leave? Those have completely different fixes and nothing recorded could
tell them apart.

WHAT IS STORED, AND WHAT DELIBERATELY IS NOT

A count. Path, hour, where they came from, and a tally. No cookie, no IP, no
session marker, no fingerprint, nothing that could pick one person out of the
tally or follow them between pages.

That is a real cost - it means this can never report "unique visitors", only
views, so one person refreshing four times looks like four. It is worth
paying. The alternative is holding an identifier for every stranger who ever
opens the site, which for a one-person product with no ICO registration is a
liability out of all proportion to knowing whether a Snapchat story worked.
It also keeps the site outside the consent-banner rules: there is nothing to
ask permission for.

At this volume the distinction barely bites anyway. "200 views of the landing
page and none of /find" and "60 people, none of whom went further" lead to
exactly the same conclusion.

CRAWLERS ARE COUNTED, SEPARATELY

Every share of this link is fetched by somebody's link preview before a human
sees it - Facebook, WhatsApp, Slack and Snapchat all do it, and so does the
uptime pinger that runs every ten minutes. Dropping those silently would be
tidy and wrong in the expensive direction: the day the page shows "40 views"
and all forty are robots, that page has told a lie that reads exactly like
good news. So a robot is recorded as a robot and shown on its own line.

The detection is a user-agent substring list, which is easily fooled and does
not need to be anything better. It is protecting a judgement call about
marketing, not an access decision.

NEVER LOAD-BEARING

Same rule as the progress bar: this describes the work, so it may never break
it. Every call swallows its own failures. A page that cannot be counted still
renders.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from .db import connect

HOUR = 3600

# Kept for two months. Long enough to compare this week's push with the last
# one, short enough that the table stays small without a scheduler to prune
# it - there is nothing here worth keeping for a year.
KEEP_FOR = 60 * 86400

# Substrings, lowercased, that mean a robot whatever else the user-agent says.
# Search crawlers, link-preview fetchers, scripting libraries and our own
# monitoring. Googlebot sends an otherwise perfectly browser-shaped string, so
# these have to win over the browser test below.
HARD_ROBOTS = (
    "bot", "crawl", "spider", "slurp", "scan", "monitor", "uptime",
    "preview", "fetcher", "headless", "python-requests", "httpx", "curl",
    "wget", "pg_net", "go-http-client", "okhttp", "java/", "libwww",
    "facebookexternalhit", "embedly", "quora link", "applebot", "ahrefs",
    "semrush", "dataprovider", "skypeuripreview", "vkshare",
)

# Apps that BOTH fetch link previews and ship an in-app browser, which is why
# none of them may ever go in the list above. A test asserts exactly that, and
# it is the only reason this tuple exists.
#
# Putting one there cost a day of Snapchat traffic. "snapchat" was a bare
# substring in the hard list - and Snapchat's in-app browser sends an ordinary
# iOS Safari user-agent with "Snapchat/12.63.0.44" appended. So does
# Instagram's, and WhatsApp's, and every other one. Real people tapping the
# link from the app that was carrying the launch were recorded as crawlers, on
# a page built specifically to stop crawlers being mistaken for an audience.
#
# Their preview fetchers are not browser-shaped - "WhatsApp/2.23.20.0", no
# Mozilla, no engine - so the shape test catches those without the name.
IN_APP_BROWSERS = (
    "snapchat", "instagram", "whatsapp", "telegram", "discord", "slack",
    "pinterest", "tiktok", "fban", "fbav", "fb_iab", "line/", "twitter",
    "linkedinapp", "reddit",
)

# What every real browser has and no plain HTTP client bothers to fake: the
# Mozilla prefix kept for compatibility since Netscape, plus a rendering
# engine. In-app browsers are real browsers and carry both.
BROWSER_MARKS = ("applewebkit", "gecko", "trident", "khtml")

PERSON, ROBOT = "person", "robot"

# WHICH CRAWLER, NOT JUST "A CRAWLER".
#
# Until now every robot was recorded as `robot` with an empty source, because
# a crawler sends no referer and `source` is the referer host. That collapsed
# 258 visits a week into one number that could not answer the only question
# worth asking of it: which pages are the assistants actually reading.
#
# So for a robot the source column carries the crawler's name instead. No
# schema change and no migration - the unique key already includes source, so
# each crawler gets its own row and totals() groups by it for free.
#
# The names are prefixed by role, and that distinction is the point:
#
#   ai-answer   Fetching a page to answer somebody's question right now.
#               A hit here means an assistant is citing this page TODAY.
#               This is the highest-signal number on the whole site.
#   ai-train    Collecting for a future model. Worth having, but it pays
#               off in a year rather than this week.
#   search      Ordinary search indexing.
#   other       Link previews, SEO tools, our own monitoring.
#
# Longest match wins, so "claude-searchbot" is not swallowed by "claudebot".
# Ordered longest-first at import for exactly that reason.
CRAWLERS = {
    # Answering a question now
    "oai-searchbot": "ai-answer:openai",
    "chatgpt-user": "ai-answer:openai",
    "claude-searchbot": "ai-answer:anthropic",
    "claude-user": "ai-answer:anthropic",
    "perplexitybot": "ai-answer:perplexity",
    "perplexity-user": "ai-answer:perplexity",
    "duckassistbot": "ai-answer:duckduckgo",
    "mistralai-user": "ai-answer:mistral",
    # Collecting for training
    "gptbot": "ai-train:openai",
    "claudebot": "ai-train:anthropic",
    "claude-web": "ai-train:anthropic",
    "anthropic-ai": "ai-train:anthropic",
    "google-extended": "ai-train:google",
    "applebot-extended": "ai-train:apple",
    "meta-externalagent": "ai-train:meta",
    "bytespider": "ai-train:bytedance",
    "amazonbot": "ai-train:amazon",
    "ccbot": "ai-train:commoncrawl",
    # Ordinary search
    "googlebot": "search:google",
    "googleother": "search:google",
    "bingbot": "search:bing",
    "applebot": "search:apple",
    "duckduckbot": "search:duckduckgo",
    "yandexbot": "search:yandex",
    # Everything else worth telling apart
    "ahrefsbot": "other:ahrefs",
    "semrushbot": "other:semrush",
    "facebookexternalhit": "other:preview",
    "pg_net": "other:ours",
    "uptime": "other:ours",
}

_CRAWLERS_BY_LENGTH = sorted(CRAWLERS.items(), key=lambda kv: -len(kv[0]))

# A crawler we have no name for. Kept as one bucket rather than recording the
# raw user-agent: user-agent strings are unbounded, high-cardinality and
# occasionally carry a URL, and this table is meant to stay small enough to
# live without a scheduled prune.
UNKNOWN_CRAWLER = "other:unnamed"


def crawler_name(user_agent: str) -> str:
    """Which crawler this is, as role:operator, or a single unknown bucket."""
    agent = (user_agent or "").strip().lower()
    for token, name in _CRAWLERS_BY_LENGTH:
        if token in agent:
            return name
    return UNKNOWN_CRAWLER

MAX_PATH = 64
MAX_SOURCE = 64


def looks_like_a_robot(user_agent: str) -> bool:
    """Decide in this order, because the order is the whole correction.

      1. No user-agent at all is a robot. Every real browser sends one, and a
         scraper that sets none is the commonest sort there is.
      2. A hard robot marker wins over everything. Googlebot's string is
         browser-shaped on purpose, so the shape test below cannot be allowed
         to rescue it.
      3. Otherwise, a browser-shaped string is a person - INCLUDING one that
         names an app. That is the fix: Snapchat, Instagram and WhatsApp all
         append their name to a normal browser user-agent, and matching the
         app name alone filed their readers as crawlers.
      4. Anything left announces no rendering engine, which covers both the
         apps' own preview fetchers ("WhatsApp/2.23.20.0") and every plain
         HTTP client. Treated as a robot: it costs an occasional obscure
         client, and the alternative costs an inflated audience, which is the
         error this page exists to avoid.
    """
    agent = (user_agent or "").strip().lower()
    if not agent:
        return True
    if any(marker in agent for marker in HARD_ROBOTS):
        return True
    if "mozilla/" in agent and any(m in agent for m in BROWSER_MARKS):
        return False
    return True


def source_of(referer: str, host: str) -> str:
    """Which site sent them, as a bare hostname.

    Three outcomes, and the difference between them is the whole value of the
    field:

      - ``""``      no referrer at all. A tap from an app - Snapchat,
                    Instagram, a WhatsApp message - usually looks like this,
                    and so does somebody typing the address in.
      - ``"self"``  they were already on the site. Internal navigation, which
                    must not be mistaken for a new arrival.
      - a hostname  an actual link somewhere else.

    Only the host is kept. The full referring URL can carry a search query or
    a private page title, and none of that is any of this product's business.
    """
    referer = (referer or "").strip()
    if not referer:
        return ""
    try:
        name = (urlsplit(referer).hostname or "").lower()
    except ValueError:
        return ""
    if not name:
        return ""
    if name.startswith("www."):
        name = name[4:]
    own = (host or "").lower().split(":")[0]
    if own.startswith("www."):
        own = own[4:]
    if name == own:
        return "self"
    return name[:MAX_SOURCE]


def record(path: str, *, user_agent: str = "", referer: str = "",
           host: str = "", when: float | None = None) -> None:
    """Add one to the tally. Failure here is never the caller's problem."""
    try:
        slot = int(when if when is not None else time.time()) // HOUR * HOUR
        kind = ROBOT if looks_like_a_robot(user_agent) else PERSON
        # A crawler sends no referer, so `source` would be empty for every one
        # of them. It carries the crawler's name instead - see CRAWLERS.
        who = (crawler_name(user_agent) if kind == ROBOT
               else source_of(referer, host))
        with connect() as c:
            c.execute(
                "INSERT INTO page_views (path, source, kind, hour_at, views) "
                "VALUES (?, ?, ?, ?, 1) "
                # Qualified on purpose: Postgres calls the bare column name
                # ambiguous inside an upsert and SQLite does not, so the
                # unqualified form passes every test and fails in production.
                # Exactly the trap rate_hits already documents.
                "ON CONFLICT(path, source, kind, hour_at) DO UPDATE "
                "SET views = page_views.views + 1",
                (path[:MAX_PATH], who[:MAX_SOURCE], kind, slot))
            c.execute("DELETE FROM page_views WHERE hour_at < ?",
                      (slot - KEEP_FOR,))
    except Exception:
        pass


# ----------------------------------------------------------------------
# reading it back
# ----------------------------------------------------------------------
def totals(*, since: float, now: float | None = None) -> dict:
    """Views per path over a window, people and robots kept apart."""
    now = time.time() if now is None else now
    # `sources` stays person-only and so does `total`. That separation is the
    # point of this module - a page read forty times where all forty are
    # crawlers has told a lie that reads exactly like good news - so the
    # crawler breakdown gets its own keys rather than being folded in.
    out: dict = {"people": {}, "robots": {}, "sources": {}, "total": 0,
                 "crawlers": {}, "crawler_pages": {}}
    try:
        with connect() as c:
            rows = c.execute(
                "SELECT path, source, kind, SUM(views) AS n FROM page_views "
                "WHERE hour_at >= ? GROUP BY path, source, kind",
                (int(since) // HOUR * HOUR,)).fetchall()
    except Exception:
        return out

    for row in rows:
        n = int(row["n"] or 0)
        bucket = out["people"] if row["kind"] == PERSON else out["robots"]
        bucket[row["path"]] = bucket.get(row["path"], 0) + n
        if row["kind"] == ROBOT:
            who = row["source"] or UNKNOWN_CRAWLER
            out["crawlers"][who] = out["crawlers"].get(who, 0) + n
            pages = out["crawler_pages"].setdefault(who, {})
            pages[row["path"]] = pages.get(row["path"], 0) + n
        if row["kind"] == PERSON:
            out["total"] += n
            # "" is a real answer here - an app tap with no referrer - so it
            # is named rather than dropped.
            if row["source"] != "self":
                label = row["source"] or "direct or an app"
                out["sources"][label] = out["sources"].get(label, 0) + n
    out["people"] = dict(sorted(out["people"].items(),
                                key=lambda kv: -kv[1]))
    out["robots"] = dict(sorted(out["robots"].items(), key=lambda kv: -kv[1]))
    out["sources"] = dict(sorted(out["sources"].items(), key=lambda kv: -kv[1]))
    out["crawlers"] = dict(sorted(out["crawlers"].items(), key=lambda kv: -kv[1]))
    out["crawler_pages"] = {
        who: dict(sorted(pages.items(), key=lambda kv: -kv[1]))
        for who, pages in sorted(out["crawler_pages"].items(),
                                 key=lambda kv: -sum(kv[1].values()))}
    return out


def by_hour(*, hours: int = 24, now: float | None = None) -> list[dict]:
    """People-only views per hour, oldest first, so a spike has a shape.

    Every hour in the window appears whether or not anything happened in it.
    A chart that silently omits the empty hours compresses a quiet day into
    something that looks busy.
    """
    now = time.time() if now is None else now
    latest = int(now) // HOUR * HOUR
    earliest = latest - (hours - 1) * HOUR
    counts = {latest - i * HOUR: 0 for i in range(hours)}
    try:
        with connect() as c:
            rows = c.execute(
                "SELECT hour_at, SUM(views) AS n FROM page_views "
                "WHERE kind = ? AND hour_at >= ? GROUP BY hour_at",
                (PERSON, earliest)).fetchall()
        for row in rows:
            slot = int(row["hour_at"])
            if slot in counts:
                counts[slot] = int(row["n"] or 0)
    except Exception:
        pass

    peak = max(counts.values()) or 1
    return [{"hour_at": slot, "count": counts[slot],
             "height": round(100 * counts[slot] / peak)}
            for slot in sorted(counts)]
