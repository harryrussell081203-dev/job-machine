"""
Ask a trade body for a conversation, not for a job and not for money.

WHY THIS IS SEPARATE FROM BOTH THE JOB MACHINE AND THE SUPPORT LETTERS
----------------------------------------------------------------------
The job machine writes to employers about a vacancy. support_outreach.py
writes to charities and funding bodies about eligibility. This writes to the
trade bodies that sit above both, and asks a third question: can I talk to
somebody about the sector.

That is worth doing because a trade body knows things no job advert contains
- who is growing, who takes service leavers, which employers in the region
are worth approaching - and because asking is free and normal. It is worth
keeping separate because the machinery around a job application is wrong
here in every particular: there is no vacancy to prove he read, no numbered
case to make against a role, no follow-up cadence to run, and no CV to
attach.

WHAT IT WILL NOT DO
-------------------
  - It does not pitch for a job. A trade body does not hire technicians, and
    treating one as an employer wastes the single approach we get.
  - It does not mention money, funding, investment or backing, in any form.
    A cold email asking somebody to put money into a business is a financial
    promotion under section 21 of the Financial Services and Markets Act, and
    making one without FCA authorisation or an exemption is a criminal
    offence. The safe line is not "phrase it carefully" - it is that money
    never appears in a cold email from this project at all.
  - It does not attach the CV. A CV is a job-application artifact; attaching
    one to a "could we talk" note turns it into the pitch it is not.
  - It does not chase. One approach per body, ever.
  - It does not guess an address. The body's name and website are the input;
    job_machine's ordinary discovery finds a real, MX-checked address or this
    sends nothing at all.

    python networking_outreach.py --dry-run    # show the letters, send none
    python networking_outreach.py --send       # send them
"""
import argparse
import json
import os
import sys
import time

import job_machine as jm

NETWORK_PATH = os.path.join(jm.ROOT, "data", "networking_targets.json")
# Small and slow-moving: three bodies today, one approach each, ever. The cap
# is pacing rather than a real ceiling, same as the support letters.
NETWORK_PER_RUN = jm.env_int("NETWORK_PER_RUN", 6)
NETWORK_INTERVAL_SECONDS = jm.env_int("NETWORK_INTERVAL_SECONDS", 45)


def load_targets():
    with open(NETWORK_PATH) as f:
        data = json.load(f)
    return [t for t in data.get("targets", []) if not t.get("skip")]


def compose(target):
    """The letter. Written once, by hand, like the support letters.

    Short on purpose. There is no vacancy to match and no case to argue, so
    the whole thing is a person saying who he is and asking for a
    conversation. Anything longer starts to read as a pitch for something,
    and the moment it reads as a pitch it is the wrong email."""
    name = (target.get("name") or "").strip()
    body = (
        f"Hello,\n\n"
        f"I am an electronics and instrumentation technician in Aberdeen - "
        f"currently testing and repairing subsea cables and connectors, three "
        f"years before that at Sonardyne on subsea acoustic systems, and two "
        f"years in the Royal Navy as a Communications and Information "
        f"Specialist.\n\n"
        f"I am trying to understand this sector properly rather than only "
        f"through job adverts. Would somebody at {name} be willing to have a "
        f"short conversation about where the work is going and where a "
        f"technician background is most useful?\n\n"
        f"Happy to work around whoever has the time - a phone call of "
        f"fifteen minutes would be plenty.\n\n"
        f"Thank you for reading this.\n\n"
        f"Harry Russell\n07398 530978"
    )
    return "Technician in Aberdeen - could I ask you about the sector?", body


def already_asked(state, target):
    return jm.company_key(target["name"]) in state.setdefault("network_asked", {})


def record(state, target, address):
    state.setdefault("network_asked", {})[jm.company_key(target["name"])] = {
        "at": jm.now(), "name": target["name"], "email": address}


def find_address(target):
    """A real, MX-checked address for this body, or None.

    Reuses the pipeline's own discovery, so the same rule applies here as
    everywhere else: nothing is guessed or pattern-generated, and no address
    means no email."""
    domain = target.get("domain")
    if not domain or not jm.has_mx(domain):
        return None, None
    address, name, tier = jm.best_email(jm.scrape_site(domain)[0])
    if not address or tier < 1:
        return None, None
    if not jm.has_mx(address.split("@")[1]):
        return None, None
    return address, name


def run(state, send=False, limit=None):
    targets = [t for t in load_targets() if not already_asked(state, t)]
    if not targets:
        print("[network] every body on the list has been asked")
        return 0
    budget = limit or NETWORK_PER_RUN
    print(f"[network] {len(targets)} body/bodies not yet asked, "
          f"writing to at most {budget}")

    blocklist = jm.load_do_not_contact()
    asked = 0
    for target in targets:
        if asked >= budget:
            break
        # Checked before discovery is spent, mirroring speculative()'s own
        # order. send_email() enforces this again at the point of sending -
        # this is the cheap early exit, not the guarantee.
        stop = jm.do_not_contact(company=target["name"], entries=blocklist)
        if stop:
            print(f"[network] skipping {target['name']} - asked not to be "
                  f"contacted again")
            continue
        address, contact = find_address(target)
        if not address:
            print(f"[network] {target['name']}: no real address found, skipping")
            continue
        subject, body = compose(target)
        if not send:
            print(f"\n--- would write to {target['name']} <{address}>")
            print(f"    subject: {subject}")
            print("    " + body.replace("\n", "\n    ")[:600])
            asked += 1
            continue
        try:
            # No CV: see the module docstring. This is a conversation
            # request, and a CV would make it a job application.
            jm.send_email(address, subject, body, attach_cv=False)
            record(state, target, address)
            asked += 1
            print(f"[network] asked {target['name']} <{address}>")
            jm.save(state)
            time.sleep(NETWORK_INTERVAL_SECONDS)
        except Exception as e:
            print(f"[network] failed {target['name']}: {e}")
    print(f"[network] {asked} body/bodies written to")
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
