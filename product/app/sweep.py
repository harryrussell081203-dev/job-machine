"""The scheduled run, for everybody.

    python -m app.sweep              # look for work, then send what is due
    python -m app.sweep --send-only  # only send; do not go looking
    python -m app.sweep --dry-run    # say what would happen, send nothing

This is the difference between an app somebody has to remember to open and a
machine that works for them. Run it from GitHub Actions three times a weekday
and a subscriber who never signs in still gets letters out.

It exits 0 unless the sweep could not run at all. One user's broken profile is
a normal Tuesday, not a failed job - failing the workflow for it would train
whoever owns the schedule to ignore red.
"""

from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the machine for every "
                                             "paying user.")
    ap.add_argument("--send-only", action="store_true",
                    help="skip harvesting; only send drafts already due")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be sent, without sending")
    args = ap.parse_args(argv)

    from . import autosend, db
    db.init()
    _say_billing_mode()

    if args.dry_run:
        return _dry_run()

    totals = autosend.sweep(run=not args.send_only)
    print(f"[sweep] {totals['users']} paying users, "
          f"{totals['drafted']} drafted, {totals['sent']} sent, "
          f"{totals.get('followups', 0)} followed up, "
          f"{totals['failed']} failed")
    for err in totals["errors"][:20]:
        print(f"[sweep] {err}", file=sys.stderr)
    return 0


def _say_billing_mode() -> None:
    """Print which side of the paywall this run is on.

    The sweep and the website read BILLING_ENABLED from two different places -
    GitHub secrets and the host's dashboard - and nothing makes them agree. If
    they disagree the failure is silent and goes both ways:

      - sweep off, website on: every account counts as paid here, so letters
        go out for people the website is still asking to pay
      - sweep on, website off: the sweep matches on a subscription status
        nobody has, finds nobody, and reports success for ever

    Neither raises. So this line exists to be READ - in the workflow log, next
    to the user count, where "paywall off, 2 users" is obviously wrong.
    """
    from . import config
    mode = "ON" if config.BILLING_ENABLED else ("OFF - every account counts "
                                                "as paid")
    print(f"[sweep] paywall {mode}")


def _dry_run() -> int:
    """What would go out, and to whom, without sending anything."""
    from . import db
    users = db.paid_user_ids()
    print(f"[sweep] {len(users)} paying users")
    for user_id in users:
        user = db.get_user(user_id)
        if not db.is_paid(user):
            continue
        settings = db.get_send_settings(user_id)
        account = db.get_mail_account(user_id)
        due = db.drafts_due(user_id, hold_minutes=settings["hold_minutes"])
        state = ("auto-send ON" if settings["auto_send"] else "auto-send off")
        mail = account["address"] if account else "no mail account"
        print(f"  user {user_id} ({user['email']}): {state}, {mail}, "
              f"{len(due)} due, {db.sent_today(user_id)} sent today "
              f"of {settings['daily_cap']}")
        for draft in due[:5]:
            print(f"      -> {draft['to_email']} at {draft['company']}: "
                  f"{draft['subject']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
