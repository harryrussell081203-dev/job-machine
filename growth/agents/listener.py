"""Watch UK job-hunting forums for threads where this product might help.

WHAT IT DOES, AND WHAT IT DOES NOT.

Reads a small allowlist of subreddits and public forums, scores each new
thread against a keyword list, and keeps the ones above the threshold in a
pending queue. Once a week - Friday at 18:00 UK time - it emails the queue
to Harry as a list of direct links. He reads each thread and replies by
hand, from his own account, where he has something genuinely useful to say.

It does not draft replies. It does not post. It does not take credentials
for the sites it reads. The output is links, and the responsibility to say
anything on them is a human's. If a reply-drafting or posting function
appears in this module, a test fails loudly.

WHY THE WEEK IS BATCHED.

Replies that arrive in a thread hours after the question are the ones that
look like a person reading it, rather than a tool watching for a keyword to
pounce on. The forums on the allowlist each discount accounts that behave
otherwise, and a Friday-evening digest is both a natural human cadence and
the schedule most likely to leave Saturday mornings for the few replies
worth sending.

WHY THE USER-AGENT IS NAMED AND POINTS AT A PAGE.

Every outbound request carries RecruitedGrowthBot/1.0 with a link to
/about-the-crawler, which is the contract this engine makes with the sites
it reads: say who you are and why, and accept any opt-out they publish.
That is what keeps us welcome rather than merely tolerated. A test fails
if any outbound request leaves without it.

FAIL CLOSED, PER SOURCE.

One subreddit timing out does not blank the queue. The run keeps the state
it was given, skips that source, and records why. The failure mode this
prevents is "listener found 0 threads this week" meaning "listener could
not reach anything this week" - two run shapes with the same cheerful
output, and only one of them is true.
"""

from __future__ import annotations

import json
import re
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from .. import USER_AGENT, Result

UK = ZoneInfo("Europe/London")

# The window the email check accepts, around 18:00 UK on a Friday. Wider than
# the cron trigger because GitHub drops crons and runs them late; narrower
# than a day because a Monday-morning digest about last Tuesday is not what
# anyone meant by "weekly".
EMAIL_WEEKDAY = 4         # Monday is 0.
EMAIL_HOUR_START = 17     # 17:00 UK.
EMAIL_HOUR_END = 20       # until 20:00 UK, inclusive-exclusive.

# Politeness between fetches to one site. Lower than a human but recognisable
# as one; the subs allow this and nothing on the allowlist is latency-bound.
POLITENESS_DELAY = 2.0

# Threshold. Below this a thread is seen-and-dropped rather than queued, so
# the queue is reviewable in one sitting.
MIN_RELEVANCE = 0.3

# The allowlist. Edit this file, not the agent, to add a source: and before
# adding one, read its self-promotion rules and set a conservative listing
# (`new` rather than `hot`, limit <= 25). The responsibility this agent
# does not have - deciding where it is welcome - lives in this list.
DEFAULT_SOURCES = [
    {"kind": "reddit", "subreddit": "UKJobs",        "listing": "new", "limit": 25},
    {"kind": "reddit", "subreddit": "jobs",          "listing": "new", "limit": 25},
    {"kind": "reddit", "subreddit": "GradJobsUK",    "listing": "new", "limit": 25},
    {"kind": "reddit", "subreddit": "recruitinghell", "listing": "new", "limit": 25},
    {"kind": "reddit", "subreddit": "cscareerquestionsuk", "listing": "new", "limit": 25},
]

# Keywords, graded. HIGH is the specific vocabulary of someone stuck in the
# problem this product solves. MEDIUM is the general vocabulary of job
# hunting. EXCLUDE is a hard veto: anything matching here is dropped even
# with ten high-signal matches, because these threads are the wrong fit
# regardless of what else they say.
DEFAULT_KEYWORDS = {
    "high": [
        "no replies", "no response", "ghosted", "ats black hole",
        "cold email", "cover letter help", "hiring manager",
        "application rejected", "100 applications", "200 applications",
        "cant get a job", "can't get a job", "no callbacks",
        "cv feedback", "resume feedback",
    ],
    "medium": [
        "job hunt", "job search", "unemployed", "graduate scheme",
        "entry level", "stuck in the ats", "redundancy",
    ],
    "exclude": [
        "visa", "sponsorship", "ir35", "contract rate", "salary negotiation",
        "legal advice", "tribunal", "discrimination", "harassment",
    ],
}


def run(state: dict, *, base: str = None, get=None, now=None, clock=None,
        sleep=None, mailer=None, sources=None,
        keywords=None) -> "tuple[dict, Result]":
    """Sweep the allowlist, maybe email the digest, return the new state."""
    result = Result()

    if get is None:                     # pragma: no cover - real network
        import requests
        get = requests.get
    now = now or (lambda: int(time.time()))
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    sources = sources or DEFAULT_SOURCES
    keywords = keywords or DEFAULT_KEYWORDS
    mailer = mailer or _mailer_from_env()

    stamp = now()
    state = dict(state)
    state.setdefault("seen_urls", [])
    state.setdefault("pending_queue", [])
    state.setdefault("last_email_at", 0)
    state.setdefault("source_errors", {})

    # Set of seen URLs for O(1) membership. The list on disk stays a list -
    # it is a readable history, not a lookup table.
    seen = set(state["seen_urls"])
    source_errors = {}

    found = 0
    for source in sources:
        if source.get("kind") != "reddit":
            # Only subreddits for now. A new kind is a new fetcher and new
            # tests, not a config flag.
            continue
        try:
            posts = _fetch_reddit(source, get=get, sleep=sleep)
        except Exception as e:
            source_errors[source["subreddit"]] = f"{type(e).__name__}: {e}"
            continue

        for post in posts:
            if post["url"] in seen:
                continue
            seen.add(post["url"])
            state["seen_urls"].append(post["url"])

            score, reasoning = _score(post, keywords)
            if score < MIN_RELEVANCE:
                continue

            state["pending_queue"].append({
                "source": f"r/{source['subreddit']}",
                "url": post["url"],
                "thread_title": post["title"],
                "thread_excerpt": (post["excerpt"] or "")[:500],
                "relevance": score,
                "relevance_reasoning": reasoning,
                "discovered_at": _iso(stamp),
            })
            found += 1

    state["source_errors"] = source_errors
    state["last_sweep_at"] = stamp

    # Keep seen_urls bounded. It is a seen-set, not an archive; the pending
    # queue is the record of what to act on. 10,000 is a few months of
    # Reddit listings even on the busier subs.
    if len(state["seen_urls"]) > 10_000:
        state["seen_urls"] = state["seen_urls"][-10_000:]

    emailed = False
    if _in_email_window(stamp, state) and state["pending_queue"]:
        body = _render_digest(state["pending_queue"])
        sent = mailer(subject=_subject(state["pending_queue"]), body=body)
        if sent:
            state["last_email_at"] = stamp
            state["pending_queue"] = []
            emailed = True
        else:
            result.for_review.append(
                "digest ready to send but no mailer configured "
                "(set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, "
                "LISTENER_TO)")

    # Fail closed only when every source failed. One flaky sub is a Tuesday;
    # all of them dark at once is damage we should not paper over.
    if sources and len(source_errors) == len([s for s in sources
                                              if s.get("kind") == "reddit"]):
        return state, result.failed(
            f"every source failed: {sorted(source_errors)}")

    for sub, why in sorted(source_errors.items()):
        result.for_review.append(f"r/{sub}: {why}")

    return state, result.say(
        f"found {found}, queue {len(state['pending_queue'])}, "
        f"emailed={emailed}",
        found=found, queued=len(state["pending_queue"]),
        emailed=1 if emailed else 0,
        source_errors=len(source_errors))


def _fetch_reddit(source: dict, *, get, sleep) -> list:
    """Fetch a subreddit listing as JSON, politely.

    Reddit serves .json for every listing with no key required and asks only
    that we identify ourselves and keep volume low. Both are rules this
    agent follows - the UA is the engine's, and one listing per source per
    sweep is the only shape in which this is called.
    """
    sleep(POLITENESS_DELAY)
    url = (f"https://www.reddit.com/r/{source['subreddit']}/"
           f"{source.get('listing', 'new')}.json"
           f"?limit={int(source.get('limit', 25))}")
    r = get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    code = getattr(r, "status_code", 0)
    if code != 200:
        raise RuntimeError(f"status {code}")
    data = r.json() if callable(getattr(r, "json", None)) else json.loads(r.text)
    out = []
    for child in (data.get("data", {}) or {}).get("children", []) or []:
        d = child.get("data", {}) or {}
        if d.get("stickied") or d.get("over_18"):
            # Stickied posts are mod-only; NSFW threads are off-topic and
            # also raise the chance of a bad excerpt in an email.
            continue
        permalink = d.get("permalink") or ""
        if not permalink:
            continue
        out.append({
            "title": d.get("title") or "",
            "excerpt": d.get("selftext") or "",
            "url": f"https://www.reddit.com{permalink}",
        })
    return out


def _score(post: dict, keywords: dict) -> "tuple[float, str]":
    """Grade a thread. Returns (score 0..1, one-line reasoning).

    Deterministic, keyword-based, by design. A model-scored queue is one
    that disagrees with itself across runs and makes a reviewer argue with
    a black box instead of reading the thread.
    """
    text = f"{post.get('title', '')} {post.get('excerpt', '')}".lower()
    for term in keywords.get("exclude", []):
        if term.lower() in text:
            return 0.0, f"excluded by '{term}'"
    high = [k for k in keywords.get("high", []) if k.lower() in text]
    med = [k for k in keywords.get("medium", []) if k.lower() in text]
    score = min(1.0, 0.4 * len(high) + 0.15 * len(med))
    parts = []
    if high:
        parts.append(f"high: {', '.join(high)}")
    if med:
        parts.append(f"medium: {', '.join(med)}")
    return round(score, 2), "; ".join(parts) or "no keyword matches"


def _in_email_window(stamp: int, state: dict) -> bool:
    """True when it is Friday 17:00-20:00 UK and the last email was > 6 days ago.

    A window rather than a point because GitHub crons slip by minutes to
    hours; the >6 day gate stops a slipped Friday cron and the next week's
    on-time cron from both firing in the same week.
    """
    uk = datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(UK)
    if uk.weekday() != EMAIL_WEEKDAY:
        return False
    if not (EMAIL_HOUR_START <= uk.hour < EMAIL_HOUR_END):
        return False
    last = state.get("last_email_at", 0) or 0
    if last and (stamp - last) < 6 * 24 * 3600:
        return False
    return True


def _render_digest(queue: list) -> str:
    """Plain text, sorted by relevance. No reply drafts.

    The one thing this body deliberately omits is a suggested reply. The
    point of the digest is handing a thread to a human who will read it
    before deciding whether they have something to say.
    """
    queue = sorted(queue, key=lambda e: (-float(e.get("relevance", 0.0)),
                                         e.get("source", "")))
    lines = [
        f"Listener digest: {len(queue)} thread(s) from this week.",
        "",
        "These are threads matched by keyword, not drafted replies. Read each",
        "one in full before saying anything, reply as yourself from your own",
        "account, and only where you have something specific and useful. Most",
        "weeks the right answer is to reply to one or two and skip the rest.",
        "",
    ]
    for e in queue:
        lines.append(f"[{e.get('relevance', 0):>4}] {e.get('source', '?')} "
                     f"- {e.get('thread_title', '')}")
        lines.append(f"  {e.get('url', '')}")
        if e.get("relevance_reasoning"):
            lines.append(f"  match: {e['relevance_reasoning']}")
        excerpt = (e.get("thread_excerpt") or "").strip().replace("\n", " ")
        if excerpt:
            lines.append(f"  {excerpt[:240]}")
        lines.append("")
    return "\n".join(lines)


def _subject(queue: list) -> str:
    n = len(queue)
    return (f"Recruited listener: {n} thread{'s' if n != 1 else ''} "
            f"to look at")


def _iso(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()


def _mailer_from_env():
    """SMTP mailer from env vars, or a no-op that returns False.

    Returns False rather than raising when unconfigured, so a sweep that
    only wants to collect threads works without any credentials in the
    environment. The caller surfaces the unconfigured case through
    for_review so it is visible in CI logs.
    """
    import os
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("LISTENER_TO")
    frm = os.environ.get("LISTENER_FROM", user)
    if not (host and port and user and pw and to):
        return lambda subject, body: False

    def send(subject: str, body: str) -> bool:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = frm
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(host, int(port)) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(msg)
        return True

    return send
