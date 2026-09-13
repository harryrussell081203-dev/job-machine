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

from . import db, delivery
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


def check_for_user(user_id: int, *, finder=None) -> ReplyReport:
    """Flag every sent application whose employer has been in touch.

    `finder` is injected so the tests exercise this without a mailbox.
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
        address, _, _, password = db.mail_login(user_id)
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
    for address_found in answered:
        for draft in by_address.get(address_found, []):
            db.mark_reply_seen(user_id, draft["id"])
            report.found += 1
    return report
