"""Finding listings worth looking at, for one person's search.

Two boards, Adzuna and Reed, swept per location and per target role, then
collapsed to one row per opportunity and filtered before anything expensive
happens to it.

The port from the original changed one thing fundamentally. That machine
carried four hardcoded keyword sets and a list of title exclusions written for
one man's trade - "sales executive", "care assistant", "hgv" - which is
exactly right for him and actively wrong for anybody else. Excluding sales
roles is a bug if your user sells for a living.

So exclusions here come in two kinds:

  - **universal**: a training course dressed up as a vacancy is never a job,
    for anyone. Those stay hardcoded, because they are a property of the
    advert rather than of the reader.
  - **personal**: anything else is the user's own `exclude_titles`, empty by
    default. A filter nobody asked for is a filter that silently hides the
    job they wanted.

Freshness is the other rule worth keeping: nothing over 48 hours old. A
week-old advert has a shortlist already.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "Mozilla/5.0 (compatible; job-machine/1.0; +job search)"}

MAX_AGE_HOURS = 48
RESULTS_PER_PAGE = 50

TAG_RE = re.compile(r"<[^>]+>")

# Course adverts dressed up as vacancies. In the original's real queue these
# arrived five at a time - "Trainee Incident Response Engineer, job
# guarantee", "IT Technician, no experience needed!" - all from training
# providers selling a course, all scoring just under the bar because the trade
# words matched. Writing to the seller of one is a wasted approach for anyone.
NOT_A_VACANCY = re.compile(
    r"job guarantee|guaranteed job|no experience needed|"
    r"course fee|tuition|payment plan|enrol|bootcamp|"
    r"training (provider|programme|program|academy|course)|"
    r"funded training|traineeship|study (with|at) us|"
    r"once qualified we|after completing the course", re.I)


class HarvestError(RuntimeError):
    pass


@dataclass
class Credentials:
    """Board keys. Passed in rather than read from the environment so one
    process can run a harvest for several people without global state."""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    reed_api_key: str = ""

    def has_adzuna(self) -> bool:
        return bool(self.adzuna_app_id and self.adzuna_app_key)

    def has_reed(self) -> bool:
        return bool(self.reed_api_key)


@dataclass
class Listing:
    external_id: str
    source: str
    title: str
    company: str
    location: str
    url: str
    description: str
    search_location: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    posted_at: str | None = None
    skipped: str = ""          # why it was dropped, if it was
    raw_emails: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


# ----------------------------------------------------------------------
# text and time
# ----------------------------------------------------------------------
def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text or "")
    text = TAG_RE.sub(" ", text)
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&#39;", "'").replace("&quot;", '"')
                .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", text).strip()


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def reed_date(value):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def fresh_enough(posted, granularity: str = "hours",
                 max_age_hours: int = MAX_AGE_HOURS, now=None) -> bool:
    """True if a listing is recent enough to be worth an application."""
    if posted is None:
        return True      # the source gave no date; let the scorer judge it
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    if granularity == "hours":
        return posted >= cutoff
    # Date-only sources (Reed) give a day, not a time. A job dated D could
    # have gone up at 00:00 that day, so only accept D if the whole day sits
    # inside the window. At 48 hours that works out as today or yesterday.
    threshold = cutoff.date()
    if cutoff.time() != datetime.min.time():
        threshold += timedelta(days=1)
    return posted.date() >= threshold


# ----------------------------------------------------------------------
# what to search for
# ----------------------------------------------------------------------
def searches(profile):
    """(where, radius, keywords) for every sweep this run should make.

    The local sweep is every target role against every location the user
    named. When they have said they want paid travel or contract work, a
    second national sweep runs for those, because field-service and
    rotational work is advertised against a site or a vessel rather than a
    town, and a radius search around one city never sees it.
    """
    for location in profile.locations:
        for role in profile.target_roles:
            yield location, profile.radius_miles, role

    national = []
    if profile.wants_travel:
        national += [f"field service {r}" for r in profile.target_roles[:3]]
        national += ["commissioning engineer", "installation engineer"]
    if profile.wants_contract:
        national += [f"contract {r}" for r in profile.target_roles[:3]]
    for kw in national:
        yield "UK", 100, kw


# ----------------------------------------------------------------------
# filters
# ----------------------------------------------------------------------
def title_excluded(title: str, exclusions) -> str | None:
    low = f" {(title or '').lower()} "
    return next((x for x in exclusions if x.lower() in low), None)


def is_course_advert(title: str, description: str) -> bool:
    return bool(NOT_A_VACANCY.search(f"{title or ''} {(description or '')[:1500]}"))


def not_worth_applying(listing: Listing, exclusions=()) -> str | None:
    """A reason to bin this listing before it costs an AI call or a send."""
    hit = title_excluded(listing.title, exclusions)
    if hit:
        return f"title excluded ({hit})"
    if is_course_advert(listing.title, listing.description):
        return "a training course being sold, not a vacancy"
    if not listing.company.strip():
        return "no employer named"
    return None


def company_key(name: str) -> str:
    out = (name or "").lower().strip()
    for suffix in (" limited", " ltd.", " ltd", " plc", " llp", " inc.",
                   " inc", " group", " uk", " (uk)"):
        if out.endswith(suffix):
            out = out[: -len(suffix)]
    return "".join(ch for ch in out if ch.isalnum())


def dedupe_key(listing: Listing) -> str:
    """Same role at the same company from two boards is one opportunity."""
    title = re.sub(r"[^a-z0-9 ]", " ", (listing.title or "").lower())
    title = re.sub(r"\s+", " ", title).strip()
    return company_key(listing.company) + "|" + title


# ----------------------------------------------------------------------
# the boards
# ----------------------------------------------------------------------
def adzuna(profile, creds: Credentials, *, pages: int = 1, session=None,
           max_age_hours: int = MAX_AGE_HOURS) -> list[Listing]:
    if not creds.has_adzuna():
        return []
    import requests
    session = session or requests
    out: list[Listing] = []
    max_days = max(1, -(-max_age_hours // 24))

    for location, radius, kw in searches(profile):
        for page in range(1, pages + 1):
            try:
                r = session.get(
                    f"https://api.adzuna.com/v1/api/jobs/gb/search/{page}",
                    params={"app_id": creds.adzuna_app_id,
                            "app_key": creds.adzuna_app_key,
                            "results_per_page": RESULTS_PER_PAGE,
                            "what_or": kw, "where": location,
                            "distance": radius, "max_days_old": max_days,
                            "sort_by": "date",
                            "content-type": "application/json"},
                    headers=UA, timeout=30)
                r.raise_for_status()
                results = r.json().get("results", [])
            except Exception as exc:
                # One bad sweep must not lose the other forty.
                print(f"[harvest] adzuna {location} '{kw}' p{page}: {exc}")
                break

            for j in results:
                posted = parse_ts(j.get("created"))
                if not fresh_enough(posted, "hours", max_age_hours):
                    continue
                out.append(Listing(
                    external_id=f"adzuna_{j.get('id')}",
                    source="adzuna",
                    title=j.get("title") or "",
                    company=(j.get("company") or {}).get("display_name", "") or "",
                    location=(j.get("location") or {}).get("display_name", "") or "",
                    search_location=location,
                    url=j.get("redirect_url") or "",
                    description=strip_html(j.get("description") or "")[:4000],
                    salary_min=j.get("salary_min"), salary_max=j.get("salary_max"),
                    posted_at=posted.isoformat() if posted else None))

            if len(results) < RESULTS_PER_PAGE:
                break
    return out


def reed(profile, creds: Credentials, *, pages: int = 1, session=None,
         max_age_hours: int = MAX_AGE_HOURS) -> list[Listing]:
    if not creds.has_reed():
        return []
    import requests
    session = session or requests
    out: list[Listing] = []

    for location in profile.locations:
        for kw in profile.target_roles:
            for page in range(pages):
                try:
                    r = session.get(
                        "https://www.reed.co.uk/api/1.0/search",
                        auth=(creds.reed_api_key, ""),
                        params={"keywords": kw, "locationName": location,
                                "distanceFromLocation": profile.radius_miles,
                                "resultsToTake": RESULTS_PER_PAGE,
                                "resultsToSkip": page * RESULTS_PER_PAGE},
                        headers=UA, timeout=30)
                    r.raise_for_status()
                    results = r.json().get("results", [])
                except Exception as exc:
                    print(f"[harvest] reed {location} '{kw}' p{page}: {exc}")
                    break

                for j in results:
                    posted = reed_date(j.get("date"))
                    if not fresh_enough(posted, "date", max_age_hours):
                        continue
                    out.append(Listing(
                        external_id=f"reed_{j.get('jobId')}",
                        source="reed",
                        title=j.get("jobTitle") or "",
                        company=j.get("employerName") or "",
                        location=j.get("locationName") or "",
                        search_location=location,
                        url=j.get("jobUrl") or "",
                        description=strip_html(j.get("jobDescription") or "")[:4000],
                        salary_min=j.get("minimumSalary"),
                        salary_max=j.get("maximumSalary"),
                        posted_at=posted.isoformat() if posted else None))

                if len(results) < RESULTS_PER_PAGE:
                    break
    return out


# ----------------------------------------------------------------------
# the run
# ----------------------------------------------------------------------
def harvest(profile, creds: Credentials, *, pages: int = 1, session=None,
            known_ids=(), exclude_titles=(),
            max_age_hours: int = MAX_AGE_HOURS) -> dict:
    """Sweep both boards and return what is worth scoring.

    Returns {"keep": [...], "dropped": [...]} rather than only the winners,
    so the caller can show a user why a listing they expected never arrived.
    Silence is the worst possible answer to "why did nothing come through?".
    """
    found = (adzuna(profile, creds, pages=pages, session=session,
                    max_age_hours=max_age_hours)
             + reed(profile, creds, pages=pages, session=session,
                    max_age_hours=max_age_hours))

    known = set(known_ids)
    seen_keys: set[str] = set()
    keep: list[Listing] = []
    dropped: list[Listing] = []

    for listing in found:
        if listing.external_id in known:
            continue

        key = dedupe_key(listing)
        if key in seen_keys:
            # The same job on both boards. Not an error, and not worth
            # reporting to the user as a rejection.
            continue
        seen_keys.add(key)

        reason = not_worth_applying(listing, exclude_titles)
        if reason:
            listing.skipped = reason
            dropped.append(listing)
        else:
            keep.append(listing)

    # Freshest first: being an early applicant beats being a well-matched one.
    keep.sort(key=lambda l: l.posted_at or "", reverse=True)
    return {"keep": keep, "dropped": dropped}
