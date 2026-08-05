"""
Get Harry onto the recruitment agencies' own databases, and keep him there.

WHY THIS IS SEPARATE FROM THE JOB MACHINE
-----------------------------------------
The main pipeline is reactive. An agency posts a vacancy, the machine reads it,
writes to the consultant about that vacancy and moves on. That works, and six
of the fourteen firms in one portal run were agencies - but it only ever
reaches the agencies that happened to advertise something in the last 48 hours,
about the one role they advertised.

A consultant's database is the other half of the same market. It is searched by
the consultant, on their own time, against roles that were never advertised at
all, and most desks sort those search results by how recently the candidate was
in touch. Being in the database is inbound: they come to Harry.

WHY THIS ONE IS ALLOWED TO REPEAT
---------------------------------
Everywhere else here the rule is one approach per company, ever, because a
second unsolicited email to an employer about a different job reads as
pestering. `job_machine.already_contacted` already carves out the exception for
agencies, on the grounds that a firm paid to place people expects to hear from
candidates who fit. This module is that exception applied to registration
rather than to a vacancy: a long cooldown between approaches instead of one
shot, capped, and the second letter is a different letter. A refresh that says
'here is my current CV' is a reason to open the file again. The same pitch sent
twice is a mail merge, and reads like one.

WHAT IT WILL NOT DO
-------------------
  - No address is ever guessed or pattern-generated. The agency's own site is
    scraped by the pipeline's ordinary discovery and the result is MX-checked.
    Nothing real found means nothing is sent, and the agency is reported so it
    can be done by hand.
  - It does not create an account, upload to a portal, or tick anything on
    Harry's behalf. Several of these firms want a profile on their own site;
    that is his to do and the run prints which ones.
  - It says nothing about him that is not true, including the awkward parts -
    he does not drive and he does not hold BOSIET or MIST yet. A consultant who
    finds that out at the point of submission has wasted their time and his.

    python agency_outreach.py                # who is due, and the letters
    python agency_outreach.py --send         # send them
    python agency_outreach.py --list         # the register, with dates
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import job_machine as jm

AGENCIES_PATH = os.path.join(jm.ROOT, "data", "agencies.json")

# A month is the shortest gap that is still a refresh rather than a nudge. It
# is also roughly how often a CV needs touching to stay near the top of a
# consultant's search results, which is the whole point of being on there.
REGISTER_GAP_DAYS = jm.env_int("AGENCY_REGISTER_GAP_DAYS", 30)
# Six approaches is half a year of monthly contact. Past that, silence from a
# desk is an answer.
REGISTER_MAX = jm.env_int("AGENCY_REGISTER_MAX", 6)
REGISTER_PER_RUN = jm.env_int("AGENCY_REGISTER_PER_RUN", 12)
REGISTER_INTERVAL_SECONDS = jm.env_int("AGENCY_REGISTER_INTERVAL_SECONDS", 45)

REGISTER = "agency_registered"

# The facts a consultant needs to place someone, in the order they need them.
# Written out once, by hand, because this is not a per-vacancy letter and there
# is no listing for a model to ground itself in - only the profile, which it
# would be free to embroider. Everything here is in the master CV.
FACTS = (
    "1. Three years at Sonardyne International building, testing and "
    "fault-finding subsea acoustic positioning systems - Ranger 2, Compatt, "
    "USBL - to IPC-A-610 Class 3.\n"
    "2. Two years Royal Navy Communications and Information Specialist on HMS "
    "Westminster, a Type 23 frigate. Secure comms, networks and cryptographic "
    "material. DV cleared.\n"
    "3. Completed Engineering Modern Apprenticeship, SCQF Level 7, electrical "
    "and asset maintenance, plus first year of a BEng in Instrumentation, "
    "Measurement and Control."
)

# The awkward half of the same honesty. A consultant who discovers these at the
# point of submission has wasted a placement and Harry's shot at it.
CONSTRAINTS = (
    "Available immediately, based in Aberdeen, looking around 35k. Happy to go "
    "offshore, rotational or fly-in fly-out anywhere that comes with travel and "
    "accommodation. Two things worth knowing up front: I do not drive, so a "
    "remote site with no transport laid on does not work, and I do not hold "
    "BOSIET or MIST yet - I will get them for the right role."
)


def load_agencies():
    with open(AGENCIES_PATH) as f:
        data = json.load(f)
    return [a for a in data.get("agencies", []) if not a.get("skip")]


def register(state):
    return state.setdefault(REGISTER, {})


def entry_for(state, agency):
    return register(state).get(jm.company_key(agency["name"]))


def days_since(stamp):
    when = jm.parse_ts(stamp)
    if not when:
        return None
    return (datetime.now(timezone.utc) - when).days


def due(state, agency):
    """(is_due, approach_number, reason_if_not).

    Approach 1 is the registration letter, 2+ are refreshes."""
    entry = entry_for(state, agency)
    if not entry:
        return True, 1, ""
    count = entry.get("count", 1)
    if count >= REGISTER_MAX:
        return False, count, f"{count} approaches already, that is the cap"
    waited = days_since(entry.get("at"))
    if waited is not None and waited < REGISTER_GAP_DAYS:
        return False, count, f"written to {waited}d ago, gap is {REGISTER_GAP_DAYS}d"
    return True, count + 1, ""


def recently_pitched(state, agency):
    """Did the main pipeline write to this agency about a vacancy just now?

    The two routes reach the same inbox. A registration letter landing days
    after a note about a live role is the pestering this repository is careful
    to avoid everywhere else, so the vacancy - which is the more useful of the
    two - wins and the registration waits its turn."""
    entry = jm.contact_history(state, {"company": agency["name"]})
    if not entry:
        return None
    waited = days_since(entry.get("at"))
    if waited is not None and waited < jm.AGENCY_GAP_DAYS:
        return waited
    return None


def find_address(agency):
    """A real, MX-checked address for this agency, or (None, None).

    Deliberately does NOT require the website's own domain to carry an MX
    record. Agencies routinely run the careers site on one domain and their
    mail on another - Orion's site is orionjobs.com and their mail is not -
    so the MX check belongs on the address that was actually found, not on the
    domain that was scraped."""
    site = agency.get("site")
    if not site:
        return None, None
    address, name, tier = jm.best_email(jm.scrape_site(site))
    if address and tier >= 1 and jm.has_mx(address.split("@")[1]):
        return address, name
    return published_address(agency)


def published_address(agency):
    """A verified published address, used only when the site cannot be read.

    Eight of the twenty-one came back with nothing on the first live run - most
    of them modern recruitment sites that take enquiries through a form, or sit
    behind a bot check. That is not the same as publishing no address, and the
    same rule as everywhere else applies: this is a transcription of something
    published, recorded with WHERE IT WAS READ in 'email_source', not a pattern
    applied to a domain. No source in the file, no letter."""
    address = (agency.get("email") or "").strip()
    if not address or "@" not in address:
        return None, None
    if not agency.get("email_source"):
        print(f"[agency] {agency['name']}: address in the file has no "
              f"email_source, refusing to use it")
        return None, None
    if not jm.has_mx(address.split("@")[1]):
        return None, None
    return address, None


def greeting(name):
    """By first name when discovery found one, the same rule the pipeline uses."""
    return f"Hi {name}," if name else "Hello,"


def compose(agency, approach, entry=None, contact=None):
    """(subject, body). Hand-written, and different on the second visit."""
    if approach <= 1:
        return first_letter(agency, contact)
    return refresh_letter(agency, entry, contact)


def first_letter(agency, contact=None):
    desk = (agency.get("desk") or "").strip().rstrip(".")
    veteran = agency.get("group") == "veteran"
    if veteran:
        opening = (
            "I am a Royal Navy veteran and an electronics and instrumentation "
            "technician in Aberdeen, and I would like to be registered with "
            "you.")
    else:
        opening = (
            "I am an electronics and instrumentation technician in Aberdeen "
            "and I would like to be on your database.")
    reason = f" I am writing to you because {desk}." if desk else ""
    body = (
        f"{greeting(contact)}\n\n"
        f"{opening}{reason} My CV is attached.\n\n"
        f"{FACTS}\n\n"
        f"{CONSTRAINTS}\n\n"
        f"Who is the right person for this to sit with, and is there anything "
        f"else you need from me to be on the system?\n\n"
        f"Harry Russell\n{jm.PHONE}"
    )
    subject = ("Royal Navy veteran, Aberdeen technician - registering"
               if veteran else
               "Aberdeen electronics technician - for your database")
    return subject, body


def refresh_letter(agency, entry, contact=None):
    """The second and later letters. A refresh, not the pitch again.

    It says when the last one went, because saying so is the difference
    between checking in and pretending the first email never happened."""
    when = ""
    stamp = jm.parse_ts((entry or {}).get("at"))
    if stamp:
        when = f" I last wrote in {stamp.strftime('%B')}, and n"
    body = (
        f"{greeting(contact)}\n\n"
        f"Harry Russell, electronics and instrumentation technician in "
        f"Aberdeen.{when or ' N'}othing has changed on the facts, so this is a "
        f"refresh with my current CV attached - the version you hold should be "
        f"this one.\n\n"
        f"{FACTS}\n\n"
        f"{CONSTRAINTS}\n\n"
        f"Anything live at the moment that this fits?\n\n"
        f"Harry Russell\n{jm.PHONE}"
    )
    return "CV refresh - Aberdeen electronics technician", body


def cv_file():
    """The electronics technician CV. There is no per-vacancy role family to
    tailor to here, and that is the one this profile leads with."""
    try:
        import cv_tailor
        return cv_tailor.build("electronics_technician") or jm.cv_path()
    except Exception as e:
        print(f"[agency] tailoring unavailable: {e}")
        return jm.cv_path()


def record(state, agency, address, approach):
    key = jm.company_key(agency["name"])
    previous = register(state).get(key, {})
    register(state)[key] = {
        "name": agency["name"],
        "at": jm.now(),
        "first_at": previous.get("first_at") or previous.get("at") or jm.now(),
        "count": approach,
        "email": address,
        "emails": sorted(set((previous.get("emails") or []) + [address])),
    }


def run(state, send=False, limit=None):
    agencies = load_agencies()
    budget = limit or REGISTER_PER_RUN
    queue = []
    for agency in agencies:
        ok, approach, reason = due(state, agency)
        if not ok:
            print(f"[agency] {agency['name']}: {reason}")
            continue
        waited = recently_pitched(state, agency)
        if waited is not None:
            print(f"[agency] {agency['name']}: pipeline wrote to them "
                  f"{waited}d ago about a vacancy, leaving them alone")
            continue
        queue.append((agency, approach))

    print(f"[agency] {len(queue)} of {len(agencies)} due, "
          f"writing to at most {budget}")
    written = 0
    for agency, approach in queue:
        if written >= budget:
            print(f"[agency] per-run cap ({budget}) reached")
            break
        address, contact = find_address(agency)
        if not address:
            print(f"[agency] {agency['name']}: no real address on "
                  f"{agency.get('site')}, do this one by hand")
            continue
        subject, body = compose(agency, approach, entry_for(state, agency),
                                contact)
        real_to = address
        to_addr = jm.GMAIL_ADDRESS if jm.TEST_MODE else real_to
        if jm.TEST_MODE:
            subject = f"[TEST -> {real_to}] {subject}"

        if not send:
            print(f"\n--- approach {approach} to {agency['name']} <{address}>")
            print(f"    subject: {subject}")
            print("    " + body.replace("\n", "\n    "))
            written += 1
            continue
        try:
            jm.send_email(to_addr, subject, body, attach_cv=True,
                          cv_file=cv_file())
            if not jm.TEST_MODE:
                record(state, agency, address, approach)
            jm.record_send(state)
            written += 1
            label = "TEST" if jm.TEST_MODE else "LIVE"
            print(f"[agency] {label} approach {approach} "
                  f"{agency['name']} -> {to_addr}")
            jm.save(state)
            time.sleep(REGISTER_INTERVAL_SECONDS)
        except Exception as e:
            print(f"[agency] failed {agency['name']}: {e}")
    print(f"[agency] {written} agency letter(s) "
          f"{'sent' if send else 'drafted'}")
    return written


def show(state):
    entries = register(state)
    if not entries:
        print("[agency] nothing written yet")
        return 0
    print(f"[agency] {len(entries)} agency register entries")
    for key, entry in sorted(entries.items()):
        waited = days_since(entry.get("at"))
        print(f"   {entry.get('name', key)[:32]:34} "
              f"approach {entry.get('count', 1)}  "
              f"{waited}d ago  {entry.get('email', '')}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--send", action="store_true",
                    help="actually send (otherwise the letters are printed)")
    ap.add_argument("--dry-run", action="store_true", help="the default")
    ap.add_argument("--list", action="store_true",
                    help="show who has been written to and when")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    state = jm.load()
    if args.list:
        return show(state)
    run(state, send=args.send and not args.dry_run, limit=args.limit)
    jm.save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
