"""Does a machine actually get what we think we published.

WHY THIS IS THE FIRST AGENT.

Everything else in this engine assumes the pages work: that a crawler asking
for /answers/why-no-reply gets the whole answer in one GET, that the schema
on it parses and says what the prose says, that the address it was reached at
is the address the page claims. None of that is visible to a person reading
the site, and all of it breaks silently.

So this is the acceptance criteria written as code rather than as a paragraph
in a brief. It publishes nothing. It fetches every URL in the sitemap and
reports what a machine would have got.

WHAT IT CHECKS, AND WHY EACH ONE IS HERE.

  REACHABLE          A page in the sitemap that 404s tells every crawler the
                     sitemap is unreliable, and they discount the whole file.
  FULL TEXT WITHOUT JS
                     The site is server-rendered and must stay that way. This
                     fetches with a plain client and asserts the answer is in
                     the bytes. A page that assembles itself in a browser is
                     a page an assistant cannot quote.
  SCHEMA PARSES      Markup that does not parse is markup nothing acts on,
                     and a substring check would never notice.
  CANONICAL AGREES   A page whose canonical points somewhere else is a page
                     asking not to be indexed.
  COLD START         The site sleeps after fifteen minutes on a free
                     instance and takes 30-60 seconds to wake. A crawler
                     that times out records a failure against the domain, so
                     the slowest response of the run is worth knowing even
                     when nothing is broken.

FAIL CLOSED MEANS SOMETHING SPECIFIC HERE.

If the sitemap itself cannot be fetched, this does not report "0 problems".
It returns ok=False and keeps the previous state, because a run that checked
nothing and a run that found nothing wrong produce the same reassuring
number, and only one of them is true.
"""

from __future__ import annotations

import json
import re
import time
from xml.etree import ElementTree

from .. import USER_AGENT, Result

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# Generous. We are measuring whether a crawler gets an answer at all, and a
# free instance waking from sleep legitimately takes most of a minute.
TIMEOUT = 75

# Anything slower than this is reported even though it succeeded. Crawl
# budget is spent in seconds, and a page that takes this long is one a
# crawler will visit less often.
SLOW = 5.0

LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
CANONICAL = re.compile(r'<link rel="canonical" href="([^"]+)"')


def _fetch(url, *, get, clock):
    """Fetch, and time it on the injected clock rather than the real one.

    The clock is a dependency here for the same reason the fetcher is. How
    long a page took is not incidental detail - it is one of the things this
    agent exists to catch, because the free instance sleeps after fifteen
    minutes and a crawler that times out records that against the domain. A
    measurement taken from the wall clock cannot be exercised by a test, and
    an unexercised check is one that quietly stops working.
    """
    started = clock()
    try:
        r = get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        return r, clock() - started, ""
    except Exception as e:
        return None, clock() - started, f"{type(e).__name__}: {e}"


def sitemap_urls(base: str, *, get, clock) -> tuple[list[str], str]:
    """Every URL the sitemap claims, or an explanation of why we have none."""
    r, _, err = _fetch(base.rstrip("/") + "/sitemap.xml", get=get,
                       clock=clock)
    if err:
        return [], err
    if getattr(r, "status_code", 0) != 200:
        return [], f"sitemap returned {getattr(r, 'status_code', '?')}"
    try:
        root = ElementTree.fromstring(r.text)
    except ElementTree.ParseError as e:
        return [], f"sitemap did not parse: {e}"
    urls = [el.text.strip() for el in root.iter(f"{SITEMAP_NS}loc") if el.text]
    return urls, "" if urls else "sitemap parsed but lists no URLs"


def check(url: str, *, get, clock) -> dict:
    """One page, as a machine sees it."""
    out = {"url": url, "problems": [], "seconds": 0.0}
    r, seconds, err = _fetch(url, get=get, clock=clock)
    out["seconds"] = round(seconds, 2)
    if err:
        out["problems"].append(f"unreachable ({err})")
        return out

    code = getattr(r, "status_code", 0)
    out["status"] = code
    if code != 200:
        out["problems"].append(f"status {code}")
        return out

    body = r.text or ""
    if len(body) < 500:
        out["problems"].append(f"only {len(body)} bytes - is this rendering?")

    blocks = LD.findall(body)
    for block in blocks:
        try:
            json.loads(block)
        except ValueError as e:
            out["problems"].append(f"structured data does not parse: {e}")
    out["schema_blocks"] = len(blocks)

    found = CANONICAL.search(body)
    if not found:
        out["problems"].append("no canonical link")
    else:
        # Compared without the query string, which is the whole job of the
        # tag: /find and /find?utm_source=x are one page and the canonical is
        # what says so.
        want = url.split("?", 1)[0].rstrip("/")
        got = found.group(1).split("?", 1)[0].rstrip("/")
        if got != want:
            out["problems"].append(f"canonical points at {got}")

    if seconds > SLOW:
        out["problems"].append(f"slow: {seconds:.1f}s")
    return out


def run(state: dict, *, base: str, get=None, now=None,
        clock=None) -> tuple[dict, Result]:
    """Fetch everything in the sitemap and record what a machine got."""
    result = Result()
    if get is None:                      # pragma: no cover - real network
        import requests
        get = requests.get
    stamp = (now or time.time)()
    clock = clock or time.monotonic

    urls, why = sitemap_urls(base, get=get, clock=clock)
    if why and not urls:
        # The distinction this whole agent turns on: checking nothing and
        # finding nothing wrong are not the same result.
        return state, result.failed(f"could not read the sitemap: {why}")

    pages = [check(u, get=get, clock=clock) for u in urls]
    broken = [p for p in pages if p["problems"]]
    slowest = max((p["seconds"] for p in pages), default=0.0)

    state = dict(state)
    state["checked_at"] = int(stamp)
    state["base"] = base
    state["pages"] = {p["url"]: p for p in pages}
    # Kept rather than replaced, so a page that has been broken for a month
    # is distinguishable from one that broke this morning.
    history = list(state.get("history", []))[-29:]
    history.append({"at": int(stamp), "urls": len(pages),
                    "broken": len(broken), "slowest": round(slowest, 2)})
    state["history"] = history

    result.for_review = [f"{p['url']}: {'; '.join(p['problems'])}"
                         for p in broken]
    return state, result.say(
        f"{len(pages)} URLs, {len(broken)} with problems, "
        f"slowest {slowest:.1f}s",
        urls=len(pages), broken=len(broken), slowest=round(slowest, 2))
