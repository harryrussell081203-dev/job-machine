"""One nudge, once, on a letter nobody has answered.

A short "still interested" a few days after the first letter is the single
cheapest thing that lifts replies, which is why the personal machine this
replaced sent them. It also sent a second nudge, and then a letter to a
second person at the same company, and on 22 and 23 September a run of those
landed on agencies that had already answered, sometimes three and four emails
deep. People noticed. So this is the conservative half of that and no more:

  - **One.** Never a second nudge, never a second person.
  - **Only letters this machine sent.** Something the user sent by hand is
    theirs to chase, and an imported letter was chased by whatever sent it.
  - **Never after anybody at the organisation has written back.** Asked of
    the inbox again right before sending, by domain, because the person who
    replies is rarely the address that was written to. If the inbox cannot
    be asked, nothing is sent - a silent inbox and an unreachable one must
    not look the same.
  - **Not after three weeks.** A nudge to a letter the reader has forgotten
    is a cold email with "Re:" on it.
  - **Inside the same daily ceiling** as the letters, which go first.
  - **Off until the user turns it on.** It is a second email in their name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config, db, delivery
from .vault import VaultError

AFTER_DAYS = 5
WITHIN_DAYS = 21
DAY = 86400


@dataclass
class FollowupReport:
    sent: int = 0
    skipped: int = 0
    answered: int = 0
    reason: str = ""
    errors: list = field(default_factory=list)


def body_for(draft, profile) -> str:
    name = (draft["to_name"] or "").strip()
    greeting = f"Hi {name}," if name else "Hi,"
    when = datetime.fromtimestamp(int(draft["sent_at"]), tz=timezone.utc)
    title = (draft["job_title"] or "").strip() or "the"
    role = f"the {title} role" if title != "the" else "the role"
    sign = "\n".join(x for x in ((profile.get("name") or "").strip(),
                                 (profile.get("phone") or "").strip()) if x)
    return (f"{greeting}\n\n"
            f"Following up on the note I sent on {when:%A} about {role}. "
            f"Still interested, and happy to do a short call whenever "
            f"suits.\n\n"
            f"Is it worth me sending anything else over?\n\n"
            f"{sign}\n")


def subject_for(draft) -> str:
    subject = (draft["subject"] or draft["job_title"] or "").strip()
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def send_for_user(user_id: int, *, now=None, sender=None,
                  finder=None) -> FollowupReport:
    report = FollowupReport()
    settings = db.get_send_settings(user_id)
    if not settings["follow_up"]:
        report.reason = "follow-ups are off"
        return report
    if not settings["auto_send"]:
        report.reason = "automatic sending is off"
        return report
    stamp = db.now() if now is None else now
    if settings["paused_until"] and settings["paused_until"] > stamp:
        report.reason = "sending is paused"
        return report

    account = db.get_mail_account(user_id)
    if not account or not account["verified_at"]:
        report.reason = "no verified mail account"
        return report
    if account["kind"] == "managed":
        # Replies to an issued address go to the user's own inbox, which this
        # cannot ask. A nudge without that check is exactly the failure this
        # module exists to prevent.
        report.reason = "cannot check replies on an issued address"
        return report

    allowance = settings["daily_cap"] - db.sent_today(user_id)
    if allowance <= 0:
        report.reason = "daily limit reached"
        return report

    due = db.drafts_due_followup(
        user_id, sent_before=stamp - AFTER_DAYS * DAY,
        sent_after=stamp - WITHIN_DAYS * DAY)
    if not due:
        report.reason = "nothing is due a follow-up"
        return report

    try:
        address, host, port, password = db.mail_login(user_id)
    except VaultError as exc:
        report.reason = str(exc)
        return report
    imap = delivery.guess_imap_host(address)
    if not imap:
        report.reason = "cannot check this inbox for replies"
        return report

    look = finder or delivery.find_replies
    try:
        answered = look(host=imap[0], port=imap[1], username=address,
                        password=password,
                        addresses=[d["to_email"] for d in due])
    except delivery.DeliveryError as exc:
        report.reason = f"could not check for replies: {exc}"
        report.errors.append(str(exc))
        return report
    answered = {a.strip().lower() for a in answered}

    profile = db.load_profile(user_id) or {}
    send = sender or delivery.send_via_smtp
    display_name = (profile.get("name") or "").strip()
    done_keys = set()
    for draft in due:
        to = (draft["to_email"] or "").strip()
        key = db.mail_key(to)
        if key and key in done_keys:
            # A second letter to the same place - it happens, JR Recruitment
            # got two on 13 September - is covered by the one nudge already
            # sent, and is marked so it is never nudged on its own later.
            db.record_followup(user_id, draft_id=draft["id"], to_email=to,
                               company=draft["company"] or "", sent=False)
            report.skipped += 1
            continue
        if to.lower() in answered:
            # Somebody there has been in touch. Put it in front of the user
            # and leave the employer alone.
            db.mark_reply_seen(user_id, draft["id"])
            report.answered += 1
            continue
        if (db.is_blocked(user_id, draft["company"] or "")
                or db.mail_blocked(user_id, to)):
            report.skipped += 1
            continue
        if report.sent >= allowance:
            report.skipped += 1
            continue
        try:
            send(host=host, port=port, username=address, password=password,
                 to_email=to, subject=subject_for(draft),
                 body=body_for(draft, profile), attachment=None,
                 display_name=display_name)
        except delivery.DeliveryError as exc:
            report.errors.append(f"{draft['company']}: {exc}")
            # One failure is a bad address or a bad minute; stop rather than
            # try every nudge against a server that is refusing.
            break
        db.record_followup(user_id, draft_id=draft["id"], to_email=to,
                           company=draft["company"] or "")
        if key:
            done_keys.add(key)
        report.sent += 1
    return report
