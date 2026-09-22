"""
Merge two data/state.json files.

Several workflows write state and they can finish at the same time, so a plain
git rebase hits a conflict in a file that is really a set of independent
records. This merges them properly instead:

    python tools/merge_state.py theirs.json ours.json out.json

Jobs are unioned, and where both sides know a job the one further along the
pipeline wins. Counters take the higher count per day, contacted companies are
unioned with the earliest contact kept.

Anything this file does not have a rule for is still carried across, because it
used to be silently dropped: the merge started from a copy of `theirs` and then
copied over only the keys it recognised, so every new top-level key was thrown
away the first time a run tried to save one. That cost the record of which
charities had already been written to - which is worse than losing data,
because a lost record of a letter means sending it twice.

THIS MERGES PLAINTEXT, ALWAYS, AND SEALS ONCE AT THE END.

Everything below compares records to decide which side wins. Ciphertext cannot
be compared: two records holding the same address hold different bytes
depending on which side sealed them, and every rule here - pick(), the
earliest-timestamp rule, the counter maxima - would be comparing the wrong
thing.

It is not a theoretical problem. The machine started sealing state.json
before this file knew about it, and the result was measured against the real
file: of 579 addresses, 570 stayed in the clear and NOT ONE enc.v1: marker
survived the merge. The reason is that every rule here prefers the copy
already on the branch when the two sides tie - pick() falls through to
`len(a) >= len(b)` with theirs as `a`, and union_earliest() keeps theirs
unless ours is strictly older. A sealed record and its plaintext twin tie on
every one of those tests, so the plaintext side won every time.

So the machine would have sealed the file on every run, and this would have
unsealed it again on every commit, for ever, while looking like it worked.

Hence: unseal both sides on the way in, merge plaintext, seal on the way out.
personal.py is the only thing that knows how, and a missing key raises rather
than letting a half-merged state through - see the fallback in
commit-state.sh, which keeps our own already-sealed copy when that happens.
"""
import json
import os
import sys

# personal.py lives at the repository root, so the root goes on the path
# explicitly - but the ORDER here is load-bearing.
#
# commit-state.sh copies this script to /tmp and runs it from there, because
# `git reset --hard origin/<target>` restores the TARGET branch's tools/ and a
# branch that changes the merge rules must be merged by its own. personal.py
# is reset the same way, so it is copied beside this script for the same
# reason - and beside has to win. Looking in the working directory first
# would import the copy the reset just restored, which is exactly the
# substitution the copying exists to prevent.
#
# Run normally from tools/, the first candidate holds no personal.py and the
# repository root is found on the second.
for _candidate in (os.path.dirname(os.path.abspath(__file__)),
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   os.getcwd()):
    if os.path.exists(os.path.join(_candidate, "personal.py")):
        sys.path.insert(0, _candidate)
        break
import personal  # noqa: E402

# Later in this list beats earlier when the two sides disagree about a job.
#
# The portal_* statuses are from the retired application-portal agent. They stay
# here because live records still carry them - three real applications sit at
# 'portal_submitted' and are watched for replies - and because a status missing
# from this list ranks 0, i.e. loses to everything. Nothing new is written into
# portal_manual/review/ready, but dropping them would mean any stale record on
# an unmerged branch could be overwritten by a bare 'new'.
PROGRESS = ["new", "scored", "skipped", "no_email", "compose_failed",
            "send_failed", "portal_manual", "portal_review", "portal_ready",
            "ready", "do_not_contact", "portal_submitted", "test_sent",
            "spec_sent", "sent", "replied"]


def rank(job):
    status = job.get("status", "new")
    return PROGRESS.index(status) if status in PROGRESS else 0


REOPENING_FIELDS = ("rescored_at", "portal_fallback_at", "rediscovered_at")


def reopened(job):
    """When was this record deliberately put back in the queue?

    Three stages do it, for different reasons, and all move a listing
    BACKWARDS through PROGRESS on purpose:

      rescored_at        a profile change invalidated the score, so the
                         listing goes back to 'new' to be judged again
      portal_fallback_at the application portal could not be driven, so the
                         listing goes back to 'scored' to be emailed instead.
                         The portal agent is retired and nothing writes this
                         field any more, but released records still carry it
                         and it still has to beat their old parked status.
      rediscovered_at    the listing was binned for want of a company domain
                         this machine turned out to already hold, so it goes
                         back to 'scored' to have its address looked for.

    Only the first was handled here. The second ranks 'scored' (1) below
    'portal_manual' (7), so every one of the eighty-six listings the fallback
    released was reverted by this function on the way back to main - the stage
    ran correctly, three runs in a row, and its work was thrown away each time.

    The third is added at the same time as the stage that writes it, rather
    than after discovering the same way that it does not survive the merge.
    'scored' (1) ranks below 'no_email' (3), so without this line every
    rediscovered listing would be reverted on its way back to main.

    The newest deliberate re-opening wins, whichever stage did it."""
    return max(str(job.get(field) or "") for field in REOPENING_FIELDS)


# Statuses a listing never comes back from. Either a message has actually
# left the building, or one must never leave it.
#
# Re-opening only ever means "put this back in the queue to be looked at
# again", and none of these is in the queue any more. An employer cannot be
# un-emailed.
TERMINAL = frozenset((
    "do_not_contact", "portal_submitted", "test_sent", "spec_sent",
    "sent", "replied",
))


def terminal(job):
    return job.get("status") in TERMINAL


def pick(a, b):
    """The more advanced record, breaking ties on how much we know.

    Except when one side was deliberately re-opened. A rescore sets a listing
    back to 'new' on purpose and the portal fallback sets one back to 'scored',
    and both of those rank below the status they came from, so the ordinary
    rule quietly undid the whole thing - a re-judging run reported 'no state
    changes' because every listing it re-opened was reverted by this function.
    A deliberate step backwards has to beat an accidental step forwards.

    But not past a letter that has already gone. The re-opening rule was
    written to beat rank, and it beat rank absolutely - so a record marked
    'sent' on one side lost to a re-opened 'scored' on the other, and the
    application would be composed and sent a second time.

    That hole was invisible because the one test covering it put the SAME
    portal_fallback_at on both sides, which makes the two equal and falls
    through to rank before the re-opening rule can do any damage. It only
    opens when one side carries a re-opening stamp the other does not - which
    is the ordinary case for a stage that re-opens listings, and would have
    shipped with rediscover().
    """
    if terminal(a) != terminal(b):
        return a if terminal(a) else b
    if reopened(a) != reopened(b):
        return a if reopened(a) > reopened(b) else b
    if rank(a) != rank(b):
        return a if rank(a) > rank(b) else b
    return a if len(a) >= len(b) else b


def union_earliest(theirs, ours):
    """Union of two 'we have already approached these' registers.

    The earliest timestamp wins, because the question these answer is 'has this
    ever been done', and the first time is the true answer."""
    out = dict(theirs)
    for key, entry in ours.items():
        existing = out.get(key)
        if existing is None:
            out[key] = entry
        elif isinstance(entry, dict) and isinstance(existing, dict):
            if str(entry.get("at", "")) < str(existing.get("at", "")):
                out[key] = entry
    return out


# Keys with a rule of their own below. Everything else is carried across by
# carry_unknown(), so a new one is never lost while nobody has written its rule.
KNOWN = {"jobs", "companies_contacted", "support_asked", "send_counts",
         "spec_counts", "call_script_counts", "spec_done", "agency_registered",
         "last_summary_at", "heartbeat"}


def carry_unknown(out, theirs, ours):
    """Keep top-level keys no rule covers, rather than dropping them.

    Two dicts are unioned and theirs wins a clash: without knowing what the key
    means, keeping both sides' records is the choice that cannot lose one. It
    says so out loud, because a key showing up here wants a real rule."""
    for key, value in ours.items():
        if key in KNOWN:
            continue
        mine = theirs.get(key)
        if isinstance(value, dict) and isinstance(mine, dict):
            merged = dict(value)
            merged.update(mine)
            out[key] = merged
        elif key not in theirs:
            out[key] = value
        print(f"merge: '{key}' has no merge rule, carried across as-is",
              file=sys.stderr)


def merge(theirs, ours):
    out = dict(theirs)

    jobs = dict(theirs.get("jobs", {}))
    for key, job in ours.get("jobs", {}).items():
        jobs[key] = pick(jobs[key], job) if key in jobs else job
    out["jobs"] = jobs

    out["companies_contacted"] = union_earliest(
        theirs.get("companies_contacted", {}), ours.get("companies_contacted", {}))

    # The charities and training bodies. One approach each, ever - so this
    # register losing an entry means an organisation gets written to twice.
    asked = union_earliest(theirs.get("support_asked", {}),
                           ours.get("support_asked", {}))
    if asked:
        out["support_asked"] = asked

    # Recruitment agencies, which unlike everyone else may be approached a few
    # times. That makes this a counter rather than a flag, and a counter that
    # merges wrong is worse than one that is lost: taking either side's count
    # rather than the higher one would let two concurrent runs each think an
    # agency had been written to once, and keep writing.
    agencies = dict(theirs.get("agency_registered", {}))
    for key, entry in ours.get("agency_registered", {}).items():
        old = agencies.get(key)
        if not isinstance(old, dict) or not isinstance(entry, dict):
            agencies.setdefault(key, entry)
            continue
        merged = dict(old)
        merged.update({k: v for k, v in entry.items() if k not in
                       ("count", "at", "first_at", "emails")})
        merged["count"] = max(old.get("count", 0), entry.get("count", 0))
        merged["at"] = max(str(old.get("at", "")), str(entry.get("at", "")))
        firsts = [s for s in (old.get("first_at"), entry.get("first_at")) if s]
        if firsts:
            merged["first_at"] = min(str(s) for s in firsts)
        seen = list(old.get("emails") or []) + list(entry.get("emails") or [])
        if seen:
            merged["emails"] = list(dict.fromkeys(seen))
        agencies[key] = merged
    if agencies:
        out["agency_registered"] = agencies

    # "When did this machine last run", and the newest answer is the true one.
    #
    # It had no rule, so carry_unknown() merged it with theirs winning - and
    # "theirs" is the copy already on main, which was itself frozen the same
    # way. last_run could therefore never advance: it sat at 2026-09-08T12:03
    # while the machine ran a dozen more times.
    #
    # That is not a cosmetic stamp. It is the input to the only alarm that
    # tells Harry the machine has DIED, and a frozen last_run means the alarm
    # fires on a healthy machine roughly once a day, forever. An alarm that is
    # always going off is one he learns to ignore, which is worse than having
    # no alarm at all - the outage it was built for would arrive looking
    # exactly like the eleven false ones before it.
    #
    # max() on both stamps: the latest run is the one that happened, and the
    # latest alert is what stops a recovered outage being re-reported.
    beats = [b for b in (theirs.get("heartbeat"), ours.get("heartbeat"))
             if isinstance(b, dict)]
    if beats:
        merged = {}
        for beat in beats:
            merged.update(beat)
        for field in ("last_run", "alerted_at"):
            stamps = [str(b[field]) for b in beats if b.get(field)]
            if stamps:
                merged[field] = max(stamps)
        out["heartbeat"] = merged

    for counter in ("send_counts", "spec_counts", "call_script_counts"):
        merged = dict(theirs.get(counter, {}))
        for day, count in ours.get(counter, {}).items():
            merged[day] = max(merged.get(day, 0), count)
        if merged:
            out[counter] = merged

    spec_done = dict(theirs.get("spec_done", {}))
    spec_done.update(ours.get("spec_done", {}))
    if spec_done:
        out["spec_done"] = spec_done

    for stamp in ("last_summary_at",):
        values = [s for s in (theirs.get(stamp), ours.get(stamp)) if s]
        if values:
            out[stamp] = max(values)

    carry_unknown(out, theirs, ours)
    return out


def load(path):
    """Read a state file as plaintext, whether or not it arrived sealed.

    A missing or unreadable file is an empty state - that is the pre-existing
    behaviour and it is right, because one side of a merge legitimately does
    not exist on a first run. A file that IS readable and IS sealed with no
    key to open it is not that case, and personal.unseal raises: merging a
    state whose contacts we cannot see would drop them on the losing side of
    every comparison below.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return personal.unseal(data) if isinstance(data, dict) else {}


def main():
    theirs_path, ours_path, out_path = sys.argv[1:4]
    merged = merge(load(theirs_path), load(ours_path))
    # Sealed once, here, after every comparison has been made in plaintext.
    # No key configured means written as it always was - the loud failure
    # belongs on the read side. See personal.py.
    merged = personal.seal(merged)
    with open(out_path, "w") as f:
        json.dump(merged, f, indent=1, sort_keys=True)
    print(f"merged state: {len(merged.get('jobs', {}))} jobs, "
          f"{len(merged.get('companies_contacted', {}))} companies contacted")


if __name__ == "__main__":
    main()
