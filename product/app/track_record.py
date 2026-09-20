"""The founder's own job search, as evidence, read from where it is published.

The landing page has always led with real numbers, because "it works" is worth
nothing and "116 letters, 30 replies" is checkable. But they were typed into
the template by hand - 26 sent, 7 replies, 27%, captioned "a fortnight in
August 2026" - and frozen from the day they were written while the real
figures went on climbing without them.

This reads the summary the personal machine publishes at the end of every run.
Three things make that safe rather than a coupling between two systems that
are meant to stay apart:

  - **One direction, always.** The machine writes data/track_record.json; this
    reads it. There is no path back: no shared database, no shared
    credentials, nothing the website can do that reaches the job hunt.
  - **A summary, not the source.** state.json is fourteen megabytes and a
    quarter of a second to parse. The published file is a handful of integers.
  - **Never load-bearing.** If the file is missing, unreadable, or nonsense,
    this returns None and the page simply does not make the claim. A marketing
    number is not worth a 500, and a stale number is worse than no number on
    a page whose entire argument is that it can be checked.
"""

from __future__ import annotations

import json
import os

# product/app/track_record.py -> product/ -> the repository root.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.environ.get("TRACK_RECORD_PATH",
                      os.path.join(ROOT, "data", "track_record.json"))

# Cached against the file's modification time rather than for a fixed period.
# The file changes a few times a day and is read on every visit to the busiest
# page on the site, so re-reading it per request is waste - but a timed cache
# would go on serving yesterday's figures after a deploy for no reason.
_cache: tuple | None = None

_FIELDS = ("applications", "replies", "reply_rate", "employers")


def read() -> dict | None:
    """The published numbers, or None if there are none worth printing."""
    global _cache
    try:
        stamp = os.path.getmtime(PATH)
    except OSError:
        return None

    if _cache and _cache[0] == stamp:
        return _cache[1]

    try:
        with open(PATH) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None

    if not isinstance(raw, dict):
        return None

    record = {}
    for field in _FIELDS:
        value = raw.get(field)
        # Written by another program, so it is checked rather than trusted.
        # A string where an integer belongs would reach the template and
        # render "26%%" or worse; better to have no claim than a mangled one.
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        record[field] = value

    # Nothing sent means nothing to claim. Zero is a true number and a
    # terrible advertisement, and "0 applications, 0 replies" on a sales page
    # reads as broken rather than as honest.
    if record["applications"] < 1:
        return None

    record["updated_at"] = str(raw.get("updated_at") or "")
    _cache = (stamp, record)
    return record


def updated_on() -> str:
    """The published date, as YYYY-MM-DD, or "" if there is none.

    For the sitemap's lastmod, which wants a date and nothing else. Taken
    from what the machine wrote rather than from the clock, so a page is
    claimed to have changed only when the figures on it actually did.
    """
    record = read() or {}
    stamp = record.get("updated_at") or ""
    date = stamp[:10]
    return date if len(date) == 10 and date[4] == "-" and date[7] == "-" else ""
