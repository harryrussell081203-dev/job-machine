"""The numbers behind /admin.

Kept out of main.py because the arithmetic is the part worth testing, and
kept out of db.py because it is arithmetic rather than storage.

What this deliberately does not do is answer "how many people replied". The
app never reads anybody's mailbox, so a reply rate here would be invented.
The honest version of that number has to come from asking the user.
"""

from __future__ import annotations

import time

DAY = 86400
WEEK = 7 * DAY

# In the order somebody moves through them. Each entry is a stage name and the
# test for having done that particular thing.
STAGES = [
    ("Signed up", lambda r: True),
    ("Uploaded a CV", lambda r: bool(r.get("cv_at"))),
    ("Finished setup", lambda r: bool(r.get("profile_at"))),
    ("Got a letter written", lambda r: (r.get("drafts") or 0) > 0),
    ("Sent one", lambda r: (r.get("sent") or 0) > 0),
    ("Connected their email", lambda r: bool(r.get("mail_at"))),
]


def funnel(rows: list[dict]) -> list[dict]:
    """How many people reached each stage, or anything past it.

    "Or anything past it" is the whole point. The stages are not actually
    compulsory - a profile can be filled in by hand without ever uploading a
    CV - so counting each stage on its own produces a funnel that goes up in
    the middle, and a funnel that goes up reads as a broken page rather than
    as a person who skipped a step. Counting everyone at or beyond a stage is
    both monotonic and true.
    """
    out = []
    for i, (name, _) in enumerate(STAGES):
        later = [test for _, test in STAGES[i:]]
        count = sum(1 for r in rows if any(test(r) for test in later))
        out.append({"name": name, "count": count})
    total = out[0]["count"] if out else 0
    for stage in out:
        stage["percent"] = round(100 * stage["count"] / total) if total else 0
    return out


def signups_by_week(rows: list[dict], weeks: int = 8, now: float | None = None):
    """Newest week last, so it reads left to right like a chart."""
    now = time.time() if now is None else now
    buckets = [0] * weeks
    for r in rows:
        created = r.get("created_at") or 0
        index = weeks - 1 - int((now - created) // WEEK)
        if 0 <= index < weeks:
            buckets[index] += 1
    peak = max(buckets) or 1
    return [{"weeks_ago": weeks - 1 - i, "count": n,
             "height": round(100 * n / peak)}
            for i, n in enumerate(buckets)]


def summarise(rows: list[dict], now: float | None = None) -> dict:
    now = time.time() if now is None else now

    def seen_within(days):
        cutoff = now - days * DAY
        return sum(1 for r in rows if (r.get("last_seen_at") or 0) > cutoff)

    drafts = sum(r.get("drafts") or 0 for r in rows)
    sent = sum(r.get("sent") or 0 for r in rows)
    discarded = sum(r.get("discarded") or 0 for r in rows)

    # Signed up long enough ago to have finished, and did not. This is the
    # only number on the page that names a problem rather than a total, so it
    # is the one worth looking at first.
    stalled = [r for r in rows
               if not r.get("profile_at")
               and (r.get("created_at") or 0) < now - 2 * DAY]

    return {
        "people": len(rows),
        "active_7d": seen_within(7),
        "active_30d": seen_within(30),
        "paying": sum(1 for r in rows
                      if r.get("subscription_status") in ("active", "trialing")),
        "drafts": drafts,
        "sent": sent,
        "discarded": discarded,
        # Of the letters that were decided about at all. A draft still sitting
        # in the queue has not been rejected, and counting it as one would
        # make a busy week look like a bad one.
        "send_rate": round(100 * sent / (sent + discarded)) if sent + discarded else None,
        "stalled": stalled,
        "funnel": funnel(rows),
        "weeks": signups_by_week(rows, now=now),
    }


def ago(when, now: float | None = None) -> str:
    """A timestamp as a person would say it out loud."""
    if not when:
        return "never"
    now = time.time() if now is None else now
    seconds = max(0, now - when)
    if seconds < 3600:
        return "just now" if seconds < 300 else f"{int(seconds // 60)}m ago"
    if seconds < DAY:
        return f"{int(seconds // 3600)}h ago"
    days = int(seconds // DAY)
    if days < 14:
        return "yesterday" if days == 1 else f"{days}d ago"
    if days < 60:
        return f"{days // 7}w ago"
    return f"{days // 30}mo ago"
