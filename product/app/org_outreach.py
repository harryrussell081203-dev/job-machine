"""The weekly letter to organisations that help people into work.

outreach.py decides who to write to and what the letter says; this runs it
for real, once a week, from .github/workflows/outreach.yml:

  - **Who it has written to lives in the database** (outreach_log), not in
    a file a fresh runner would not have. Each organisation is written to
    once, ever, whichever run it was.
  - **It sends through the site's own mail route** (Brevo), never the
    mailbox the job hunt runs on. Replies go to OUTREACH_REPLY_TO, an
    address a person reads.
  - **It sends nothing until OUTREACH_REPLY_TO is set** and OUTREACH_ENABLED
    is on. Until then each run is a rehearsal that prints the letters it
    would have sent.
  - **A week's quota is a week's quota.** A second run in the same week
    sends only what is left of OUTREACH_PER_RUN (default 20).

Switches: OUTREACH_ENABLED (default on), OUTREACH_REPLY_TO,
OUTREACH_PER_RUN.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from jobseeker import settings

from . import auth, config, db

WEEK = 7 * 86400
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


class LogState:
    """outreach.run's state, kept in outreach_log instead of a file."""

    def __contains__(self, key) -> bool:
        with db.connect() as c:
            return c.execute("SELECT 1 FROM outreach_log WHERE org_key = ?",
                             (key,)).fetchone() is not None

    def __setitem__(self, key, value) -> None:
        with db.connect() as c:
            c.execute("INSERT INTO outreach_log (org_key, name, address, "
                      "outcome, at) VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT (org_key) DO NOTHING",
                      (key, (value.get("name") or "")[:200],
                       value.get("address") or "", value.get("outcome") or "",
                       int(time.time())))


def sent_this_week(now: float | None = None) -> int:
    now = time.time() if now is None else now
    with db.connect() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM outreach_log WHERE "
                        "outcome = 'sent' AND at > ?",
                        (int(now - WEEK + 3600),)).fetchone()
    return int(row["n"] or 0)


def ready() -> tuple[bool, str]:
    if not settings.flag("OUTREACH_ENABLED"):
        return False, "OUTREACH_ENABLED is off"
    if not settings.text("OUTREACH_REPLY_TO"):
        return False, "OUTREACH_REPLY_TO is not set, so nothing is sent"
    if not config.mail_route():
        return False, "no mail route (BREVO_API_KEY and APP_SMTP_ADDRESS)"
    return True, ""


def run(orgs: list, *, session=None, send_mail=None, delay: float = 30,
        scrape_delay: float = 1, out=print) -> dict:
    import outreach

    per_run = int(settings.number("OUTREACH_PER_RUN", 20))
    live, why = ready()
    left = max(0, per_run - sent_this_week()) if live else per_run
    if live and not left:
        out(f"[outreach] this week's {per_run} have gone; nothing to do")
        return {"sent": 0, "reason": "quota"}
    if not live:
        out(f"[outreach] rehearsal: {why}")
    reply_to = settings.text("OUTREACH_REPLY_TO")

    def sender(to, subject, body):
        (send_mail or auth.send_app_email)(
            to, subject, body, reply_to=reply_to, sender_name="Harry Russell")

    done = outreach.run(orgs, LogState(), send=live, limit=left,
                        session=session, sender=sender if live else None,
                        delay=delay, scrape_delay=scrape_delay, out=out)
    if not live:
        for letter in done["letters"][:2]:
            out(f"\n--- would send to {letter['to']} ({letter['org']}) ---\n"
                f"Subject: {letter['subject']}\n\n{letter['body']}\n")
    done.pop("letters", None)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="weekly organisation outreach")
    ap.add_argument("--orgs", required=True,
                    help="the list tools/find_orgs.py wrote")
    args = ap.parse_args(argv)
    db.init()
    with open(args.orgs, encoding="utf-8") as f:
        orgs = json.load(f)
    print(f"[outreach] {len(orgs)} organisations on the list")
    print(f"[outreach] {run(orgs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
