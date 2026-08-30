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
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL UNIQUE,
    created_at      INTEGER NOT NULL,
    last_seen_at    INTEGER,
    -- billing
    stripe_customer_id     TEXT,
    stripe_subscription_id TEXT,
    -- 'none' until Stripe says otherwise. Never inferred from a redirect:
    -- a user who lands on /success has not necessarily paid.
    subscription_status    TEXT NOT NULL DEFAULT 'none',
    paid_until      INTEGER
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data        TEXT    NOT NULL,          -- the Profile, as JSON
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_title    TEXT NOT NULL,
    company      TEXT NOT NULL,
    location     TEXT,
    listing_url  TEXT,
    salary_text  TEXT,
    score        INTEGER,
    -- who it is addressed to, and how good that address is
    to_email     TEXT,
    to_name      TEXT,
    contact_tier INTEGER,                  -- 3 named, 2 hiring inbox, 1 generic
    subject      TEXT,
    body         TEXT,
    -- 'draft' -> 'sent' (the user pressed send) | 'discarded'
    status       TEXT NOT NULL DEFAULT 'draft',
    created_at   INTEGER NOT NULL,
    sent_at      INTEGER
);

CREATE INDEX IF NOT EXISTS drafts_by_user
    ON drafts(user_id, status, created_at DESC);

-- One email per employer, ever. Enforced here rather than remembered in
-- application code, because the cost of getting it wrong is a real person
-- receiving the same pitch twice.
CREATE TABLE IF NOT EXISTS contacted (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_key TEXT    NOT NULL,          -- normalised company name
    first_at    INTEGER NOT NULL,
    PRIMARY KEY (user_id, company_key)
);

-- A company that has asked to be left alone. Checked before anything is
-- drafted, and never removable through the UI.
CREATE TABLE IF NOT EXISTS do_not_contact (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_key TEXT    NOT NULL,
    reason      TEXT,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (user_id, company_key)
);

CREATE TABLE IF NOT EXISTS login_tokens (
    jti        TEXT PRIMARY KEY,
    used_at    INTEGER
);
"""


def now() -> int:
    return int(time.time())


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


# ----------------------------------------------------------------------
# users
# ----------------------------------------------------------------------
def get_or_create_user(email: str) -> sqlite3.Row:
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
        try:
            c.execute("INSERT INTO login_tokens (jti, used_at) VALUES (?, ?)",
                      (jti, now()))
            return True
        except sqlite3.IntegrityError:
            return False


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
    placeholders = ", ".join("?" * len(cols))
    with connect() as c:
        cur = c.execute(
            f"INSERT INTO drafts ({', '.join(cols)}) VALUES ({placeholders})",
            vals)
        return cur.lastrowid


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
        c.execute("INSERT OR IGNORE INTO contacted (user_id, company_key, "
                  "first_at) VALUES (?, ?, ?)",
                  (user_id, company_key(company), now()))


def is_blocked(user_id: int, company: str) -> bool:
    with connect() as c:
        return c.execute(
            "SELECT 1 FROM do_not_contact WHERE user_id = ? AND company_key = ?",
            (user_id, company_key(company))).fetchone() is not None


def block_company(user_id: int, company: str, reason: str = "") -> None:
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO do_not_contact (user_id, company_key, "
                  "reason, added_at) VALUES (?, ?, ?, ?)",
                  (user_id, company_key(company), reason, now()))


def may_contact(user_id: int, company: str) -> bool:
    return not is_blocked(user_id, company) and not already_contacted(user_id, company)
