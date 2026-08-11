"""
Asking people how they got in.

A SEPARATE AVENUE, ON PURPOSE
-----------------------------
Everything else in this project asks for something: consider me, register me,
is the role still open. This asks for nothing, and that is the whole design.

Harry wants offshore rotational work - North Sea or abroad, 2/2 or 3/3 - and
has no civilian offshore time and none of the tickets. The people who can tell
him how that gap is actually crossed are the people who crossed it. They are
not recruiters and mostly are not hiring, so there is no vacancy to apply for
and nothing for them to say no to. There is only a question, and most people
like being asked one they know the answer to.

    python ask_around.py              # who is next, and what would be said
    python ask_around.py --send       # send them

WHAT MAKES THIS WORK, AND WHAT WOULD RUIN IT
--------------------------------------------
IT SAYS HE IS LOOKING. Out loud, in the second paragraph. The temptation is to
write a pure advice letter and let the job come up later, and that is a trick -
the reader works it out on the reply and knows exactly what the first message
really was. Someone who has been in the industry twenty years has had that
letter before. Saying 'this is what I am trying to do' is honest, it is what he
would say in a pub, and it is the version that gets answered.

ONE QUESTION, ANSWERABLE IN TWO LINES. 'How did you get your first offshore
post?' can be replied to from a phone in thirty seconds. Three questions cannot,
so three questions get no reply at all.

NO CV. The moment a CV is attached this becomes an application and gets filed
as one. It is offered, never sent.

NO ASK FOR A JOB IN THE FIRST MESSAGE. Not 'do you have anything', not 'who
should I speak to'. If the conversation goes anywhere, it goes there because
they took it there.

ONE PERSON, ONCE, EVER. And two or three a week. This is not a campaign - it is
Harry asking around, and the moment it looks like a campaign it stops working
and costs him the contact permanently.

WHAT IT DOES NOT DO
-------------------
It never invents a person. Named individuals come from the same discovery the
rest of the machine uses - a real published address on the company's own site,
MX-checked - and if no named person can be found at an organisation, nobody is
written to there. A letter to 'info@' asking how you got into the industry is a
letter to nobody.

Replies are not answered automatically. A person who takes ten minutes to write
back about their career gets Harry, not a machine.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import job_machine as jm

ASKED = "asked_around"
MISSED = "asked_around_missed"
# A site that showed no named person today may show one next month - people
# get promoted onto the 'our team' page. Long enough not to hammer anybody.
RETRY_MISS_DAYS = jm.env_int("ASK_AROUND_RETRY_DAYS", 30)
PEOPLE_PATH = os.path.join(jm.ROOT, "data", "ask_around.json")
# Two or three a week. The cap is the point: this only works while it looks
# like a man asking around, and it stops working the moment it looks sent.
PER_RUN = jm.env_int("ASK_AROUND_PER_RUN", 1)
GAP_DAYS = jm.env_int("ASK_AROUND_GAP_DAYS", 2)


def load_people():
    try:
        with open(PEOPLE_PATH) as f:
            return json.load(f).get("organisations", [])
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ask] cannot read {PEOPLE_PATH}: {e}")
        return []


def asked_register(state):
    return state.setdefault(ASKED, {})


def missed_register(state):
    """Organisations where no named person could be found, and when.

    Kept apart from the asked register on purpose: a miss is a maybe-later,
    and an ask is forever."""
    return state.setdefault(MISSED, {})


def looked_recently(state, key):
    when = jm.parse_ts(missed_register(state).get(key))
    if not when:
        return False
    return (datetime.now(timezone.utc) - when).days < RETRY_MISS_DAYS


def too_soon(state):
    """Has one gone recently? Spacing is what keeps this looking human."""
    stamps = [jm.parse_ts(e.get("at")) for e in asked_register(state).values()]
    stamps = [s for s in stamps if s]
    if not stamps:
        return False
    return (datetime.now(timezone.utc) - max(stamps)).days < GAP_DAYS


# ======================================================================
# THE LETTER
# ======================================================================
# Deliberately not generated. A model asked for 'a warm networking email' will
# produce flattery, three questions and a paragraph about passion, which is
# exactly the letter that gets deleted. This is the letter Harry would write if
# he sat down and wrote it, and the only thing that varies is the one line
# about who they are - which comes from the data file and is checkable.
def compose(person, organisation, note):
    first = (person or "").split()[0] if person else ""
    subject = "Getting into offshore - a question from an Aberdeen technician"
    body = (
        f"Hello{' ' + first if first else ''},\n\n"
        f"I hope you do not mind a cold email. I am an electrical and "
        f"instrumentation technician in Aberdeen - two years in the Royal Navy "
        f"on a Type 23, and the last three at Sonardyne building and fault-"
        f"finding subsea acoustic positioning kit.\n\n"
        f"What I am trying to do is get offshore on a rotation. That is the "
        f"job I want, and I will be straight with you: I have no civilian "
        f"offshore time and none of the tickets yet, though I can get BOSIET "
        f"and MIST through the Job Centre once something is offered.\n\n"
        f"I am writing to you because {note}.\n\n"
        f"My question is just this: how did you get your first offshore post? "
        f"I am trying to work out whether the way in is the operators, the "
        f"contractors, the agencies, or something I have not thought of.\n\n"
        f"Two lines would be plenty, and I am grateful for any of them. Happy "
        f"to send my CV if it is of any use, but I am not asking you for a job "
        f"- just for how you did it.\n\n"
        f"Harry Russell\n"
        f"07398 530978\n"
    )
    return subject, body


def find_person(organisation, domain=None):
    """A real named human at this organisation, or None.

    Same discovery the rest of the machine uses, and the same rule: tier 3 or
    nothing. A letter to info@ asking how you got into the industry is a letter
    to nobody, and it is the sort of thing that makes the whole approach look
    like a mailshot - which it would then be."""
    domain = domain or jm.find_domain(organisation)
    if not domain or not jm.has_mx(domain):
        return None
    address, name, tier = jm.best_email(jm.scrape_site(domain))
    if tier < 3 or not address or not name:
        return None
    if not jm.has_mx(address.split("@")[1]):
        return None
    return {"email": address, "name": name, "domain": domain}


# The one place this could do real harm. A cold email to his current employer
# asking how to get out of the workshop and offshore is a message that can
# reach a line manager, and what it costs is not a wasted approach - it is the
# job he already has. Worth having as a conversation, in person, when he
# chooses. Never as an automated letter.
def his_own_employer(name):
    current = jm.company_key(
        (jm.load_answers_safe() or {}).get("current_employer", ""))
    other = jm.company_key(name)
    if not current or not other:
        return False
    return current == other or current in other or other in current


def due(state, entry):
    key = jm.company_key(entry.get("organisation"))
    if not key or key in asked_register(state):
        return False
    if his_own_employer(entry.get("organisation")):
        print(f"[ask] {entry.get('organisation')} is where he works - "
              f"that conversation is his to have in person")
        return False
    if looked_recently(state, key):
        return False
    # Never twice into the same building by two different routes.
    return key not in state.get("companies_contacted", {})


def run(state, dry_run=True, limit=None):
    people = load_people()
    if not people:
        return 0
    if too_soon(state) and not dry_run:
        print(f"[ask] one went within the last {GAP_DAYS} day(s) - "
              f"spacing these out is the point")
        return 0
    budget = limit or PER_RUN
    sent = 0
    for entry in people:
        if sent >= budget:
            break
        if not due(state, entry):
            continue
        organisation = entry["organisation"]
        found = find_person(organisation, entry.get("domain"))
        if not found:
            # NOT recorded as asked. Failing to find somebody is not the same
            # as having written to them, and the first version of this wrote
            # the miss into the same register - so one bad network day would
            # have burned the entire list permanently, with nobody ever
            # written to and nothing to show why.
            missed_register(state)[jm.company_key(organisation)] = jm.now()
            print(f"[ask] no named person found at {organisation} - will look "
                  f"again in {RETRY_MISS_DAYS} days")
            continue
        subject, body = compose(found["name"], organisation, entry["why"])
        if dry_run:
            print(f"\n--- would ask {found['name']} <{found['email']}> "
                  f"at {organisation} ---")
            print(f"    Subject: {subject}")
            print("    " + body.replace("\n", "\n    "))
            sent += 1
            continue
        to_addr = jm.GMAIL_ADDRESS if jm.TEST_MODE else found["email"]
        try:
            # No CV. Attaching one turns this into an application and it gets
            # filed as one. It is offered in the last paragraph instead.
            jm.send_email(to_addr, subject, body, attach_cv=False)
        except Exception as e:
            print(f"[ask] could not write to {found['email']}: {e}")
            continue
        asked_register(state)[jm.company_key(organisation)] = {
            "at": jm.now(), "person": found["name"],
            "email": found["email"], "organisation": organisation}
        print(f"[ask] asked {found['name']} at {organisation}")
        sent += 1
    if not sent:
        print("[ask] nobody new to ask")
    return sent


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ask people how they got in")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    state = jm.load()
    sent = run(state, dry_run=not args.send, limit=args.limit)
    if args.send and sent:
        jm.save(state)
    print(f"\n[ask] {sent} {'sent' if args.send else 'to send'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
