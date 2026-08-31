"""
Reading the real advert instead of the board's stub.

THE NUMBER THAT MADE THIS NECESSARY
-----------------------------------
269 listings have scored 70 or better. 185 of them - 69% - are sitting at
no_email: judged worth going for, and never written to, because no address was
ever found for the employer.

They are not obscure companies. They are BES Group, Oceaneering, James Fisher
and Sons, Speedy Hire. The reason nothing could be found is upstream of the
address hunt entirely, and it is one line in the harvester:

    "url": j.get("redirect_url", "")

That is Adzuna's tracking link - www.adzuna.co.uk/jobs/details/5819685259 - and
it is a doorway, not a destination. Everything downstream treats it as the
advert: the domain hunt scrapes it, the ATS detector classifies it, the portal
agent navigates to it. All three are looking at Adzuna. 134 of the 185 have no
ATS detected at all, and 156 recorded a failed portal attempt whose reason
begins 'Page.goto: Navigation to "https://www.adzuna.co.uk/jo...'.

THE SECOND NUMBER, WHICH IS THE SAME BUG WEARING A DIFFERENT HAT
----------------------------------------------------------------
3,452 of 3,927 descriptions in the file end in an ellipsis. Adzuna's API caps
the description at 500 characters and Reed's search results at about 450. So
every score this system has ever produced was formed from the first paragraph
of an advert.

For most candidates that is merely lossy. For Harry it is close to fatal,
because the things that decide whether a job suits him - is it 2/2 or 3/3, is
it electrical or mechanical, does it demand tickets he has not got yet, is it
offshore at all - are almost never in the first paragraph. They are in the
requirements list, which is halfway down.

WHAT THIS DOES
--------------
    python listings.py                # what it would resolve
    python listings.py --resolve      # follow the links, keep the real advert
    python listings.py --rescue       # go after the no_email backlog

One HTTP GET per advert, following redirects, landing on the employer's own
page. From that one fetch come three things the machine has never had: the
full text to score on, the real domain to find an address at, and the real host
so the ATS detector has something true to classify.

WHAT IT REFUSES TO DO
---------------------
IT WILL NOT REPLACE A GOOD DESCRIPTION WITH A WORSE ONE. A redirect can land on
a cookie wall, a bot check, an expired-advert page or a plain 404, and all four
return 200 with a page full of words. So the new text has to be substantially
longer than what is already held AND not look like a block page, or it is
thrown away and the stub is kept. Losing a real advert to a cookie banner would
cost more than never having fetched it.

IT WILL NOT RETRY FOREVER. A failure is recorded with its reason. Boards go
down, adverts expire, and a run that spends its time on the same forty dead
links is a run that resolves nothing new.

IT NEVER CHANGES A JOB THAT HAS BEEN WRITTEN TO. Only listings still upstream
of an approach are touched, so nothing here can rewrite the record of what was
actually sent to somebody.
"""
import argparse
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

import job_machine as jm

RESOLVED = "resolved_at"
FAILED = "resolve_failed_at"
BOARD_URL = "board_url"

PER_RUN = jm.env_int("RESOLVE_PER_RUN", 60)
TIMEOUT = jm.env_int("RESOLVE_TIMEOUT", 20)
# A failed link is usually a dead advert, but boards do have bad afternoons.
RETRY_AFTER_DAYS = jm.env_int("RESOLVE_RETRY_DAYS", 7)

# The boards whose links are a doorway rather than the advert. Anything on one
# of these hosts is worth following even when the description looks complete,
# because the URL itself is the thing every downstream stage is misreading.
A_BOARD_STUB = re.compile(
    r"^https?://(www\.)?("
    r"adzuna\.co\.uk|adzuna\.com|"
    r"jobs\.adzuna|"
    r"reed\.co\.uk|"
    r"totaljobs\.com|cv-library\.co\.uk|jobsite\.co\.uk|s1jobs\.com"
    r")/", re.I)

# What a page says when it is not an advert. A redirect can land on any of
# these and every one of them returns 200 with plenty of text, which is exactly
# how a good description gets overwritten with a cookie banner.
NOT_AN_ADVERT = re.compile(
    r"enable javascript|are you a (human|robot)|verify you are|"
    r"access denied|403 forbidden|page not found|404 error|"
    r"this (job|vacancy|position) (has|is no longer)|no longer (available|"
    r"accepting)|expired|cloudflare|checking your browser|"
    r"accept (all )?cookies to continue", re.I)

# The advert has to be materially better than the stub to be worth keeping.
# Adzuna's cap is 500 characters, so anything under this is not a full advert
# either and swapping one truncated blurb for another gains nothing.
MIN_REAL_ADVERT = jm.env_int("RESOLVE_MIN_CHARS", 900)


def truncated(job):
    """Did the board's API hand us a blurb rather than the advert?"""
    text = (job.get("description") or "").rstrip()
    return text.endswith("…") or text.endswith("...") or len(text) < 520


def needs_resolving(job):
    if job.get(RESOLVED):
        return False
    url = job.get("url") or ""
    if not url:
        return False
    # Never rewrite the record of something already put in front of a person.
    if job.get("status") in ("sent", "spec_sent", "replied", "test_sent"):
        return False
    when = jm.parse_ts(job.get(FAILED))
    if when and (datetime.now(timezone.utc) - when).days < RETRY_AFTER_DAYS:
        return False
    return bool(A_BOARD_STUB.search(url)) or truncated(job)


def looks_like_an_advert(text):
    if len(text) < MIN_REAL_ADVERT:
        return False
    # Only the top of the page - a legitimate advert can mention cookies in a
    # footer, and rejecting it for that would throw away most of the internet.
    return not NOT_AN_ADVERT.search(text[:1500])


def fetch(job):
    """(final_url, text) for one advert, or (None, "").

    Reed first, because it has an API that returns the whole description and
    costs nothing. Everything else is one GET with redirects followed, which is
    the entire trick: the board's link is a doorway and requests walks through
    it for us."""
    if job.get("source") == "reed" and jm.REED_API_KEY:
        try:
            jid = str(job.get("external_id", "")).split("_", 1)[-1]
            r = requests.get(f"https://www.reed.co.uk/api/1.0/jobs/{jid}",
                             auth=(jm.REED_API_KEY, ""), headers=jm.UA,
                             timeout=TIMEOUT)
            if r.ok:
                body = r.json()
                text = jm.strip_html(body.get("jobDescription") or "")
                if text:
                    return body.get("jobUrl") or job.get("url"), text
        except Exception as e:
            print(f"[listings] reed api {job.get('external_id')}: {e}")
    try:
        r = requests.get(job["url"], headers=jm.UA, timeout=TIMEOUT,
                         allow_redirects=True)
        if not r.ok:
            return None, ""
        return r.url, jm.strip_html(r.text)
    except Exception as e:
        print(f"[listings] {job.get('company')}: {type(e).__name__}: {e}")
        return None, ""


def keep_the_better_text(job, text):
    """Replace the description only when the new one is genuinely the advert.

    Half again as long AND long enough to be a real advert. A redirect that
    lands on a bot check returns a page full of words, and swapping a true
    500-character blurb for a false 2,000-character one would make every score
    downstream worse while looking like an improvement."""
    old = job.get("description") or ""
    if not looks_like_an_advert(text):
        return False
    if len(text) < max(len(old) * 1.5, MIN_REAL_ADVERT):
        return False
    job["description"] = text[:8000]
    return True


def resolve_one(job):
    """True if this job learned something. Records the failure if not."""
    final, text = fetch(job)
    if not final and not text:
        job[FAILED] = jm.now()
        return False
    original = job.get("url") or ""
    if final and final != original:
        # Kept, not discarded: it is how the listing was found, and the board's
        # own page is sometimes the only thing still standing when an employer
        # takes their advert down.
        job.setdefault(BOARD_URL, original)
        job["url"] = final
    better = keep_the_better_text(job, text) if text else False
    job[RESOLVED] = jm.now()
    job.pop(FAILED, None)
    moved = bool(final and final != original)
    print(f"[listings] {(job.get('company') or '?')[:24]:24} "
          f"{'moved' if moved else 'same '} "
          f"{'+advert' if better else '       '} "
          f"{(job.get('url') or '')[:64]}")
    return moved or better


def candidates(state, backlog=False):
    jobs = [j for j in state.get("jobs", {}).values() if needs_resolving(j)]
    if backlog:
        # The prize: judged worth going for, and never written to because no
        # address was ever found at an Adzuna page.
        jobs = [j for j in jobs if j.get("status") == "no_email"]
    # Best first. A run that hits its cap should have spent it on the listings
    # most worth having.
    return sorted(jobs, key=lambda j: -(j.get("score") or 0))


def run(state, limit=None, backlog=False, dry_run=False):
    jobs = candidates(state, backlog=backlog)[:limit or PER_RUN]
    if not jobs:
        print("[listings] nothing to resolve")
        return 0
    if dry_run:
        for job in jobs:
            print(f"[listings] would resolve {(job.get('company') or '?')[:26]:26}"
                  f" {(job.get('url') or '')[:70]}")
        print(f"\n[listings] {len(jobs)} would be resolved")
        return 0
    learned = 0
    for job in jobs:
        if resolve_one(job):
            learned += 1
            if backlog:
                # Back into the queue. It failed at the address hunt against a
                # board page; now there is a real employer page to hunt on.
                job["status"] = "scored"
    print(f"[listings] {len(jobs)} fetched, {learned} learned something new")
    return learned


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read the real advert")
    ap.add_argument("--resolve", action="store_true",
                    help="actually fetch them")
    ap.add_argument("--rescue", action="store_true",
                    help="go after the no_email backlog and re-queue it")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    state = jm.load()
    learned = run(state, limit=args.limit, backlog=args.rescue,
                  dry_run=not (args.resolve or args.rescue))
    if learned:
        jm.save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
