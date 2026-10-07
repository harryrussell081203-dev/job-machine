"""Why people look and do not sign up: where they stop, and what they say.

Two kinds of evidence, both anonymous, both in the same tally as page views
(views.outcome), so nothing here can pick one reader out:

  - WHERE. How far down the landing page people get, which button they
    tap, whether they start typing on /start, and which step they leave on.
    A drop between "scrolled to the proof" and "tapped the button" is a
    trust problem; a drop between "started typing" and "pressed Next" is a
    confusion problem. Counts tell those apart without asking anybody.

  - WHY. One question, once, to somebody about to leave without an
    account: what's stopping you? One tap. The optional "something else"
    line is the only free text the site keeps from a visitor, it is capped,
    and the widget says not to put personal details in it.

The event names a page may send are fixed here. Anything else is ignored, so
the tally cannot be filled with whatever a script chooses to post.
"""

from __future__ import annotations

import time

from . import views
from .store import connect

SCROLL = ("25", "50", "75", "100")
CTAS = ("hero", "how", "final", "price", "nav", "find", "hub")
REASONS = {
    "unsure": "I'm not sure it works",
    "unclear": "I don't get what it does",
    "details": "I don't want to give my details",
    "not-looking": "I'm not looking for a job right now",
    "browsing": "Just having a look for now",
    "other": "Something else",
}

EVENTS = (
    {f"land:scroll:{s}" for s in SCROLL}
    | {f"land:cta:{c}" for c in CTAS}
    | {"land:try", "start:typing", "start:next", "start:boxes",
       "start:left:describe-empty", "start:left:describe-typed",
       "start:left:check", "why:shown", "why:dismissed"}
    | {f"why:{r}" for r in REASONS}
)

MAX_TEXT = 200
KEEP_FOR = 60 * 86400


# Pages a real visitor is counted on. Anything else is "other", so the tally
# cannot be filled with paths a script makes up.
REAL_PAGES = {"/": "front", "/start": "start", "/find": "find",
              "/playbook": "playbook", "/numbers": "numbers", "/login": "login",
              "/privacy": "privacy", "/terms": "terms", "/tools": "tools"}


def real_page(path: str) -> str:
    path = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if path in REAL_PAGES:
        return REAL_PAGES[path]
    for prefix, name in (("/answers", "answers"), ("/employers", "employers"),
                         ("/jobs", "jobs"),
                         ("/employer", "employers"), ("/tools/", "tools")):
        if path.startswith(prefix):
            return name
    return "other"


def allowed(name: str) -> bool:
    return name in EVENTS


def count(name: str, user_agent: str = "") -> bool:
    if not allowed(name):
        return False
    views.outcome(name, user_agent=user_agent)
    return True


# What a /start error was about, from the message the person was shown. The
# message is never stored; only which of these it was.
_ERROR_KINDS = (
    ("email", "email"), ("phone", "phone"), ("least you would work", "pay"),
    ("salary", "pay"), ("hourly", "pay"), ("name in", "name"),
    ("job you are after", "role"), ("job title", "role"), ("the job", "role"),
    ("where you live", "place"), ("the place", "place"),
    ("last job", "last-job"), ("our end", "server"),
)


def error_kind(message: str) -> str:
    low = (message or "").lower()
    for needle, kind in _ERROR_KINDS:
        if needle in low:
            return kind
    return "other"


def save_reason(reason: str, text: str, page: str, user_agent: str = "") -> bool:
    if reason not in REASONS:
        return False
    count(f"why:{reason}", user_agent)
    text = " ".join((text or "").split())[:MAX_TEXT]
    if text and not views.looks_like_a_robot(user_agent):
        try:
            now = int(time.time())
            with connect() as c:
                c.execute("INSERT INTO feedback (at, page, reason, text) "
                          "VALUES (?, ?, ?, ?)", (now, page[:40], reason, text))
                # The privacy notice says 60 days, the same as the tallies.
                c.execute("DELETE FROM feedback WHERE at < ?",
                          (now - KEEP_FOR,))
        except Exception:
            pass
    return True


def comments(*, since: float, limit: int = 20) -> list[dict]:
    try:
        with connect() as c:
            rows = c.execute(
                "SELECT at, page, reason, text FROM feedback WHERE at >= ? "
                "ORDER BY at DESC LIMIT ?", (int(since), limit)).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def report(*, since: float) -> dict:
    """The funnel in order, the drop at each step, and what people said."""
    out = views.outcomes(since=since)
    people = views.totals(since=since).get("people", {})

    def n(name):
        return int(out.get(name, 0))

    taps = sum(n(f"land:cta:{c}") for c in CTAS)
    joined = n("start:in") + n("start:sent")
    steps = [
        ("Real people on the front page", n("real:front")),
        ("Scrolled to the proof (25%)", n("land:scroll:25")),
        ("Scrolled halfway", n("land:scroll:50")),
        ("Tapped a sign-up button", taps),
        ("Opened the sign-up page", n("real:start")),
        ("Started typing", n("start:typing")),
        ("Pressed Next", n("start:next")),
        ("Finished: account made or link sent", joined),
    ]
    funnel, prev = [], None
    for label, value in steps:
        kept = (round(100 * value / prev) if prev else None)
        funnel.append({"label": label, "n": value, "kept": kept})
        prev = value or prev
    real = {k.split(":", 1)[1]: v for k, v in out.items()
            if k.startswith("real:")}
    return {
        "funnel": funnel,
        # Real people per page, next to what the old counter saw, so the gap
        # (scrapers wearing a browser's name) stays visible.
        "real": dict(sorted(real.items(), key=lambda x: -x[1])),
        "raw_front": int(people.get("/", 0)),
        "ctas": {c: n(f"land:cta:{c}") for c in CTAS if n(f"land:cta:{c}")},
        "left": {k.split(":", 2)[2]: v for k, v in out.items()
                 if k.startswith("start:left:")},
        "errors": {k.split(":", 2)[2]: v for k, v in out.items()
                   if k.startswith("start:error:")},
        "why": [(REASONS[r], n(f"why:{r}")) for r in REASONS if n(f"why:{r}")],
        "asked": n("why:shown"),
        "dismissed": n("why:dismissed"),
        "comments": comments(since=since),
    }
