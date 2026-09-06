"""Teach the product who the personal machine has already written to.

Run this ONCE, BEFORE connecting a mailbox that has already been used to send
applications by hand or by the machine in the root of this repository.

Why it has to exist
-------------------
The product enforces "one employer, one letter, ever" against its own
`contacted` table, and that table starts empty. Harry's personal machine has
sent 102 applications from the same Gmail. Connect that mailbox without doing
this and the product will cheerfully write to Cirrus, Fred Olsen and
Matchtech a second time - companies that have already replied to him. From
the reader's side that is not a system with an empty table; it is a man who
does not remember writing to them.

The trap this avoids
--------------------
There are two `company_key()` functions in this repository and they do not
agree:

    job_machine.py:698        strips ltd|limited|plc|llp|inc|group|holdings|
                              uk|international|services|solutions|recruitment
    jobseeker/names.py        the same, plus 'incorporated', and it drops
                              apostrophes so O'Brien and OBrien collide

So the keys in data/state.json are NOT interchangeable with the product's.
Copying them across would produce a table that looks full and misses on
exactly the names the two functions disagree about.

Every entry here therefore travels as the DISPLAY NAME the personal machine
recorded - "ABERTAY UNIVERSITY", not "abertay university" - and is re-keyed
on arrival by the product's own function. If the product later changes how it
keys names, re-running this produces the right answer again.

Usage
-----
    python -m tools.import_history --user harryrussell081203@gmail.com --dry-run
    python -m tools.import_history --user harryrussell081203@gmail.com

The dry run is the default posture: look at what it says before letting it
write.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(HERE)
REPO = os.path.dirname(PRODUCT)
sys.path.insert(0, PRODUCT)

from app import db  # noqa: E402

STATE = os.path.join(REPO, "data", "state.json")
DNC = os.path.join(REPO, "data", "do_not_contact.json")

# Statuses that mean a letter left the building. `no_email` and `skipped` did
# not, and `ready` has not yet - importing those would block employers who
# have never heard from him, which is the opposite failure and just as bad.
WAS_WRITTEN_TO = {"sent", "replied", "spec_sent", "portal_submitted"}


def unix(stamp) -> int:
    """An ISO timestamp from state.json as unix seconds.

    The real date is kept rather than stamped with today. `first_at` means
    "when this employer was first written to", and a table claiming 102
    companies were all contacted the moment this script ran is a table that
    lies about the one thing it records.
    """
    if not stamp:
        return db.now()
    try:
        return int(datetime.datetime.fromisoformat(str(stamp)).timestamp())
    except (ValueError, TypeError):
        return db.now()


def contacted_from_state(state: dict) -> dict:
    """{display name: first_at}, earliest wins.

    Two sources, because neither is complete on its own. `companies_contacted`
    is the register the sender writes to, but it is keyed twice over - once by
    company key and once by domain - so it is read for its values, never its
    keys. The jobs themselves catch anything that went out before that
    register existed.
    """
    found: dict[str, int] = {}

    def note(name, stamp):
        name = (name or "").strip()
        if not name:
            return
        when = unix(stamp)
        if name not in found or when < found[name]:
            found[name] = when

    for entry in (state.get("companies_contacted") or {}).values():
        if isinstance(entry, dict):
            note(entry.get("company"), entry.get("at"))

    for job in (state.get("jobs") or {}).values():
        if isinstance(job, dict) and job.get("status") in WAS_WRITTEN_TO:
            note(job.get("company"), job.get("sent_at") or job.get("at"))

    return found


def blocked_from_register() -> list[tuple[str, str]]:
    """The do-not-contact register, as (name, reason).

    Not an optimisation. It holds Hydro Group - Harry's employer - and
    Allstaff, who asked to be left alone. An employer finding out you are job
    hunting because your own software wrote to them is the single most
    expensive mistake either machine could make.

    The register supports `"match": "exact"` for generic names, and the
    product's do_not_contact table has no column for that: it stores a
    company key and nothing else. So "Hydro Group" arrives as the key
    `hydro` and blocks Hydro Cleansing and Hydro Systems too.

    That is the right error to make here and it is deliberate, not an
    oversight. Over-blocking costs one letter that was never sent to a
    drainage firm in London. Under-blocking tells Harry's employer he is
    looking. Those are not comparable, so the loose one wins.
    """
    try:
        with open(DNC) as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return []
    if isinstance(entries, dict):
        # "blocked" is the real key. It was read as "entries" first, which
        # silently imported nothing and reported "to block 0" as though the
        # register were empty - a failure that looks exactly like success.
        entries = entries.get("blocked") or entries.get("entries") or []
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        if name:
            out.append((name, (entry.get("reason") or "imported").strip()))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--user", required=True,
                    help="email address of the account to import into")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would be written, write nothing")
    args = ap.parse_args(argv)

    # Idempotent - CREATE TABLE IF NOT EXISTS throughout - and the same call
    # app.sweep makes. It costs nothing against the live database and means
    # this runs against a fresh one without a chicken-and-egg problem.
    db.init()

    user = db.get_user_by_email(args.user)
    if not user:
        print(f"no account for {args.user}. Sign in on the site once first, "
              f"so there is somewhere to put this.", file=sys.stderr)
        return 1
    uid = user["id"]

    try:
        with open(STATE) as f:
            state = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"cannot read {STATE}: {exc}", file=sys.stderr)
        return 1

    companies = contacted_from_state(state)
    blocks = blocked_from_register()

    # Refuse rather than continue. An empty block list reads identically to a
    # successful import of nothing, and the thing not imported is Harry's
    # employer. Whatever went wrong - a moved file, a renamed key, a broken
    # JSON edit - the answer is to look, not to carry on and connect a
    # mailbox on the strength of a green run.
    if os.path.exists(DNC) and not blocks:
        print(f"{DNC} exists but yielded no entries to block. Refusing to "
              f"continue: this is what a silently broken register looks "
              f"like, and the register holds Harry's own employer.",
              file=sys.stderr)
        return 1
    if not companies:
        print(f"no contacted companies found in {STATE}. Refusing: importing "
              f"nothing would leave the product free to write to all 102.",
              file=sys.stderr)
        return 1

    # Keyed by the PRODUCT's function, so collisions collapse the way they
    # will when the product itself checks. Two names landing on one key is
    # correct behaviour, not loss.
    keyed: dict[str, tuple[str, int]] = {}
    for name, when in sorted(companies.items(), key=lambda kv: kv[1]):
        key = db.company_key(name)
        if key and key not in keyed:
            keyed[key] = (name, when)

    already = 0
    with db.connect() as c:
        rows = c.execute("SELECT company_key FROM contacted WHERE user_id = ?",
                         (uid,)).fetchall()
    existing = {r["company_key"] for r in rows}
    already = len(existing & set(keyed))

    print(f"account            {args.user} (id {uid})")
    print(f"companies in state {len(companies)} names -> {len(keyed)} keys")
    print(f"already recorded   {already}")
    print(f"to add             {len(keyed) - already}")
    print(f"to block           {len(blocks)}")
    print()
    for key, (name, when) in sorted(keyed.items())[:8]:
        when_str = datetime.datetime.fromtimestamp(
            when, datetime.timezone.utc).date()
        mark = "  (have)" if key in existing else ""
        print(f"  {when_str}  {name}  ->  {key}{mark}")
    if len(keyed) > 8:
        print(f"  ... and {len(keyed) - 8} more")
    print()
    for name, reason in blocks:
        print(f"  BLOCK  {name}  ->  {db.company_key(name)}   ({reason})")

    if args.dry_run:
        print("\ndry run, nothing written")
        return 0

    with db.connect() as c:
        for key, (_, when) in keyed.items():
            # DO NOTHING rather than DO UPDATE, matching db.record_contacted:
            # a re-run must not move a date that is already right.
            c.execute("INSERT INTO contacted (user_id, company_key, first_at) "
                      "VALUES (?, ?, ?) ON CONFLICT (user_id, company_key) "
                      "DO NOTHING", (uid, key, when))
        for name, reason in blocks:
            key = db.company_key(name)
            if key:
                c.execute("INSERT INTO do_not_contact "
                          "(user_id, company_key, reason, added_at) "
                          "VALUES (?, ?, ?, ?) "
                          "ON CONFLICT (user_id, company_key) DO NOTHING",
                          (uid, key, reason, db.now()))

    with db.connect() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM contacted "
                          "WHERE user_id = ?", (uid,)).fetchone()["n"]
        blocked = c.execute("SELECT COUNT(*) AS n FROM do_not_contact "
                            "WHERE user_id = ?", (uid,)).fetchone()["n"]
    print(f"\ndone. {total} companies on the contacted list, "
          f"{blocked} blocked.")
    print("Safe to connect the mailbox now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
