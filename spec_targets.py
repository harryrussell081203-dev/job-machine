"""
Grow the speculative target list from employers the machine has already seen.

WHY
---
A speculative note goes to a firm that is not advertising, on the grounds that
most technician hiring in this market never reaches a job board at all. The
reactive route is capped by reality: Adzuna and Reed between them yield a
median of about four in-trade Aberdeen listings a day, and no amount of
automation raises that, because the adverts do not exist.

The speculative route has no such ceiling - but it had a different one. The
target list was thirty companies, typed out by hand, and at two notes a day it
was exhausted in a fortnight.

Meanwhile the state file had been quietly accumulating the answer. Every
company that has ever advertised a role Harry's own scorer rated as in-trade
is, by definition, an employer of his trade in his market. That is a target
list built from evidence rather than from my guesses about who operates in
Aberdeen, it costs nothing, and it grows every single run as a by-product of
work the machine is already doing.

WHAT IS DELIBERATELY EXCLUDED
-----------------------------
Recruitment agencies. A speculative "you don't seem to be advertising
anything" note is nonsense to a firm whose entire business is advertising
things, and sending it would mark Harry as someone who does not understand who
he is writing to. Agencies are a real channel and a good one - they are just a
different letter, which the pipeline already has, and a different rule (a few
approaches over time rather than one, ever).

Also excluded: anything already contacted, already a target, or already
written to speculatively. One approach per company, ever, same as everywhere.

    python spec_targets.py            # show what would be added
    python spec_targets.py --write    # add them to data/targets.json
"""
import argparse
import json
import sys

import job_machine as jm

# How well a listing had to score for its employer to count as evidence. The
# scorer judges the ROLE against Harry's profile, so a company that posted
# something it rated highly is a company posting his kind of work. Set at the
# send threshold rather than lower: a firm whose only in-trade advert scraped a
# 60 is weaker evidence than one that posted an 85.
EVIDENCE_SCORE = jm.env_int("SPEC_EVIDENCE_SCORE", 70)

# Statuses that are themselves evidence, whatever the score says. A listing
# that got as far as an application or a parked portal was judged worth
# applying for by the whole pipeline, not just by the scorer.
EVIDENCE_STATUSES = ("portal_manual", "portal_review", "portal_submitted",
                     "ready", "sent", "spec_sent", "replied")


# Names that are not a company you can write a letter to. Every one of these
# came out of the real data: a training provider selling a course is not an
# employer, and 'Fisher House, PO Box 4' is a scraped address that ended up in
# the company field.
NOT_AN_EMPLOYER = jm.re.compile(
    r"\bpo box\b|\bp\.o\. box\b|^\W*$|"
    r"training|academy|\bcollege\b|apprenticeship|bootcamp|\bcourse\b|"
    r"confidential|undisclosed|various|unknown|^client$",
    jm.re.I)


def looks_like_an_employer(company):
    """Is this a name a letter can sensibly be addressed to?"""
    company = (company or "").strip()
    if len(company) < 3:
        return False
    return not NOT_AN_EMPLOYER.search(company)


def evidence(job):
    """Does this listing prove its employer hires Harry's trade?"""
    company = (job.get("company") or "").strip()
    if not company or not looks_like_an_employer(company):
        return False
    if is_agency_row(job):
        return False
    if job.get("status") in EVIDENCE_STATUSES:
        return True
    return (job.get("score") or 0) >= EVIDENCE_SCORE


def is_agency_row(job):
    """A recruiter, not an employer.

    Checked on the job as the pipeline stores it, so both tests apply: the
    company name, and the phrases agencies are legally required to put in the
    advert ('acting as an employment agency', 'our client')."""
    return jm.is_agency(job)


def note_for(company, jobs):
    """One line on what this firm does, taken from its own adverts.

    Never invented. The speculative letter says what the company does as a
    reason for writing to them, and a made-up line there is worse than none -
    it tells the reader immediately that this went to a hundred people."""
    titles = []
    for job in jobs:
        title = (job.get("title") or "").strip()
        if title and title.lower() not in [t.lower() for t in titles]:
            titles.append(title)
    if not titles:
        return ""
    shown = ", ".join(titles[:3])
    return f"has advertised in Aberdeen for {shown}"


def known_already(state, existing):
    """Everything that must not be added again, from every register we keep."""
    seen = {jm.company_key(t.get("company")) for t in existing}
    seen |= set(state.get("companies_contacted", {}))
    seen |= set(state.get("spec_done", {}))
    return {key for key in seen if key}


def candidates(state, existing):
    """[(company, note)] for employers worth a speculative note, best first."""
    known = known_already(state, existing)
    by_company = {}
    for job in state.get("jobs", {}).values():
        if not evidence(job):
            continue
        company = job["company"].strip()
        key = jm.company_key(company)
        if not key or key in known:
            continue
        by_company.setdefault(key, {"company": company, "jobs": []})
        by_company[key]["jobs"].append(job)

    out = []
    for entry in by_company.values():
        note = note_for(entry["company"], entry["jobs"])
        if not note:
            continue
        best = max((j.get("score") or 0) for j in entry["jobs"])
        out.append((best, len(entry["jobs"]), entry["company"], note))
    # the firm with the strongest advert first, then the one that advertises
    # most often - both are reasons to think there is work there
    out.sort(key=lambda row: (-row[0], -row[1], row[2].lower()))
    return [{"company": c, "note": n} for _, _, c, n in out]


def run(write=False):
    state = jm.load()
    try:
        with open(jm.TARGETS_PATH) as f:
            data = json.load(f)
    except OSError:
        print(f"[targets] cannot read {jm.TARGETS_PATH}")
        return 1
    existing = data.get("targets", [])
    found = candidates(state, existing)

    print(f"[targets] {len(existing)} curated, {len(found)} new employer(s) "
          f"with evidence they hire this trade")
    for entry in found[:40]:
        print(f"   {entry['company'][:40]:42} {entry['note'][:60]}")
    if not found:
        return 0
    if not write:
        print("\n[targets] nothing written, pass --write")
        return 0
    data["targets"] = existing + found
    with open(jm.TARGETS_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"\n[targets] data/targets.json now lists {len(data['targets'])}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="add them to data/targets.json")
    return run(write=ap.parse_args(argv).write)


if __name__ == "__main__":
    sys.exit(main())
