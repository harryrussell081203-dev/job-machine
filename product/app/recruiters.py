"""Which recruitment agencies are advertising your kind of work, right now.

A recruiter worth writing to is one already filling jobs like yours, in your
area, this month. That is exactly what the job boards show, so this reads
them: the same search the sweep runs for this person, over a wider window,
keeping only the adverts an agency placed (see jobseeker/agencies.py), and
grouping them by agency.

Each agency comes with the reason it is on the list ("advertising 4 jobs like
yours near Aberdeen in the last 14 days") and the adverts themselves, so the
person can see why before they write. Writing goes through the same path as
every other letter: a real address from the advert or the agency's own site,
never guessed, one letter per agency ever, and never to an agency on their
never-contact list.

No model is called and nothing is sent until the person picks an agency. The
search is kept for a few hours per person, so filtering the list costs no
board calls.

Switch: RECRUITERS_ENABLED (default on).
"""

from __future__ import annotations

import json
import time
from dataclasses import fields as dataclass_fields

from jobseeker import agencies, settings
from jobseeker.names import company_key
from jobseeker.pipeline import harvest

from . import db

LOOKBACK_DAYS = 30
CACHE_SECONDS = 6 * 3600
REFRESH_SECONDS = 3600
KEEP_TEXT = 1500
WINDOWS = ((7, "Last week"), (14, "Last 2 weeks"), (30, "Last month"))
MIN_ROLES = ((1, "Any"), (2, "2 or more"), (3, "3 or more"))


def enabled() -> bool:
    return settings.flag("RECRUITERS_ENABLED")


def _key(user_id: int) -> str:
    return f"recruiters:{user_id}"


def _slim(listing) -> dict:
    return {"external_id": listing.external_id, "source": listing.source,
            "title": listing.title, "company": listing.company,
            "location": listing.location, "url": listing.url,
            "search_location": listing.search_location,
            "description": (listing.description or "")[:KEEP_TEXT],
            "salary_min": listing.salary_min, "salary_max": listing.salary_max,
            "posted_at": listing.posted_at, "contract": listing.contract,
            "advertiser": listing.advertiser}


def saved(user_id: int) -> dict | None:
    raw = db.get_meta(_key(user_id))
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def gather(user_id: int, profile, creds, *, session=None, refresh=False,
           now: float | None = None) -> dict:
    """{"at": ts, "listings": [...]} of agency adverts for this person.

    From the saved search when it is fresh. A refresh is honoured at most
    hourly, because each one is a round of board calls."""
    now = time.time() if now is None else now
    have = saved(user_id)
    if have:
        age = now - have.get("at", 0)
        if age < CACHE_SECONDS and not (refresh and age >= REFRESH_SECONDS):
            return have
    found = harvest.harvest(profile, creds, session=session,
                            exclude_titles=profile.exclude_titles,
                            max_age_hours=LOOKBACK_DAYS * 24)
    listings = [_slim(item) for item in found["keep"]
                if item.advertiser == agencies.AGENCY]
    out = {"at": int(now), "listings": listings}
    db.set_meta(_key(user_id), json.dumps(out))
    return out


def _age_days(posted: str | None, now: float) -> float | None:
    parsed = harvest.parse_ts(posted)
    return None if parsed is None else (now - parsed.timestamp()) / 86400


def group(listings: list, *, area: str = "", terms=(), days: int = 30,
          min_roles: int = 1, words: str = "",
          now: float | None = None) -> list[dict]:
    """Agencies, best first, after the person's filters."""
    now = time.time() if now is None else now
    terms = {t for t in terms if t}
    words = [w for w in (words or "").lower().split() if w]
    agencies_by_key: dict[str, dict] = {}
    for item in listings:
        if area and (item.get("search_location") or "") != area:
            continue
        if terms and item.get("contract") and item["contract"] not in terms:
            continue
        age = _age_days(item.get("posted_at"), now)
        if age is not None and age > days:
            continue
        if words and not all(w in (item.get("title") or "").lower()
                             for w in words):
            continue
        key = company_key(item.get("company") or "")
        if not key:
            continue
        a = agencies_by_key.setdefault(key, {
            "key": key, "name": item["company"], "roles": [],
            "places": [], "terms": [], "latest": None})
        a["roles"].append(item)
        place = item.get("search_location") or ""
        if place and place not in a["places"]:
            a["places"].append(place)
        kind = item.get("contract") or ""
        if kind and kind not in a["terms"]:
            a["terms"].append(kind)
        if item.get("posted_at") and (a["latest"] is None
                                      or item["posted_at"] > a["latest"]):
            a["latest"] = item["posted_at"]

    out = [a for a in agencies_by_key.values() if len(a["roles"]) >= min_roles]
    for a in out:
        a["roles"].sort(key=lambda r: r.get("posted_at") or "", reverse=True)
        a["count"] = len(a["roles"])
        a["reason"] = reason(a, days)
    # Most matching jobs first; the most recently active breaks a tie.
    out.sort(key=lambda a: a["latest"] or "", reverse=True)
    out.sort(key=lambda a: -a["count"])
    return out


def reason(agency: dict, days: int) -> str:
    n = agency["count"]
    jobs = "job" if n == 1 else "jobs"
    near = (" near " + " and ".join(agency["places"][:2])
            if agency["places"] else "")
    when = {7: "the last week", 14: "the last 2 weeks"}.get(days,
                                                           "the last month")
    return f"Advertising {n} {jobs} like yours{near} in {when}"


def status(user_id: int, name: str) -> str:
    """'' when it can be written to, else why not."""
    if db.is_blocked(user_id, name):
        return "blocked"
    if db.already_contacted(user_id, name):
        return "written"
    if db.has_draft_for(user_id, name):
        return "drafted"
    return ""


def to_listing(item: dict) -> harvest.Listing:
    known = {f.name for f in dataclass_fields(harvest.Listing)}
    return harvest.Listing(**{k: v for k, v in item.items() if k in known})


def write_to(user_id: int, key: str, *, ai=None, session=None,
             now: float | None = None) -> str:
    """Draft one letter to the agency with this key, about its newest advert.

    Returns what happened: drafted, blocked, written, no_address,
    undeliverable, gone (no longer in the list) or no_profile."""
    from jobseeker.profile import Profile, ProfileError

    from . import runner

    have = saved(user_id) or {"listings": []}
    roles = [i for i in have["listings"]
             if company_key(i.get("company") or "") == key]
    if not roles:
        return "gone"
    roles.sort(key=lambda r: r.get("posted_at") or "", reverse=True)
    name = roles[0]["company"]
    why_not = status(user_id, name)
    if why_not:
        return why_not
    raw = db.load_profile(user_id)
    try:
        profile = Profile.from_dict(raw or {})
    except ProfileError:
        return "no_profile"
    if ai is None:
        from .ai import gemini_now
        ai = gemini_now
    report = runner.RunReport()
    for item in roles[:3]:
        before = report.drafted
        runner._draft_one(user_id, to_listing(item), profile, ai, session,
                          report, cv_attached=bool(db.get_cv(user_id)),
                          delay=0)
        if report.drafted > before:
            return "drafted"
        if report.undeliverable:
            return "undeliverable"
        if report.already_contacted:
            return "written"
    return "no_address"
