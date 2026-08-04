"""
Build the Armed Forces Covenant signatory list from the official register.

WHY THIS IS A TOOL AND NOT A HAND-WRITTEN FILE
----------------------------------------------
Many Covenant signatories run a guaranteed interview scheme: a veteran who
meets the minimum criteria for a role is interviewed. Harry served two years as
a Royal Navy Communications and Information Specialist, so every employer on
that register is a company where his service moves him from the pile to the
shortlist. It is the only route in this project that produces an interview by
policy rather than by persuasion, which makes the list the single most valuable
piece of data here.

A list I typed out by hand would be a guess, would be short, and would rot. The
Ministry of Defence publishes the real thing as an A-Z of pledges on GOV.UK, so
this reads it and writes data/veteran_employers.json from what is actually
there.

    python tools/covenant_list.py --check          # what would change, write nothing
    python tools/covenant_list.py --write          # update the data file

Run it from GitHub Actions - push a branch named fire-covenant/anything. The
register is not reachable from every network, and the runner has open access.

WHAT IT DOES NOT DO
-------------------
It records that a company signed the Covenant. It does not record, claim, or
imply that any of them runs a guaranteed interview scheme - that varies by
employer and is theirs to state. The email only ever asks.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import job_machine as jm  # noqa: E402

# GOV.UK's own search index. Everything below goes through this rather than
# through URLs built from a template, because the template was wrong: the
# first version asked for
#   /publications/businesses-who-have-signed-...-beginning-with-a
# and got nothing back for almost every letter. The A page is really at
#   /publications/armed-forces-corporate-covenant-signed-pledges
# The register's slugs are simply not consistent, and an --inspect run proved
# the guessed ones return no content at all - not from a blocked network, from
# a GitHub runner with open access. Ask the index where the pages are instead.
SEARCH_API = "https://www.gov.uk/api/search.json"
BUSINESS_PREFIX = "/armed-forces-covenant-businesses/"
SEARCH_TERMS = "businesses who have signed the armed forces covenant"
PAGE = 100          # the search API's maximum page size
MAX_RESULTS = 8000  # a stop, so a change at the far end cannot loop forever
# Pledge pages are one request each. The shortlist is ordered Scotland-first,
# so a cap costs the least useful names rather than an arbitrary slice.
PLEDGE_PAGE_CAP = 600

BUSINESS_LINK = re.compile(
    r'href="(/armed-forces-covenant-businesses/[a-z0-9-]+)"[^>]*>(.*?)</a>',
    re.I | re.S)
TIMEOUT = 25

# Harry's trades and the places he can work. A signatory who makes biscuits in
# Kent is real, but it is not a lead.
RELEVANT = re.compile(
    r"electr|electronic|instrument|engineer|technician|subsea|offshore|marine|"
    r"energy|oil|gas|renewab|wind|power|utilit|telecom|communication|radio|"
    r"network|defence|defense|aerospace|maritime|naval|survey|calibrat|"
    r"maintenance|facilities|rail|water|nuclear|manufactur|fabricat|"
    r"technolog|systems|controls|automation", re.I)
SCOTLAND = re.compile(
    r"aberdeen|dundee|edinburgh|glasgow|inverness|scotland|scottish|fife|"
    r"perth|stirling|montrose|peterhead|fraserburgh|highland|grampian|"
    r"lanarkshire|ayrshire|renfrew|falkirk|livingston|dunfermline", re.I)


def fetch(url, session=None):
    """The page's text, or "".

    Says which of the two it was and why. The old version returned "" for a
    404, a 403 and a refused connection alike, so a run that quietly fetched
    nothing at all looked exactly like a run that found an empty register."""
    try:
        r = (session or requests).get(url, headers=jm.UA, timeout=TIMEOUT,
                                      allow_redirects=True)
        if r.status_code != 200:
            print(f"  ! {r.status_code} {url[:80]}")
            return ""
        return r.text
    except Exception as e:
        print(f"  ! {url[:70]}: {type(e).__name__}")
        return ""


def api_search(session=None, **params):
    text = fetch(f"{SEARCH_API}?{urllib.parse.urlencode(params)}", session)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print("  ! search API did not return JSON")
        return {}


def register_pages(session=None):
    """The real URLs of the A-Z register pages, as GOV.UK itself lists them."""
    data = api_search(session, q=SEARCH_TERMS, count=PAGE,
                      fields="title,link")
    pages, seen = [], set()
    for result in data.get("results", []):
        link = result.get("link") or ""
        title = (result.get("title") or "").lower()
        if "covenant" not in title or "signed" not in title:
            continue
        url = link if link.startswith("http") else f"https://www.gov.uk{link}"
        if url not in seen:
            seen.add(url)
            pages.append(url)
    return pages


def business_document_type(session=None):
    """What GOV.UK calls a covenant-business page in its own search index.

    Discovered rather than hard-coded, so a rename upstream shows up as a
    smaller list instead of a wrong one."""
    data = api_search(session, q="armed forces covenant business", count=PAGE,
                      fields="link,format,document_type")
    counts = {}
    for result in data.get("results", []):
        if (result.get("link") or "").startswith(BUSINESS_PREFIX):
            kind = result.get("document_type") or result.get("format")
            if kind:
                counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def signatories_via_api(session=None):
    """Every business the search index holds, paged through directly."""
    kind = business_document_type(session)
    if not kind:
        return {}
    print(f"[covenant] search index calls these '{kind}'")
    found, start = {}, 0
    while start < MAX_RESULTS:
        data = api_search(session, filter_document_type=kind, count=PAGE,
                          start=start, fields="title,link", order="title")
        results = data.get("results", [])
        if not results:
            break
        for result in results:
            link = result.get("link") or ""
            if link.startswith(BUSINESS_PREFIX):
                name = (result.get("title") or "").strip()
                if name:
                    found.setdefault(link.rsplit("/", 1)[-1], name)
        start += len(results)
        if len(results) < PAGE:
            break
    return found


def signatories_via_pages(session=None):
    """The same businesses, read off the A-Z pages instead of the index."""
    pages = register_pages(session)
    print(f"[covenant] {len(pages)} register page(s) found by search")
    found = {}
    if not pages:
        return found
    with ThreadPoolExecutor(max_workers=6) as pool:
        for html in pool.map(lambda u: fetch(u, session), pages):
            for path, name in BUSINESS_LINK.findall(html or ""):
                clean = jm.strip_html(name).strip()
                if clean:
                    found.setdefault(path.rsplit("/", 1)[-1], clean)
    return found


def signatories(session=None):
    """{slug: company name} for every business on the register.

    Two routes, unioned. They fail for different reasons - the index route to
    a renamed document type, the page route to a restructured publication - so
    running both is the difference between a short list and no list."""
    found = signatories_via_api(session)
    print(f"[covenant] {len(found)} from the search index")
    from_pages = signatories_via_pages(session)
    print(f"[covenant] {len(from_pages)} from the register pages")
    for slug, name in from_pages.items():
        found.setdefault(slug, name)
    return found


def pledge_text(slug, session=None):
    """The company's own pledge page, which is where any location appears."""
    return jm.strip_html(fetch(
        f"https://www.gov.uk/armed-forces-covenant-businesses/{slug}", session))


def worth_keeping(name, pledge):
    """Is this a company Harry could plausibly work for?

    The trade is the test. Geography used to be a second, harder test - a
    signatory had to look Scottish as well - and that was written when the
    whole search was a commute from Aberdeen. It is not: he will take
    rotational, offshore and fly-in fly-out work anywhere that comes with the
    arrangements to live it, so screening out a Covenant employer for being
    in Great Yarmouth throws away the exact thing this list exists to find.

    Generous on the trade for the same reason it always was: a wrong name here
    only means an employer gets asked a question they can answer with 'no',
    while a missing one is a guaranteed interview never asked for."""
    return bool(RELEVANT.search(f"{name} {pledge}"))


def nearness(name, pledge):
    """Scotland first, then everyone else. Not a filter - an order."""
    return 0 if SCOTLAND.search(f"{name} {pledge}") else 1


def build(limit=None, session=None):
    print("[covenant] reading the register")
    everyone = signatories(session)
    print(f"[covenant] {len(everyone)} signatories on the register")
    if not everyone:
        print("[covenant] nothing came back - the register was unreachable")
        return []

    # Only companies whose NAME already looks like Harry's trade get their
    # pledge page read; the rest would be thousands of requests for nothing.
    shortlist = [(slug, name) for slug, name in everyone.items()
                 if RELEVANT.search(name) or SCOTLAND.search(name)]
    shortlist.sort(key=lambda s: (nearness(s[1], ""), s[1].lower()))
    shortlist = shortlist[:limit or PLEDGE_PAGE_CAP]
    print(f"[covenant] reading {len(shortlist)} pledge page(s)")

    keep = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        pledges = list(pool.map(lambda s: pledge_text(s[0], session), shortlist))
    for (slug, name), pledge in zip(shortlist, pledges):
        if worth_keeping(name, pledge):
            where = SCOTLAND.search(f"{name} {pledge}")
            keep.append({"company": name, "award": "signatory",
                         "note": "Covenant signatory" + (
                             f", {where.group(0).lower()}" if where else ""),
                         "source": f"https://www.gov.uk/armed-forces-covenant-"
                                   f"businesses/{slug}"})
    keep.sort(key=lambda e: (nearness(e["company"], e["note"]),
                             e["company"].lower()))
    print(f"[covenant] {len(keep)} in Harry's trade")
    return keep


def merge(found, path=None):
    """Add to the file without losing what is already curated there."""
    path = path or jm.VETERAN_PATH
    with open(path) as f:
        data = json.load(f)
    existing = data.get("employers", [])
    known = {jm.company_key(e.get("company")) for e in existing}
    added = [e for e in found if jm.company_key(e["company"]) not in known]
    data["employers"] = existing + added
    data["employers"].sort(key=lambda e: (e.get("company") or "").lower())
    return data, added


def inspect(url=None):
    """Report what the register really looks like from a machine that can see it.

    GOV.UK answers 403 to the network this was written on, so every question
    about its structure costs a push and a runner. The first --inspect answered
    exactly one of them - 'is this URL real' - and the answer, no, left the
    next question unasked. So this one asks all of them in a single run: does
    the search API respond, what does it call these pages, how many businesses
    can be paged out of it, where are the A-Z pages really, and does scraping
    one still find names."""
    if url:
        html = fetch(url)
        print(f"[inspect] {url} -> {len(html)} characters, "
              f"{len(BUSINESS_LINK.findall(html))} business link(s)")
        return 0 if html else 1

    print("[inspect] --- the search API ---")
    probe = api_search(q=SEARCH_TERMS, count=5, fields="title,link")
    print(f"[inspect] {probe.get('total', 'no')} result(s) for the register query")
    for result in probe.get("results", [])[:5]:
        print(f"    {result.get('title', '')[:58]:60} {result.get('link', '')}")

    print("[inspect] --- what the index calls a business page ---")
    kind = business_document_type()
    print(f"[inspect] document_type: {kind!r}")
    if kind:
        page = api_search(filter_document_type=kind, count=5,
                          fields="title,link", order="title")
        print(f"[inspect] {page.get('total', 'unknown')} business page(s) in total")
        for result in page.get("results", [])[:5]:
            print(f"    {result.get('title', '')[:58]:60} {result.get('link', '')}")

    print("[inspect] --- the A-Z pages ---")
    pages = register_pages()
    for url in pages[:12]:
        print(f"    {url}")
    if pages:
        html = fetch(pages[0])
        found = BUSINESS_LINK.findall(html)
        print(f"[inspect] first page: {len(html)} chars, {len(found)} business links")
        for path, name in found[:5]:
            print(f"    {path}  {jm.strip_html(name).strip()[:50]}")
        others = re.findall(r'href="([^"]+)"', html)
        attachments = [u for u in others if re.search(
            r"assets\.publishing|\.csv|\.ods|\.xlsx|attachment", u, re.I)]
        print(f"[inspect] {len(attachments)} attachment link(s):")
        for u in list(dict.fromkeys(attachments))[:10]:
            print(f"    {u[:110]}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inspect", nargs="?", const=True, metavar="URL",
                    help="print what an index page really contains and stop")
    ap.add_argument("--write", action="store_true", help="update the data file")
    ap.add_argument("--check", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--limit", type=int, help="only read this many pledge pages")
    args = ap.parse_args(argv)
    if args.inspect:
        return inspect(None if args.inspect is True else args.inspect)

    found = build(limit=args.limit)
    if not found:
        return 1
    data, added = merge(found)
    print(f"\n[covenant] {len(added)} new employer(s):")
    for entry in added:
        print(f"   {entry['company'][:52]:54} {entry['note']}")
    if args.write:
        with open(jm.VETERAN_PATH, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        print(f"\n[covenant] data/veteran_employers.json now lists "
              f"{len(data['employers'])} employers")
    else:
        print("\n[covenant] --check only, nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
