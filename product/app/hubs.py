"""Role and town pages: "warehouse jobs in Leeds - who to email".

The employer directory answers somebody who already knows the company. Most
people search by what they do and where: "care assistant jobs Leeds",
"forklift driver jobs Hull". These pages answer that search with the
employers who advertised that job there recently AND print an address for
applications on their own website - so the page is a list of places to write
to today, not another list of Apply buttons.

Built entirely from the directory (directory.py), at request time:

  - every employer on a page has a live directory page, so the same rules
    hold: shared inboxes only, never a person's, removable, re-checked
  - an employer counts for a role and town only when the daily board search
    for that role in that town found its advert (directory_builder records
    which search it was). Older entries without that are matched on the
    advert's own title and location words, never on a guess
  - a page exists only with MIN_EMPLOYERS or more employers. A page with one
    name on it helps nobody and reads as filler, to people and to search
    engines alike; it appears by itself once the directory has grown

Nothing here is stored. Remove an employer's page and it leaves every role
and town page with it.
"""

from __future__ import annotations

import json
import re

from . import db, directory
from .directory_builder import ROLES, TOWNS

MIN_EMPLOYERS = 3
MAX_JOBS_EACH = 3


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


ROLE_BY_SLUG = {slug(r): r for r in ROLES}
TOWN_BY_SLUG = {slug(t): t for t in TOWNS}


def role_label(role: str) -> str:
    return role[:1].upper() + role[1:]


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", (text or "").lower())}


def role_of(job: dict) -> str:
    """Which of ROLES this advert was for, or ""."""
    if job.get("search") in ROLES:
        return job["search"]
    have = _words(job.get("title", ""))
    for role in ROLES:
        if _words(role) <= have:
            return role
    return ""


def town_of(job: dict) -> str:
    if job.get("town") in TOWNS:
        return job["town"]
    where = (job.get("location") or "").lower()
    for town in TOWNS:
        if re.search(r"\b" + re.escape(town.lower()) + r"\b", where):
            return town
    return ""


def _live_pages() -> list[dict]:
    if not directory.enabled():
        return []
    with db.connect() as c:
        rows = c.execute(
            "SELECT slug, company, domain, inboxes, roles, checked_at FROM "
            "employer_pages WHERE removed_at IS NULL AND inboxes != '[]' "
            "AND roles != '[]' ORDER BY company").fetchall()
    out = []
    for r in rows:
        page = dict(r)
        try:
            page["inboxes"] = json.loads(page["inboxes"] or "[]")
            page["roles"] = json.loads(page["roles"] or "[]")
        except ValueError:
            continue
        if page["inboxes"]:
            out.append(page)
    return out


def index() -> dict:
    """{(role, town or ""): [employer, ...]} for every role and town with
    any employer at all. Callers apply MIN_EMPLOYERS."""
    groups: dict[tuple[str, str], dict[str, dict]] = {}
    for page in _live_pages():
        inbox = next((i for i in page["inboxes"]
                      if i.get("kind") == directory.HIRING), page["inboxes"][0])
        for job in page["roles"]:
            role = role_of(job)
            if not role:
                continue
            town = town_of(job)
            for key in ((role, ""),) + (((role, town),) if town else ()):
                entry = groups.setdefault(key, {}).setdefault(page["slug"], {
                    "slug": page["slug"], "company": page["company"],
                    "domain": page["domain"], "email": inbox["email"],
                    "kind": inbox.get("kind", ""),
                    "checked_at": page["checked_at"], "jobs": []})
                if len(entry["jobs"]) < MAX_JOBS_EACH and job not in entry["jobs"]:
                    entry["jobs"].append(job)
    out = {}
    for key, group in groups.items():
        employers = sorted(group.values(), key=lambda e: e["company"].lower())
        # Most recently advertised first, then a hiring inbox before a
        # general one. Stable sorts, so A to Z breaks the ties.
        employers.sort(key=lambda e: max(
            (j.get("posted_at") or "" for j in e["jobs"]), default=""),
            reverse=True)
        employers.sort(key=lambda e: e["kind"] != directory.HIRING)
        out[key] = employers
    return out


def published(groups: dict | None = None) -> dict:
    groups = index() if groups is None else groups
    return {k: v for k, v in groups.items() if len(v) >= MIN_EMPLOYERS}


def path(role: str, town: str = "") -> str:
    return f"/jobs/{slug(role)}" + (f"/{slug(town)}" if town else "")


def paths() -> list[str]:
    """Every published role and town page, for the sitemap and IndexNow."""
    pages = published()
    if not pages:
        return []
    return ["/jobs"] + sorted(path(r, t) for r, t in pages)


def page(role_slug: str, town_slug: str = "") -> dict | None:
    role = ROLE_BY_SLUG.get(role_slug)
    town = TOWN_BY_SLUG.get(town_slug) if town_slug else ""
    if not role or (town_slug and not town):
        return None
    pages = published()
    employers = pages.get((role, town))
    if not employers:
        return None
    hiring = sum(1 for e in employers if e["kind"] == directory.HIRING)
    if town:
        nearby = sorted((t for r, t in pages if r == role and t and t != town))
        elsewhere = sorted((r for r, t in pages if t == town and r != role))
    else:
        nearby = sorted(t for r, t in pages if r == role and t)
        elsewhere = []
    return {"role": role, "role_label": role_label(role), "town": town,
            "role_slug": slug(role), "employers": employers,
            "hiring": hiring, "towns": [(t, path(role, t)) for t in nearby],
            "other_roles": [(role_label(r), path(r, town)) for r in elsewhere],
            "path": path(role, town)}


def overview() -> list[dict]:
    """The /jobs index: each role with its town pages."""
    pages = published()
    out = []
    for role in ROLES:
        if (role, "") not in pages:
            continue
        out.append({"role": role_label(role), "path": path(role),
                    "count": len(pages[(role, "")]),
                    "towns": [(t, path(role, t)) for t in TOWNS
                              if (role, t) in pages]})
    return out
