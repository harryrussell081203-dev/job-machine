"""The weekly letter to organisations that help people into work.

outreach.py decides who to write to and what the letter says; this runs it
for real, once a week, from .github/workflows/outreach.yml:

  - **Who it has written to lives in the database** (outreach_log), not in
    a file a fresh runner would not have. Each organisation is written to
    once, ever, whichever run it was.
  - **It sends through the site's own mail route** (Brevo), never the
    mailbox the job hunt runs on. Replies go to OUTREACH_REPLY_TO, an
    address a person reads - or, when that is not set, to the address kept
    in the database (site_meta "outreach_reply_to"), which keeps a personal
    address out of a public repository's settings and logs.
  - **It sends nothing until OUTREACH_REPLY_TO is set** and OUTREACH_ENABLED
    is on. Until then each run is a rehearsal that prints the letters it
    would have sent.
  - **A week's quota is a week's quota.** A second run in the same week
    sends only what is left of OUTREACH_PER_RUN (default 40).

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


def reply_to_address() -> str:
    """Where replies go: the setting, else the address kept in the database."""
    address = settings.text("OUTREACH_REPLY_TO")
    if address:
        return address
    try:
        return (db.get_meta("outreach_reply_to") or "").strip()
    except Exception:
        return ""


# ----------------------------------------------------------------------
# Sending through the website.
#
# The mail service (Brevo) only takes mail from addresses it has approved,
# and a GitHub runner has a different one every run: the first live run on
# 16 October was refused before a single letter left. The website's address
# is approved - it sends every sign-in link - so the weekly job finds the
# organisations and writes the letters, and hands each one to the site to
# send. OUTREACH_SEND_VIA=site switches it on (the workflow sets it).
#
# The site end is not a relay for anything: the caller must hold the site's
# own secret, the letter must pass the same checks, the address must be an
# organisation's shared inbox, the sender name and reply address are the
# site's, never the caller's, and there is a daily cap.
# ----------------------------------------------------------------------
RELAY_PATH = "/internal/outreach/send"
RELAY_PER_DAY = 60


def via_site() -> bool:
    return settings.text("OUTREACH_SEND_VIA").lower() == "site"


def site_token() -> str:
    import hashlib
    import hmac
    return hmac.new((config.SECRET_KEY or "").encode(), b"outreach-send-v1",
                    hashlib.sha256).hexdigest()


def send_via_site(to: str, subject: str, body: str, *, post=None,
                  pause: float = 10) -> None:
    """Hand one letter to the website to send. The free instance may be
    asleep, so the first call can take a minute and is retried."""
    if post is None:
        import requests
        post = requests.post
    url = config.BASE_URL.rstrip("/") + RELAY_PATH
    last = ""
    for _ in range(3):
        try:
            r = post(url, json={"to": to, "subject": subject, "body": body},
                     headers={"Authorization": f"Bearer {site_token()}"},
                     timeout=90)
            status = getattr(r, "status_code", 0)
            if status == 200:
                return
            last = f"{status} {(getattr(r, 'text', '') or '')[:200]}"
            if status < 500:
                break           # refused on purpose: retrying will not help
        except Exception as exc:
            last = str(exc)
        if pause:
            time.sleep(pause)
    raise RuntimeError(f"the site would not send to {to}: {last}")


def relay(to: str, subject: str, body: str) -> tuple[int, dict]:
    """The site end: check, send once, remember. Returns (status, reply)."""
    import outreach
    from . import ratelimit
    to = (to or "").strip().lower()
    if not auth.valid_email(to) or not outreach.shared_inbox(to):
        return 422, {"ok": False, "why": "not an organisation's shared inbox"}
    problems = outreach.check(subject or "", body or "")
    if problems:
        return 422, {"ok": False, "why": problems}
    with db.connect() as c:
        if c.execute("SELECT 1 FROM outreach_log WHERE address = ? AND "
                     "outcome IN ('sent', 'relayed')", (to,)).fetchone():
            # Already sent, perhaps on a call whose answer was lost.
            return 200, {"ok": True, "already": True}
    reply_to = reply_to_address()
    if not reply_to:
        return 409, {"ok": False, "why": "no reply address"}
    if not ratelimit.hit("outreach:relay", limit=RELAY_PER_DAY, window=86400):
        return 429, {"ok": False, "why": "today's letters have gone"}
    auth.send_app_email(to, subject, body, reply_to=reply_to,
                        sender_name="Harry Russell")
    with db.connect() as c:
        c.execute("INSERT INTO outreach_log (org_key, name, address, outcome, "
                  "at) VALUES (?, '', ?, 'relayed', ?) "
                  "ON CONFLICT (org_key) DO NOTHING",
                  (f"relay:{to}", to, int(time.time())))
    return 200, {"ok": True}


def ready() -> tuple[bool, str]:
    if not settings.flag("OUTREACH_ENABLED"):
        return False, "OUTREACH_ENABLED is off"
    if not reply_to_address() and not via_site():
        return False, "OUTREACH_REPLY_TO is not set, so nothing is sent"
    if via_site():
        if not config.SECRET_KEY:
            return False, "SECRET_KEY is needed to send through the site"
        return True, ""
    if not config.mail_route():
        return False, "no mail route (BREVO_API_KEY and APP_SMTP_ADDRESS)"
    return True, ""


def run(orgs: list, *, session=None, send_mail=None, delay: float = 30,
        scrape_delay: float = 1, out=print) -> dict:
    import outreach

    per_run = int(settings.number("OUTREACH_PER_RUN", 40))
    live, why = ready()
    left = max(0, per_run - sent_this_week()) if live else per_run
    if live and not left:
        out(f"[outreach] this week's {per_run} have gone; nothing to do")
        return {"sent": 0, "reason": "quota"}
    if not live:
        out(f"[outreach] rehearsal: {why}")
    reply_to = reply_to_address()

    def sender(to, subject, body):
        if send_mail is None and via_site():
            send_via_site(to, subject, body)
            return
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
