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

# Substrings, lowercased. Anything matching is filed as a robot rather than a
# person. The list covers three separate things that all arrive as traffic:
# search crawlers, the link-preview fetchers that run on every share, and our
# own monitoring.
ROBOTS = (
    "bot", "crawl", "spider", "slurp", "scan", "monitor", "uptime",
    "preview", "fetcher", "headless", "python-requests", "httpx", "curl",
    "wget", "pg_net", "go-http-client", "okhttp", "java/", "libwww",
    "facebookexternalhit", "whatsapp", "telegram", "discord", "slack",
    "embedly", "quora link", "pinterest", "applebot", "ahrefs", "semrush",
    "dataprovider", "skypeuripreview", "vkshare", "snapchat",
)

PERSON, ROBOT = "person", "robot"

MAX_PATH = 64
MAX_SOURCE = 64


def looks_like_a_robot(user_agent: str) -> bool:
    """A missing user-agent counts as one too.

    Every real browser sends one. Nothing that omits it is a person reading
    the page, and a scraper that sets none is the commonest sort there is.
    """
    agent = (user_agent or "").strip().lower()
    if not agent:
        return True
    return any(marker in agent for marker in ROBOTS)


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
                (path[:MAX_PATH], source_of(referer, host), kind, slot))
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
    out: dict = {"people": {}, "robots": {}, "sources": {}, "total": 0}
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
