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
# How stale last_seen_at has to be before a page view rewrites it. Every
# authenticated request would otherwise be a write, on a database where the
# busiest screen is read several times a minute. Fifteen minutes is far finer
# than the day-level buckets anything actually asks of it.
TOUCH_AFTER = 900


def get_or_create_user(email: str):
    """Called when somebody types their email into the sign-in form.

    It deliberately does NOT touch last_seen_at, and that is the fix rather
    than an oversight. It used to, which made the column mean "last asked for
    a sign-in link" while every reader of it - the admin page's "active this
    week", and me - took it for "last used the app". Those are not the same
    event and the gap between them is the entire question: somebody who
    requests a link and never clicks it has not come back, and was being
    counted as though they had.

    A new row leaves it NULL, so "signed up and never returned" is
    distinguishable from "returned at some point". Rows created before this
    carry a sign-in-request timestamp and cannot be corrected after the fact;
    they age out of the 30-day window on their own.
    """
    email = email.strip().lower()
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return row
        c.execute("INSERT INTO users (email, created_at) VALUES (?, ?)",
                  (email, now()))
        return c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def touch_user(user_id: int, last_seen: int | None, at: int | None = None) -> None:
    """Record that this user is here, at most once every TOUCH_AFTER seconds.

    Caller passes the value it already has rather than this re-reading the
    row, because it is called from the one function every authenticated
    request goes through and that function has just loaded the user.

    Failures are swallowed. This is a metric, and a metric is never worth a
    500 on somebody's dashboard.
    """
    at = now() if at is None else at
    if last_seen and at - last_seen < TOUCH_AFTER:
        return
    try:
        with connect() as c:
            c.execute("UPDATE users SET last_seen_at = ? WHERE id = ?",
                      (at, user_id))
    except Exception:
        pass


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


# What an employer did about a letter. Ordered worst to best, so a summary
# can be read as a funnel, and kept small on purpose: a tracker with fifteen
# states is one nobody updates.
OUTCOMES = ("rejected", "replied", "interview", "offer")


def set_outcome(user_id: int, draft_id: int, outcome: str) -> bool:
    """Record what came back. Returns False for anything not in OUTCOMES.

    An empty string is allowed and means "undo" - somebody who taps the wrong
    row on a phone has to be able to put it back, and a tracker you cannot
    correct is one people stop trusting after the first mistake.
    """
    outcome = (outcome or "").strip().lower()
    if outcome and outcome not in OUTCOMES:
        return False
    with connect() as c:
        c.execute(
            "UPDATE drafts SET outcome = ?, outcome_at = ? "
            "WHERE id = ? AND user_id = ? AND status = 'sent'",
            (outcome, now() if outcome else None, draft_id, user_id))
    return True


def drafts_awaiting_reply(user_id: int, limit: int = 500):
    """Sent letters nobody has answered for yet, so the inbox check knows
    which addresses to ask about.

    Excludes anything already flagged or already classified: once the user has
    said what happened, or the machine has already noticed, there is nothing
    to learn by asking again - and every address dropped here is one fewer
    question asked of somebody's mailbox.
    """
    with connect() as c:
        return c.execute(
            "SELECT * FROM drafts WHERE user_id = ? AND status = 'sent' "
            "AND to_email <> '' AND reply_seen_at IS NULL "
            "AND (outcome IS NULL OR outcome = '') "
            "ORDER BY sent_at DESC LIMIT ?",
            (user_id, limit)).fetchall()


def mark_reply_seen(user_id: int, draft_id: int) -> None:
    """Record that this employer has been in touch.

    Never writes `outcome`. A FROM match proves a message exists and nothing
    about what it says, and the difference between "they answered" and "their
    system acknowledged receipt" is the difference between this product's
    numbers meaning something and not. The user classifies; this only puts the
    row in front of them.
    """
    with connect() as c:
        c.execute("UPDATE drafts SET reply_seen_at = ? WHERE id = ? "
                  "AND user_id = ? AND reply_seen_at IS NULL",
                  (now(), draft_id, user_id))


def applications(user_id: int, limit: int = 200):
    """Every letter that actually went, newest first.

    Only 'sent'. A draft is not an application and a discarded one never
    was - putting them in the same list is how a tracker ends up flattering
    somebody with a number that means nothing.
    """
    with connect() as c:
        return c.execute(
            "SELECT * FROM drafts WHERE user_id = ? AND status = 'sent' "
            "ORDER BY sent_at DESC, id DESC LIMIT ?",
            (user_id, limit)).fetchall()


def application_stats(user_id: int) -> dict:
    """The numbers for the top of the tracker.

    Split the way `--stats` splits them in the personal machine, and for the
    reason Harry found there: "awaiting a reply" DRAINS as answers arrive, so
    quoting it as a total makes the thing look like it is going backwards on
    its best days. `sent` here only ever goes up.
    """
    rows = applications(user_id, limit=10000)
    sent = len(rows)

    # A reply the inbox check noticed counts, and counts WITHOUT waiting to be
    # confirmed. The tracker used to require a tap for every letter, so it
    # showed "0 heard back, 0% reply rate" on a search that had already had
    # answers - the most discouraging possible lie, told to somebody who is
    # job hunting and needs to know the thing is working.
    #
    # What the user says still wins where they have said anything. An explicit
    # "rejected" is hearing back and is deliberately NOT a reply, so a
    # detection cannot quietly promote it into one and make the numbers
    # improve as things go worse.
    def detected(row):
        return bool(row["reply_seen_at"]) and not row["outcome"]

    heard = [r for r in rows if r["outcome"] or detected(r)]
    positive = [r for r in rows
                if r["outcome"] in ("replied", "interview", "offer")
                or detected(r)]
    stamp = now()
    waiting = [r for r in rows if not r["outcome"] and not detected(r)]
    return {
        "sent": sent,
        "heard_back": len(positive),
        "interviews": len([r for r in heard
                           if r["outcome"] in ("interview", "offer")]),
        "reply_rate": round(100 * len(positive) / sent) if sent else 0,
        "awaiting": len(waiting),
        # How many of the above nobody has confirmed yet. The screen uses it
        # to say so out loud rather than presenting a detection as a verdict.
        "detected": len([r for r in rows if detected(r)]),
        # The oldest thing still unanswered, in days. This is the number that
        # tells somebody it is time to chase rather than wait.
        "longest_wait_days": max(
            [int((stamp - (r["sent_at"] or stamp)) // 86400) for r in waiting],
            default=0),
    }


# A breakdown needs this many letters in a bucket before it is shown at all.
#
# Eight is not a strong sample, but it is enough that one lucky reply cannot
# create a headline: at n=3 a single answer reads as 33% and would send
# somebody off changing their search on nothing. Buckets below it are counted
# and named, never given a percentage.
MIN_FOR_A_BREAKDOWN = 8

TIER_NAMES = {
    3: "a named person",
    2: "a hiring inbox",
    1: "a generic inbox",
    0: "an address of unknown kind",
}


def what_is_working(user_id: int) -> dict:
    """What this person's own results say, rather than what we believe.

    The product's whole argument is that WHO you write to decides everything,
    and that has been a claim on the landing page taken from one person's
    history. This is the same question asked of the reader's own sending, and
    it is the most useful screen the product can show them: it is theirs, it
    is checkable, and it tells them what to do differently on Monday.

    A bucket under MIN_FOR_A_BREAKDOWN gets a count and no percentage. A page
    that puts "100%" next to one letter teaches somebody to distrust
    everything else on it.
    """
    rows = applications(user_id, limit=10000)

    def answered(row):
        # The same rule the headline uses: a rejection is hearing back and is
        # deliberately not a reply, or the number improves as things go worse.
        return (row["outcome"] in ("replied", "interview", "offer")
                or (not row["outcome"] and row["reply_seen_at"]))

    buckets = {}
    for row in rows:
        tier = int(row["contact_tier"] or 0)
        entry = buckets.setdefault(tier, {"tier": tier, "sent": 0, "heard": 0,
                                          "name": TIER_NAMES.get(tier,
                                                                 "unknown")})
        entry["sent"] += 1
        entry["heard"] += bool(answered(row))

    contacts = []
    for entry in sorted(buckets.values(), key=lambda e: -e["tier"]):
        enough = entry["sent"] >= MIN_FOR_A_BREAKDOWN
        entry["rate"] = (round(100 * entry["heard"] / entry["sent"])
                         if enough else None)
        contacts.append(entry)

    # The single sentence worth acting on, or nothing. Only drawn when two
    # buckets both clear the bar AND the gap between them is big enough to
    # survive the noise in samples this size.
    rated = [c for c in contacts if c["rate"] is not None]
    lesson = None
    if len(rated) >= 2:
        best, worst = rated[0], rated[-1]
        for c in rated:
            if c["rate"] > best["rate"]:
                best = c
            if c["rate"] < worst["rate"]:
                worst = c
        if best["tier"] != worst["tier"] and best["rate"] - worst["rate"] >= 15:
            lesson = {"best": best, "worst": worst,
                      "gap": best["rate"] - worst["rate"]}

    total = len(rows)
    return {
        "sent": total,
        "contacts": contacts,
        "lesson": lesson,
        "enough": total >= MIN_FOR_A_BREAKDOWN,
        "min_for_a_breakdown": MIN_FOR_A_BREAKDOWN,
    }


# Below this many people the totals are one person's diary rather than a
# statistic, so the rate is withheld and the page says why. The raw counts are
# always shown - they are the honest thing and hiding them would look like
# there is something to hide.
MIN_PEOPLE_FOR_A_RATE = 3


def public_stats() -> dict:
    """The numbers anybody may see, on /numbers. Aggregate only, forever.

    Counted from sent_log rather than drafts.status, and the difference is the
    whole point. `status` is current state: it changes to 'replied' when an
    answer arrives and it disappears entirely if the draft is deleted, so a
    total built on it goes DOWN on the machine's best days. sent_log is one
    row per letter that actually left and is never rewritten, so it only ever
    goes up. Harry found this exact trap in the personal machine's numbers.

    ok = 1 only. A send that failed reached nobody, and counting attempts as
    letters would be the first lie on a page whose only job is being checkable.
    """
    with connect() as c:
        row = c.execute(
            "SELECT COUNT(*) AS letters, COUNT(DISTINCT user_id) AS people "
            "FROM sent_log WHERE ok = 1").fetchone()
        letters = int(row["letters"] or 0)
        people = int(row["people"] or 0)

        # THE DENOMINATOR IS EVERY LETTER NOW, AND THAT IS THE CHANGE.
        #
        # It used to be "letters somebody has told us the outcome of",
        # because the app could not see replies and the only ones it knew
        # about were the ones a user came back and tapped. That made the rate
        # a percentage of a self-selected sample - people are far likelier to
        # come back and record good news - and the page had to spend a
        # paragraph explaining which letters were in it.
        #
        # The inbox check now looks at every letter that went out, so every
        # letter can be in the denominator and the number means the plain
        # thing a reader assumes it means. Counted from drafts rather than
        # sent_log so the numerator and denominator are the same population:
        # mixing the two would divide answers about one set of letters by the
        # size of another.
        tracked = c.execute(
            "SELECT COUNT(*) AS n FROM drafts "
            "WHERE status = 'sent'").fetchone()
        tracked = int(tracked["n"] or 0)
        marked = c.execute(
            "SELECT COUNT(*) AS n FROM drafts "
            "WHERE status = 'sent' AND outcome <> ''").fetchone()
        marked = int(marked["n"] or 0)
        # A detection counts only where the user has not already answered:
        # their own "rejected" is hearing back and is not a reply, and must
        # not be overturned by the fact that something arrived.
        heard = c.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE status = 'sent' "
            "AND (outcome IN ('replied', 'interview', 'offer') "
            "     OR (COALESCE(outcome, '') = '' "
            "         AND reply_seen_at IS NOT NULL))").fetchone()
        heard = int(heard["n"] or 0)
        detected = c.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE status = 'sent' "
            "AND COALESCE(outcome, '') = '' "
            "AND reply_seen_at IS NOT NULL").fetchone()
        detected = int(detected["n"] or 0)
        interviews = c.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE status = 'sent' "
            "AND outcome IN ('interview', 'offer')").fetchone()
        interviews = int(interviews["n"] or 0)
        first = c.execute(
            "SELECT MIN(sent_at) AS t FROM sent_log WHERE ok = 1").fetchone()

    return {
        "letters": letters,
        "people": people,
        "tracked": tracked,
        "marked": marked,
        "detected": detected,
        "heard_back": heard,
        "interviews": interviews,
        # A rejection is hearing back and belongs in the tracker, but it is
        # not a reply worth boasting about, so it is excluded here exactly as
        # it is in application_stats. One rule, two places.
        "reply_rate": (round(100 * heard / tracked)
                       if tracked and people >= MIN_PEOPLE_FOR_A_RATE
                       else None),
        "rate_withheld": bool(tracked and people < MIN_PEOPLE_FOR_A_RATE),
        "first_send_at": (int(first["t"]) if first and first["t"] else 0),
        "min_people": MIN_PEOPLE_FOR_A_RATE,
    }


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
                      password: str, kind: str = "own",
                      reply_to: str = "", verified: bool = True) -> None:
    """Store credentials, encrypted.

    `verified` records whether the password has actually been proved to work.
    Never pass True without having proved it, and never store one already
    KNOWN to be bad: a mail server that refuses credentials has given a
    definite answer and the user must be told to their face.

    verified=False is for the third case, which used to be conflated with the
    second: we could not reach the mail server to ask. Free hosting blocks
    outbound SMTP almost everywhere, so on the free plan that is every
    password, correct ones included. The account is stored unchecked and the
    sweep - which runs on a host that is not blocked - does the real
    verification before it sends anything.

    The guarantee this preserves is the one that matters: nothing is ever
    sent on a password that has not been proved, and the user is never told
    their letters are going out when they are not. What changes is only WHERE
    the proof happens.
    """
    from . import vault
    secret = vault.encrypt(password)
    with connect() as c:
        c.execute(
            "INSERT INTO mail_accounts (user_id, address, host, port, secret, "
            "kind, reply_to, verified_at, last_error, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "address = excluded.address, host = excluded.host, "
            "port = excluded.port, secret = excluded.secret, "
            "kind = excluded.kind, reply_to = excluded.reply_to, "
            "verified_at = excluded.verified_at, last_error = NULL, "
            "updated_at = excluded.updated_at",
            (user_id, address.strip().lower(), host.strip(), int(port),
             secret, kind, (reply_to or "").strip().lower(),
             now() if verified else None, now()))


def mark_mail_verified(user_id: int) -> None:
    """Record that these credentials have now been proved to work.

    Called by the sweep after it has successfully logged in on a host that is
    not SMTP-blocked, so a deferred check leaves the same state behind as an
    immediate one and nothing downstream has to know which happened.
    """
    with connect() as c:
        c.execute("UPDATE mail_accounts SET verified_at = ?, last_error = NULL, "
                  "updated_at = ? WHERE user_id = ?", (now(), now(), user_id))


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


def issue_managed_address(user_id: int, name: str, fallback_email: str = "") -> str:
    """Claim a Recruited address for this user, or return the one they have.

    Idempotent on purpose. Somebody who disconnects and reconnects keeps the
    address employers already have - reissuing a different one would orphan
    every reply still in flight, which is the whole point of having an address
    at all.

    The dedupe is the fiddly part: two people called Harry Russell cannot both
    have harry.russell@, and the second one must not silently receive the
    first one's replies. So the local part is claimed against every address
    already issued, and a collision appends a number rather than failing.
    """
    from . import config, delivery
    existing = get_mail_account(user_id)
    if existing and existing["kind"] == "managed" and existing["address"]:
        return existing["address"]

    domain = config.MANAGED_MAIL_DOMAIN
    base = delivery.local_part_for(name, fallback_email)
    with connect() as c:
        rows = c.execute(
            "SELECT address FROM mail_accounts WHERE kind = 'managed'"
        ).fetchall()
    taken = {(r["address"] or "").split("@")[0].lower() for r in rows}

    local = base
    n = 1
    while local in taken:
        n += 1
        local = f"{base}{n}"
    return f"{local}@{domain}"


def managed_sent_today(now_ts: int | None = None) -> int:
    """How many letters have gone out from Recruited addresses today.

    Across ALL users, because the provider's allowance is across all users.
    Counting per-user would let ten people each stay under their own limit
    and blow the shared one between them.
    """
    stamp = now() if now_ts is None else now_ts
    start = stamp - (stamp % 86400)
    with connect() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM managed_sends WHERE sent_at >= ?",
            (start,)).fetchone()
    return int(row["n"])


def record_managed_send(user_id: int) -> None:
    with connect() as c:
        c.execute("INSERT INTO managed_sends (user_id, sent_at) VALUES (?, ?)",
                  (user_id, now()))


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


# ----------------------------------------------------------------------
# what a run is doing while somebody watches
# ----------------------------------------------------------------------
# A run that has said nothing for this long is treated as dead.
#
# The background thread can be killed without getting the chance to write
# anything - the host restarts, the container is recycled, the process runs
# out of memory. Without this the page would show a bar creeping nowhere for
# ever, which is a worse failure than an error, because an error at least
# tells somebody to press the button again.
#
# Generous, because the slow step is Gemini on a free tier answering one
# listing at a time with six seconds between calls, and declaring a working
# run dead is its own kind of lie.
RUN_STALE_AFTER = 300


def start_run(user_id: int) -> bool:
    """Claim the right to run for this user. False if one is already going.

    The claim and the check are one statement, because two taps on a phone
    half a second apart are not hypothetical and the second must lose. The
    condition is in the WHERE of the UPDATE rather than in a read followed by
    a write, so the database decides rather than two racing requests.
    """
    stamp = now()
    with connect() as c:
        row = c.execute("SELECT state, updated_at FROM run_progress "
                        "WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["state"] == "running" and (
                stamp - int(row["updated_at"] or 0)) < RUN_STALE_AFTER:
            return False
        c.execute(
            "INSERT INTO run_progress (user_id, state, step, done, total, "
            "drafted, result, started_at, updated_at) "
            "VALUES (?, 'running', ?, 0, 0, 0, '', ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET state = 'running', "
            "step = excluded.step, done = 0, total = 0, drafted = 0, "
            "result = '', started_at = excluded.started_at, "
            "updated_at = excluded.updated_at",
            (user_id, "Getting started", stamp, stamp))
    return True


def set_run_step(user_id: int, step: str, *, done: int | None = None,
                 total: int | None = None, drafted: int | None = None) -> None:
    """Say what is happening now. Never raises: progress is not the work.

    A failure to report must not take down the run it is reporting on. The
    person loses the commentary and still gets their letters, which is the
    right way round.
    """
    try:
        sets = ["step = ?", "updated_at = ?"]
        values = [step[:200], now()]
        for name, value in (("done", done), ("total", total),
                            ("drafted", drafted)):
            if value is not None:
                sets.insert(0, f"{name} = ?")
                values.insert(0, int(value))
        values.append(user_id)
        with connect() as c:
            c.execute(f"UPDATE run_progress SET {', '.join(sets)} "
                      f"WHERE user_id = ?", tuple(values))
    except Exception:
        pass


def finish_run(user_id: int, *, result: str, ok: bool = True,
               drafted: int = 0) -> None:
    try:
        with connect() as c:
            c.execute("UPDATE run_progress SET state = ?, step = '', "
                      "result = ?, drafted = ?, updated_at = ? "
                      "WHERE user_id = ?",
                      ("done" if ok else "failed", (result or "")[:300],
                       int(drafted), now(), user_id))
    except Exception:
        pass


def run_progress(user_id: int) -> dict | None:
    """What to show the person watching, or None if they have never run one."""
    with connect() as c:
        row = c.execute("SELECT * FROM run_progress WHERE user_id = ?",
                        (user_id,)).fetchone()
    if not row:
        return None

    state = row["state"]
    stamp = now()
    age = stamp - int(row["updated_at"] or 0)
    if state == "running" and age >= RUN_STALE_AFTER:
        # Nothing has reported in for minutes, so the thread doing the work is
        # gone and nobody is coming back to say so. Say it stopped rather than
        # animating a bar at somebody indefinitely.
        state = "failed"

    done, total = int(row["done"] or 0), int(row["total"] or 0)
    return {
        "state": state,
        "step": row["step"] or "",
        "done": done, "total": total,
        "drafted": int(row["drafted"] or 0),
        # None, not 0, when there is nothing to base a fraction on. The bar
        # shows an indeterminate stripe for that rather than sitting at 0%,
        # which reads as broken when it only means "counting".
        "percent": round(100 * done / total) if total else None,
        "result": row["result"] or ("It stopped part way through. "
                                    "Press the button again."
                                    if state == "failed" and not row["result"]
                                    else ""),
        "seconds": max(0, stamp - int(row["started_at"] or stamp)),
    }


def machine_status(user_id: int) -> dict:
    """Is it on, when did it last do anything, and what is left today.

    Every figure here is something that HAPPENED, never a prediction. The
    obvious version of this panel says "next run at 11:10", and that would be
    a lie: the schedule is GitHub's, and GitHub delays scheduled workflows
    under load and drops the ones it cannot place - this workflow's own
    history has it running twice on a day it was set to run three times, and
    never once at a listed minute.

    So it reports the last thing it did and what it has left to spend, which
    are both true and both more use than a time that may not arrive.
    """
    settings = get_send_settings(user_id)
    account = get_mail_account(user_id)
    progress = run_progress(user_id)
    sent = sent_today(user_id)

    with connect() as c:
        last = c.execute(
            "SELECT MAX(sent_at) AS t FROM sent_log "
            "WHERE user_id = ? AND ok = 1", (user_id,)).fetchone()
        waiting = c.execute(
            "SELECT COUNT(*) AS n FROM drafts "
            "WHERE user_id = ? AND status = 'draft'", (user_id,)).fetchone()

    return {
        # The three things that decide whether anything happens at all, in
        # the order they stop it.
        "mailbox": bool(account),
        "mailbox_verified": bool(account and account["verified_at"]),
        "auto_send": bool(settings["auto_send"]),
        "paused": bool(settings["paused_until"]
                       and settings["paused_until"] > now()),

        "sent_today": sent,
        "daily_cap": settings["daily_cap"],
        "left_today": max(0, settings["daily_cap"] - sent),
        "hold_minutes": settings["hold_minutes"],
        "waiting": int(waiting["n"] or 0),

        "last_sent_at": int(last["t"]) if last and last["t"] else 0,
        "last_looked_at": _last_run_at(user_id),
        "running": bool(progress and progress["state"] == "running"),
    }


def _last_run_at(user_id: int) -> int:
    with connect() as c:
        row = c.execute("SELECT updated_at FROM run_progress "
                        "WHERE user_id = ?", (user_id,)).fetchone()
    return int(row["updated_at"]) if row and row["updated_at"] else 0


def record_sent(user_id: int, *, draft_id, to_email: str, company: str,
                ok: bool = True, error: str = "") -> None:
    with connect() as c:
        insert_returning_id(
            c, "sent_log",
            ["user_id", "draft_id", "to_email", "company", "sent_at", "ok",
             "error"],
            [user_id, draft_id, to_email, company, now(), int(bool(ok)),
             (error or "")[:300]])


def record_delivered(user_id: int, *, draft_id, to_email: str,
                     company: str) -> None:
    """Mark the draft sent AND log the letter, in one transaction.

    These were two statements in two transactions, and the gap between them is
    not theoretical: the first sweep that ever had a user to work on was killed
    by the workflow timeout in exactly that gap. The draft was marked 'sent' at
    12:40:44 and the sent_log row for it does not exist, because the process
    was terminated between the two.

    That gap is the worst place in this codebase to be interrupted, because the
    two records answer different questions and BOTH are load-bearing:

      - drafts.status stops the same letter going twice
      - sent_log is what /numbers counts, and what sent_today() uses to hold
        the daily cap

    So a crash there leaves a letter that was really delivered, invisible to
    the public figures and uncounted against the cap - which means the next run
    could exceed a ceiling the user set, using a number it believes is true.

    The ORDER is deliberate where atomicity cannot be had, and this keeps it:
    the draft is marked first. Of the two ways to be half-finished, "delivered
    but unlogged" under-reports, while "logged but still marked draft" sends
    somebody a second copy of the same application. Under-reporting is the one
    to choose.
    """
    with connect() as c:
        c.execute("UPDATE drafts SET status = ?, sent_at = ? "
                  "WHERE id = ? AND user_id = ?",
                  ("sent", now(), draft_id, user_id))
        insert_returning_id(
            c, "sent_log",
            ["user_id", "draft_id", "to_email", "company", "sent_at", "ok",
             "error"],
            [user_id, draft_id, to_email, company, now(), 1, ""])


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
    """Everyone the scheduled sweep should consider. is_paid() decides.

    This used to be a narrower SQL prefilter - subscription_status IN
    ('active', 'trialing') - with a docstring calling is_paid() the single
    source of truth. It was not. is_paid() opens the gate three ways: billing
    switched off, FREE_ACCESS_EMAILS, and a claimed free place. The SQL knew
    about none of the last two.

    A prefilter NARROWER than the thing it is prefiltering for is not a
    prefilter. It is a second, stricter gate that nothing re-checks, and it
    silently excluded every user whose access did not come from Stripe. On the
    live database that was three accounts out of four, the founder's included:
    the web app let them in, the sweep reported "0 paying users", and not one
    letter could ever be sent for any of them. The sweep even said so in the
    log every run, and it read like a billing fact rather than a bug.

    So the query is now a superset by construction rather than by a list of
    conditions somebody has to remember to update in two places. Every caller
    already re-filters through is_paid(), which is where the decision belongs
    and is now genuinely the only place it is made.

    If this table ever grows enough for the scan to matter, the prefilter that
    replaces it has to be PROVED a superset of is_paid() - there is a test
    named for exactly that.
    """
    with connect() as c:
        rows = c.execute("SELECT id FROM users ORDER BY id").fetchall()
    return [r["id"] for r in rows]


# ----------------------------------------------------------------------
# things the site remembers about itself
# ----------------------------------------------------------------------
def get_meta(key: str) -> str:
    """A note a previous process left. Missing is "" rather than an error:
    every caller here is asking "have I already done this", and the answer
    before the first time is no."""
    try:
        with connect() as c:
            row = c.execute("SELECT value FROM site_meta WHERE key = ?",
                            (key,)).fetchone()
        return (row["value"] if row else "") or ""
    except Exception:
        return ""


def set_meta(key: str, value: str) -> None:
    """Failures are swallowed. Nothing stored here is worth a 500 - the worst
    case is a piece of work repeated, which is what it was before."""
    try:
        with connect() as c:
            c.execute(
                "INSERT INTO site_meta (key, value, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, now()))
    except Exception:
        pass


# ----------------------------------------------------------------------
# the funnel
# ----------------------------------------------------------------------
# The transitions worth knowing about, in the order they happen. Written down
# rather than left implicit because the ORDER is the report: an activation
# rate is two adjacent entries in this list divided by each other, and a list
# that drifts out of order produces a rate nobody notices is wrong.
FUNNEL = ("signed_up", "onboarded", "first_email_sent", "first_reply",
          "first_interview")

# Not in FUNNEL because it is not a stage somebody passes through - it can
# happen any number of times to one account, and to an account at any stage.
REFERRED_USER = "referred_user"

# Also not a funnel stage: putting the app on a home screen is something that
# can happen at any point, or never, and somebody who never installs is not
# stuck. It is recorded because "do the people who install it stick around" is
# a question the retention figure cannot answer on its own, and this is the
# cheapest way to ask it.
INSTALLED = "installed"

# Two more that are not stages, and exist for the referral programme.
#
# The funnel counts what a person SAYS as readily as what the machine SEES -
# a quick-start form, a "mark as sent" tap - because for measuring the
# funnel that is the honest thing to count. A reward is different: anything
# that pays out has to hang off something a click cannot fake. So the two
# steps that do pay get events of their own, fired only where the evidence
# is real: a CV that arrived as a file, and a letter the machine itself
# delivered through a mailbox it verified.
CV_UPLOADED = "cv_uploaded"
FIRST_AUTO_SEND = "first_auto_send"

EVENT_KINDS = FUNNEL + (REFERRED_USER, INSTALLED, CV_UPLOADED,
                        FIRST_AUTO_SEND)


def record_event(user_id: int, kind: str, *, ref: str = "",
                 detail: str = "", at: int | None = None) -> bool:
    """Write down that something happened for the first time.

    True if this is new, False if it had already been recorded. Idempotent by
    construction rather than by checking first: UNIQUE (user_id, kind, ref)
    and ON CONFLICT DO NOTHING, so two concurrent sweeps recording the same
    first send cannot race into two rows.

    That matters more than it looks. Every rate computed from this table has
    an event count as its numerator, so one duplicated row is an activation
    rate above 100% - a number that gets explained away rather than
    investigated.

    Unknown kinds raise. A typo'd event name is invisible otherwise: it
    inserts cleanly, and the stage it was meant to record silently reads
    zero for ever.
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind: {kind!r}")
    stamp = now() if at is None else at
    try:
        with connect() as c:
            # RETURNING is what makes this honest. ON CONFLICT DO NOTHING
            # returns a row when this insert landed and nothing when it hit
            # the unique constraint, so "was this the first" is answered by
            # the database rather than inferred.
            #
            # Inferring it was the first attempt here, by reading the stored
            # timestamp back and comparing: which reports TRUE twice for two
            # calls in the same second, because the timestamps are equal. The
            # cost of that is a referral month paid twice, and the test that
            # caught it does exactly what a retry does.
            #
            # Needs SQLite 3.35 (2021) or Postgres. Both are well past that.
            row = c.execute(
                "INSERT INTO events (user_id, kind, ref, at, detail) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING "
                "RETURNING id",
                (user_id, kind, ref, stamp, detail)).fetchone()
        return row is not None
    except Exception:
        # An event that cannot be written must never take down the thing that
        # happened. A send is worth more than the record of it being a first.
        return False


def first_event_at(user_id: int, kind: str) -> int | None:
    with connect() as c:
        row = c.execute(
            "SELECT MIN(at) AS at FROM events WHERE user_id = ? AND kind = ?",
            (user_id, kind)).fetchone()
    return int(row["at"]) if row and row["at"] is not None else None


def event_counts() -> dict:
    """How many accounts have ever reached each stage.

    DISTINCT user_id rather than a row count, because referred_user has many
    rows per account and folding that into a funnel would read as a stage
    more people reached than signed up.
    """
    with connect() as c:
        rows = c.execute(
            "SELECT kind, COUNT(DISTINCT user_id) AS n FROM events "
            "GROUP BY kind").fetchall()
    return {r["kind"]: int(r["n"]) for r in rows}


def users_by_source() -> list[dict]:
    """Sign-ups grouped by where they came from, and how far each group got.

    The join is to events rather than to cvs or sent_log on purpose: those
    say what is true NOW, and a user who sent letters and later deleted their
    account or replaced their CV would silently leave the channel that
    brought them looking worse than it was.
    """
    with connect() as c:
        rows = c.execute(
            "SELECT u.utm_source, u.utm_campaign, u.ref_code, "
            "       COUNT(*) AS signups, "
            "       SUM(CASE WHEN e.user_id IS NOT NULL THEN 1 ELSE 0 END) "
            "         AS activated "
            "FROM users u "
            "LEFT JOIN events e "
            "  ON e.user_id = u.id AND e.kind = 'first_email_sent' "
            "GROUP BY u.utm_source, u.utm_campaign, u.ref_code "
            "ORDER BY signups DESC").fetchall()
    out = []
    for r in rows:
        source = "referral" if r["ref_code"] else (r["utm_source"] or "direct")
        out.append({"source": source,
                    "campaign": r["utm_campaign"] or "",
                    "signups": int(r["signups"]),
                    "activated": int(r["activated"] or 0)})
    return out


def set_user_source(user_id: int, source: dict) -> None:
    """Stamp a brand-new account with where it came from.

    Only ever called for an account that has just been created, and it does
    not overwrite: a returning user signing in from a different link is the
    same user, and re-attributing them would move a sign-up between channels
    weeks after the fact and make last month's report change.
    """
    if not source:
        return
    try:
        with connect() as c:
            c.execute(
                "UPDATE users SET utm_source = ?, utm_campaign = ?, "
                "ref_code = ?, landing_path = ? "
                "WHERE id = ? AND utm_source = '' AND ref_code = ''",
                (source.get("utm_source", ""), source.get("utm_campaign", ""),
                 source.get("ref", ""), source.get("landing", ""), user_id))
    except Exception:
        pass


# ----------------------------------------------------------------------
# referrals
# ----------------------------------------------------------------------
def claim_referral_code(user_id: int, code: str) -> bool:
    """Take a code if nobody else holds it. False means try another.

    The uniqueness is enforced by the SELECT and the UPDATE together rather
    than by a constraint, because the column has a DEFAULT '' and every
    account that has never asked for a code shares that value - a UNIQUE index
    would refuse the second such account. The loser of a race gets False and
    picks a different code, which is the behaviour either way.
    """
    code = (code or "").strip().lower()
    if not code:
        return False
    with connect() as c:
        taken = c.execute(
            "SELECT id FROM users WHERE referral_code = ?", (code,)).fetchone()
        if taken:
            return False
        c.execute("UPDATE users SET referral_code = ? "
                  "WHERE id = ? AND referral_code = ''", (code, user_id))
        row = c.execute("SELECT referral_code FROM users WHERE id = ?",
                        (user_id,)).fetchone()
    return bool(row) and (row["referral_code"] or "") == code


def user_by_referral_code(code: str):
    code = (code or "").strip().lower()
    if not code:
        return None
    with connect() as c:
        return c.execute("SELECT * FROM users WHERE referral_code = ?",
                         (code,)).fetchone()


def set_referrer(user_id: int, referrer_id: int) -> bool:
    """Record who brought this account. Once, and never changed after.

    The guard is `referred_by IS NULL`: a second referral link tapped later
    must not move an account between referrers, because the first reward may
    already have been paid on it and the second would be paid again.
    """
    with connect() as c:
        c.execute("UPDATE users SET referred_by = ? "
                  "WHERE id = ? AND referred_by IS NULL",
                  (referrer_id, user_id))
        row = c.execute("SELECT referred_by FROM users WHERE id = ?",
                        (user_id,)).fetchone()
    return bool(row) and row["referred_by"] == referrer_id


def referral_months_earned(user_id: int) -> int:
    with connect() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE user_id = ? AND kind = ?", (user_id, REFERRED_USER)
        ).fetchone()
    return int(row["n"]) if row else 0


def referral_stats(user_id: int) -> dict:
    """What to show somebody on their own referral screen.

    `people` counts accounts, `rewards` counts payouts, and they are different
    numbers on purpose: one person who signs up, uploads a CV and then sends
    their first letter earns two months. Showing only one of them would make
    the page either understate the reward or overstate the friends.
    """
    with connect() as c:
        people = c.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referred_by = ?",
            (user_id,)).fetchone()
        rewards = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE user_id = ? AND kind = ?",
            (user_id, REFERRED_USER)).fetchone()
    return {"people": int(people["n"]) if people else 0,
            "months": int(rewards["n"]) if rewards else 0}


def extend_paid_until(user_id: int, seconds: int) -> int:
    """Add free access, from now or from whenever they are already paid to.

    Taking the later of the two is the whole of it. Setting it to now+30d
    would SHORTEN a subscriber who is paid up for a year, turning a reward
    into a punishment for the only people already giving us money.
    """
    if seconds <= 0:
        return 0
    with connect() as c:
        row = c.execute("SELECT paid_until FROM users WHERE id = ?",
                        (user_id,)).fetchone()
        if row is None:
            return 0
        base = max(int(row["paid_until"] or 0), now())
        until = base + seconds
        c.execute("UPDATE users SET paid_until = ? WHERE id = ?",
                  (until, user_id))
    return until
