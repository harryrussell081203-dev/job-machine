"""Grow the employer directory a little every day, with nobody involved.

Two sources of company names, both real employers advertising real jobs:

  - **Every company the product has drafted a letter to**, across all
    accounts. Only the company's name is used; nothing about who wrote to
    it, and the page never says anybody did.
  - **A rotating slice of the UK job boards**: common roles in the larger
    towns, a few searches a day, so over a few weeks the directory covers
    the employers jobseekers are most likely to look up. The jobs each one
    advertised go on its page.

Each company is then read the same way /find reads it (company_lookup: its
own site, seven pages at most), and directory.record() keeps only the shared
inboxes. A company already listed is read again after a month; one that
asked to be removed never is.

Run by .github/workflows/directory.yml. Switches: DIRECTORY_ENABLED,
DIRECTORY_PER_RUN (companies read per run, default 150),
DIRECTORY_SEARCHES (board searches per run, default 10).
"""

from __future__ import annotations

import datetime

from jobseeker import agencies, settings
from jobseeker.pipeline import discover, harvest

from . import company_lookup, config, db, directory

ROLES = ("warehouse operative", "care assistant", "electrician",
         "HGV driver", "chef", "cleaner", "administrator", "forklift driver",
         "maintenance engineer", "customer service", "labourer",
         "delivery driver", "support worker", "receptionist", "mechanic",
         "kitchen porter", "sales assistant", "plumber", "welder",
         "production operative")
TOWNS = ("London", "Birmingham", "Manchester", "Glasgow", "Leeds",
         "Liverpool", "Bristol", "Sheffield", "Edinburgh", "Newcastle",
         "Cardiff", "Nottingham", "Leicester", "Aberdeen", "Belfast",
         "Southampton", "Coventry", "Hull", "Stoke-on-Trent", "Plymouth")
SEARCHES_PER_RUN = 10


def searches_per_run() -> int:
    return max(1, int(settings.number("DIRECTORY_SEARCHES", SEARCHES_PER_RUN)))


def todays_searches(day: datetime.date) -> list[tuple[str, str]]:
    """A different slice of role x town each day, cycling through all 400."""
    pairs = [(r, t) for t in TOWNS for r in ROLES]
    n = searches_per_run()
    start = (day.toordinal() * n) % len(pairs)
    return [pairs[(start + i) % len(pairs)] for i in range(n)]


def from_boards(day: datetime.date, *, session=None) -> dict[str, list]:
    """{company: [recent roles]} from today's searches. Employers only."""
    if not (config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY):
        return {}
    import requests
    session = session or requests
    out: dict[str, list] = {}
    for role, town in todays_searches(day):
        try:
            r = session.get(
                "https://api.adzuna.com/v1/api/jobs/gb/search/1",
                params={"app_id": config.ADZUNA_APP_ID,
                        "app_key": config.ADZUNA_APP_KEY,
                        "results_per_page": 50, "what": role, "where": town,
                        "max_days_old": 7, "sort_by": "date",
                        "content-type": "application/json"},
                headers=harvest.UA, timeout=30)
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as exc:
            print(f"[directory] {role} in {town}: {exc}")
            continue
        for j in results:
            company = ((j.get("company") or {}).get("display_name") or "").strip()
            description = harvest.strip_html(j.get("description") or "")
            if not company or agencies.advertiser(company, description) \
                    == agencies.AGENCY:
                continue
            out.setdefault(company, []).append({
                "title": (j.get("title") or "").strip()[:80],
                "location": ((j.get("location") or {})
                             .get("display_name") or "")[:60],
                "posted_at": (j.get("created") or "")[:10],
                # Which search found it, so the role and town pages
                # (hubs.py) group by what was asked, not a guess at a title.
                "search": role, "town": town})
    return out


def from_drafts() -> list[str]:
    with db.connect() as c:
        rows = c.execute("SELECT DISTINCT company FROM drafts "
                         "WHERE company IS NOT NULL AND company != ''"
                         ).fetchall()
    return [r["company"] for r in rows
            if agencies.advertiser(r["company"]) != agencies.AGENCY]


def run(*, day: datetime.date | None = None, session=None, lookup=None,
        per_run: int | None = None) -> dict:
    if not directory.enabled():
        return {"reason": "off"}
    day = day or datetime.date.today()
    per_run = per_run or int(settings.number("DIRECTORY_PER_RUN", 150))
    boards = from_boards(day, session=session)
    # Today's advertisers first: they come with jobs to show.
    names = list(dict.fromkeys(list(boards) + from_drafts()))
    todo = directory.due(names)[:per_run]
    look = lookup or (lambda name: company_lookup.lookup(
        name, store=db._PlaceCache(), find_domain=discover.find_domain_wide,
        retry_unknown=True))
    published = 0
    for name in todo:
        try:
            result = look(name)
        except Exception as exc:
            print(f"[directory] {name}: {exc}")
            continue
        if directory.record(name, result, roles=boards.get(name),
                            remember_miss=True):
            published += 1
    return {"candidates": len(names), "read": len(todo),
            "published": published, "listed": len(directory.listed()),
            "websites_found_by": dict(discover.FOUND_BY)}


def announce() -> str:
    """Tell IndexNow about the directory and the role and town pages the
    moment they change, rather than whenever the web app next restarts.
    Its own fingerprint, so it never cancels out the app's whole-site one."""
    from . import hubs, indexnow
    paths = directory.paths()
    if paths:
        paths = ["/employers"] + paths + hubs.paths()
    return indexnow.submit_if_changed(
        [config.BASE_URL + p for p in paths], get_meta=db.get_meta,
        set_meta=db.set_meta, memory_key="indexnow_directory")


def main() -> int:
    db.init()
    print(f"[directory] {run()}")
    print(f"[directory] indexnow: {announce()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
