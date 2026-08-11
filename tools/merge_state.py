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
            "send_failed", "portal_failed", "portal_manual", "portal_review",
            "portal_awaiting_captcha", "portal_ready",
            "ready", "portal_submitted", "test_sent", "spec_sent", "sent",
            "replied"]

# Anything the code can set that is missing from PROGRESS ranks 0 - below
# 'new' - and loses every merge, so the work that produced it is thrown away
# on the way back to main. That is not hypothetical:
# 'portal_awaiting_captcha' was missing, and it is the status for an
# application the agent has FILLED IN COMPLETELY and banked every answer for,
# blocked only by a bot check. Five of them were built and discarded in a
# single run, and the run before, and the one before that. The state file
# still shows 121 jobs carrying portal_attempted_at whose status is 'no_email'
# or 'skipped' - the portal work happened and the merge deleted it.
#
# tests/test_merge_state.py greps every status the code can set and fails if
# one is not listed above, so the next new status cannot repeat this quietly.


def rank(job):
    status = job.get("status", "new")
    return PROGRESS.index(status) if status in PROGRESS else -1


REOPENING_FIELDS = ("rescored_at", "portal_fallback_at", "portal_reopened_at",
                    "pruned_overseas_at")


def reopened(job):
    """When was this record deliberately put back in the queue?

    Four stages do it, for different reasons, and all move a listing
    BACKWARDS through PROGRESS on purpose:

      rescored_at        a profile change invalidated the score, so the
                         listing goes back to 'new' to be judged again
      portal_fallback_at the application portal could not be driven, so the
                         listing goes back to 'scored' to be emailed instead
      portal_reopened_at a bug that parked it has been fixed, so it goes back
                         to 'scored' to be tried again
      pruned_overseas_at the advert says the job is in another country, so it
                         goes to 'skipped' however far along it was

    Only the first was handled here. The second ranks 'scored' (1) below
    'portal_manual' (7), so every one of the eighty-six listings the fallback
    released was reverted by this function on the way back to main - the stage
    ran correctly, three runs in a row, and its work was thrown away each time.

    THE THIRD IS THE SAME BUG AGAIN, POINTING THE OTHER WAY, and it is worse.
    reopen_fallbacks() REMOVES portal_fallback_at and writes portal_reopened_at
    in its place. So after a burn run:

        ours   (the runner)  no portal_fallback_at, status portal_manual
        theirs (main)        portal_fallback_at from days ago, status no_email

    reopened(ours) was the empty string and reopened(theirs) was a real
    timestamp, so THEIRS won every time - and the side that had actually
    opened a browser, filled a form and recorded the outcome was discarded.
    A whole burn run landed on main having recorded nothing at all: seven
    pages captured, nine screenshots taken, zero attempts saved.

    The newest deliberate re-opening wins, whichever stage did it - and a
    re-opening is now always newer than the fallback it replaced, so the run
    that did the work keeps it."""
    return max(str(job.get(field) or "") for field in REOPENING_FIELDS)


def known(job):
    return job.get("status", "new") in PROGRESS


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
    # A status this file has never heard of is not evidence that the record is
    # behind - it is evidence that somebody added a stage and did not come
    # here. Keeping the fuller record is the only choice that cannot silently
    # bin an application that was actually filled in.
    if known(a) != known(b):
        print(f"merge: unknown status "
              f"{(a if not known(a) else b).get('status')!r}, keeping the "
              f"fuller record rather than assuming it is behind",
              file=sys.stderr)
        return a if len(a) >= len(b) else b
    if rank(a) != rank(b):
        return a if rank(a) > rank(b) else b
    # Same status on both sides. Then the newer record is the true one, and
    # 'more keys' is not a proxy for it.
    #
    # A re-attempt could never be recorded. T-Tech was filled to 13 of 15
    # fields at 09:32 and attempted again at 09:44 with two bugs fixed - and
    # the merge kept the 09:32 version, because both said
    # portal_awaiting_captcha, both had the same number of keys, and the
    # tie-break was '>=' which favours whatever is already on main. Three of
    # the five applications that run worked on were discarded that way, and
    # the numbers came back looking as though it had barely run.
    fresh_a, fresh_b = last_touched(a), last_touched(b)
    if fresh_a != fresh_b:
        return a if fresh_a > fresh_b else b
    return a if len(a) >= len(b) else b


def last_touched(job):
    """The most recent thing that happened to this record.

    Every stage stamps what it did with an ISO timestamp, so the newest of
    them is when this record was last worked on. String comparison is
    correct for ISO-8601 and does not need parsing."""
    return max((str(v) for k, v in job.items()
                if k.endswith("_at") and isinstance(v, str)), default="")


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


def merge_inbox(theirs, ours):
    """The inbox register: one entry per message, marks kept from both sides.

    This needs a rule of its own, and the reason is the bug that has now bitten
    this file four separate times: carry_unknown() unions the two dicts and lets
    THEIRS win a clash, which for this register means a runner's marks are
    thrown away. The runner reads the mailbox, classifies a message and records
    texted_at; main already has that message from an earlier run without the
    mark; theirs wins; the mark is gone; and Harry gets texted about the same
    email again on the next run, and the one after that.

    So the marks are merged rather than the entries. texted_at and done_at are
    'has this ever happened' facts, and the earliest answer is the true one. A
    triaged entry beats an untriaged one because triage costs a model call and
    losing it means paying for it twice."""
    out = dict(theirs)
    for key, entry in ours.items():
        existing = out.get(key)
        if not isinstance(existing, dict) or not isinstance(entry, dict):
            if existing is None:
                out[key] = entry
            continue
        # Whichever side actually read the message is the better base.
        base, other = ((entry, existing) if entry.get("category")
                       and not existing.get("category") else (existing, entry))
        merged = dict(base)
        for mark in ("texted_at", "done_at"):
            stamps = [str(e[mark]) for e in (entry, existing) if e.get(mark)]
            if stamps:
                merged[mark] = min(stamps)
        if not merged.get("category") and other.get("category"):
            merged["category"] = other["category"]
            merged["do"] = other.get("do")
        out[key] = merged
    return out


# Keys with a rule of their own below. Everything else is carried across by
# carry_unknown(), so a new one is never lost while nobody has written its rule.
KNOWN = {"jobs", "companies_contacted", "support_asked", "send_counts",
         "portal_counts", "spec_counts", "spec_done", "ats_boards",
         "agency_registered", "sms_sent", "contact_numbers",
         "portal_answer_cache", "last_summary_at", "inbox",
         "agency_tickets_asked", "agency_meeting_asked", "morning_sent_on",
         "evening_sent_on", "handoff_emailed_on", "morning_goal_rotation"}


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

    # The answers the machine has already worked out, keyed by the question.
    # Both sides are kept and the earliest answer wins, because these are
    # questions whose answer does not depend on the employer - the second
    # answer to 'what is your notice period' is not better than the first, and
    # keeping one steady answer means two applications never contradict each
    # other. Losing this costs a Gemini call per question on a free tier that
    # allows about ten a minute, which is what once put a run eight minutes
    # into 429s for a single application.
    cached = union_earliest(theirs.get("portal_answer_cache", {}),
                            ours.get("portal_answer_cache", {}))
    if cached:
        out["portal_answer_cache"] = cached

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

    # Every email that has been read, what it wants, and whether it has been
    # texted about or dealt with. See merge_inbox for why theirs-wins is wrong.
    seen = merge_inbox(theirs.get("inbox", {}), ours.get("inbox", {}))
    if seen:
        out["inbox"] = seen

    # The two one-off asks to an agency: would you sponsor the tickets, and
    # could I come in and see you. Same rule as the charities, for the same
    # reason - the question is "has this ever been done", and losing an entry
    # means a consultant gets the same letter twice a fortnight apart.
    #
    # These were falling through to carry_unknown, which says so in the log on
    # every single run. It got them right by luck: it unions the dicts and lets
    # theirs win, and theirs is main. A runner that wrote the letter and lost
    # the record is exactly how the same firm gets asked twice.
    for register in ("agency_tickets_asked", "agency_meeting_asked"):
        asked = union_earliest(theirs.get(register, {}), ours.get(register, {}))
        if asked:
            out[register] = asked

    # Day stamps: the later one wins. Each answers "has today's gone out yet",
    # so an older value re-opens a guard that has already been spent and sends
    # a second copy of something he has read.
    for stamp in ("last_summary_at", "morning_sent_on", "evening_sent_on",
                  "handoff_emailed_on"):
        values = [s for s in (theirs.get(stamp), ours.get(stamp)) if s]
        if values:
            out[stamp] = max(values)

    # Which standing goal the morning text is up to. The higher number wins:
    # it only ever advances, and going backwards repeats a goal he read
    # yesterday, which is the one thing that makes a daily text ignorable.
    rotation = [n for n in (theirs.get("morning_goal_rotation"),
                            ours.get("morning_goal_rotation"))
                if isinstance(n, int)]
    if rotation:
        out["morning_goal_rotation"] = max(rotation)

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
