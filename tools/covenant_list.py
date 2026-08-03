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
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import job_machine as jm  # noqa: E402

INDEX = ("https://www.gov.uk/government/publications/"
         "businesses-who-have-signed-the-armed-forces-covenant-company-names-"
         "beginning-with-{letter}")
# The register's own naming is not consistent: numbers and the tail of the
# alphabet sit under differently-worded pages.
EXTRA_INDEXES = (
    "https://www.gov.uk/government/publications/"
    "armed-forces-corporate-covenant-signed-pledges",
    "https://www.gov.uk/government/publications/"
    "armed-forces-corporate-covenant-signed-pledges-part-2",
    "https://www.gov.uk/government/publications/"
    "armed-forces-corporate-covenant-signed-pledges-part-3",
    "https://www.gov.uk/government/publications/"
    "search-for-businesses-who-have-signed-the-armed-forces-covenant",
)

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
    try:
        r = (session or requests).get(url, headers=jm.UA, timeout=TIMEOUT,
                                      allow_redirects=True)
        return r.text if r.status_code == 200 else ""
    except Exception as e:
        print(f"  ! {url[:70]}: {type(e).__name__}")
        return ""


def index_pages():
    return [INDEX.format(letter=chr(c)) for c in range(ord("a"), ord("z") + 1)] \
        + list(EXTRA_INDEXES)


def signatories(session=None):
    """{slug: company name} for every business on the register."""
    found = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for html in pool.map(lambda u: fetch(u, session), index_pages()):
            for path, name in BUSINESS_LINK.findall(html or ""):
                slug = path.rsplit("/", 1)[-1]
                clean = jm.strip_html(name).strip()
                if clean and slug not in found:
                    found[slug] = clean
    return found


def pledge_text(slug, session=None):
    """The company's own pledge page, which is where any location appears."""
    return jm.strip_html(fetch(
        f"https://www.gov.uk/armed-forces-covenant-businesses/{slug}", session))


def worth_keeping(name, pledge):
    """Is this a company Harry could plausibly work for, in a place he could
    plausibly reach?

    Kept deliberately generous on the trade and strict on the geography: a
    wrong name here only means an employer gets asked a question they can
    answer with 'no', while a missing one is a guaranteed interview never
    asked for."""
    blob = f"{name} {pledge}"
    return bool(SCOTLAND.search(blob)) and bool(RELEVANT.search(blob))


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
    if limit:
        shortlist = shortlist[:limit]
    print(f"[covenant] reading {len(shortlist)} pledge page(s)")

    keep = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        pledges = list(pool.map(lambda s: pledge_text(s[0], session), shortlist))
    for (slug, name), pledge in zip(shortlist, pledges):
        if worth_keeping(name, pledge):
            where = SCOTLAND.search(f"{name} {pledge}")
            keep.append({"company": name, "award": "signatory",
                         "note": f"Covenant signatory, {where.group(0).lower()}",
                         "source": f"https://www.gov.uk/armed-forces-covenant-"
                                   f"businesses/{slug}"})
    keep.sort(key=lambda e: e["company"].lower())
    print(f"[covenant] {len(keep)} in Harry's trade and reach")
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="update the data file")
    ap.add_argument("--check", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--limit", type=int, help="only read this many pledge pages")
    args = ap.parse_args(argv)

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
