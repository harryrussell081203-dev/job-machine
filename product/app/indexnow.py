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

TWO THINGS THIS HAS TO GET RIGHT.

**Not resubmitting the same list forever.** This runs on a free instance that
sleeps after fifteen minutes and cold-starts on the next request, so "submit
on startup" would mean submitting the whole site several times a day. Repeated
identical submissions are what gets a key ignored. So the URL list plus the
date the figures last changed is fingerprinted, the fingerprint is stored, and
a submission only happens when it differs - which is on a deploy that adds
pages, or a day the published numbers move.

**Never delaying a page.** The submission is a network call to somebody else's
server from a process whose job is serving a page. It is wrapped, it has a
short timeout, and every failure is swallowed. A search engine not hearing
about a page today is worth nothing next to a visitor waiting on a cold start.
"""

from __future__ import annotations

import hashlib

from . import config, track_record

ENDPOINT = "https://api.indexnow.org/indexnow"

# Where the last submitted fingerprint lives, in site_meta.
MEMORY_KEY = "indexnow_fingerprint"

TIMEOUT = 8


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
        "keyLocation": f"{config.BASE_URL}/{key_filename()}",
        "urlList": sorted(urls),
    }


def submit(urls, *, post=None) -> bool:
    """POST the list. Returns whether it was accepted, and never raises.

    `post` is injected so the tests never touch the network, and so a caller
    that wants to see what would be sent can pass a recorder.
    """
    if not available() or not urls:
        return False
    if post is None:
        import requests
        post = requests.post
    try:
        r = post(ENDPOINT, json=payload(urls), timeout=TIMEOUT)
        # 200 accepted, 202 accepted but the key is still being checked.
        return getattr(r, "status_code", 0) in (200, 202)
    except Exception:
        return False


def submit_if_changed(urls, *, get_meta, set_meta, post=None) -> str:
    """Submit only when the list or the figures have moved since last time.

    Returns a short word describing what happened, for a log line: "sent",
    "unchanged", "off" or "failed". The accessors are passed in rather than
    imported so this module stays testable without a database.
    """
    if not available():
        return "off"
    urls = list(urls)
    mark = fingerprint(urls, track_record.updated_on())
    if get_meta(MEMORY_KEY) == mark:
        return "unchanged"
    if not submit(urls, post=post):
        return "failed"
    # Written only after acceptance. A failed submission that recorded its
    # fingerprint would never be retried, which is the one way this quietly
    # stops working and nothing looks wrong.
    set_meta(MEMORY_KEY, mark)
    return "sent"
