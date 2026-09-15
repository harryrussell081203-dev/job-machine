"""Noticing that an employer has answered.

The tracker used to depend entirely on the user remembering to come back and
tap a button. That is the part of any job-search tool people stop doing after
the first fortnight, and a tracker nobody updates shows a wall of "waiting"
that gets less true every day.

So the sweep now looks. What it is allowed to look AT is the whole design:

  - Only addresses this user has already sent an application to. Nothing else
    in the mailbox is asked about, so nothing else can be learned.
  - Only whether a message exists. SEARCH FROM returns message ids; no FETCH,
    no subject, no body, no attachment, no sender name.
  - Only read-only. The INBOX is selected readonly, so nothing is marked read,
    moved, flagged or deleted by us.
  - Only accounts whose owner connected a mailbox for sending anyway. The
    credential is the same one; this adds no new ask and no new scope.

And what it records is deliberately weaker than what the user records.
`reply_seen_at` means "something arrived from them". It is not an outcome,
because an IMAP search cannot tell a real reply from "thank you for your
application, please apply through our portal" - and the landing page promises
in as many words that autoresponders are not counted. The machine surfaces
the row; the person who can actually read it says what it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config, db, delivery
from .vault import VaultError


@dataclass
class ReplyReport:
    checked: int = 0                       # addresses asked about
    found: int = 0                         # employers who had been in touch
    reason: str = ""                       # why nothing was checked
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        if self.reason and not self.checked:
            return f"inbox not checked: {self.reason}"
        return f"{self.found} new repl(ies) across {self.checked} application(s)"


def check_for_user(user_id: int, *, finder=None, notify=None) -> ReplyReport:
    """Flag every sent application whose employer has been in touch.

    `finder` and `notify` are injected so the tests exercise this without a
    mailbox.
    """
    report = ReplyReport()

    account = db.get_mail_account(user_id)
    if not account:
        report.reason = "no mail account connected"
        return report
    if not account["verified_at"]:
        # The password has not been proved yet. Trying it here would be a
        # second failed login against a mailbox that may already be close to
        # locking, for a feature nobody is waiting on.
        report.reason = "mail account not verified yet"
        return report

    waiting = db.drafts_awaiting_reply(user_id)
    if not waiting:
        report.reason = "nothing is waiting on a reply"
        return report

    try:
        address, smtp_host, smtp_port, password = db.mail_login(user_id)
    except VaultError as exc:
        report.reason = str(exc)
        return report

    guessed = delivery.guess_imap_host(address)
    if not guessed:
        # Their provider is not one we know the IMAP host for. Sending still
        # works; this one feature does not, and saying so is better than
        # guessing a hostname and reporting the timeout as a failure.
        report.reason = f"no known inbox server for {address.split('@')[-1]}"
        return report
    host, port = guessed

    by_address = {}
    for draft in waiting:
        by_address.setdefault((draft["to_email"] or "").strip().lower(),
                              []).append(draft)

    look = finder or delivery.find_replies
    try:
        answered = look(host=host, port=port, username=address,
                        password=password, addresses=list(by_address))
    except delivery.DeliveryError as exc:
        # Unreachable or refused. Leave every draft exactly as it was: an
        # empty result here would be indistinguishable from a quiet week, and
        # would quietly stop the feature working while looking fine.
        db.note_mail_error(user_id, str(exc))
        report.reason = str(exc)
        report.errors.append(str(exc))
        return report

    report.checked = len(by_address)
    newly = []
    for address_found in answered:
        for draft in by_address.get(address_found, []):
            db.mark_reply_seen(user_id, draft["id"])
            report.found += 1
            company = (draft["company"] or "").strip()
            if company and company not in newly:
                newly.append(company)

    if newly:
        _tell_them(user_id, newly, address=address, password=password,
                   host=smtp_host, port=smtp_port, notify=notify)
    return report


def _tell_them(user_id, companies, *, address, password, host, port,
               notify=None) -> None:
    """Email the user that somebody has answered.

    A tracker that quietly updates itself is only useful to somebody who
    happens to open it. Rob at Alexander James replied, and then rang, and the
    first Harry knew of either was being asked about it days later - so the
    machine noticing is worth nothing on its own. The alert is the feature.

    Sent from the user's own mailbox to the same mailbox, so it costs nothing,
    needs no new service, and cannot be mistaken for spam by the one person it
    is for. It is sent ONCE per employer: a draft is flagged only once and
    drafts_awaiting_reply excludes anything already flagged, so there is no
    path by which this repeats every sweep.

    Failing to send it must never lose the flag that has already been
    recorded. The tracker is the source of truth; this is a nudge towards it.
    """
    try:
        names = ", ".join(companies[:5])
        if len(companies) > 5:
            names += f" and {len(companies) - 5} more"
        one = len(companies) == 1
        subject = (f"{companies[0]} has been in touch" if one
                   else f"{len(companies)} employers have been in touch")
        body = (
            f"{names} {'has' if one else 'have'} replied to "
            f"{'an application' if one else 'applications'} you sent.\n\n"
            # Said plainly, because the alternative is somebody believing the
            # app read their mail.
            "This only knows that something arrived from them - not what it "
            "says. It is in your inbox now; have a look and it is two taps to "
            "tell the tracker what happened.\n\n"
            f"{config.BASE_URL}/applications\n")
        send = notify or delivery.send_via_smtp
        send(host=host, port=port, username=address, password=password,
             to_email=address, subject=subject, body=body)
    except Exception as exc:
        # An alert that cannot be sent is a worse day, not a lost reply.
        print(f"[replies] user {user_id}: could not send the alert: {exc}")
