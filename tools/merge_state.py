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
away the first time a run tried to save one. That cost the ATS board cache, and
then the record of which charities had already been written to - which is worse
than losing data, because a lost record of a letter means sending it twice.
"""
import json
import sys

# Later in this list beats earlier when the two sides disagree about a job.
PROGRESS = ["new", "scored", "skipped", "no_email", "compose_failed",
            "send_failed", "portal_manual", "portal_review", "portal_ready",
            "ready", "portal_submitted", "test_sent", "spec_sent", "sent",
            "replied"]


def rank(job):
    status = job.get("status", "new")
    return PROGRESS.index(status) if status in PROGRESS else 0


REOPENING_FIELDS = ("rescored_at", "portal_fallback_at")


def reopened(job):
    """When was this record deliberately put back in the queue?

    Two stages do it, for different reasons, and both move a listing BACKWARDS
    through PROGRESS on purpose:

      rescored_at        a profile change invalidated the score, so the
                         listing goes back to 'new' to be judged again
      portal_fallback_at the application portal could not be driven, so the
                         listing goes back to 'scored' to be emailed instead

    Only the first was handled here. The second ranks 'scored' (1) below
    'portal_manual' (7), so every one of the eighty-six listings the fallback
    released was reverted by this function on the way back to main - the stage
    ran correctly, three runs in a row, and its work was thrown away each time.

    The newest deliberate re-opening wins, whichever stage did it."""
    return max(str(job.get(field) or "") for field in REOPENING_FIELDS)


def pick(a, b):
    """The more advanced record, breaking ties on how much we know.

    Except when one side was deliberately re-opened. A rescore sets a listing
    back to 'new' on purpose and the portal fallback sets one back to 'scored',
    and both of those rank below the status they came from, so the ordinary
    rule quietly undid the whole thing - a re-judging run reported 'no state
    changes' because every listing it re-opened was reverted by this function.
    A deliberate step backwards has to beat an accidental step forwards."""
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


def union_latest(theirs, ours):
    """Union of two 'how recently have we approached these' registers.

    The newest timestamp wins, and the approach count is the higher of the two
    - so two runs that both wrote to the same agency cannot leave the register
    claiming fewer approaches than were actually made."""
    out = dict(theirs)
    for key, entry in ours.items():
        existing = out.get(key)
        if existing is None:
            out[key] = entry
            continue
        if not (isinstance(entry, dict) and isinstance(existing, dict)):
            continue
        winner = entry if str(entry.get("at", "")) > str(
            existing.get("at", "")) else existing
        winner = dict(winner)
        winner["count"] = max(entry.get("count", 0), existing.get("count", 0))
        first = [str(e.get("first_at") or e.get("at") or "")
                 for e in (entry, existing)]
        first = [f for f in first if f]
        if first:
            winner["first_at"] = min(first)
        out[key] = winner
    return out


# Keys with a rule of their own below. Everything else is carried across by
# carry_unknown(), so a new one is never lost while nobody has written its rule.
KNOWN = {"jobs", "companies_contacted", "support_asked", "send_counts",
         "portal_counts", "spec_counts", "spec_done", "ats_boards",
         "agency_registered", "sms_sent", "contact_numbers",
         "last_summary_at"}


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

    # The agency register. The opposite rule to the one above, and for the
    # opposite reason: an agency is written to more than once on purpose, so
    # what this register answers is not 'has this ever been done' but 'when was
    # it last done and how many times'. Keeping the earliest would let the
    # cooldown expire against a letter that went out weeks later, and the run
    # after that would write to a consultant twice in a fortnight.
    registered = union_latest(theirs.get("agency_registered", {}),
                              ours.get("agency_registered", {}))
    if registered:
        out["agency_registered"] = registered

    # One text per person, ever. Losing a record here means texting somebody
    # a second time, so the earliest wins for the same reason it does for the
    # charities: the question is 'has this ever been done'.
    texted = union_earliest(theirs.get("sms_sent", {}),
                            ours.get("sms_sent", {}))
    if texted:
        out["sms_sent"] = texted

    # The phone book. Two runs can each have read a different signature for
    # the same firm, so the numbers are unioned rather than one side winning.
    book = dict(theirs.get("contact_numbers", {}))
    for key, entry in ours.get("contact_numbers", {}).items():
        existing = book.get(key)
        if not existing:
            book[key] = entry
            continue
        merged = dict(existing)
        numbers = list(existing.get("numbers", []))
        for number in entry.get("numbers", []):
            if number not in numbers:
                numbers.append(number)
        merged["numbers"] = numbers
        merged["name"] = existing.get("name") or entry.get("name")
        book[key] = merged
    if book:
        out["contact_numbers"] = book

    for counter in ("send_counts", "portal_counts", "spec_counts"):
        merged = dict(theirs.get(counter, {}))
        for day, count in ours.get(counter, {}).items():
            merged[day] = max(merged.get(day, 0), count)
        if merged:
            out[counter] = merged

    spec_done = dict(theirs.get("spec_done", {}))
    spec_done.update(ours.get("spec_done", {}))
    if spec_done:
        out["spec_done"] = spec_done

    # Which ATS a company uses, remembered so it is not re-discovered every
    # run. The fresher answer wins; a found board beats a same-age miss.
    boards = dict(theirs.get("ats_boards", {}))
    for key, entry in ours.get("ats_boards", {}).items():
        old = boards.get(key)
        if not old:
            boards[key] = entry
            continue
        newer = str(entry.get("checked_at", "")) > str(old.get("checked_at", ""))
        if newer or (entry.get("ats") and not old.get("ats")):
            boards[key] = entry
    if boards:
        out["ats_boards"] = boards

    for stamp in ("last_summary_at",):
        values = [s for s in (theirs.get(stamp), ours.get(stamp)) if s]
        if values:
            out[stamp] = max(values)

    carry_unknown(out, theirs, ours)
    return out


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main():
    theirs_path, ours_path, out_path = sys.argv[1:4]
    merged = merge(load(theirs_path), load(ours_path))
    with open(out_path, "w") as f:
        json.dump(merged, f, indent=1, sort_keys=True)
    print(f"merged state: {len(merged.get('jobs', {}))} jobs, "
          f"{len(merged.get('companies_contacted', {}))} companies contacted")


if __name__ == "__main__":
    main()
