"""Storage that runs on SQLite locally and Postgres in production.

One interface, two backends, chosen by whether DATABASE_URL is set. The point
is not portability for its own sake - it is that the tests stay fast and
offline while the deployed app keeps its data somewhere that survives a
redeploy.

Why this exists at all: every free app host either sleeps or has no persistent
disk. A SQLite file on such a host is wiped by the next deploy, taking the
customer table with it. Supabase gives a free Postgres that outlives the app
process, so the app can be disposable and the data cannot.

The SQL is written once, in the dialect both understand. Only three things
actually differ, and each is handled in one place:

  - placeholders: `?` here, rewritten to `%s` for Postgres
  - auto-increment keys: AUTOINCREMENT against GENERATED ... AS IDENTITY
  - RETURNING on insert, which SQLite gained late enough that lastrowid is
    still the safer route there
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager

from . import config

# A Supabase connection string. When absent, everything falls back to the
# local file, which is what the tests and `--reload` development use.
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

_PLACEHOLDER = re.compile(r"\?")


def _sql(query: str) -> str:
    """`?` is the placeholder everywhere in this codebase; Postgres wants %s."""
    return _PLACEHOLDER.sub("%s", query) if IS_POSTGRES else query


# ----------------------------------------------------------------------
# schema
# ----------------------------------------------------------------------
def _schema() -> str:
    key = ("INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY" if IS_POSTGRES
           else "INTEGER PRIMARY KEY AUTOINCREMENT")
    return f"""
CREATE TABLE IF NOT EXISTS users (
    id              {key},
    email           TEXT    NOT NULL UNIQUE,
    created_at      BIGINT  NOT NULL,
    last_seen_at    BIGINT,
    stripe_customer_id     TEXT,
    stripe_subscription_id TEXT,
    -- 'none' until Stripe says otherwise on a verified webhook. Never
    -- inferred from a redirect: anyone can arrive at a success page.
    subscription_status    TEXT NOT NULL DEFAULT 'none',
    paid_until      BIGINT
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data        TEXT    NOT NULL,
    updated_at  BIGINT  NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id           {key},
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_title    TEXT NOT NULL,
    company      TEXT NOT NULL,
    location     TEXT,
    listing_url  TEXT,
    salary_text  TEXT,
    score        INTEGER,
    to_email     TEXT,
    to_name      TEXT,
    contact_tier INTEGER,
    subject      TEXT,
    body         TEXT,
    status       TEXT NOT NULL DEFAULT 'draft',
    created_at   BIGINT NOT NULL,
    sent_at      BIGINT
);

CREATE INDEX IF NOT EXISTS drafts_by_user
    ON drafts(user_id, status, created_at DESC);

-- One email per employer, ever. Enforced in the schema because the cost of
-- getting it wrong is a real person receiving the same pitch twice.
CREATE TABLE IF NOT EXISTS contacted (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_key TEXT    NOT NULL,
    first_at    BIGINT  NOT NULL,
    PRIMARY KEY (user_id, company_key)
);

CREATE TABLE IF NOT EXISTS do_not_contact (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_key TEXT    NOT NULL,
    reason      TEXT,
    added_at    BIGINT  NOT NULL,
    PRIMARY KEY (user_id, company_key)
);

CREATE TABLE IF NOT EXISTS seen_listings (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    external_id TEXT    NOT NULL,
    outcome     TEXT,
    seen_at     BIGINT  NOT NULL,
    PRIMARY KEY (user_id, external_id)
);

CREATE TABLE IF NOT EXISTS login_tokens (
    jti        TEXT PRIMARY KEY,
    used_at    BIGINT
);

CREATE TABLE IF NOT EXISTS rate_hits (
    bucket     TEXT    NOT NULL,
    window_at  BIGINT  NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, window_at)
);
"""


# ----------------------------------------------------------------------
# connections
# ----------------------------------------------------------------------
class Row(dict):
    """Rows behave like the sqlite3.Row the rest of the code expects."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


@contextmanager
def connect():
    if IS_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row,
                               connect_timeout=15)
        try:
            yield _PgWrapper(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        import sqlite3
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


class _PgWrapper:
    """Gives a psycopg connection the handful of sqlite3 habits used here."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, args=()):
        cur = self._conn.cursor()
        cur.execute(_sql(query), tuple(args))
        return _PgCursor(cur)

    def executescript(self, script):
        cur = self._conn.cursor()
        cur.execute(script)
        return _PgCursor(cur)


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        row = self._cur.fetchone()
        return Row(row) if row else None

    def fetchall(self):
        return [Row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def lastrowid(self):
        # Callers that need the new id use insert_returning_id instead; this
        # exists so an accidental read fails loudly rather than silently
        # handing back the wrong number.
        raise NotImplementedError(
            "use insert_returning_id() - lastrowid has no meaning on Postgres")


def insert_returning_id(conn, table: str, columns, values) -> int:
    """Insert one row and return its new id, on either backend."""
    cols = ", ".join(columns)
    marks = ", ".join("?" * len(columns))
    if IS_POSTGRES:
        row = conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks}) RETURNING id",
            values).fetchone()
        return int(row["id"])
    return conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({marks})", values).lastrowid


def init() -> None:
    """Create the schema if it is not there. Safe to call on every boot: every
    statement in _schema() is IF NOT EXISTS."""
    with connect() as c:
        c.executescript(_schema())


def describe() -> str:
    """A one-line description safe to print in a log or a health page.

    Parsed rather than split on "@", so the password in the connection string
    can never end up in output. Only the host is ever shown.
    """
    if IS_POSTGRES:
        from urllib.parse import urlsplit
        host = urlsplit(DATABASE_URL).hostname or "a local socket"
        return f"postgres at {host}"
    return f"sqlite at {config.DB_PATH}"


def ping() -> bool:
    """Touch the database. Used by the scheduled run to keep a free-tier
    Supabase project from pausing itself after a quiet week - a paused
    database means nobody can sign in, and it fails silently."""
    try:
        with connect() as c:
            c.execute("SELECT 1")
        return True
    except Exception as exc:
        print(f"[store] ping failed: {exc}")
        return False
