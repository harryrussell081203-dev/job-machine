"""Sending letters without being asked each time.

The user turns this on once and letters go out on their own. That is the
product they are paying for, and the drafts screen becomes somewhere to watch
rather than somewhere to work.

Four rules hold whether or not a human is looking, and three of them exist
because the person receiving the letter is real:

  - **One employer, one letter, ever.** Checked again here and not only at
    drafting time, because two drafts for the same company can both be sitting
    in the queue when the sweep runs. Checked by company name AND by where the
    letter is going, because one agency can advertise under two names.
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

from . import config, db, delivery, funnel
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


def send_due_for_user(user_id: int, *, now=None, sender=None,
                      verifier=None) -> SendReport:
    """Send every draft that is due. Safe to call as often as you like.

    `sender` and `verifier` are injected so the tests exercise this whole path
    without a mail server. Nothing else in here knows how mail works.
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

    # Credentials the web app could not check are proved here instead.
    #
    # The setup screen runs on a host whose outbound SMTP is blocked - free
    # plans shut ports 25, 465 and 587 almost everywhere, because that is how
    # spam gets sent - so it cannot tell a correct password from a wrong one
    # and stores it unchecked rather than accusing the user. This runs on
    # GitHub Actions, which is not blocked, and is therefore the first place
    # the question can actually be asked.
    #
    # It happens before the daily allowance and before the queue is read, so
    # somebody who has just connected a mailbox gets a definite answer on the
    # next sweep rather than waiting for a draft to come due. Once verified
    # it never runs again.
    if not account["verified_at"]:
        check = verifier or delivery.verify
        try:
            check(host=host, port=port,
                  username=(config.MANAGED_MAIL_USERNAME
                            if account["kind"] == "managed" else address),
                  password=password)
            db.mark_mail_verified(user_id)
        except delivery.DeliveryAuthError as exc:
            # A definite answer, and a bad one. Stop before a single letter,
            # switch sending off, and leave the reason where the user sees it
            # - silently retrying a rejected password every sweep is how a
            # mailbox gets locked.
            db.note_mail_error(user_id, str(exc))
            db.save_send_settings(user_id, auto_send=0)
            report.reason = ("automatic sending was switched off because the "
                             "mail account rejected the password")
            return report
        except delivery.DeliveryError as exc:
            # Still could not ask. Nothing is proved either way, so nothing is
            # sent and nothing is blamed on the user. Try again next sweep.
            db.note_mail_error(user_id, str(exc))
            report.reason = f"could not check the mail account yet: {exc}"
            return report

    allowance = settings["daily_cap"] - db.sent_today(user_id)
    if allowance <= 0:
        report.reason = f"daily limit of {settings['daily_cap']} already reached"
        return report

    # A Recruited address draws on a shared provider allowance rather than
    # the user's own mailbox, so there is a second ceiling above their own and
    # it belongs to everybody. Checked here rather than left to the provider:
    # going over does not degrade politely, it rejects, and a rejection would
    # be recorded as a failed send and shown to the user as though their
    # letter had bounced.
    managed = account["kind"] == "managed"
    if managed:
        shared_left = config.MANAGED_MAIL_DAILY_CAP - db.managed_sent_today()
        if shared_left <= 0:
            report.reason = ("today's shared sending allowance is used up - "
                             "your letters will go out tomorrow")
            return report
        allowance = min(allowance, shared_left)

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
        where = db.mail_key(draft["to_email"] or "")
        if (key in done_now or (where and where in done_now)
                or not db.may_contact(user_id, company,
                                      draft["to_email"] or "")):
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
            # On a managed account the SMTP username is the provider's
            # ("resend"), the From line is the issued address, and Reply-To is
            # the user's real inbox - so an employer's answer reaches them
            # directly and we never hold it.
            send(host=host, port=port,
                 username=config.MANAGED_MAIL_USERNAME if managed else address,
                 password=password,
                 to_email=draft["to_email"], subject=draft["subject"],
                 body=draft["body"], attachment=attachment,
                 display_name=display_name,
                 from_address=address if managed else "",
                 reply_to=account["reply_to"] if managed else "")
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
        # One transaction, because a process killed between marking the draft
        # and logging the letter leaves a delivered application that /numbers
        # cannot see and the daily cap does not count. That is not a
        # hypothetical - it is what the first real sweep did.
        db.record_delivered(user_id, draft_id=draft["id"],
                            to_email=draft["to_email"], company=company)
        db.record_contacted(user_id, company)
        db.record_mail_contacted(user_id, draft["to_email"])
        # The moment this account became a working one: a real letter, to a
        # real employer, out of their own mailbox. Called on every send rather
        # than guarded by "is this the first" - the event table answers that,
        # and a guard here would be a query that can race with a concurrent
        # sweep. Cannot raise; see funnel.py.
        funnel.reached(user_id, "first_email_sent", detail=company)
        # Separately, because this one pays referrals and a hand-marked send
        # must not: here the machine delivered the letter itself.
        funnel.reached(user_id, "first_auto_send", detail=company)
        if managed:
            # After the send, not before. Counting an attempt that then failed
            # would spend the shared allowance on letters nobody received.
            db.record_managed_send(user_id)
        done_now.add(key)
        if where:
            done_now.add(where)
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

    users = [u for u in (db.get_user(i) for i in db.paid_user_ids())
             if db.is_paid(u)]
    totals["users"] = len(users)

    # SEND FIRST, DRAFT SECOND, and in two separate passes over everybody.
    #
    # This used to be one loop doing both per user, and the first run that
    # ever had users to work on was killed by the 30-minute workflow timeout
    # while still drafting. Harvesting listings and scoring them through
    # Gemini is minutes per user; sending a letter that is already written and
    # already waiting is milliseconds. So the slow, optional half was standing
    # in front of the fast, essential half, and a run that ran out of time
    # delivered nothing at all.
    #
    # Worse, it starved by position: user 1's drafting could consume the whole
    # budget, so users further down the list never reached their own send step
    # however long their letters had been sitting there.
    #
    # Drafting is preparation and can wait for the next run; sending is the
    # job. If this is ever cut short again, it is cut short having already
    # done the thing anybody would have chosen to do first.
    for user in users:
        user_id = user["id"]
        # Notice what came back before writing anything new, so the tracker is
        # current even on a run that gets no further than this.
        try:
            from . import replies
            seen = replies.check_for_user(user_id)
            totals["replies"] = totals.get("replies", 0) + seen.found
        except Exception as exc:
            totals["errors"].append(f"user {user_id} inbox: {exc}")

        try:
            sent = send_due_for_user(user_id, sender=sender)
            totals["sent"] += sent.sent
            totals["failed"] += sent.failed
        except Exception as exc:
            totals["errors"].append(f"user {user_id} send: {exc}")

        # After the letters, so a nudge never takes a place in the daily
        # ceiling that a first letter wanted. Off unless the user turned it on.
        try:
            from . import followups
            nudged = followups.send_for_user(user_id, sender=sender)
            totals["followups"] = totals.get("followups", 0) + nudged.sent
            totals["errors"].extend(f"user {user_id} follow-up: {e}"
                                    for e in nudged.errors)
        except Exception as exc:
            totals["errors"].append(f"user {user_id} follow-up: {exc}")

        # Last, so it reports what this sweep just did.
        try:
            from . import digest
            digest.send_for_user(user_id, sender=sender)
        except Exception as exc:
            totals["errors"].append(f"user {user_id} digest: {exc}")

    if run:
        for user in users:
            user_id = user["id"]
            # Recorded exactly as a hand-started run is, so the dashboard can
            # say "last looked for work an hour ago" about a scheduled sweep
            # too. Without this the only runs the app could see were the ones
            # somebody pressed a button for, and a machine that works while
            # you are asleep is precisely the thing worth showing.
            #
            # start_run() refusing means that user already has a run in
            # flight, so the drafting still happens and only the bookkeeping
            # is skipped - a sweep must never be silently dropped to protect
            # a progress row.
            mine = db.start_run(user_id)
            if mine:
                db.set_run_step(user_id, "Looking for work")
            try:
                report = runner.run_for_user(
                    user_id, ai=ai, session=session,
                    on_step=((lambda text, **counts:
                              db.set_run_step(user_id, text, **counts))
                             if mine else None))
                totals["drafted"] += report.drafted
                if mine:
                    db.finish_run(user_id, result=report.summary(),
                                  drafted=report.drafted)
            except Exception as exc:
                totals["errors"].append(f"user {user_id} run: {exc}")
                if mine:
                    db.finish_run(user_id, result=f"It stopped: {exc}",
                                  ok=False)

    return totals
