"""
The second letter.

Thirty-one letters have gone out and three came back. The other twenty-eight
were sent once and then left alone forever, which is the single cheapest thing
wrong with this system: the address is already known, the letter is already
written, the reader has already been chosen, and nobody ever asked again.

    python followup.py              # who is due, and what would be said
    python followup.py --send       # send them

WHY THIS IS ONE LETTER AND NEVER A SEQUENCE

A chase is worth sending because inboxes are busy and a first email genuinely
gets missed. A second chase is worth sending to the sender, not the reader.
Harry has to work in this city afterwards, and with the agencies he is trying
to get onto a database rather than win an argument - a consultant who
remembers him as the man who emailed four times is worse off than one who
never heard of him. So: one, a working week later, and then the record is closed.

WHY IT THREADS ONTO THE ORIGINAL

The reply carries In-Reply-To and References, so it lands in the same
conversation with the first letter directly above it. That does three things a
fresh email cannot: it proves the first one arrived, it saves the reader
reconstructing who this is, and it reads as a follow-up rather than as a
second cold approach from somebody who has forgotten they already wrote.

WHY THERE IS NO MODEL IN THIS FILE

A follow-up is four sentences and has one job. Generating it invites a
paraphrase of the original pitch, which is the one thing a chase must not be -
the reader has that email open directly below this one. The wording is fixed,
the only variable is what he is asking for, and that is decided by whether the
recipient is an agency or the employer.
"""
import argparse
import random
import re
import sys
from datetime import datetime, timedelta, timezone

import job_machine as jm

# WORKING days, not calendar days, and the distinction is the whole point of
# the unit. The question a chase asks is 'has a busy person had a fair chance
# to read this', and nobody reads a recruitment email on a Sunday. A letter
# sent on the Friday is four calendar days old on the Tuesday and has had
# exactly one working day of attention.
#
# Five is long enough to be a fair run at a busy inbox and short enough that
# the vacancy is usually still open - most of these adverts fill inside three
# weeks.
CHASE_AFTER_WORKING_DAYS = jm.env_int("CHASE_AFTER_WORKING_DAYS", 5)
# After this there is no point: the role has gone and a chase about a filled
# vacancy makes the sender look like he is not paying attention.
CHASE_BEFORE_DAYS = jm.env_int("CHASE_BEFORE_DAYS", 45)
CHASE_PER_RUN = jm.env_int("CHASE_PER_RUN", 8)
CHASED = "chased_at"


def days_since(when):
    stamp = jm.parse_ts(when)
    if not stamp:
        return None
    return (datetime.now(timezone.utc) - stamp).days


def working_days_since(when):
    """Weekdays elapsed. Saturday and Sunday are not chances to reply."""
    stamp = jm.parse_ts(when)
    if not stamp:
        return None
    day = stamp.date()
    today = datetime.now(timezone.utc).date()
    if day > today:
        return 0
    working = 0
    while day < today:
        day += timedelta(days=1)
        if day.weekday() < 5:
            working += 1
    return working


def already_answered(job):
    """A human wrote back. An autoresponder did not, and is still worth a
    chase - it proves the address works and that nobody has read it yet."""
    return bool(job.get("replied_at")) and \
        job.get("reply_category") != "auto_acknowledgement"


def due(job):
    """Is this one worth asking again?"""
    if not job.get("sent_at") or not job.get("contact_email"):
        return False
    if job.get(CHASED):
        return False                      # one, ever
    if already_answered(job):
        return False
    if job.get("status") in ("portal_submitted", "replied"):
        return False
    if days_since(job.get("sent_at")) is None:
        return False
    if days_since(job.get("sent_at")) > CHASE_BEFORE_DAYS:
        return False
    return (working_days_since(job.get("sent_at")) or 0) \
        >= CHASE_AFTER_WORKING_DAYS


def pending(state):
    out = [j for j in state["jobs"].values() if due(j)]
    # Oldest first: the ones closest to the point where chasing stops being
    # worth doing at all.
    return sorted(out, key=lambda j: j.get("sent_at") or "")


# Words that show up in a mailbox handle and never on a birth certificate.
NOT_A_PERSON = ("hr", "support", "info", "careers", "career", "jobs", "job",
                "recruit", "recruitment", "admin", "team", "office", "enquiries",
                "enquiry", "contact", "hello", "hi", "mail", "apply",
                "applications", "people", "talent", "resourcing", "cv", "vacancies")


def greeting(job):
    """'Hello Sarah,' or 'Hello,' - and never anything in between.

    The first draft of this produced 'Hello Mysupporthr,' to Baker Hughes,
    because contact_name had been filled in from the local part of
    mysupporthr@bakerhughes.com and 'Mysupporthr' is a single alphabetic word
    longer than two letters. It would have gone out looking like a mail merge
    that had failed, which is worse than no name at all - a chase is asking a
    stranger for a favour, and the one thing it cannot afford is to look
    automated.

    So the name has to survive three tests: it is one word, it is not a
    mailbox word, and it is not simply the address with the @ taken off."""
    # Worked out from the address, never read off the record - see
    # jm.trusted_contact_name(). A stored name was written under whatever the
    # rules were that day, and those rules have been wrong.
    name = (jm.trusted_contact_name(job) or "").strip()
    if not name or len(name.split()) != 1 or not name.isalpha() or len(name) < 3:
        return "Hello,"
    low = name.lower()
    if any(word in low for word in NOT_A_PERSON):
        return "Hello,"
    local = (job.get("contact_email") or "").split("@")[0].lower()
    # 'Mysupporthr' from mysupporthr@..., 'Giadung' from giadung@... - if the
    # name IS the handle, nobody ever told us a person's name.
    if local and low == re.sub(r"[^a-z]", "", local):
        return "Hello,"
    return f"Hello {name.capitalize()},"


# What he is actually asking for, which is different for the two audiences.
# An agency is a relationship to open; an employer is one vacancy to be
# considered for. Asking an agency about "the role" wastes the approach - they
# have fifty roles and he wants to be on the list for all of them.
ASK_EMPLOYER = [
    "Is the {title} role still open, and is there anything you need from me?",
    "Is that vacancy still live? Happy to send anything else that would help.",
    "Would it be worth me applying formally, or has the role been filled?",
]
ASK_AGENCY = [
    "Is it worth me registering with you properly, so I am on the list when "
    "something suits?",
    "Could you add me to your database for technician and instrumentation work?",
    "Is there someone else there I should be speaking to about workshop and "
    "instrumentation roles?",
]


def compose(job):
    """(subject, body). Short on purpose - the original is directly below."""
    subject = job.get("sent_subject") or f"{job.get('title')} role"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    agency = jm.is_agency(job)
    asks = ASK_AGENCY if agency else ASK_EMPLOYER
    # Deterministic per job, so a dry run shows exactly what a real one sends.
    ask = random.Random(job.get("external_id") or "").choice(asks)
    ask = ask.format(title=job.get("title") or "the")
    what = ("your agency" if agency else (job.get("company") or "you"))
    body = (
        f"{greeting(job)}\n\n"
        f"I wrote to {what} last week about the {job.get('title')} position "
        f"and I know how easily an email gets buried, so I thought I would "
        f"check in once.\n\n"
        f"{ask}\n\n"
        f"I am an electronics and instrumentation technician in Aberdeen, "
        f"currently at Sonardyne, and before that I served in the Royal Navy. "
        f"My CV is on the email below.\n\n"
        f"Thanks for your time.\n\n"
        f"Harry Russell\n"
        f"07398 530978\n"
    )
    return subject, body


def threading_headers(job):
    """Land it in the same conversation as the first letter."""
    parent = job.get("message_id")
    if not parent:
        return None
    return {"In-Reply-To": parent, "References": parent}


def send_one(state, job, dry_run=True):
    subject, body = compose(job)
    to_addr = job["contact_email"]
    if dry_run:
        print(f"\n--- would chase {job.get('company')} <{to_addr}> ---")
        print(f"    threaded: {'yes' if threading_headers(job) else 'no'}")
        print(f"    Subject: {subject}")
        print("    " + body.replace("\n", "\n    ")[:520])
        return True
    try:
        # The CV went with the first letter and is in the thread below. Sending
        # it again says he has not noticed that he already did.
        jm.send_email(to_addr, subject, body, attach_cv=False,
                      headers=threading_headers(job))
    except Exception as e:
        print(f"[chase] could not send to {to_addr}: {type(e).__name__}: {e}")
        return False
    job[CHASED] = jm.now()
    print(f"[chase] {job.get('company')} - {job.get('title')}")
    return True


def run(state, dry_run=True, limit=None):
    ready = pending(state)
    if not ready:
        print(f"[chase] nobody is due - a letter is chased once, "
              f"{CHASE_AFTER_WORKING_DAYS} working days after it went")
        return 0
    budget = limit or CHASE_PER_RUN
    print(f"[chase] {len(ready)} letter(s) unanswered and due, sending "
          f"{min(budget, len(ready))}")
    sent = 0
    for job in ready[:budget]:
        if send_one(state, job, dry_run):
            sent += 1
    return sent


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chase the letters nobody answered")
    ap.add_argument("--send", action="store_true", help="really send them")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    state = jm.load()
    sent = run(state, dry_run=not args.send, limit=args.limit)
    if args.send and sent:
        jm.save(state)
    print(f"\n[chase] {sent} follow-up(s) {'sent' if args.send else 'to send'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
