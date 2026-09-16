"""Bring the founder's own applications into the product's numbers.

The companion to import_history.py, which carries across only the register of
who must not be written to again. This carries the applications themselves -
what was sent, to whom, and what came back - so the tracker and /numbers show
a real record instead of starting from zero.

Why the mapping is the interesting part
---------------------------------------
The personal machine reads replies and CLASSIFIES them. The product cannot:
it sees that a message arrived from an address it wrote to and nothing more.
So this import carries knowledge the product could never have worked out for
itself, and the mapping has to spend it honestly.

    interview_invite            -> interview
    question, other             -> replied
    rejection                   -> rejected      (heard back, not a reply)
    auto_acknowledgement        -> NOTHING AT ALL
    unclassified, missing       -> reply_seen_at, no outcome

The two ends of that list are the ones worth defending.

An **autoresponder** is imported as though nothing came back. Somebody read
those five messages and established they were "thank you for your
application, we will be in touch" - so recording them as a reply would put a
number on the page that the person who read them knows to be false. Recording
them as reply_seen_at would do the same thing by a slower route, because the
tracker counts a seen reply as heard back. The count therefore UNDERSTATES by
five, which is the direction to be wrong in.

An **unclassified** reply is imported as reply_seen_at with no outcome, which
is exactly what that column means: a message arrived and nobody has said what
it was. It surfaces as "has been in touch" with the four buttons underneath,
which is the same thing the live inbox check produces.

Repeatable
----------
Every row carries the personal machine's listing id in `imported_ref`, and a
row already holding that id is updated rather than inserted again. Run it as
often as you like; the counts do not move unless the underlying history has.

Usage
-----
    python -m tools.import_applications --user you@example.com --dry-run
    python -m tools.import_applications --user you@example.com
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(HERE)
REPO = os.path.dirname(PRODUCT)
sys.path.insert(0, PRODUCT)

from app import db  # noqa: E402

STATE = os.path.join(REPO, "data", "state.json")

# What the personal machine called it -> what the product records.
# None means "import as though nothing came back"; see the module docstring.
OUTCOME_FOR = {
    "interview_invite": "interview",
    "offer": "offer",
    "question": "replied",
    "other": "replied",
    "rejection": "rejected",
    "auto_acknowledgement": None,
}
# Categories that mean "a message arrived but nobody classified it".
UNCLASSIFIED = ("unclassified", "", None)


def unix(stamp) -> int:
    """An ISO timestamp as seconds, or 0."""
    if not stamp:
        return 0
    try:
        return int(datetime.datetime.fromisoformat(stamp).timestamp())
    except (TypeError, ValueError):
        return 0


def applications(state: dict) -> list[dict]:
    """Every letter that actually left, newest last."""
    out = []
    for external_id, job in (state.get("jobs") or {}).items():
        if not job.get("sent_at"):
            continue
        category = job.get("reply_category")
        replied = job.get("status") == "replied"
        outcome = OUTCOME_FOR.get(category, "replied" if replied else "")
        # A reply nobody classified is flagged as seen, not scored.
        seen = 0
        if replied and category in UNCLASSIFIED:
            outcome = ""
            seen = unix(job.get("replied_at")) or unix(job.get("sent_at"))
        out.append({
            "ref": str(external_id),
            "company": (job.get("company") or "").strip(),
            "to_email": (job.get("sent_to") or job.get("contact_email")
                         or "").strip(),
            "to_name": (job.get("contact_name") or "").strip(),
            "job_title": (job.get("title") or "").strip(),
            "location": (job.get("location") or "").strip(),
            "listing_url": (job.get("url") or "").strip(),
            "subject": (job.get("sent_subject")
                        or job.get("draft_subject") or "").strip(),
            "body": (job.get("sent_body") or job.get("draft_body") or ""),
            "score": int(job.get("score") or 0),
            "score_reason": (job.get("score_reason") or "")[:500],
            "contact_tier": job.get("email_tier") or 0,
            "sent_at": unix(job.get("sent_at")),
            "outcome": outcome or "",
            "outcome_at": unix(job.get("replied_at")) if outcome else 0,
            "reply_seen_at": seen,
            "category": category or "unclassified",
        })
    out.sort(key=lambda a: a["sent_at"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True,
                    help="the email address of the account to import into")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would happen and write nothing")
    ap.add_argument("--state", default=STATE)
    args = ap.parse_args(argv)

    if not os.path.exists(args.state):
        print(f"no state file at {args.state}")
        return 1
    with open(args.state) as f:
        state = json.load(f)

    rows = applications(state)
    if not rows:
        print("nothing to import: no sent applications in that state file")
        return 1

    db.init()
    user = db.get_or_create_user(args.user)
    user_id = user["id"]

    heard = sum(1 for r in rows if r["outcome"] in ("replied", "interview",
                                                    "offer"))
    seen = sum(1 for r in rows if r["reply_seen_at"])
    rejected = sum(1 for r in rows if r["outcome"] == "rejected")
    dropped = sum(1 for r in rows
                  if r["category"] == "auto_acknowledgement")

    print(f"{len(rows)} applications for {args.user}")
    print(f"  replies worth counting   {heard}")
    print(f"  seen but unclassified    {seen}")
    print(f"  rejections               {rejected}")
    print(f"  autoresponders, not counted as anything   {dropped}")
    print(f"  reply rate as the tracker will show it    "
          f"{round(100 * (heard + seen) / len(rows))}%")

    missing = [r for r in rows if not r["to_email"]]
    if missing:
        print(f"  {len(missing)} have no recorded address and are skipped")
        rows = [r for r in rows if r["to_email"]]

    if args.dry_run:
        print("\nthe five most recent, as they would land:")
        for r in rows[-5:]:
            when = datetime.datetime.utcfromtimestamp(r["sent_at"])
            print(f"  {when:%Y-%m-%d} {r['company'][:28]:28s} "
                  f"tier {r['contact_tier']}  "
                  f"{r['outcome'] or ('seen' if r['reply_seen_at'] else '-')}")
        print("\nnothing written (--dry-run)")
        return 0

    added = updated = 0
    for r in rows:
        with db.connect() as c:
            existing = c.execute(
                "SELECT id FROM drafts WHERE user_id = ? AND imported_ref = ?",
                (user_id, r["ref"])).fetchone()
            fields = {
                "job_title": r["job_title"], "company": r["company"],
                "location": r["location"], "listing_url": r["listing_url"],
                "to_email": r["to_email"], "to_name": r["to_name"],
                "contact_tier": r["contact_tier"], "score": r["score"],
                "score_reason": r["score_reason"], "subject": r["subject"],
                "body": r["body"], "status": "sent",
                "sent_at": r["sent_at"], "outcome": r["outcome"],
                "outcome_at": r["outcome_at"] or None,
                "reply_seen_at": r["reply_seen_at"] or None,
            }
            if existing:
                sets = ", ".join(f"{k} = ?" for k in fields)
                c.execute(f"UPDATE drafts SET {sets} WHERE id = ?",
                          list(fields.values()) + [existing["id"]])
                draft_id = existing["id"]
                updated += 1
            else:
                cols = ["user_id", "created_at", "imported_ref"] + list(fields)
                vals = ([user_id, r["sent_at"], r["ref"]] + list(fields.values()))
                from app.store import insert_returning_id
                draft_id = insert_returning_id(c, "drafts", cols, vals)
                added += 1

            # sent_log is what /numbers counts and what the daily cap reads.
            # Matched on draft_id so a re-run cannot log the same letter twice.
            already = c.execute(
                "SELECT id FROM sent_log WHERE user_id = ? AND draft_id = ?",
                (user_id, draft_id)).fetchone()
            if not already:
                from app.store import insert_returning_id
                insert_returning_id(
                    c, "sent_log",
                    ["user_id", "draft_id", "to_email", "company", "sent_at",
                     "ok", "error"],
                    [user_id, draft_id, r["to_email"], r["company"],
                     r["sent_at"], 1, ""])

        # And the employer goes on the never-again register, keyed by the
        # product's own function rather than copied across. Cheap to repeat:
        # record_contacted is ON CONFLICT DO NOTHING.
        if r["company"]:
            db.record_contacted(user_id, r["company"])

    print(f"\n{added} added, {updated} updated")
    stats = db.application_stats(user_id)
    print(f"the tracker now reads: {stats['sent']} sent, "
          f"{stats['heard_back']} heard back, {stats['reply_rate']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
