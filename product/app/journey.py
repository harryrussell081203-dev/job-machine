"""Visitor journeys, for the people who said yes to a cookie.

The anonymous tallies (views.py, pulse.py) count steps. They cannot say
whether the person who left the sign-up page is the same one who came back
on Thursday and joined, which source turns into accounts rather than visits,
or how many visits it takes. A journey can - but only with a cookie that
recognises the browser, and in the UK that needs consent first (PECR reg. 6).

So:
  - Nothing here runs until the visitor taps Accept. "No thanks" is as big
    as Accept, is remembered, and records nothing about them.
  - The cookie is a random code. It says which browser, never who. It is
    never joined to an account's email address; a sign-up is recorded on the
    journey as an event, not as a link to the person.
  - Everything is first-party, kept in our own database, deleted after 90
    days. No third-party script, which is a promise the privacy page makes.

What it unlocks: visits before sign-up, conversion by source, the section of
the page somebody was reading when they left, how long they stayed, and a
fair test of one headline against another (the visitor keeps the version
they first saw).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time

from .store import connect

CONSENT_COOKIE = "consent"          # "yes" or "no"; remembering a no is allowed
VISITOR_COOKIE = "vid"
CONSENT_MAX_AGE = 180 * 86400
VISITOR_MAX_AGE = 90 * 86400
KEEP_FOR = 90 * 86400

_VID = re.compile(r"^[A-Za-z0-9_-]{16,40}$")

# What a page may tell us about a consenting visitor. Fixed, like pulse.py.
CLIENT_KINDS = {"left", "consented"}
SECTIONS = {"hero", "proof", "how", "looks", "story", "why", "try", "versus",
            "promises", "price", "faq", "final", "start", "start-check",
            "dashboard", "other"}

# The headline test. Two versions, decided by the visitor code so somebody
# who comes back sees the one they saw before - otherwise neither version
# can be credited with the sign-up.
HEADLINES = {
    "A": ("Stop clicking Apply. Start getting replies.",
          "We find jobs near you, find the real person who is hiring, and "
          "write them a short email from you. You just tell us what work you "
          "want."),
    "B": ("Get your CV in front of the person hiring.",
          "Job sites hide your application in a pile. We find the real email "
          "address of the person hiring and write to them for you, from your "
          "own email."),
}


def consent(request) -> str:
    value = request.cookies.get(CONSENT_COOKIE, "")
    return value if value in ("yes", "no") else ""


def visitor(request) -> str:
    """The visitor code, only if they said yes and it looks like ours."""
    if consent(request) != "yes":
        return ""
    vid = request.cookies.get(VISITOR_COOKIE, "")
    return vid if _VID.match(vid) else ""


def new_visitor() -> str:
    return secrets.token_urlsafe(16)


def variant(vid: str) -> str:
    """A or B, fixed per visitor; A for anybody without a code."""
    if not vid:
        return "A"
    return "AB"[hashlib.sha256(vid.encode()).digest()[0] % 2]


def headline(request) -> dict:
    v = variant(visitor(request))
    title, lede = HEADLINES[v]
    return {"variant": v, "title": title, "lede": lede}


def _source(referer: str, host: str = "") -> str:
    from . import views
    try:
        return views.source_of(referer or "", host or "")
    except Exception:
        return ""


def record(vid: str, kind: str, path: str = "", detail: str = "",
           source: str = "", variant_: str = "", when: float | None = None):
    """One step of one consenting visitor's journey. Never raises."""
    if not vid:
        return
    try:
        at = int(when if when is not None else time.time())
        with connect() as c:
            c.execute(
                "INSERT INTO journey_events (vid, at, kind, path, detail, "
                "source, variant) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (vid[:40], at, kind[:30], (path or "")[:120],
                 (detail or "")[:120], (source or "")[:60],
                 (variant_ or "")[:2]))
            c.execute("DELETE FROM journey_events WHERE at < ?",
                      (at - KEEP_FOR,))
    except Exception:
        pass


def record_request(request, kind: str, detail: str = "") -> None:
    vid = visitor(request)
    if vid:
        record(vid, kind, request.url.path, detail,
               _source(request.headers.get("referer", ""),
                       request.headers.get("host", "")),
               variant(vid))


# ----------------------------------------------------------------------
# reading it back
# ----------------------------------------------------------------------
def _rows(since: float):
    try:
        with connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT vid, at, kind, path, detail, source, variant FROM "
                "journey_events WHERE at >= ? ORDER BY vid, at",
                (int(since),)).fetchall()]
    except Exception:
        return []


def report(*, since: float) -> dict:
    rows = _rows(since)
    by_vid: dict[str, list] = {}
    for r in rows:
        by_vid.setdefault(r["vid"], []).append(r)

    visitors = len(by_vid)
    joined = {v for v, ev in by_vid.items()
              if any(e["kind"] == "signed_up" for e in ev)}
    returning = sum(1 for ev in by_vid.values()
                    if len({e["at"] // 86400 for e in ev}) > 1)

    # Where they first came from, and how many of each became accounts.
    sources: dict[str, list] = {}
    for v, ev in by_vid.items():
        first = next((e["source"] for e in ev if e["source"]
                      and e["source"] != "self"), "") or "direct or an app"
        s = sources.setdefault(first, [0, 0])
        s[0] += 1
        s[1] += v in joined

    # Visits (distinct days) before signing up.
    days_to_join = []
    for v in joined:
        ev = by_vid[v]
        stop = next(e["at"] for e in ev if e["kind"] == "signed_up")
        days_to_join.append(len({e["at"] // 86400 for e in ev if e["at"] <= stop}))

    # The section somebody was reading when they left without joining, and
    # how long they had been there.
    left_at: dict[str, int] = {}
    seconds = []
    for v, ev in by_vid.items():
        if v in joined:
            continue
        for i, e in enumerate(ev):
            # Only a real exit: the last thing in that visit. Moving from the
            # front page to sign-up also fires "left", and is the opposite.
            nxt = ev[i + 1] if i + 1 < len(ev) else None
            if nxt and nxt["at"] - e["at"] < 1800:
                continue
            if e["kind"] == "left" and e["path"] in ("/", "/start"):
                section, _, secs = (e["detail"] or "").partition("|")
                key = f"{e['path']} - {section or 'top'}"
                left_at[key] = left_at.get(key, 0) + 1
                if secs.isdigit():
                    seconds.append(int(secs))

    # The headline test: per version, visitors who saw the front page, who
    # opened sign-up, and who made an account.
    test = {}
    for v, ev in by_vid.items():
        if not any(e["path"] == "/" and e["kind"] == "view" for e in ev):
            continue
        arm = ev[0]["variant"] or "A"
        t = test.setdefault(arm, {"saw": 0, "opened_start": 0, "joined": 0})
        t["saw"] += 1
        t["opened_start"] += any(e["path"] == "/start" for e in ev)
        t["joined"] += v in joined

    seconds.sort()
    return {
        "visitors": visitors,
        "returning": returning,
        "joined": len(joined),
        "sources": sorted(((k, n, j) for k, (n, j) in sources.items()),
                          key=lambda x: -x[1]),
        "visits_before_joining": (round(sum(days_to_join) / len(days_to_join), 1)
                                  if days_to_join else None),
        "left_at": sorted(left_at.items(), key=lambda x: -x[1])[:8],
        "median_seconds_before_leaving": (seconds[len(seconds) // 2]
                                          if seconds else None),
        "headline_test": {k: dict(v, title=HEADLINES[k][0])
                          for k, v in sorted(test.items())},
    }
