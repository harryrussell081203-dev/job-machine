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
import re
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


# ======================================================================
# THE COVENANT SIGNATORIES
# ======================================================================
# The register in data/veteran_employers.json is used reactively: when a job
# happens to come up at a signatory, the letter mentions the Covenant. That
# leaves the best thing about the list on the table entirely, because it waits
# for the employer to advertise.
#
# Many signatories run a guaranteed interview scheme - a veteran who meets the
# minimum criteria for a role gets interviewed. That is the only route in this
# project that produces an interview by POLICY rather than by persuasion, and
# there is no reason to wait for an advert before asking about it.
#
# WHAT THE LETTER MAY AND MAY NOT SAY. That a company signed the Covenant is a
# published fact and may be stated. That they run a guaranteed interview
# scheme is NOT - it varies by employer and is theirs to say. So the note
# records only the signing, and the letter asks the question.


def covenant_note(entry):
    """Why this firm is being written to, in terms that are true of them.

    Deliberately does not claim a scheme. 'has signed the Armed Forces
    Covenant' is on GOV.UK with their name against it; 'runs a guaranteed
    interview scheme' would be a claim about an employer's internal policy
    made on their behalf, to their face, by someone asking them for a job."""
    where = (entry.get("note") or "").strip()
    base = "has signed the Armed Forces Covenant"
    return f"{base}, {where}" if where and where != "Covenant signatory" else base


# Where he can physically attend an interview tomorrow. Not a filter - he will
# take rotational work anywhere - but a guaranteed interview in Aberdeen is
# worth more than one in Portsmouth, so it goes first.
NEAR_HOME = re.compile(
    r"aberdeen|dundee|edinburgh|glasgow|inverness|scotland|scottish|fife|"
    r"stirling|montrose|peterhead|fraserburgh|highland|grampian|"
    r"lanarkshire|ayrshire|renfrew|falkirk|livingston|dunfermline", re.I)


def covenant_rank(entry):
    """Best first: near home, then the ones we know something about.

    Sorting on the name alone put '(TMFL) Titan Facilities Management' and
    '2CL Communications' above Aberdeen City Council and BAE Systems, purely
    because of the bracket and the digit. Alphabetical order is not a
    judgement about anything."""
    text = f"{entry.get('company') or ''} {entry.get('note') or ''}"
    near = 0 if NEAR_HOME.search(text) else 1
    # A note that says something about the firm means the register carried a
    # real pledge, which is both a better letter and a sign of a real employer.
    described = 0 if (entry.get("note") or "").strip() not in (
        "", "Covenant signatory") else 1
    return (near, described, (entry.get("company") or "").lower())


def covenant_candidates(state, existing):
    """[(company, note)] for signatories worth a speculative letter.

    Scotland first, because a guaranteed interview he can physically attend
    beats one in Portsmouth - but not filtered on it, because he will take
    rotational and offshore work anywhere that comes with the arrangements."""
    known = known_already(state, existing)
    try:
        with open(jm.VETERAN_PATH) as f:
            entries = json.load(f).get("employers", [])
    except Exception as e:
        print(f"[spec] no Covenant register to read ({e})")
        return []
    out = []
    for entry in entries:
        company = (entry.get("company") or "").strip()
        key = jm.company_key(company)
        if not company or not key or key in known:
            continue
        if not looks_like_an_employer(company):
            continue
        # A recruiter is a different letter entirely - see the note at the top
        # of this file. looks_like_an_employer() only checks the SHAPE of the
        # name; whether the firm is an agency is jm.is_agency's question, and
        # signatories include Morson and Hays.
        if jm.is_agency({"company": company}):
            continue
        out.append((covenant_rank(entry), company, covenant_note(entry)))
    out.sort(key=lambda row: row[0])
    print(f"[spec] {len(out)} Covenant signatory(s) not yet written to")
    return [{"company": c, "note": n, "covenant": True} for _, c, n in out]


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
    # Covenant signatories FIRST. Every other target is a firm that might read
    # the letter; a signatory has published a commitment to people exactly
    # like him, and where a scheme exists he meets it by having served.
    #
    # Composed here rather than inside candidates(), which stays a pure
    # function of the state file - it is the thing worth unit-testing, and
    # having it read the register off disk made it untestable and broke two
    # tests that were right to complain.
    found = covenant_candidates(state, existing) + candidates(state, existing)

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
