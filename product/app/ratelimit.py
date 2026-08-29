"""Rate limiting for the endpoints a stranger can reach.

`/login` is the one that matters. Without a limit, anybody can make the app
send unlimited sign-in emails to any address they like. Two things go wrong,
and both are expensive:

  - somebody else's inbox is used as a weapon, from your domain
  - the sending account is suspended for abuse, and every real customer's
    sign-in link stops arriving at the same moment

Fixed windows in SQLite rather than a token bucket in Redis, because this app
already has a database and does not need another moving part. Slightly leaky
at a window boundary, which does not matter when the limit is "five an hour".

Two separate limits, because they stop different attacks:

  - **per email**: one address cannot be mailed repeatedly, however many
    machines ask for it
  - **per IP**: one machine cannot walk a list of addresses
"""

from __future__ import annotations

import time

from .db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_hits (
    bucket     TEXT    NOT NULL,
    window_at  INTEGER NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, window_at)
);
"""


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def hit(bucket: str, *, limit: int, window: int) -> bool:
    """Count one attempt. True if it is allowed, False if over the limit.

    The row is written before the check so that a caller which ignores the
    answer still cannot get a free attempt out of it.
    """
    slot = int(time.time()) // window * window
    with connect() as c:
        c.execute(
            "INSERT INTO rate_hits (bucket, window_at, hits) VALUES (?, ?, 1) "
            "ON CONFLICT(bucket, window_at) DO UPDATE SET hits = hits + 1",
            (bucket, slot))
        row = c.execute(
            "SELECT hits FROM rate_hits WHERE bucket = ? AND window_at = ?",
            (bucket, slot)).fetchone()
        # Opportunistic cleanup; there is no scheduler here to do it.
        c.execute("DELETE FROM rate_hits WHERE window_at < ?", (slot - window * 4,))
    return row["hits"] <= limit


def client_ip(request) -> str:
    """The caller's address, trusting one proxy hop.

    Every sensible host for this app terminates TLS in front of the process,
    so request.client.host is the proxy. X-Forwarded-For's *first* entry is
    the original client; later entries are proxies. It is spoofable by the
    client, which is why it is only ever a rate-limit key here and never an
    authorisation decision.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]
