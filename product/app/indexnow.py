"""Tell the search engines a page exists, instead of waiting to be found.

IndexNow is an open protocol: generate a key, serve it as a text file at the
site root, then POST a list of URLs. Bing, Yandex and Seznam accept it, share
submissions with each other, and typically index within hours rather than the
weeks an unprompted crawl takes. No account, no API key to register, no free
tier to run out of. It is the cheapest indexing lever that exists and this
site had none of it.

Google does not participate. That is fine - Google finds a sitemap on its own,
and the engines that do participate are the ones feeding several of the
assistants this site is written to be quoted by.

FOUR THINGS THIS HAS TO GET RIGHT.

**Not resubmitting the same list forever.** This runs on a free instance that
sleeps after fifteen minutes and cold-starts on the next request, so "submit
on startup" would mean submitting the whole site several times a day. Repeated
identical submissions are what gets a key ignored. So the URL list plus the
date the figures last changed is fingerprinted, the fingerprint is stored, and
a submission only happens when it differs - which is on a deploy that adds
pages, or a day the published numbers move.

**Not retrying a refusal on every wake.** A refused list is not recorded as
sent, so it would be sent again on the next boot, and the next. From 16
October 2026 every submission failed, and the site sent its whole page list
again on every cold start, several times a night, for three weeks. A refusal
now waits a day before the next try.

**Saying why.** That failure logged one word, "failed", from the web app and
from the daily directory run alike, and the engine's answer was thrown away.
The answer (403: the key was not accepted; 429: too many submissions; and so
on) is now in the log line and in site_meta under "<memory key>:last", where
the weekly check can read it without the hosting logs.

**Never delaying a page.** The submission is a network call to somebody else's
server from a process whose job is serving a page. It is wrapped, it has a
short timeout, and every failure is swallowed. A search engine not hearing
about a page today is worth nothing next to a visitor waiting on a cold start.
The web app sends from a background thread once it is serving, never from
startup itself: the engine checks the key by fetching it from this site, and
a site still starting up cannot answer.
"""

from __future__ import annotations

import hashlib
import json
import time

from . import config, track_record

ENDPOINT = "https://api.indexnow.org/indexnow"

# Where the last submitted fingerprint lives, in site_meta. What happened on
# the last try lives beside it, under this plus LAST.
MEMORY_KEY = "indexnow_fingerprint"
LAST = ":last"

TIMEOUT = 8

# Reading our own key file before sending. Long enough for a sleeping free
# instance to wake, which is also the point: the engine's own check of the
# file then finds the site awake.
CHECK_TIMEOUT = 45

# After a refusal, how long before the next try.
RETRY_AFTER = 24 * 3600

# What the protocol's refusals mean, in words for the log.
REFUSALS = {
    400: "the request was malformed",
    403: "the key was not accepted (the engine could not read the key file, "
         "or the file did not hold the key)",
    422: "an address is not on this site, or the key is the wrong shape",
    429: "too many submissions",
}


def available() -> bool:
    """A key, somewhere to serve it from, and not a development instance.

    The DEV check is not tidiness, it is the difference between a test suite
    that is self-contained and one that posts to somebody else's server. The
    tests configure a key and a real-looking BASE_URL, and without this the
    startup hook would submit those URLs to IndexNow from CI, with a key that
    does not resolve - which is both rude and the fastest way to get the real
    key distrusted.

    Unset key: the feature is simply absent, rather than submitting with a
    key no engine can verify.
    """
    return bool(config.INDEXNOW_KEY and config.BASE_URL and not config.DEV)


def key_filename() -> str:
    """The protocol wants the key served as <key>.txt at the site root."""
    return f"{config.INDEXNOW_KEY}.txt"


def key_location() -> str:
    return f"{config.BASE_URL}/{key_filename()}"


def fingerprint(urls, stamp: str) -> str:
    """What was submitted, and when the figures behind it last moved.

    The date is in here deliberately. Without it, a site whose page list is
    stable would submit once and never again - but the answer pages carry
    live figures, and a page whose numbers changed is a page worth
    re-crawling even though its address did not.
    """
    payload = "\n".join(sorted(urls)) + "|" + (stamp or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def payload(urls) -> dict:
    host = config.BASE_URL.split("//", 1)[-1].split("/", 1)[0]
    return {
        "host": host,
        "key": config.INDEXNOW_KEY,
        "keyLocation": key_location(),
        "urlList": sorted(urls),
    }


def key_problem(*, get=None) -> str:
    """Empty when the key file, fetched from outside like the engine fetches
    it, answers with the key. Otherwise what is wrong with it.

    Sending a key the engine cannot read is how a key gets rejected, so a
    file that does not answer means no submission."""
    if get is None:
        import requests
        get = requests.get
    try:
        r = get(key_location(), timeout=CHECK_TIMEOUT)
    except Exception as exc:
        return f"the key file did not answer ({type(exc).__name__})"
    code = getattr(r, "status_code", 0)
    if code != 200:
        return f"the key file answered {code}"
    if str(getattr(r, "text", "") or "").strip() != config.INDEXNOW_KEY:
        return "the key file does not hold the key"
    return ""


def send(urls, *, post=None) -> tuple[bool, str]:
    """POST the list. Returns (accepted, what the engine said), and never
    raises.

    `post` is injected so the tests never touch the network, and so a caller
    that wants to see what would be sent can pass a recorder.
    """
    if post is None:
        import requests
        post = requests.post
    try:
        r = post(ENDPOINT, json=payload(urls), timeout=TIMEOUT,
                 headers={"Content-Type": "application/json; charset=utf-8"})
    except Exception as exc:
        return False, f"no answer ({type(exc).__name__})"
    code = getattr(r, "status_code", 0)
    # 200 accepted, 202 accepted but the key is still being checked.
    if code in (200, 202):
        return True, str(code)
    said = " ".join(str(getattr(r, "text", "") or "").split())[:160]
    return False, (f"{code} {REFUSALS.get(code, 'refused')}"
                   + (f" - {said}" if said else ""))


def submit(urls, *, post=None) -> bool:
    """POST the list. Returns whether it was accepted, and never raises."""
    if not available() or not urls:
        return False
    return send(urls, post=post)[0]


def last_try(get_meta, memory_key: str = MEMORY_KEY) -> dict:
    """What happened the last time this list was tried: {"at", "ok",
    "said", "urls"}, or {} before the first try."""
    try:
        data = json.loads(get_meta(memory_key + LAST) or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def submit_if_changed(urls, *, get_meta, set_meta, post=None, get=None,
                      memory_key: str = MEMORY_KEY, now=None) -> str:
    """Submit only when the list or the figures have moved since last time,
    and not within a day of a refusal.

    Returns a short phrase describing what happened, for a log line: "sent",
    "unchanged", "off", "waiting (...)" or "failed: <why>". The accessors
    are passed in rather than imported so this module stays testable without
    a database.
    """
    if not available():
        return "off"
    urls = list(urls)
    if not urls:
        return "unchanged"
    mark = fingerprint(urls, track_record.updated_on())
    if get_meta(memory_key) == mark:
        return "unchanged"
    now = time.time() if now is None else now
    last = last_try(get_meta, memory_key)
    if last and not last.get("ok") and now - float(last.get("at") or 0) < RETRY_AFTER:
        return f"waiting (refused at the last try: {last.get('said', '')})"
    problem = key_problem(get=get)
    accepted, said = (False, problem) if problem else send(urls, post=post)
    set_meta(memory_key + LAST, json.dumps(
        {"at": int(now), "ok": accepted, "said": said, "urls": len(urls)}))
    if not accepted:
        return f"failed: {said}"
    # Written only after acceptance. A failed submission that recorded its
    # fingerprint would never be retried, which is the one way this quietly
    # stops working and nothing looks wrong.
    set_meta(memory_key, mark)
    return "sent"
