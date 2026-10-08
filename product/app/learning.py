"""What the machine learns as it works, kept so it does not learn it twice.

Two kinds, both small, both anonymous:

CRAWL MEMORY - one row per website read (crawl_facts).
  Every time a company's site is read for an address (the sweep, /find, the
  directory builder), what each page did is written down: had an address,
  had none, does not exist, refused us, timed out. Next time:
    - a page that does not exist is not asked for again for 60 days
    - a site that refused every page is left alone for 14 days, and one
      that did not answer at all for 3, instead of costing a run its time
    - a site that could not be reached is never mistaken for one whose
      address has gone, so a live directory page is not wiped by an outage
  And the admin page shows which pages actually carry addresses across
  every site read, which is the evidence for changing the list.

LETTER LESSONS - one row per sent letter (letter_lessons).
  Features of the letter and what came back: how long, whether it asks a
  question, the kind of subject line, the kind of address it went to,
  agency or employer, the day it went. Never the text, never a name, never
  an address, never which account sent it. Kept after an account is
  deleted, because nothing in it says whose it was.
  The report says plainly when there is too little to tell. Reply rates
  move by chance at small numbers, and a lesson drawn from twenty letters
  would be a guess with a decimal point.

Storage: a few hundred bytes a row. Ten thousand sites and ten thousand
letters together are a few megabytes of the 500 the free database allows.
No model is called for any of it.
"""

from __future__ import annotations

import json
import math
import re
import time

from .store import connect

DAY = 86400
DEAD_FOR = 60 * DAY
REFUSED_FOR = 14 * DAY
DOWN_FOR = 3 * DAY

ADDRESS, EMPTY, DEAD, REFUSED, ERROR, AWAY = (
    "address", "empty", "dead", "refused", "error", "away")


def kind_of_response(status: int, had_address: bool) -> str:
    if status == 200:
        return ADDRESS if had_address else EMPTY
    if status in (404, 410):
        return DEAD
    if status in (401, 403, 429, 451, 503):
        return REFUSED
    if status in (301, 302, 303, 307, 308):
        return AWAY
    return ERROR


# ----------------------------------------------------------------------
# crawl memory
# ----------------------------------------------------------------------
def _row(domain: str) -> dict | None:
    try:
        with connect() as c:
            row = c.execute("SELECT * FROM crawl_facts WHERE domain = ?",
                            (domain.lower(),)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def skip(domain: str, *, now: float | None = None) -> set[str]:
    """Pages of this site known not to exist, recently."""
    now = time.time() if now is None else now
    row = _row(domain)
    if not row:
        return set()
    pages = json.loads(row.get("pages") or "{}")
    return {p for p, (kind, at) in pages.items()
            if kind == DEAD and now - at < DEAD_FOR}


def resting(domain: str, *, now: float | None = None) -> bool:
    """Refused us or did not answer, recently enough to leave it be."""
    now = time.time() if now is None else now
    row = _row(domain)
    return bool(row and (row.get("rest_until") or 0) > now)


def remember(domain: str, tried: dict[str, str], *, found_by: str = "",
             agency: bool | None = None, now: float | None = None) -> None:
    """Write down what each page did. `tried` is {path: kind}. Never raises:
    a failure to remember must never cost the read itself."""
    if not domain or not tried:
        return
    now = int(time.time() if now is None else now)
    domain = domain.lower()
    try:
        old = _row(domain) or {}
        pages = json.loads(old.get("pages") or "{}")
        for path, kind in tried.items():
            pages[path or "/"] = [kind, now]
        kinds = set(tried.values())
        rest = int(old.get("rest_until") or 0)
        if kinds <= {REFUSED}:
            rest = now + REFUSED_FOR
        elif kinds <= {ERROR, REFUSED}:
            rest = now + DOWN_FOR
        elif kinds & {ADDRESS, EMPTY}:
            rest = 0
        agency_v = old.get("agency") or 0
        if agency is not None:
            agency_v = int(bool(agency))
        with connect() as c:
            if old:
                c.execute(
                    "UPDATE crawl_facts SET pages = ?, rest_until = ?, "
                    "agency = ?, found_by = ?, reads = reads + 1, "
                    "read_at = ? WHERE domain = ?",
                    (json.dumps(pages), rest, agency_v,
                     found_by or old.get("found_by") or "", now, domain))
            else:
                c.execute(
                    "INSERT INTO crawl_facts (domain, found_by, pages, "
                    "rest_until, agency, reads, read_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (domain, found_by or "", json.dumps(pages), rest,
                     agency_v, now))
    except Exception:
        pass


def reachable(tried: dict[str, str]) -> bool:
    """Did any page answer at all? If not, the site was down or refusing,
    which says nothing about whether its address is still there."""
    return bool(set(tried.values()) & {ADDRESS, EMPTY, DEAD})


def crawl_report() -> dict:
    """Across every site read: which pages carry addresses, which do not
    exist, which source found the sites, and how many are resting."""
    try:
        with connect() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT found_by, pages, rest_until, agency FROM crawl_facts"
            ).fetchall()]
    except Exception:
        rows = []
    now = time.time()
    paths: dict[str, dict] = {}
    found_by: dict[str, int] = {}
    for r in rows:
        if r["found_by"]:
            found_by[r["found_by"]] = found_by.get(r["found_by"], 0) + 1
        for path, (kind, _) in json.loads(r["pages"] or "{}").items():
            p = paths.setdefault(path, {"tried": 0, ADDRESS: 0, DEAD: 0})
            p["tried"] += 1
            if kind in (ADDRESS, DEAD):
                p[kind] += 1
    table = sorted(
        ({"path": k, "tried": v["tried"],
          "address_pct": round(100 * v[ADDRESS] / v["tried"]),
          "dead_pct": round(100 * v[DEAD] / v["tried"])}
         for k, v in paths.items()),
        key=lambda x: (-x["address_pct"], -x["tried"]))
    return {"sites": len(rows), "paths": table,
            "found_by": dict(sorted(found_by.items(), key=lambda x: -x[1])),
            "resting": sum(1 for r in rows if (r["rest_until"] or 0) > now),
            "agencies": sum(1 for r in rows if r["agency"])}


# ----------------------------------------------------------------------
# letter lessons
# ----------------------------------------------------------------------
GOOD = ("replied", "interview", "offer")
TOO_FEW = 30


def _band(n: int | None, edges, labels) -> str:
    if n is None:
        return "unknown"
    for edge, label in zip(edges, labels):
        if n <= edge:
            return label
    return labels[-1]


def features(d: dict) -> dict:
    """What a sent letter was like, from the draft row. Nothing personal
    survives this: counts and categories only."""
    from jobseeker.pipeline import contacts
    body = d.get("body") or ""
    subject = d.get("subject") or ""
    words = len(re.findall(r"\b\w+\b", body))
    title = (d.get("job_title") or "").strip().lower()
    subj = subject.strip().lower()
    if title and subj == title:
        subject_kind = "the job title"
    elif title and title in subj:
        subject_kind = "job title plus more"
    else:
        subject_kind = "something else"
    tier, _ = contacts.classify(d.get("to_email") or "") \
        if d.get("to_email") else (d.get("contact_tier") or 0, "")
    sent = d.get("sent_at") or 0
    lt = None
    if sent:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        lt = datetime.fromtimestamp(sent, ZoneInfo("Europe/London")).timetuple()
    return {
        "words": words,
        "length": _band(words, (60, 90, 130), ("up to 60 words", "61-90 words",
                                                "91-130 words", "over 130 words")),
        "asks_question": int("?" in body),
        "subject_kind": subject_kind,
        "address_kind": contacts.TIER_NAMES.get(tier, "unknown"),
        "advertiser": d.get("advertiser") or "unknown",
        "weekday": time.strftime("%A", lt) if lt else "unknown",
        "send_hour": (_band(lt.tm_hour, (8, 11, 14, 17),
                            ("before 9am", "9-12", "12-3pm", "3-6pm",
                             "after 6pm")) if lt else "unknown"),
        "followed_up": int(bool(d.get("followup_sent_at"))),
    }


def sync() -> int:
    """Bring the lessons up to date from every sent letter: new letters
    added, outcomes refreshed. Idempotent; returns how many rows changed."""
    try:
        with connect() as c:
            drafts = [dict(r) for r in c.execute(
                "SELECT id, job_title, subject, body, to_email, contact_tier, "
                "advertiser, sent_at, followup_sent_at, outcome, outcome_at "
                "FROM drafts WHERE status = 'sent'").fetchall()]
    except Exception:
        return 0
    changed = 0
    now = int(time.time())
    for d in drafts:
        f = features(d)
        outcome = d.get("outcome") or ""
        days = None
        if outcome and d.get("outcome_at") and d.get("sent_at"):
            days = max(0, int((d["outcome_at"] - d["sent_at"]) // DAY))
        try:
            with connect() as c:
                c.execute(
                    "INSERT INTO letter_lessons (draft_id, words, length, "
                    "asks_question, subject_kind, address_kind, advertiser, "
                    "weekday, send_hour, followed_up, outcome, outcome_days, "
                    "synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(draft_id) DO UPDATE SET "
                    "followed_up = excluded.followed_up, "
                    "outcome = excluded.outcome, "
                    "outcome_days = excluded.outcome_days, "
                    "synced_at = excluded.synced_at",
                    (d["id"], f["words"], f["length"], f["asks_question"],
                     f["subject_kind"], f["address_kind"], f["advertiser"],
                     f["weekday"], f["send_hour"], f["followed_up"], outcome,
                     days, now))
            changed += 1
        except Exception:
            continue
    return changed


def _interval(good: int, n: int) -> tuple[int, int]:
    """A 90% range for the true rate (Wilson). Wide at small n, on purpose."""
    if not n:
        return 0, 0
    z = 1.645
    p = good / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(100 * max(0, centre - half)), round(100 * min(1, centre + half))


def letter_report() -> dict:
    """Reply rate by each feature, with how many letters each rests on and
    a range, and a plain flag where there are too few to tell."""
    try:
        with connect() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM letter_lessons").fetchall()]
    except Exception:
        rows = []
    total = len(rows)
    good_all = sum(1 for r in rows if r["outcome"] in GOOD)
    out = {"letters": total, "replied": good_all,
           "rate": round(100 * good_all / total) if total else None,
           "features": []}
    labels = {"length": "Length", "asks_question": "Asks a question",
              "subject_kind": "Subject line", "address_kind": "Sent to",
              "advertiser": "Advert from", "weekday": "Day sent",
              "send_hour": "Time sent", "followed_up": "Followed up"}
    for key, label in labels.items():
        groups: dict[str, list] = {}
        for r in rows:
            value = r[key]
            if key in ("asks_question", "followed_up"):
                value = "yes" if value else "no"
            groups.setdefault(str(value), []).append(r)
        lines = []
        for value, rs in sorted(groups.items(), key=lambda x: -len(x[1])):
            n = len(rs)
            good = sum(1 for r in rs if r["outcome"] in GOOD)
            low, high = _interval(good, n)
            lines.append({"value": value, "n": n, "good": good,
                          "rate": round(100 * good / n) if n else 0,
                          "low": low, "high": high, "too_few": n < TOO_FEW})
        out["features"].append({"label": label, "lines": lines})
    return out
