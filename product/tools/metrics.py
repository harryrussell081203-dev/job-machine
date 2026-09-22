#!/usr/bin/env python3
"""What the product is actually doing, printed in one block.

    cd product && python3 tools/metrics.py
    python3 tools/metrics.py --json          # for the weekly email

WHY THIS EXISTS RATHER THAN A DASHBOARD.

The numbers that matter here are small enough to read. Ten users is not a
charting problem, and a dashboard at this size mostly teaches you to glance at
a shape instead of reading a number - which is how "nine of our ten users
never sent anything" stays invisible for a month.

EVERY FIGURE COMES FROM THE DATABASE AND SAYS SO.

Nothing here is estimated, projected or rounded up. Where a number is small it
is printed small. The whole argument for this product is that its published
figures are checkable, and a growth report that flatters itself is the fastest
way to lose that.

WHY RATES ARE COMPUTED AND NEVER STORED.

A stored rate is a number that can disagree with the counts beside it. Every
percentage below is derived at the moment of printing from two counts that are
also printed, so a reader can always do the division themselves. It is the
same rule study.py follows for the published figures.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

os.environ.setdefault("SECRET_KEY", "metrics-read-only")

from app import db  # noqa: E402
from app.store import connect  # noqa: E402

DAY = 24 * 3600


def pct(part: int, whole: int) -> str:
    """A rate, or an honest dash when there is nothing to divide by.

    0% and "no data yet" are different statements and printing the first for
    the second is a lie that reads as a failure - it would say the channel
    does not convert when nobody has been through it.
    """
    if not whole:
        return "  - "
    return f"{100 * part / whole:4.0f}%"


def funnel() -> list[dict]:
    """How many accounts ever reached each stage, in order.

    `signed_up` is taken from the users table rather than the events table
    on purpose. Events only start when the code that writes them ships, so
    every account that existed before today would read as never having signed
    up, and every rate below it would be divided by the wrong number.
    """
    counts = db.event_counts()
    with connect() as c:
        total = int(c.execute("SELECT COUNT(*) AS n FROM users")
                    .fetchone()["n"])
    out = []
    previous = total
    for stage in db.FUNNEL:
        n = total if stage == "signed_up" else counts.get(stage, 0)
        out.append({"stage": stage, "users": n,
                    "of_previous": pct(n, previous),
                    "of_all": pct(n, total)})
        previous = n
    return out


def by_source() -> list[dict]:
    return db.users_by_source()


def replies_per_user() -> dict:
    """Replies against letters, per account that has sent anything.

    Divided by senders rather than by all users, deliberately. "Replies per
    user" across accounts that have never sent a letter measures how many
    people signed up, not how well the letters work, and those two numbers
    moving in opposite directions is exactly the case this has to survive.
    """
    with connect() as c:
        rows = c.execute(
            "SELECT COUNT(DISTINCT user_id) AS senders, COUNT(*) AS sent "
            "FROM sent_log WHERE ok = 1").fetchone()
        replied = c.execute(
            "SELECT COUNT(*) AS n FROM drafts "
            "WHERE outcome NOT IN ('', 'no')").fetchone()
    senders = int(rows["senders"] or 0)
    sent = int(rows["sent"] or 0)
    replies = int(replied["n"] or 0)
    return {"senders": senders, "sent": sent, "replies": replies,
            "reply_rate": pct(replies, sent),
            "sent_per_sender": round(sent / senders, 1) if senders else 0.0,
            "replies_per_sender": round(replies / senders, 1) if senders else 0.0}


def retention(days: int = 7) -> dict:
    """Of the accounts old enough to have come back, how many did.

    The cohort excludes anybody who signed up inside the window, and that is
    the whole correctness of it: somebody who joined this morning has not
    failed to return after seven days, they have not had seven days. Counting
    them makes retention fall every time signups rise, which reads as growth
    breaking the product.
    """
    cutoff = db.now() - days * DAY
    with connect() as c:
        eligible = c.execute(
            "SELECT COUNT(*) AS n FROM users WHERE created_at <= ?",
            (cutoff,)).fetchone()
        returned = c.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE created_at <= ? AND last_seen_at IS NOT NULL "
            "  AND last_seen_at - created_at >= ?",
            (cutoff, days * DAY)).fetchone()
    n = int(eligible["n"] or 0)
    back = int(returned["n"] or 0)
    return {"days": days, "eligible": n, "returned": back,
            "rate": pct(back, n)}


def referrals() -> dict:
    with connect() as c:
        referred = c.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referred_by IS NOT NULL"
        ).fetchone()
        referrers = c.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM events WHERE kind = ?",
            (db.REFERRED_USER,)).fetchone()
        rewards = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind = ?",
            (db.REFERRED_USER,)).fetchone()
        total = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return {"referred_users": int(referred["n"] or 0),
            "users_who_referred": int(referrers["n"] or 0),
            "months_given": int(rewards["n"] or 0),
            "share_referring": pct(int(referrers["n"] or 0),
                                   int(total["n"] or 0))}


def collect() -> dict:
    return {"funnel": funnel(), "by_source": by_source(),
            "letters": replies_per_user(), "retention": retention(),
            "referrals": referrals()}


def report(data: dict) -> str:
    lines = [f"Recruited, {db.describe()}", ""]

    lines.append("THE FUNNEL")
    lines.append(f"  {'stage':<18} {'users':>6} {'of prev':>8} {'of all':>8}")
    for row in data["funnel"]:
        lines.append(f"  {row['stage']:<18} {row['users']:>6} "
                     f"{row['of_previous']:>8} {row['of_all']:>8}")

    lines += ["", "WHERE THEY CAME FROM"]
    if not data["by_source"]:
        lines.append("  nobody yet")
    for row in data["by_source"]:
        label = row["source"] + (f" / {row['campaign']}" if row["campaign"]
                                 else "")
        lines.append(f"  {label:<26} {row['signups']:>4} signed up, "
                     f"{row['activated']:>3} sent a letter")

    letters = data["letters"]
    lines += ["", "LETTERS"]
    lines.append(f"  {letters['sent']} sent by {letters['senders']} people, "
                 f"{letters['replies']} replies ({letters['reply_rate'].strip()})")
    if letters["senders"]:
        lines.append(f"  {letters['sent_per_sender']} letters and "
                     f"{letters['replies_per_sender']} replies per sender")

    r = data["retention"]
    lines += ["", "COMING BACK"]
    lines.append(f"  {r['returned']} of {r['eligible']} accounts older than "
                 f"{r['days']} days came back ({r['rate'].strip()})")

    ref = data["referrals"]
    lines += ["", "REFERRALS"]
    lines.append(f"  {ref['referred_users']} arrived on somebody's link")
    lines.append(f"  {ref['users_who_referred']} people have referred somebody "
                 f"({ref['share_referring'].strip()} of all accounts)")
    lines.append(f"  {ref['months_given']} free months given away")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true",
                    help="machine-readable, for the weekly email")
    args = ap.parse_args(argv)
    data = collect()
    print(json.dumps(data, indent=1) if args.json else report(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
