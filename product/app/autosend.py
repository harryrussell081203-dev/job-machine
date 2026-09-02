"""Sending letters without being asked each time.

The user turns this on once and letters go out on their own. That is the
product they are paying for, and the drafts screen becomes somewhere to watch
rather than somewhere to work.

Four rules hold whether or not a human is looking, and three of them exist
because the person receiving the letter is real:

  - **One employer, one letter, ever.** Checked again here and not only at
    drafting time, because two drafts for the same company can both be sitting
    in the queue when the sweep runs.
  - **Never an employer who asked to be left alone.**
  - **A daily ceiling.** Somebody who never opens the app must not send four
    hundred letters over a weekend because their search terms were too broad.
  - **A holding window.** A draft waits before it goes. "Automatic" does not
    have to mean "irrevocable", and an employer will not read the letter in
    the next hour anyway, so the window is free. It is the difference between
    a bad letter being embarrassing and a bad letter being unrecallable.

On a rejected password this switches automatic sending **off** rather than
retrying. Repeatedly failing to authenticate against Gmail gets the account
flagged, and the user's own mailbox is not something to gamble with to save
them a click.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import db, delivery
from .vault import VaultError

MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class SendReport:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    reason: str = ""                       # why nothing was sent at all
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        if self.reason and not self.sent:
            return f"nothing sent: {self.reason}"
        return (f"{self.sent} sent, {self.failed} failed, "
                f"{self.skipped} skipped")


def send_due_for_user(user_id: int, *, now=None, sender=None) -> SendReport:
    """Send every draft that is due. Safe to call as often as you like.

    `sender` is injected so the tests exercise this whole path without a mail
    server. Nothing else in here knows how mail works.
    """
    report = SendReport()
    settings = db.get_send_settings(user_id)

    if not settings["auto_send"]:
        report.reason = "automatic sending is off"
        return report

    stamp = db.now() if now is None else now
    if settings["paused_until"] and settings["paused_until"] > stamp:
        report.reason = "sending is paused"
        return report

    account = db.get_mail_account(user_id)
    if not account:
        report.reason = "no mail account connected"
        return report

    try:
        address, host, port, password = db.mail_login(user_id)
    except VaultError as exc:
        # The key changed. Say so; do not leave the user thinking letters went.
        db.note_mail_error(user_id, str(exc))
        report.reason = str(exc)
        return report

    allowance = settings["daily_cap"] - db.sent_today(user_id)
    if allowance <= 0:
        report.reason = f"daily limit of {settings['daily_cap']} already reached"
        return report

    due = db.drafts_due(user_id, hold_minutes=settings["hold_minutes"])
    if not due:
        report.reason = "nothing is due yet"
        return report

    profile = db.load_profile(user_id) or {}
    display_name = (profile.get("name") or "").strip()
    cv_row = db.get_cv(user_id)
    attachment = ((cv_row["filename"], bytes(cv_row["blob"])) if cv_row
                  else None)

    send = sender or delivery.send_via_smtp
    # Companies written to during this run. already_contacted() is only
    # updated as we go, so without this two drafts for one employer that
    # arrived in the same sweep would both go out.
    done_now = set()
    # A mail server that is down is down for every draft in the queue.
    # Twenty doomed connections in a row is a waste at best and looks like
    # abuse at worst, so give up after a few and try again next sweep.
    consecutive_failures = 0

    for draft in due:
        if report.sent >= allowance:
            report.skipped += 1
            continue

        company = draft["company"] or ""
        key = db.company_key(company)
        if key in done_now or not db.may_contact(user_id, company):
            # Not a failure: the rule worked. Take it off the queue so it does
            # not come back every sweep.
            db.mark_draft(user_id, draft["id"], "skipped")
            report.skipped += 1
            continue

        if not (draft["to_email"] or "").strip():
            db.mark_draft(user_id, draft["id"], "skipped")
            report.skipped += 1
            continue

        try:
            send(host=host, port=port, username=address, password=password,
                 to_email=draft["to_email"], subject=draft["subject"],
                 body=draft["body"], attachment=attachment,
                 display_name=display_name)
        except delivery.DeliveryError as exc:
            message = str(exc)
            report.failed += 1
            report.errors.append(f"{company}: {message}")
            db.record_sent(user_id, draft_id=draft["id"],
                           to_email=draft["to_email"], company=company,
                           ok=False, error=message)
            db.note_mail_error(user_id, message)
            if _is_auth_failure(message):
                # Stop. Another twenty attempts with a bad password is how an
                # account gets locked, and every later draft would fail the
                # same way.
                db.save_send_settings(user_id, auto_send=0)
                report.reason = ("automatic sending was switched off because "
                                 "the mail account rejected the password")
                break
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                report.reason = (
                    f"stopped after {consecutive_failures} failures in a row - "
                    f"{message}")
                break
            continue

        consecutive_failures = 0
        db.mark_draft(user_id, draft["id"], "sent")
        db.record_contacted(user_id, company)
        db.record_sent(user_id, draft_id=draft["id"],
                       to_email=draft["to_email"], company=company, ok=True)
        done_now.add(key)
        report.sent += 1

    return report


def _is_auth_failure(message: str) -> bool:
    text = (message or "").lower()
    return "rejected the password" in text or "app password" in text


def sweep(*, ai=None, session=None, run=True, sender=None) -> dict:
    """One pass over every paying user: look for work, then send what is due.

    This is what makes the product run without anybody opening it. Called from
    a schedule, and deliberately tolerant - one user's broken profile must not
    stop everybody else's letters.
    """
    from . import runner
    totals = {"users": 0, "drafted": 0, "sent": 0, "failed": 0, "errors": []}

    for user_id in db.paid_user_ids():
        user = db.get_user(user_id)
        if not db.is_paid(user):
            continue
        totals["users"] += 1
        if run:
            try:
                report = runner.run_for_user(user_id, ai=ai, session=session)
                totals["drafted"] += report.drafted
            except Exception as exc:
                totals["errors"].append(f"user {user_id} run: {exc}")
        try:
            sent = send_due_for_user(user_id, sender=sender)
            totals["sent"] += sent.sent
            totals["failed"] += sent.failed
        except Exception as exc:
            totals["errors"].append(f"user {user_id} send: {exc}")

    return totals
