"""Storage. SQLite, because this app does not need more than SQLite.

A paid product for a few hundred people is not a distributed systems problem.
One file, WAL mode, and honest indexes will carry this a long way, and moving
to Postgres later is a schema copy rather than a rewrite.

What is deliberately NOT stored:

  - passwords. There are none; sign-in is a signed magic link.
  - anybody's mail credentials. See delivery.py.
  - the contents of scraped pages. Only the address that came out of one.
"""

from __future__ import annotations

import json
import time

from . import config

from .store import connect, describe, init, insert_returning_id, ping  # noqa: F401


def now() -> int:
    return int(time.time())


# ----------------------------------------------------------------------
# users
# ----------------------------------------------------------------------
def get_or_create_user(email: str):
    email = email.strip().lower()
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            c.execute("UPDATE users SET last_seen_at = ? WHERE id = ?",
                      (now(), row["id"]))
            return row
        c.execute("INSERT INTO users (email, created_at, last_seen_at) "
                  "VALUES (?, ?, ?)", (email, now(), now()))
        return c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def get_user_by_email(email: str):
    with connect() as c:
        return c.execute("SELECT * FROM users WHERE email = ?",
                         (email.strip().lower(),)).fetchone()


def get_user(user_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def is_paid(user) -> bool:
    """The single source of truth for whether the paywall opens.

    Deliberately strict: an unknown status is unpaid, and an expired
    paid_until is unpaid even if the status still reads active, because a
    cancelled-then-expired subscription can leave the latter stale.

    Two ways in are not Stripe: FREE_ACCESS_EMAILS, the people the app was
    given to rather than sold to, and a free launch place already claimed.
    Both are checked before the status columns so that a stale or cancelled
    Stripe record cannot shut out somebody who was never a customer in the
    first place.
    """
    if not config.BILLING_ENABLED:
        return True
    if user is None:
        return False
    if (user["email"] or "").strip().lower() in config.FREE_ACCESS_EMAILS:
        return True
    # Claimed, not claimable. Somebody who has a place keeps it even after
    # FREE_SPOTS is turned down to zero, because taking back what was given
    # is not something a config change should be able to do quietly.
    if _has_free_spot(user):
        return True
    if user["subscription_status"] not in ("active", "trialing"):
        return False
    until = user["paid_until"]
    return until is None or until > now()


def _has_free_spot(user) -> bool:
    """Tolerant of a row read before the column existed."""
    try:
        return bool(user["free_spot"])
    except (KeyError, IndexError, TypeError):
        return False


def free_spots_taken() -> int:
    with connect() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM users WHERE free_spot = 1").fetchone()
    return int(row["n"] or 0)


def free_spots_left() -> int:
    return max(0, config.FREE_SPOTS - free_spots_taken())


def claim_free_spot(user_id: int) -> bool:
    """Take one of the launch places for this account, if any are left.

    One statement, because the count and the write have to be the same
    decision. Two signups arriving together against a pool of one would
    otherwise both read "one left" and both take it, and the app would have
    given away a place it does not have.
    """
    if config.FREE_SPOTS <= 0:
        return False
    with connect() as c:
        c.execute(
            "UPDATE users SET free_spot = 1 "
            "WHERE id = ? AND free_spot = 0 "
            "  AND (SELECT COUNT(*) FROM users u2 WHERE u2.free_spot = 1) < ?",
            (user_id, config.FREE_SPOTS))
        row = c.execute("SELECT free_spot FROM users WHERE id = ?",
                        (user_id,)).fetchone()
    return bool(row and row["free_spot"])


def set_billing(user_id: int, *, customer_id=None, subscription_id=None,
                status=None, paid_until=None) -> None:
    sets, args = [], []
    for col, val in (("stripe_customer_id", customer_id),
                     ("stripe_subscription_id", subscription_id),
                     ("subscription_status", status),
                     ("paid_until", paid_until)):
        if val is not None:
            sets.append(f"{col} = ?")
            args.append(val)
    if not sets:
        return
    args.append(user_id)
    with connect() as c:
        c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", args)


def delete_user(user_id: int) -> None:
    """Erase a person from this system.

    Everything else about them hangs off users(id) with ON DELETE CASCADE, so
    one row goes and the profile, drafts, contacted list and block list go with
    it. Nothing is kept "for analytics" - a deletion request that leaves a
    shadow copy is not a deletion.
    """
    with connect() as c:
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))


def user_by_stripe_customer(customer_id: str):
    with connect() as c:
        return c.execute("SELECT * FROM users WHERE stripe_customer_id = ?",
                         (customer_id,)).fetchone()


# ----------------------------------------------------------------------
# what everybody has actually done, for /admin
# ----------------------------------------------------------------------
#
# One row per person, every milestone as the timestamp it happened at, so the
# funnel and the per-user table are the same query read two ways.
#
# The aggregation is done in Python rather than SQL because this file has to
# run on SQLite and Postgres both, and date bucketing is where those two
# dialects diverge hardest. Correlated subqueries are the portable option and
# the right one at this size: a few hundred customers is a few hundred index
# lookups. If this ever gets slow it wants a rewrite, not an index.
_OVERVIEW = """
SELECT u.id, u.email, u.created_at, u.last_seen_at,
       u.subscription_status, u.paid_until, u.stripe_subscription_id,
       u.free_spot,
       (SELECT uploaded_at FROM cvs      WHERE user_id = u.id) AS cv_at,
       (SELECT updated_at  FROM profiles WHERE user_id = u.id) AS profile_at,
       (SELECT verified_at FROM mail_accounts WHERE user_id = u.id) AS mail_at,
       (SELECT COUNT(*) FROM drafts WHERE user_id = u.id) AS drafts,
       (SELECT COUNT(*) FROM drafts WHERE user_id = u.id
                                      AND status = 'sent') AS sent,
       (SELECT COUNT(*) FROM drafts WHERE user_id = u.id
                                      AND status = 'discarded') AS discarded,
       (SELECT MAX(sent_at) FROM drafts WHERE user_id = u.id
                                          AND status = 'sent') AS last_sent_at
FROM users u
ORDER BY u.created_at DESC
"""


def overview() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(_OVERVIEW).fetchall()]


# ----------------------------------------------------------------------
# magic-link replay protection
# ----------------------------------------------------------------------
def claim_token(jti: str) -> bool:
    """True the first time a token id is seen, False every time after.

    A signed link stays valid until it expires, so without this a link
    forwarded, logged by a mail scanner, or left in a browser history is a
    working key for fifteen minutes.
    """
    with connect() as c:
        # ON CONFLICT DO NOTHING ... RETURNING is understood by both backends,
        # and says exactly what this needs: a row comes back only the first
        # time this token id is seen.
        row = c.execute(
            "INSERT INTO login_tokens (jti, used_at) VALUES (?, ?) "
            "ON CONFLICT (jti) DO NOTHING RETURNING jti", (jti, now())
        ).fetchone()
        return row is not None


# ----------------------------------------------------------------------
# profiles
# ----------------------------------------------------------------------
def save_profile(user_id: int, data: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO profiles (user_id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, "
            "updated_at = excluded.updated_at",
            (user_id, json.dumps(data), now()))


def load_profile(user_id: int):
    with connect() as c:
        row = c.execute("SELECT data FROM profiles WHERE user_id = ?",
                        (user_id,)).fetchone()
    return json.loads(row["data"]) if row else None


# ----------------------------------------------------------------------
# drafts
# ----------------------------------------------------------------------
def add_draft(user_id: int, **fields) -> int:
    cols = ["user_id", "created_at"] + list(fields)
    vals = [user_id, now()] + list(fields.values())
    with connect() as c:
        return insert_returning_id(c, "drafts", cols, vals)


def list_drafts(user_id: int, status: str = "draft", limit: int = 50):
    with connect() as c:
        return c.execute(
            "SELECT * FROM drafts WHERE user_id = ? AND status = ? "
            "ORDER BY contact_tier DESC, score DESC, created_at DESC LIMIT ?",
            (user_id, status, limit)).fetchall()


def get_draft(user_id: int, draft_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM drafts WHERE id = ? AND user_id = ?",
                         (draft_id, user_id)).fetchone()


def mark_draft(user_id: int, draft_id: int, status: str) -> None:
    with connect() as c:
        c.execute(
            "UPDATE drafts SET status = ?, sent_at = ? WHERE id = ? AND user_id = ?",
            (status, now() if status == "sent" else None, draft_id, user_id))


def seen_ids(user_id: int) -> set:
    with connect() as c:
        return {r["external_id"] for r in c.execute(
            "SELECT external_id FROM seen_listings WHERE user_id = ?",
            (user_id,))}


def mark_seen(user_id: int, external_id: str, outcome: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO seen_listings "
                  "(user_id, external_id, outcome, seen_at) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT (user_id, external_id) DO UPDATE SET "
                  "outcome = excluded.outcome, seen_at = excluded.seen_at",
                  (user_id, external_id, outcome[:200], now()))


def recent_outcomes(user_id: int, limit: int = 40):
    """Why listings did not become drafts. 'Nothing today' needs a reason."""
    with connect() as c:
        return c.execute(
            "SELECT external_id, outcome, seen_at FROM seen_listings "
            "WHERE user_id = ? AND outcome != 'drafted' "
            "ORDER BY seen_at DESC LIMIT ?", (user_id, limit)).fetchall()


def counts(user_id: int) -> dict:
    with connect() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) n FROM drafts WHERE user_id = ? "
            "GROUP BY status", (user_id,)).fetchall()
    return {r["status"]: r["n"] for r in rows}


# ----------------------------------------------------------------------
# who must not be written to
# ----------------------------------------------------------------------
def company_key(name: str) -> str:
    """Shared with the pipeline, so a company blocked here is the same company
    the harvester skips. See jobseeker/names.py for why it over-matches."""
    from jobseeker.names import company_key as _key
    return _key(name)


def already_contacted(user_id: int, company: str) -> bool:
    with connect() as c:
        return c.execute(
            "SELECT 1 FROM contacted WHERE user_id = ? AND company_key = ?",
            (user_id, company_key(company))).fetchone() is not None


def record_contacted(user_id: int, company: str) -> None:
    with connect() as c:
        # DO NOTHING, not DO UPDATE: first_at is when this employer was first
        # written to, and a later run must not move that date.
        c.execute("INSERT INTO contacted (user_id, company_key, first_at) "
                  "VALUES (?, ?, ?) ON CONFLICT (user_id, company_key) "
                  "DO NOTHING", (user_id, company_key(company), now()))


def is_blocked(user_id: int, company: str) -> bool:
    with connect() as c:
        return c.execute(
            "SELECT 1 FROM do_not_contact WHERE user_id = ? AND company_key = ?",
            (user_id, company_key(company))).fetchone() is not None


def block_company(user_id: int, company: str, reason: str = "") -> None:
    with connect() as c:
        c.execute("INSERT INTO do_not_contact (user_id, company_key, reason, "
                  "added_at) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT (user_id, company_key) DO NOTHING",
                  (user_id, company_key(company), reason, now()))


def may_contact(user_id: int, company: str) -> bool:
    return not is_blocked(user_id, company) and not already_contacted(user_id, company)


# ----------------------------------------------------------------------
# the user's own mail account, for sending as them
# ----------------------------------------------------------------------
def save_mail_account(user_id: int, *, address: str, host: str, port: int,
                      password: str) -> None:
    """Store credentials, encrypted. Never call this with a password that has
    not just been proved to work - see delivery.verify()."""
    from . import vault
    secret = vault.encrypt(password)
    with connect() as c:
        c.execute(
            "INSERT INTO mail_accounts (user_id, address, host, port, secret, "
            "verified_at, last_error, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "address = excluded.address, host = excluded.host, "
            "port = excluded.port, secret = excluded.secret, "
            "verified_at = excluded.verified_at, last_error = NULL, "
            "updated_at = excluded.updated_at",
            (user_id, address.strip().lower(), host.strip(), int(port),
             secret, now(), now()))


def get_mail_account(user_id: int):
    """The stored row, secret still encrypted. Callers that need to send use
    mail_login() instead, so the plaintext has exactly one path out."""
    with connect() as c:
        return c.execute("SELECT * FROM mail_accounts WHERE user_id = ?",
                         (user_id,)).fetchone()


def mail_login(user_id: int):
    """(address, host, port, password) or None. The only place a stored mail
    password is decrypted."""
    from . import vault
    row = get_mail_account(user_id)
    if not row:
        return None
    return (row["address"], row["host"], int(row["port"]),
            vault.decrypt(row["secret"]))


def note_mail_error(user_id: int, message: str) -> None:
    """Record why sending failed, so the user is told rather than left
    wondering why nothing arrives."""
    with connect() as c:
        c.execute("UPDATE mail_accounts SET last_error = ? WHERE user_id = ?",
                  ((message or "")[:300], user_id))


def forget_mail_account(user_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM mail_accounts WHERE user_id = ?", (user_id,))


# ----------------------------------------------------------------------
# the CV
# ----------------------------------------------------------------------
def save_cv(user_id: int, *, filename: str, content_type: str, blob: bytes,
            extracted: str = "") -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO cvs (user_id, filename, content_type, blob, "
            "extracted, uploaded_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET filename = excluded.filename, "
            "content_type = excluded.content_type, blob = excluded.blob, "
            "extracted = excluded.extracted, uploaded_at = excluded.uploaded_at",
            (user_id, filename[:200], content_type[:100], blob,
             (extracted or "")[:20000], now()))


def get_cv(user_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM cvs WHERE user_id = ?",
                         (user_id,)).fetchone()


def cv_summary(user_id: int):
    """Filename and size without dragging the whole file out of the database
    to render a settings page."""
    row = get_cv(user_id)
    if not row:
        return None
    return {"filename": row["filename"], "bytes": len(row["blob"]),
            "uploaded_at": row["uploaded_at"]}


def delete_cv(user_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM cvs WHERE user_id = ?", (user_id,))


# ----------------------------------------------------------------------
# how letters go out
# ----------------------------------------------------------------------
DEFAULT_SEND_SETTINGS = {"auto_send": 0, "hold_minutes": 60, "daily_cap": 12,
                         "search_days": 2, "paused_until": None}


def get_send_settings(user_id: int) -> dict:
    with connect() as c:
        row = c.execute("SELECT * FROM send_settings WHERE user_id = ?",
                        (user_id,)).fetchone()
    if not row:
        return dict(DEFAULT_SEND_SETTINGS)
    return {"auto_send": int(row["auto_send"] or 0),
            "hold_minutes": int(row["hold_minutes"] or 0),
            "daily_cap": int(row["daily_cap"] or 0),
            # A row written before search_days existed has no such key, and a
            # row written after a failed migration has NULL. Both mean "the
            # default", not "look back zero days and find nothing".
            "search_days": int(row["search_days"] or 0) or 2,
            "paused_until": row["paused_until"]}


def save_send_settings(user_id: int, **fields) -> None:
    current = get_send_settings(user_id)
    current.update({k: v for k, v in fields.items() if k in current})
    with connect() as c:
        c.execute(
            "INSERT INTO send_settings (user_id, auto_send, hold_minutes, "
            "daily_cap, search_days, paused_until, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET auto_send = excluded.auto_send, "
            "hold_minutes = excluded.hold_minutes, "
            "daily_cap = excluded.daily_cap, "
            "search_days = excluded.search_days, "
            "paused_until = excluded.paused_until, "
            "updated_at = excluded.updated_at",
            (user_id, int(bool(current["auto_send"])),
             int(current["hold_minutes"]), int(current["daily_cap"]),
             int(current["search_days"]), current["paused_until"], now()))


def record_sent(user_id: int, *, draft_id, to_email: str, company: str,
                ok: bool = True, error: str = "") -> None:
    with connect() as c:
        insert_returning_id(
            c, "sent_log",
            ["user_id", "draft_id", "to_email", "company", "sent_at", "ok",
             "error"],
            [user_id, draft_id, to_email, company, now(), int(bool(ok)),
             (error or "")[:300]])


def sent_today(user_id: int) -> int:
    """How many letters have actually left today, for the daily cap.

    Counts only successes: a failed send did not reach anybody and must not
    eat into the allowance.
    """
    since = now() - 86400
    with connect() as c:
        row = c.execute(
            "SELECT COUNT(*) n FROM sent_log WHERE user_id = ? "
            "AND sent_at > ? AND ok = 1", (user_id, since)).fetchone()
    return int(row["n"])


def drafts_due(user_id: int, *, hold_minutes: int, limit: int = 50):
    """Drafts old enough to send, oldest first.

    Oldest first matters: it makes the hold window mean what it says, and it
    stops a burst of new drafts pushing an older one past its turn forever.
    """
    cutoff = now() - int(hold_minutes) * 60
    with connect() as c:
        return c.execute(
            "SELECT * FROM drafts WHERE user_id = ? AND status = 'draft' "
            "AND created_at <= ? ORDER BY created_at ASC LIMIT ?",
            (user_id, cutoff, limit)).fetchall()


def paid_user_ids() -> list:
    """Everyone the scheduled sweep should run for.

    Filtered again through is_paid() by the caller, because the SQL here is a
    cheap prefilter and is_paid is the single source of truth.
    """
    with connect() as c:
        if not config.BILLING_ENABLED:
            # With the paywall off every account is paid, and none of them has
            # a subscription status to match on. Without this the sweep finds
            # nobody in exactly the configuration used to demo the product.
            rows = c.execute("SELECT id FROM users ORDER BY id").fetchall()
        else:
            rows = c.execute(
                "SELECT id FROM users WHERE subscription_status IN "
                "('active', 'trialing') ORDER BY id").fetchall()
    return [r["id"] for r in rows]
