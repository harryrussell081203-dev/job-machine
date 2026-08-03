"""
Merge two data/state.json files.

Several workflows write state and they can finish at the same time, so a plain
git rebase hits a conflict in a file that is really a set of independent
records. This merges them properly instead:

    python tools/merge_state.py theirs.json ours.json out.json

Jobs are unioned, and where both sides know a job the one further along the
pipeline wins. Counters take the higher count per day, contacted companies are
unioned with the earliest contact kept.
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


def reopened(job):
    """Was this record deliberately put back in the queue to be judged again?"""
    return str(job.get("rescored_at") or "")


def pick(a, b):
    """The more advanced record, breaking ties on how much we know.

    Except when one side was deliberately re-opened. A rescore sets a listing
    back to 'new' on purpose, and 'new' ranks below 'skipped', so the ordinary
    rule quietly undid the whole thing - a re-judging run reported 'no state
    changes' because every listing it re-opened was reverted by this function.
    A deliberate step backwards has to beat an accidental step forwards."""
    if reopened(a) != reopened(b):
        return a if reopened(a) > reopened(b) else b
    if rank(a) != rank(b):
        return a if rank(a) > rank(b) else b
    return a if len(a) >= len(b) else b


def merge(theirs, ours):
    out = dict(theirs)

    jobs = dict(theirs.get("jobs", {}))
    for key, job in ours.get("jobs", {}).items():
        jobs[key] = pick(jobs[key], job) if key in jobs else job
    out["jobs"] = jobs

    contacted = dict(theirs.get("companies_contacted", {}))
    for key, entry in ours.get("companies_contacted", {}).items():
        if key not in contacted:
            contacted[key] = entry
        else:
            existing = contacted[key]
            if isinstance(entry, dict) and isinstance(existing, dict):
                if str(entry.get("at", "")) < str(existing.get("at", "")):
                    contacted[key] = entry
    out["companies_contacted"] = contacted

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
