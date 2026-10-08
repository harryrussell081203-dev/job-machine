"""The public employer directory: how to email a company about a job.

"Tesco careers email", "[company] HR email address", "how to apply to
[company]": people search for exactly this every day, and the answer is
usually sitting on the company's own contact or careers page where nobody
looks. Every time this site reads a company's website (a /find lookup, the
Telegram bot, the weekly builder) and finds a shared inbox there, that
company gets a page saying what the address is, which of their own pages it
is printed on, and when it was last checked.

Rules, all enforced here rather than trusted to the template:

  - **Shared inboxes only.** careers@, jobs@, recruitment@, hr@ ("hiring")
    and info@, enquiries@, office@, hello@, contact@ ("general"). An
    address that looks like a person's (jane.smith@) is never published,
    and neither is a name. sales@, accounts@ and the like are irrelevant to
    a job and left out.
  - **Their own domain, their own page.** Only addresses the company
    printed on its own site, with the page they were found on.
  - **Removable.** Any page can be taken down from the page itself, and a
    removed page is never rebuilt.
  - **Checked.** Re-read when the builder comes back round to it; an
    address that has gone from their site goes from the page.
  - **Never a company anybody asked never to contact.** A name or domain on
    any account's never-contact list (a current employer, an agency they
    fell out with) never gets a page, and neither does anything in
    DIRECTORY_EXCLUDE.

Switch: DIRECTORY_ENABLED (default on).
"""

from __future__ import annotations

import json
import re
import time

from jobseeker import settings
from jobseeker.names import company_key
from jobseeker.pipeline import contacts

from . import db

HIRING = "hiring"
GENERAL = "general"
GENERAL_LOCALS = frozenset({"info", "office", "enquiries", "enquiry",
                            "inquiries", "hello", "contact", "reception",
                            "mail", "general", "admin"})
RECHECK_SECONDS = 30 * 86400
MAX_ROLES = 6


def enabled() -> bool:
    return settings.flag("DIRECTORY_ENABLED")


def slugify(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    text = re.sub(r"-(ltd|limited|plc|llp|uk)$", "", text)
    return text[:60].strip("-")


def display_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())[:80]
    return name.title() if name.islower() or name.isupper() else name


# An inbox for another country's office is not where a UK applicant writes.
# Live examples: hr.middleeast@aecom.com, hrsystemsupport.usa@sodexo.com,
# dubaicareersservice@hw.ac.uk. Whole words where a short one would match
# inside others ("us" in "business"), anywhere for the long ones.
ABROAD_WORDS = frozenset({"us", "usa", "uae", "ksa", "apac", "mena", "latam",
                          "anz", "nz", "na", "emea"})
ABROAD_ANYWHERE = ("middleeast", "dubai", "qatar", "saudi", "india",
                   "australia", "canada", "singapore", "america", "china",
                   "africa", "asia")


def abroad(local: str) -> bool:
    words = set(re.split(r"[._\-0-9]+", local))
    return bool(words & ABROAD_WORDS) or any(w in local for w in ABROAD_ANYWHERE)


def kind_of(address: str) -> str:
    """HIRING, GENERAL, or "" for anything that must not be published."""
    local, _, host = address.lower().partition("@")
    if contacts.never_write_to(local) or contacts.is_personal(local):
        return ""
    if abroad(local):
        return ""
    # At a university, careers@ is the service for its own students, not
    # its recruitment team (careers@hw.ac.uk was published as a hiring inbox).
    if host.endswith(".ac.uk") and "career" in local:
        return ""
    tier, name = contacts.classify(address)
    if name:
        return ""
    if tier == 2:
        return HIRING
    if tier == 1 and re.split(r"[._\-0-9]+", local)[0] in GENERAL_LOCALS:
        return GENERAL
    return ""


def inboxes(result: dict) -> list[dict]:
    domain = (result.get("domain") or "").lower()
    found_on = result.get("found_on") or {}
    out = []
    for address in result.get("emails") or []:
        host = address.split("@")[-1].lower()
        if not domain or not (host == domain or host.endswith("." + domain)):
            continue
        kind = kind_of(address)
        if kind:
            out.append({"email": address, "kind": kind,
                        "found_on": found_on.get(address, "")})
    out.sort(key=lambda i: (i["kind"] != HIRING, i["email"]))
    return out


# What an agency's own home page calls itself. The directory and the role
# pages are for employers: an agency's info@ is not the person hiring, and
# the first live directory was over a third agencies (Lorien, Morgan Hunt,
# Berkeley Scott...) whose names alone give nothing away.
AGENCY_SITE = re.compile(
    # "We're recruiting!" is an employer; these are how agencies describe
    # themselves.
    r"\brecruitment (agency|agencies|consultancy|consultants?|specialists?|"
    r"company|business|firm|partner|solutions|services|group)\b|"
    r"\b(specialist|leading|independent|award[- ]winning) recruit(ment|ers?)\b|"
    r"\brecruiters\b|\bstaffing\b|\bexecutive search\b|\bheadhunt|"
    r"\btalent (solutions|partners?)\b|\bemployment agency\b|"
    r"\bsupply (teachers?|staff)\b|"
    r"\b(temporary|permanent|contract|temp)(,| and| &) (permanent|contract|"
    r"temporary|perm)(,? (and|&) (permanent|contract|temporary))? "
    r"(recruitment|staff|roles|positions|placements)\b",
    re.I)


def agency(company: str, result: dict) -> bool:
    from jobseeker import agencies
    if agencies.advertiser(company) == agencies.AGENCY:
        return True
    return bool(AGENCY_SITE.search(result.get("about") or ""))


def excluded(company: str, domain: str = "") -> bool:
    """On somebody's never-contact list, by name or domain, or excluded by
    the site owner."""
    key = company_key(company)
    domain = (domain or "").lower()
    extra = {company_key(x) for x in settings.text("DIRECTORY_EXCLUDE")
             .split(",") if x.strip()}
    if key and key in extra:
        return True
    with db.connect() as c:
        if key and c.execute("SELECT 1 FROM do_not_contact WHERE "
                             "company_key = ?", (key,)).fetchone():
            return True
        if domain and c.execute("SELECT 1 FROM contacted_mail WHERE "
                                "mail_key = ? AND reason != ''",
                                (domain,)).fetchone():
            return True
    return False


def record(company: str, result: dict, *, roles: list | None = None,
           now: float | None = None, remember_miss: bool = False) -> str:
    """Create or refresh this company's page from a fresh read of its site.
    Returns the slug when there is a page, "" when there is nothing to
    publish. Never resurrects a removed page.

    remember_miss: keep an empty, unpublished row for a company that had
    nothing to publish, so the daily builder does not read it again for a
    month. Without it the same few dozen misses took every day's places and
    the new employers from the boards were never reached. Only the builder
    sets this; a name somebody typed into /find is never kept."""
    if not enabled():
        return ""
    now = int(time.time() if now is None else now)
    slug = slugify(company)
    found = inboxes(result)
    if not slug or excluded(company, result.get("domain") or ""):
        return ""
    if agency(company, result):
        found = []      # unpublished, and remembered like any other miss
    with db.connect() as c:
        row = c.execute("SELECT * FROM employer_pages WHERE slug = ?",
                        (slug,)).fetchone()
        if row and row["removed_at"]:
            return ""
        if not found:
            if row:
                # Gone from their site, so gone from the page.
                c.execute("UPDATE employer_pages SET inboxes = '[]', "
                          "checked_at = ? WHERE slug = ?", (now, slug))
            elif remember_miss:
                c.execute("INSERT INTO employer_pages (slug, company, domain, "
                          "inboxes, roles, created_at, checked_at) "
                          "VALUES (?, ?, ?, '[]', '[]', ?, ?)",
                          (slug, display_name(company),
                           result.get("domain") or "", now, now))
            return ""
        keep_roles = json.loads(row["roles"]) if row else []
        if roles:
            keep_roles = (roles + [r for r in keep_roles
                                   if r not in roles])[:MAX_ROLES]
        if row:
            c.execute("UPDATE employer_pages SET domain = ?, inboxes = ?, "
                      "roles = ?, checked_at = ? WHERE slug = ?",
                      (result["domain"], json.dumps(found),
                       json.dumps(keep_roles), now, slug))
        else:
            c.execute("INSERT INTO employer_pages (slug, company, domain, "
                      "inboxes, roles, created_at, checked_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (slug, display_name(company), result["domain"],
                       json.dumps(found), json.dumps(keep_roles), now, now))
    return slug


def _page(row) -> dict:
    page = dict(row)
    page["inboxes"] = json.loads(page.get("inboxes") or "[]")
    page["roles"] = json.loads(page.get("roles") or "[]")
    return page


def get(slug: str) -> dict | None:
    with db.connect() as c:
        row = c.execute("SELECT * FROM employer_pages WHERE slug = ? AND "
                        "removed_at IS NULL", (slug,)).fetchone()
    if not row:
        return None
    page = _page(row)
    return page if page["inboxes"] else None


def listed() -> list[dict]:
    """Every live page, A to Z."""
    with db.connect() as c:
        rows = c.execute("SELECT slug, company, checked_at FROM employer_pages "
                         "WHERE removed_at IS NULL AND inboxes != '[]' "
                         "ORDER BY company").fetchall()
    return [dict(r) for r in rows]


def paths() -> list[str]:
    if not enabled():
        return []
    return [f"/employers/{p['slug']}" for p in listed()]


def remove(slug: str, *, now: float | None = None) -> bool:
    with db.connect() as c:
        row = c.execute("SELECT slug FROM employer_pages WHERE slug = ?",
                        (slug,)).fetchone()
        if not row:
            return False
        c.execute("UPDATE employer_pages SET removed_at = ?, inboxes = '[]' "
                  "WHERE slug = ?", (int(time.time() if now is None else now),
                                     slug))
    return True


def due(slugs_or_names: list[str], *, now: float | None = None) -> list[str]:
    """The names from this list worth reading again: no page yet, or a page
    last checked a month ago. Removed pages never are."""
    now = time.time() if now is None else now
    with db.connect() as c:
        rows = {r["slug"]: r for r in c.execute(
            "SELECT slug, checked_at, removed_at FROM employer_pages").fetchall()}
    out = []
    for name in slugs_or_names:
        row = rows.get(slugify(name))
        if row is None or (not row["removed_at"]
                           and now - row["checked_at"] > RECHECK_SECONDS):
            out.append(name)
    return out
