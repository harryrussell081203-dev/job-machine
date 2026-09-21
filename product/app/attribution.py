"""Where somebody came from, remembered from the first page they landed on.

WHY THIS CANNOT BE READ AT SIGN-UP.

Signing in here is a link in an email. Somebody arrives on the landing page
from a TikTok bio, types their address, goes to their inbox, and taps a link
that arrives at /auth/verify carrying no referrer, no campaign and no
referral code - because it came from their mail client, not from TikTok.

So reading the source where the account is made reports every user as
"direct", including the ones a channel worked for. The source has to be
captured when they first arrive and carried forward.

FIRST TOUCH, NOT LAST.

The cookie is written once and never overwritten while it lives. If somebody
arrives from a TikTok video, reads for a week, and comes back through a
Google search to sign up, the answer to "what got this user" is TikTok - the
search was them looking for something they already knew about. Last-touch
attribution would credit Google and quietly tell us to stop making videos.

WHAT IS STORED, AND WHAT IS DELIBERATELY NOT.

Four short strings: utm_source, utm_campaign, ref (a referral code) and the
path they landed on. No identifier, no address, nothing that outlives the
account it is attached to, and nothing a second site could read. It is
first-party, it exists to answer "which channel is worth the effort", and
that is all it can answer.

The page-view counter in views.py stays as it is - no cookie, no marker of
any kind. This one is a cookie and so it is described in the privacy notice,
which is the price of asking the question at all.

EVERYTHING HERE IS UNTRUSTED INPUT.

These arrive in a URL anyone can write, and they end up in a database and on
an admin screen. So each is cut to a length, filtered to a charset that
cannot be mistaken for markup, and lower-cased so "TikTok" and "tiktok" are
one row in the report rather than two.
"""

from __future__ import annotations

import json
import re

# Short on purpose. A campaign name is a label, and anything longer than this
# is either a mistake or somebody trying their luck.
MAX = 60

# A path is longer than a label and is worth keeping whole, because "which
# page do sign-ups start on" is one of the few questions that changes what
# gets built next.
MAX_PATH = 120

COOKIE = "src"

# A month. Long enough to cover the read-then-return-later case this exists
# for, short enough that it is not a durable marker.
MAX_AGE = 30 * 24 * 3600

# Letters, digits and the punctuation campaign names really use. Everything
# else goes, which makes the value safe to print without depending on the
# template escaping it - the defence that stops being true the moment
# somebody writes this into a CSV or a log line instead.
SAFE = re.compile(r"[^a-z0-9._/+-]+")

FIELDS = ("utm_source", "utm_campaign", "ref", "landing")


def clean(value: str, limit: int = MAX) -> str:
    return SAFE.sub("", (value or "").strip().lower())[:limit]


def from_query(params) -> dict:
    """The source in a URL, or an empty dict if it carries none.

    `ref` doubles as the referral code, which is why it is read here rather
    than in a module of its own: a referral link IS a source, and counting it
    twice in two places is how two numbers that should agree stop agreeing.
    """
    found = {
        "utm_source": clean(params.get("utm_source", "")),
        "utm_campaign": clean(params.get("utm_campaign", "")),
        "ref": clean(params.get("ref", "")),
    }
    return found if any(found.values()) else {}


def read(request) -> dict:
    """What we already decided this visitor's source was, if anything."""
    raw = request.cookies.get(COOKIE) or ""
    if not raw:
        return {}
    try:
        got = json.loads(raw)
    except ValueError:
        # A malformed cookie is not worth an error page. It is worth being
        # treated as absent rather than as an empty source, so the next page
        # with a campaign on it can set a real one.
        return {}
    if not isinstance(got, dict):
        return {}
    out = {k: clean(got.get(k, ""), MAX_PATH if k == "landing" else MAX)
           for k in FIELDS}
    return out if any(out.values()) else {}


def stamp(request, response, path: str = "") -> dict:
    """Record the source on first touch. Returns what is now remembered.

    Does nothing at all when the visitor already has one, which is the whole
    of the first-touch rule, and nothing when this page carries no campaign -
    a cookie saying "direct" would lock in an answer that a later page might
    have improved on.
    """
    already = read(request)
    if already:
        return already
    found = from_query(request.query_params)
    if not found:
        return {}
    found["landing"] = clean(path or request.url.path, MAX_PATH)
    response.set_cookie(
        COOKIE, json.dumps(found, separators=(",", ":")),
        max_age=MAX_AGE, httponly=True, samesite="lax")
    return found


def describe(source: dict) -> str:
    """One label for a report. 'direct' when we genuinely do not know.

    A referral is named as one rather than folded into utm_source, because
    "users we were given by other users" is the number the referral programme
    lives or dies on and it must not be able to hide inside a campaign.
    """
    if not source:
        return "direct"
    if source.get("ref"):
        return "referral"
    return source.get("utm_source") or "direct"
