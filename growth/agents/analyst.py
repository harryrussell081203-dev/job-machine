"""The weekly read-out: who is reading this site, and what Google thinks of it.

WHY THIS AGENT EXISTS.

Three things that would otherwise live in three separate places and never
be read together:

  - What Google's Search Console says the site is nearly ranking for (the
    cheapest traffic there is, documented in `product/app/search_console.py`
    and refreshed by its own weekly job).
  - How often the AI-answer crawlers - OpenAI, Anthropic, Perplexity,
    DuckDuckGo, Mistral - fetched each page in the last seven days. These
    are the hits where somebody asked an assistant a question and the
    assistant quoted a page from this site to answer. They are the highest
    signal number on the whole site.
  - Which pages got nobody at all. A page nobody arrives at and no crawler
    reads is a page whose first job - being findable - is not getting done.

The agent fetches all three in one call to `/growth/metrics.json`, writes a
sorted summary into `growth/data/analyst.json`, and lets the committed
diff be the weekly report. A page's position in that file is readable from
`git log growth/data/analyst.json`.

WHAT THIS AGENT DOES NOT DO.

It does not touch the site, the directory, or any crawler. It does not
email - the committed state is the digest. It does not run the Search
Console analysis itself; that job already runs weekly in the app and
writes to site_meta. This agent reads what the app has already measured
and arranges it for a person to review.

Bing Webmaster is deliberately not here. Setting it up needs a Microsoft
account that is not yet configured; it is a follow-up that slots into the
same shape (another dict under `search_consoles`) when the credentials
exist.

FAIL CLOSED.

A fetch that cannot read the endpoint - 401 because the secret is wrong,
404 because the app is cold, JSON that will not parse - returns the
previous state untouched and `ok=False`. A run that failed to measure
and a run that measured nothing produce the same empty report at a
glance, and only one of them is true.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .. import USER_AGENT, Result

WINDOW_DAYS = 7

# Highest-signal rows go at the top of the report. Everything else is sorted
# by its own natural ranking (impressions, position, hit count).
TOP_N = {
    "ai_answer_pages": 20,     # pages the AI assistants fetched most
    "nearly_there": 15,        # position 8-20 on Google
    "seen_not_clicked": 10,    # ranked but not persuading
    "rising": 5,
    "falling": 5,
    "zero_impression_pages": 15,
}


def run(state: dict, *, base: str = "https://recruited.org.uk",
        get=None, now=None, secret=None,
        window_days: int = WINDOW_DAYS) -> "tuple[dict, Result]":
    """Fetch /growth/metrics.json and write a weekly summary."""
    result = Result()
    if get is None:                       # pragma: no cover - real network
        import requests
        get = requests.get
    now = now or (lambda: int(time.time()))
    stamp = now()

    if secret is None:
        import os
        secret = os.environ.get("METRICS_SECRET", "")
    if not secret:
        return state, result.failed(
            "METRICS_SECRET is not set; cannot authenticate to "
            f"{base}/growth/metrics.json")

    url = (f"{base.rstrip('/')}/growth/metrics.json"
           f"?window_days={int(window_days)}")
    try:
        r = get(url, headers={
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {secret}",
        }, timeout=60)
    except Exception as e:
        return state, result.failed(
            f"could not fetch {url}: {type(e).__name__}: {e}")

    code = getattr(r, "status_code", 0)
    if code != 200:
        return state, result.failed(f"{url} returned {code}")

    try:
        data = r.json() if callable(getattr(r, "json", None)) \
            else json.loads(r.text)
    except Exception as e:
        return state, result.failed(f"{url} did not parse: {e}")

    sc = data.get("search_console") or {}
    v = data.get("views") or {}
    pages_allowlist = data.get("pages") or []

    report = {
        "generated_at": stamp,
        "generated_at_iso": _iso(stamp),
        "window_days": window_days,
        "window_ends_at_iso": _iso(stamp),
        "search_console": _search_console_summary(sc),
        "ai_answer": _ai_answer_summary(v),
        "zero_impression_pages": _zero_impression_pages(v, pages_allowlist),
        "crawler_mix": _crawler_mix(v),
        "notes": _notes(sc, v, pages_allowlist),
    }

    new_state = dict(state)
    new_state["last_run_at"] = stamp
    new_state["base"] = base
    new_state["report"] = report
    # Keep a short rolling history of the summary numbers, so a week-over-
    # week trend is readable from the state alone without opening git log.
    history = list(new_state.get("history") or [])[-11:]
    history.append({
        "at": stamp,
        "window_days": window_days,
        "ai_answer_total": report["ai_answer"]["total"],
        "nearly_there": len(report["search_console"].get("nearly_there")
                            or []),
        "zero_impression": len(report["zero_impression_pages"]),
    })
    new_state["history"] = history

    counts = {
        "ai_answer_hits": report["ai_answer"]["total"],
        "ai_answer_pages": len(report["ai_answer"]["pages"]),
        "nearly_there": len(report["search_console"].get("nearly_there")
                            or []),
        "zero_impression": len(report["zero_impression_pages"]),
    }

    # Surface the top line in for_review so the Actions log reads as a
    # one-glance digest: a weekly report that only lands in a committed
    # file is a report nobody reads.
    for line in report["notes"]:
        result.for_review.append(line)

    return new_state, result.say(
        f"{counts['ai_answer_hits']} AI-answer hits across "
        f"{counts['ai_answer_pages']} pages, "
        f"{counts['nearly_there']} nearly-there, "
        f"{counts['zero_impression']} zero-impression",
        **counts)


def _search_console_summary(sc: dict) -> dict:
    """The parts of search_console.latest() worth putting at the top."""
    if not sc:
        return {}
    out = {
        "from": sc.get("from"),
        "to": sc.get("to"),
        "nearly_there": (sc.get("nearly_there") or [])[:TOP_N["nearly_there"]],
        "seen_not_clicked": (sc.get("seen_not_clicked")
                             or [])[:TOP_N["seen_not_clicked"]],
        "rising": (sc.get("rising") or [])[:TOP_N["rising"]],
        "falling": (sc.get("falling") or [])[:TOP_N["falling"]],
        "two_pages_one_search": sc.get("two_pages_one_search") or [],
        "questions_without_a_page": (sc.get("questions_without_a_page")
                                     or []),
    }
    return out


def _ai_answer_summary(v: dict) -> dict:
    """Per page, how many AI-answer crawlers fetched it.

    `views.totals().crawler_pages` is a {crawler_name: {path: n}} two-level
    map. The AI-answer crawlers are the ones whose role prefix is
    "ai-answer:" - see product/app/views.py. We flatten by page and keep
    the per-operator breakdown so a reviewer can see which assistant is
    quoting which page.
    """
    crawler_pages = (v or {}).get("crawler_pages") or {}
    pages: "dict[str, dict]" = {}
    total = 0
    for crawler, by_path in crawler_pages.items():
        if not isinstance(crawler, str) or not crawler.startswith("ai-answer:"):
            continue
        operator = crawler.split(":", 1)[1] if ":" in crawler else crawler
        for path, n in (by_path or {}).items():
            n = int(n or 0)
            if n <= 0:
                continue
            p = pages.setdefault(path, {"path": path, "total": 0,
                                        "operators": {}})
            p["total"] += n
            p["operators"][operator] = p["operators"].get(operator, 0) + n
            total += n
    ranked = sorted(pages.values(), key=lambda p: (-p["total"], p["path"]))
    return {
        "total": total,
        "pages": ranked[:TOP_N["ai_answer_pages"]],
    }


def _zero_impression_pages(v: dict, allowlist: list) -> list:
    """Pages the allow-list says exist that nothing has read in the window.

    "Nothing" is strict: no person arrived at them, no robot of any kind
    fetched them. A page with people and no crawlers, or crawlers and no
    people, is interesting but not this - this is the list of pages that
    may as well not have been published.
    """
    seen = set((v or {}).get("people", {}).keys()) \
         | set((v or {}).get("robots", {}).keys())
    missing = [p for p in allowlist if p not in seen]
    missing.sort()
    return missing[:TOP_N["zero_impression_pages"]]


def _crawler_mix(v: dict) -> dict:
    """A compact view of which crawlers are visiting at all. Not ranked
    further - it is background context for the AI-answer numbers rather
    than a signal in its own right."""
    return (v or {}).get("crawlers") or {}


def _notes(sc: dict, v: dict, allowlist: list) -> list:
    """Three one-line observations for the Actions log and for_review.

    Deterministic rather than clever: a reviewer should be able to read
    the state file and get the same lines out that the Actions log did.
    """
    notes = []
    ai = _ai_answer_summary(v)
    if ai["total"] == 0:
        notes.append("no AI-answer crawlers this week - something is wrong "
                     "with the robots.txt, the schema, or the hosting")
    else:
        notes.append(f"{ai['total']} AI-answer hits; top page is "
                     f"{ai['pages'][0]['path']} with "
                     f"{ai['pages'][0]['total']}")
    nearly = (sc or {}).get("nearly_there") or []
    if nearly:
        best = nearly[0]
        notes.append(
            f"nearly-there: {best.get('page') or best.get('path') or '?'} "
            f"at position {best.get('position') or '?'} - "
            "cheapest traffic to earn")
    missing = _zero_impression_pages(v, allowlist)
    if missing:
        notes.append(f"{len(missing)} page(s) with zero traffic: "
                     + ", ".join(missing[:3])
                     + (" ..." if len(missing) > 3 else ""))
    return notes


def _iso(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
