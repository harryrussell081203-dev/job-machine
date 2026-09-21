"""Find the organisations that hold jobseekers in bulk.

WHY ORGANISATIONS AND NOT PEOPLE.

The product needs users and the obvious idea is the wrong one: scrape
jobseekers and mail them. That is spam, it is the user's own legal exposure
under PECR, and it is the exact thing this product refuses to do - the whole
differentiator is that it never writes to an address a human did not publish
for the purpose. Marketing it by breaking its own rule would not be a
compromise, it would make the claim untrue.

The alternative is gatekeepers. A council employability team, a college
careers service, a job club or an ex-forces employment charity each has
hundreds of jobseekers already on a mailing list, and spends its working day
looking for free things to hand them. One letter to one of those is worth more
than a thousand scraped addresses, and it is a letter they are glad to get.

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT.

It ingests public registers - the Scottish Charity Register, the Charity
Commission extract, published lists of councils and colleges - and decides
which rows are plausibly in the business of helping unemployed people into
work. That decision is the hard part and it is all this file does.

It does NOT find email addresses. That is outreach.py's job and it uses the
same discovery the rest of this project uses: read the organisation's own
website, take a real published address or take nothing. No pattern is ever
guessed here or anywhere downstream of here.

A NOTE ON THE DOWNLOAD URLS, WHICH IS A REAL CAVEAT AND NOT A DISCLAIMER.

The sandbox this was written in has no general web egress - every request to
oscr.org.uk, the Charity Commission and gov.uk was refused by the gateway - so
the register URLs below could not be fetched, and their exact column headings
could not be confirmed. Two consequences, both handled rather than hoped away:

  - Columns are matched by a set of accepted aliases, not by one exact name,
    and a source whose headings match none of them raises rather than
    silently producing zero organisations. A scraper that quietly finds
    nothing looks identical to a register with nothing in it.
  - SOURCES is configuration, not fact. Confirm each URL on the first real
    run. It is intended to run from GitHub Actions, where the network works,
    the same place the rest of this project's fetching happens.

    python -m tools.find_orgs --from-file oscr.csv --dry-run
    python -m tools.find_orgs --source oscr --out data/orgs.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(HERE)

# Configuration, not fact - see the note above. Each entry is the page a
# human should check first and the file the machine should pull.
SOURCES = {
    "oscr": {
        "about": "https://www.oscr.org.uk/about-charities/search-the-register/"
                 "charity-register-download/",
        "url": os.environ.get("OSCR_REGISTER_URL", ""),
        "format": "csv",
    },
    "ccew": {
        "about": "https://register-of-charities.charitycommission.gov.uk/"
                 "en/register/full-register-download",
        "url": os.environ.get("CCEW_REGISTER_URL", ""),
        "format": "csv",
    },
}

# Header aliases. Registers disagree about what to call the same column and
# they rename them between releases, so every field accepts several spellings
# and a source matching none of them is an error rather than an empty result.
FIELDS = {
    "name": ("charity name", "charityname", "name", "organisation name",
             "registered name", "charity_name"),
    "website": ("website", "web address", "url", "charity_company_website",
                "web"),
    "postcode": ("postcode", "post code", "charity_postcode", "postal code"),
    "purpose": ("objectives", "activities", "charitable purposes", "purposes",
                "charity_activities", "objects"),
}

# What the organisation has to be about. Two lists rather than one score,
# because "employment" appearing anywhere is far too loose - an employment
# LAW charity and a youth employability service are not the same target.
WANTED = (
    "employability", "employment support", "back to work", "into work",
    "job club", "jobclub", "careers service", "careers guidance",
    "careers advice", "job search", "jobseeker", "job seeker",
    "unemployed", "unemployment", "out of work", "worklessness",
    "training and employment", "skills and employment", "work experience",
    "cv writing", "interview skills", "apprenticeship",
    "employment advice", "return to work", "labour market",
)

# Present in the objectives and the row is out, whatever else it says. These
# are organisations whose people are not looking for a job this week, or whose
# "employment" is somebody else's.
UNWANTED = (
    "employment law", "employment tribunal", "employment rights",
    "sheltered employment", "employer's liability",
    "animal", "church building", "cathedral", "village hall",
    "scout group", "guide group", "sports club", "football club",
    "grave", "cemetery", "allotment",
)

# Nothing to write to and nothing to read. A row with no website cannot have
# an address found for it, so it is not a target no matter how good it sounds.
_URL = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(/|$)", re.I)


class SourceError(Exception):
    """The register did not look like the register."""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def pick_columns(headers) -> dict:
    """Map our field names onto this file's actual headings.

    Raises rather than returning a partial map. Half a mapping produces rows
    with no name or no website, which the filter then discards, which looks
    exactly like a register containing nothing relevant.
    """
    lowered = {(h or "").strip().lower(): h for h in headers}
    found = {}
    for field, aliases in FIELDS.items():
        for alias in aliases:
            if alias in lowered:
                found[field] = lowered[alias]
                break
    for required in ("name", "website"):
        if required not in found:
            raise SourceError(
                f"no column for {required!r} in {sorted(lowered)!r}. "
                "The register has probably renamed it; add the new spelling "
                "to FIELDS rather than loosening the check.")
    return found


def tidy_website(value: str) -> str:
    value = (value or "").strip()
    if not value or not _URL.match(value):
        return ""
    if not value.lower().startswith("http"):
        value = "https://" + value
    return value.rstrip("/")


def serves_jobseekers(purpose: str, name: str = "") -> bool:
    """Is this organisation plausibly in the business of getting people work.

    Deliberately conservative. A false positive costs a real letter to a
    charity that never asked for one, and their time belongs to the people
    they exist for. A false negative costs nothing but a name on a list.
    """
    text = _norm(f"{name} {purpose}")
    if any(bad in text for bad in UNWANTED):
        return False
    return any(good in text for good in WANTED)


def rows_from_csv(text: str):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise SourceError("the file has no header row")
    cols = pick_columns(reader.fieldnames)
    for row in reader:
        yield {field: (row.get(column) or "").strip()
               for field, column in cols.items()}


def select(rows, limit: int | None = None) -> list[dict]:
    """The organisations worth writing to, deduplicated by website.

    Deduplicated by site rather than by name because the registers carry the
    same body under several registrations, and two letters to one inbox is
    the failure mode this whole approach is supposed to avoid.
    """
    seen, out = set(), []
    for row in rows:
        site = tidy_website(row.get("website", ""))
        if not site:
            continue
        if not serves_jobseekers(row.get("purpose", ""), row.get("name", "")):
            continue
        key = re.sub(r"^www\.", "", site.split("//", 1)[-1].split("/", 1)[0].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": row.get("name", "").strip(),
                    "website": site,
                    "postcode": row.get("postcode", "").strip()})
        if limit and len(out) >= limit:
            break
    return out


def fetch(url: str, opener=urllib.request.urlopen) -> str:
    if not url:
        raise SourceError(
            "no URL configured for this source. Set the environment variable "
            "named in SOURCES, or pass --from-file. The register addresses "
            "could not be confirmed from the sandbox this was written in.")
    with opener(url, timeout=120) as response:
        return response.read().decode("utf-8", "replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=sorted(SOURCES))
    ap.add_argument("--from-file", help="a register already downloaded")
    ap.add_argument("--out", default=os.path.join(PRODUCT, "data", "orgs.json"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written, write nothing")
    args = ap.parse_args(argv)

    if args.from_file:
        text = open(args.from_file, encoding="utf-8", errors="replace").read()
    elif args.source:
        text = fetch(SOURCES[args.source]["url"])
    else:
        ap.error("give --source or --from-file")

    orgs = select(rows_from_csv(text), limit=args.limit)
    print(f"{len(orgs)} organisations")
    for org in orgs[:10]:
        print(f"  {org['name']}  {org['website']}")
    if len(orgs) > 10:
        print(f"  ... and {len(orgs) - 10} more")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(orgs, f, indent=1, ensure_ascii=False)
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
