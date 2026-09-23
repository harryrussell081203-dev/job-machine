"""The end-of-day email: what went out, who wrote back, what is waiting.

The personal machine this replaced sent its owner one of these every night,
and it is how he knew the thing was working without opening anything. It
goes from the user's own mailbox to the same mailbox, so it costs nothing,
needs no new service, and cannot land in anybody else's inbox.

Once a day, from the last sweep of the afternoon onwards. A day on which
nothing happened sends nothing: "nothing happened" every evening is how an
email gets filtered and the one day it matters goes unread.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config, db, delivery
from .vault import VaultError

# The last scheduled sweep is 16:40 UTC. Anything from 16:00 on counts as
# "the end of the day", so a late-running sweep still sends it.
FROM_HOUR_UTC = 16
MIN_GAP = 20 * 3600


def due(settings, stamp: int) -> bool:
    if not settings["digest"]:
        return False
    if datetime.fromtimestamp(stamp, tz=timezone.utc).hour < FROM_HOUR_UTC:
        return False
    last = settings["last_digest_at"]
    return not last or stamp - int(last) >= MIN_GAP


def compose(day: dict) -> tuple[str, str] | None:
    letters, nudges, replies = day["letters"], day["followups"], day["replies"]
    if not (letters or nudges or replies):
        return None
    parts = []
    if replies:
        parts.append(f"{len(replies)} replied" if len(replies) != 1
                     else "1 reply")
    parts.append(f"{len(letters)} application{'s' if len(letters) != 1 else ''}")
    subject = "Recruited today: " + ", ".join(parts)

    lines = []
    if replies:
        lines.append("Somebody wrote back - it is in your inbox now:")
        lines += [f"  - {r['company']} ({r['job_title']})" for r in replies]
        lines.append("")
    if letters:
        lines.append("Applications sent:")
        lines += [f"  - {r['company']} - {r['to_email']}" for r in letters]
        lines.append("")
    if nudges:
        lines.append("Follow-ups sent (one each, never again):")
        lines += [f"  - {r['company']}" for r in nudges]
        lines.append("")
    if day["waiting"]:
        lines.append(f"{day['waiting']} more written and waiting to go.")
        lines.append("")
    lines.append(f"{config.BASE_URL}/applications")
    return subject, "\n".join(lines) + "\n"


def send_for_user(user_id: int, *, now=None, sender=None) -> bool:
    stamp = db.now() if now is None else now
    settings = db.get_send_settings(user_id)
    if not due(settings, stamp):
        return False
    account = db.get_mail_account(user_id)
    if not account or not account["verified_at"] or account["kind"] != "own":
        return False
    since = int(settings["last_digest_at"] or stamp - 86400)
    message = compose(db.day_so_far(user_id, since))
    # Marked whether or not there was anything to say, so a quiet day is not
    # re-examined by every later sweep.
    db.save_send_settings(user_id, last_digest_at=stamp)
    if message is None:
        return False
    try:
        address, host, port, password = db.mail_login(user_id)
    except VaultError:
        return False
    subject, body = message
    try:
        (sender or delivery.send_via_smtp)(
            host=host, port=port, username=address, password=password,
            to_email=address, subject=subject, body=body)
    except delivery.DeliveryError as exc:
        print(f"[digest] user {user_id}: could not send: {exc}")
        return False
    return True
