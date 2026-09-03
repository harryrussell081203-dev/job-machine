"""
Ask the organisations that exist to help Harry whether they can.

WHY THIS IS SEPARATE FROM THE JOB MACHINE
-----------------------------------------
Everything else here writes to employers, asking them to consider him for a
vacancy. This writes to charities, colleges and training bodies, asking a
different question: is he eligible for anything you offer.

He plausibly sits in three categories at once, and most people only think of
one of them:

  VETERAN   Royal Navy 2021-2023. Two years is under the four-year mark, so he
            is an Early Service Leaver - a specific category with its own
            provision, not a lesser one.
  YOUNG     Twenty-two. Most youth funds run to twenty-five or thirty.
  LOCAL     Aberdeen and Scotland have their own employability money.

WHAT IT ASKS FOR
----------------
In order of how much it would change: OPITO offshore survival tickets (about a
thousand pounds, and effectively mandatory for the offshore market his trade
sits in), a driving licence, then anything toward the BEng he paused.

THE RULES, WHICH MATTER MORE HERE THAN ANYWHERE ELSE
----------------------------------------------------
These are charities. Their time and their money belong to people who need them.
So:

  - The email ASKS whether he is eligible. It never asserts that he is, and it
    says nothing whatever about his finances - I do not know his situation, and
    inventing hardship in his name to a charity would be indefensible. The
    request implies what it needs to imply; the letter states only facts.
  - It says plainly what he is doing to help himself, because that is true and
    because it is the difference between a request and a demand.
  - One approach per organisation, ever. No chasing, no follow-up sequence.
    A charity that has not replied has answered.
  - No address is ever guessed. The organisation's name and website are the
    input; job_machine's ordinary discovery finds a real, MX-checked address
    or this sends nothing at all.

    python support_outreach.py --dry-run    # show the letters, send nothing
    python support_outreach.py --send       # send them
"""
import argparse
import json
import os
import sys
import time

import job_machine as jm

SUPPORT_PATH = os.path.join(jm.ROOT, "data", "support_orgs.json")
# The real bound on this is one approach per organisation, ever, against a
# hand-written list of sixteen. The per-run number is only pacing, so it is set
# high enough to clear the list in a single run rather than leaving letters
# sitting unsent between fires.
SUPPORT_PER_RUN = jm.env_int("SUPPORT_PER_RUN", 12)
SUPPORT_INTERVAL_SECONDS = jm.env_int("SUPPORT_INTERVAL_SECONDS", 45)

# What he is actually asking for, worst-first in terms of what blocks him.
NEEDS = (
    "OPITO BOSIET and MIST offshore survival tickets - around a thousand "
    "pounds, and effectively mandatory for offshore work",
    "a driving licence, which is the one thing that limits where I can take "
    "work",
    "anything toward finishing the BEng in Instrumentation, Measurement and "
    "Control I paused at RGU",
)


def load_orgs():
    with open(SUPPORT_PATH) as f:
        data = json.load(f)
    return [o for o in data.get("organisations", []) if not o.get("skip")]


def compose(org):
    """The letter. Deliberately written once, by hand, rather than generated.

    A charity reading this should be able to tell in one line whether he is
    their business, and should not have to wade through a pitch to find the
    question. It asks; it does not assert."""
    group = org.get("group", "")
    who = {
        "veteran": "I left the Royal Navy in 2023 after two years as a "
                   "Communications and Information Specialist on HMS "
                   "Westminster.",
        "young":   "I am 22, based in Aberdeen, and left the Royal Navy in "
                   "2023 after two years as a Communications and Information "
                   "Specialist.",
        "local":   "I am 22 and live in Aberdeen. I left the Royal Navy in "
                   "2023 and have spent the last three years as a workshop "
                   "technician at Sonardyne.",
        "industry": "I am an electronics technician in Aberdeen - three years "
                    "at Sonardyne on subsea acoustic systems, and two years "
                    "before that in the Royal Navy.",
    }.get(group, "I am a 22 year old electronics technician in Aberdeen and a "
                 "Royal Navy veteran.")

    # Some of these organisations have no money to give and are not the right
    # people to ask for any. What the RFCAs and the veteran employment
    # charities have is the list - which employers in a given area have
    # committed to the Armed Forces Covenant, and which of those run a
    # guaranteed interview scheme. That is worth more than the funding ask,
    # because a guaranteed interview is an interview by policy rather than by
    # persuasion. Asking them for training money instead would be asking the
    # right people the wrong question.
    # A recruiter is not being asked for help. They are being told what is
    # available, because a technician they can place is the thing their
    # business runs on. Harry has already sent his CV to several of these
    # himself; a second approach from a candidate who is clearly still looking
    # costs a recruiter nothing to read and is normal in this market.
    if org.get("ask") == "representation":
        body = (
            f"Hello,\n\n"
            f"I am an electronics and instrumentation technician in Aberdeen, "
            f"looking for work and available immediately. I would like to be "
            f"on your books for technician and engineering roles.\n\n"
            f"Briefly:\n\n"
            f"1. Three years at Sonardyne building, testing and fault-finding "
            f"subsea acoustic electronics to IPC-A-610 Class 3.\n"
            f"2. Two years Royal Navy Communications and Information "
            f"Specialist on a Type 23 frigate.\n"
            f"3. SCQF Level 7 engineering apprenticeship completed, part way "
            f"through a BEng in Instrumentation, Measurement and Control.\n\n"
            f"I am 22, I do not drive yet, and I am open to rotational, "
            f"offshore and fly-in fly-out work anywhere that comes with "
            f"transport and accommodation - so location is far less of a "
            f"constraint for me than it is for most people.\n\n"
            f"CV attached. Happy to come in or take a call whenever suits.\n\n"
            f"Harry Russell\n07398 530978"
        )
        return "Electronics / instrumentation technician, Aberdeen - available now", body

    # CTP is a service he is entitled to rather than a favour he is asking
    # for, and the account has to be opened by him because it verifies his
    # service record. So this asks how, and does not pretend to be able to
    # do it for him.
    if org.get("ask") == "ctp access":
        body = (
            f"Hello,\n\n"
            f"I left the Royal Navy in 2023 after two years as a "
            f"Communications and Information Specialist. I understand service "
            f"leavers keep access to CTP and RightJob for life rather than "
            f"only during resettlement.\n\n"
            f"Could you tell me how to get my RightJob access set up or "
            f"reactivated? I am job hunting now - electronics and "
            f"instrumentation technician work - and I would rather be seeing "
            f"the vacancies that are meant for service leavers than only the "
            f"open market.\n\n"
            f"I am 22, based in Aberdeen, three years at Sonardyne on subsea "
            f"electronics since leaving, SCQF Level 7 apprenticeship, "
            f"available immediately and happy with rotational or "
            f"offshore work. CV attached.\n\n"
            f"Thank you.\n\n"
            f"Harry Russell\n07398 530978"
        )
        return "Service leaver - how do I get RightJob access set up?", body

    if org.get("ask") == "employer introductions":
        body = (
            f"Hello,\n\n"
            f"{who}\n\n"
            f"I am looking for technician work - electronics, instrumentation "
            f"and communications - and I am applying steadily on my own. I am "
            f"writing because you work with the employers in this area who "
            f"have committed to supporting the armed forces community.\n\n"
            f"Two questions, if you have a moment:\n\n"
            f"1. Are there employers in the north east of Scotland you would "
            f"point a service leaver in my trade toward?\n"
            f"2. Do any of them run a guaranteed interview scheme for "
            f"veterans who meet the criteria for a role?\n\n"
            f"Three years at Sonardyne building and fault-finding subsea "
            f"electronics, two years Royal Navy communications, SCQF Level 7 "
            f"apprenticeship, available immediately and happy with "
            f"rotational or offshore work anywhere. My CV is attached.\n\n"
            f"Thank you for reading this.\n\n"
            f"Harry Russell\n07398 530978"
        )
        return ("Royal Navy veteran, 22, Aberdeen - employers worth "
                "approaching?"), body

    body = (
        f"Hello,\n\n"
        f"{who}\n\n"
        f"I am looking for technician work in the energy and defence sectors "
        f"and applying steadily. Two things are holding me back:\n\n"
        f"1. {NEEDS[0]}.\n"
        f"2. {NEEDS[1]}.\n\n"
        f"Is either of those something you are able to help with, or point me "
        f"toward? I would also be glad of {NEEDS[2]}, though that is less "
        f"urgent.\n\n"
        f"For context, I completed an SCQF Level 7 engineering apprenticeship, "
        f"and I am available for work immediately, "
        f"including rotational and offshore. My CV is attached.\n\n"
        f"Thank you for reading this.\n\n"
        f"Harry Russell\n07398 530978"
    )
    subject = {
        "veteran": "Royal Navy veteran, 22 - help with offshore tickets?",
        "young":   "22 and job hunting in Aberdeen - training funding?",
        "local":   "Aberdeen, 22, ex-Royal Navy - training support?",
        "industry": "Ex-Navy technician - route to OPITO tickets?",
    }.get(group, "Royal Navy veteran in Aberdeen - any training support?")
    return subject, body


def already_asked(state, org):
    return jm.company_key(org["name"]) in state.setdefault("support_asked", {})


def record(state, org, address):
    state.setdefault("support_asked", {})[jm.company_key(org["name"])] = {
        "at": jm.now(), "name": org["name"], "email": address}


def find_address(org):
    """A real, MX-checked address for this organisation, or None.

    Reuses the pipeline's own discovery, which means the same rule applies
    here as everywhere else in this project: nothing is ever guessed or
    pattern-generated, and no address means no email.

    One exception, and it is not a loophole. An organisation may carry a
    recorded address, but only alongside an 'email_source' saying where it was
    published. Some of these sites sit behind a bot check that serves a
    challenge page to the scraper, so a charity that prints its address
    plainly on its own contact page reads as having none - which is how the
    Forces Employment Charity, who run the government-funded employment
    service Harry is eligible for, came back as 'no real address found'.

    A published address with a citation is the opposite of a guess. It is
    still MX-checked like any other, and an entry with no source is ignored
    however tempting it looks."""
    recorded = (org.get("email") or "").strip().lower()
    if recorded and org.get("email_source") and "@" in recorded:
        if jm.has_mx(recorded.split("@")[1]):
            return recorded, None
        print(f"[support] {org['name']}: recorded address does not resolve")

    domain = org.get("domain")
    if not domain or not jm.has_mx(domain):
        return None, None
    address, name, tier = jm.best_email(jm.scrape_site(domain)[0])
    if not address or tier < 1:
        return None, None
    if not jm.has_mx(address.split("@")[1]):
        return None, None
    return address, name


def run(state, send=False, limit=None):
    orgs = [o for o in load_orgs() if not already_asked(state, o)]
    if not orgs:
        print("[support] every organisation on the list has been asked")
        return 0
    budget = limit or SUPPORT_PER_RUN
    print(f"[support] {len(orgs)} organisation(s) not yet asked, "
          f"writing to at most {budget}")

    asked = 0
    for org in orgs:
        if asked >= budget:
            break
        address, contact = find_address(org)
        if not address:
            print(f"[support] {org['name']}: no real address found, skipping")
            continue
        subject, body = compose(org)
        if not send:
            print(f"\n--- would write to {org['name']} <{address}>")
            print(f"    subject: {subject}")
            print("    " + body.replace("\n", "\n    ")[:600])
            asked += 1
            continue
        try:
            jm.send_email(address, subject, body, attach_cv=True)
            record(state, org, address)
            asked += 1
            print(f"[support] asked {org['name']} <{address}>")
            jm.save(state)
            time.sleep(SUPPORT_INTERVAL_SECONDS)
        except Exception as e:
            print(f"[support] failed {org['name']}: {e}")
    print(f"[support] {asked} organisation(s) written to")
    return asked


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--send", action="store_true",
                    help="actually send (otherwise the letters are printed)")
    ap.add_argument("--dry-run", action="store_true", help="the default")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    state = jm.load()
    run(state, send=args.send and not args.dry_run, limit=args.limit)
    jm.save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
