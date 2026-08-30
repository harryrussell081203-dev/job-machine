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


def get_user(user_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def is_paid(user) -> bool:
    """The single source of truth for whether the paywall opens.

    Deliberately strict: an unknown status is unpaid, and an expired
    paid_until is unpaid even if the status still reads active, because a
    cancelled-then-expired subscription can leave the latter stale.
    """
    if not config.BILLING_ENABLED:
        return True
    if user is None:
        return False
    if user["subscription_status"] not in ("active", "trialing"):
        return False
    until = user["paid_until"]
    return until is None or until > now()


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
