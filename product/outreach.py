"""Tell the organisations that already hold jobseekers that a free tool exists.

WHY THIS IS THE ONLY OUTREACH THIS PRODUCT GETS.

The product needs users. The obvious way to get them is to scrape jobseekers
and mail them, and that is the one thing it must never do - not out of
squeamishness but because the entire claim the site makes is that it never
writes to an address a human did not publish for the purpose. Every answer
page says so. Marketing it by breaking its own rule would not be a compromise,
it would make the claim false, and the claim is the product.

So this writes to gatekeepers instead. A council employability team, a college
careers service, a job club, an ex-forces employment charity: each already has
hundreds of jobseekers on a list and spends its week looking for free things
to hand them. One letter to one of those reaches more people than a thousand
scraped addresses, and it is a letter they are glad to receive.

THE RULES, WHICH ARE THE SAME RULES THE PRODUCT ITSELF OBEYS.

  - NO ADDRESS IS EVER GUESSED. The organisation's own website is read, a
    real published address is taken or nothing is. An organisation with no
    published address gets no letter, and that is a correct outcome.
  - ONE APPROACH PER ORGANISATION, EVER. No follow-up sequence, no chasing.
    A charity that has not replied has answered. This is stricter than the
    job machine's own three-touch rule, deliberately: that is a person
    applying for a job, this is a product asking for attention.
  - IT SENDS NOTHING BY DEFAULT. --send is required, and --send without a
    configured sender refuses rather than falling back to anything.
  - IT OBEYS THE BANNED LIST. Every letter is checked against the same
    phrase list the job machine enforces, because a letter that reads as
    generated is exactly as unwelcome here as it is to an employer.
  - IT ASKS NOTHING OF THEM. No form, no call, no partnership. A link they
    can pass on if it is useful, and an explicit line saying no reply is
    needed.

THE SENDING IDENTITY IS NOT DECIDED HERE, AND THAT IS ON PURPOSE.

Sending this from the mailbox the job hunt runs on would put that mailbox's
sending reputation behind a marketing campaign, and if it tips, what breaks
is job applications landing in employers' spam folders. The two machines are
not supposed to interfere. So the sender is configuration with no default,
and a run with none configured stops instead of guessing.

    python outreach.py --dry-run           # write the letters, send nothing
    python outreach.py --dry-run --limit 3
    python outreach.py --send --limit 20   # needs OUTREACH_FROM set
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from jobseeker.pipeline import compose, contacts, discover  # noqa: E402

ORGS = os.path.join(HERE, "data", "orgs.json")
STATE = os.path.join(HERE, "data", "outreach_state.json")

# No default. See the docstring: picking one for him is picking which mailbox
# carries the risk, and that is his call, not this file's.
FROM_ADDRESS = os.environ.get("OUTREACH_FROM", "")

SITE = os.environ.get("BASE_URL", "https://recruited.org.uk")

# Small. This is cold mail to charities, and volume is not the lever - the
# whole finding behind this product is that who receives it matters more.
DEFAULT_LIMIT = 20
POLITE_DELAY = 30          # seconds between letters


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def key_for(org) -> str:
    """One row per organisation, keyed by site rather than by name.

    Registers carry the same body under several names and several
    registrations. Keying by name would let the same inbox be written to
    twice, which is the failure this file exists to avoid.
    """
    site = (org.get("website") or "").lower()
    host = site.split("//", 1)[-1].split("/", 1)[0]
    return host[4:] if host.startswith("www.") else host


def already_asked(state, org) -> bool:
    return key_for(org) in state


def record(state, org, outcome, address=""):
    state[key_for(org)] = {"name": org.get("name", ""), "at": now(),
                           "outcome": outcome, "address": address}


# The organisation's front door. Their careers@ and hr@ are about hiring
# their own staff, which is the wrong desk for this letter.
FRONT_DOOR = ("info", "enquiries", "enquiry", "hello", "contact", "office",
              "admin", "mail", "reception", "general", "team")
SERVICE_WORDS = ("employ", "skills", "work", "jobclub", "support", "advice")


def shared_inbox(address: str) -> bool:
    """An organisation's front-door inbox (info@, enquiries@, hello@) or a
    service inbox (employability@, skills@), never a named person's and
    never their own hr@ or careers@. Letters to organisations go to the
    organisation: an employee's own address is a person, and this tool does
    not write to people who did not publish an address for the purpose."""
    local = (address or "").split("@")[0].lower()
    if not local or contacts.never_write_to(local):
        return False
    if contacts.is_personal(local):
        return False
    first = re.split(r"[._\-0-9]+", local)[0]
    return first in FRONT_DOOR or any(w in local for w in SERVICE_WORDS)


def find_address(org, *, session=None, scrape_delay=None):
    """A real published shared inbox on their own site, or None.

    Exactly the discovery the product sells, pointed at the organisation
    rather than at an employer, and then held to one extra rule: the
    organisation's shared inbox, never a person's address. The register's
    own published contact address is used if it is one, on their domain.
    None is the common answer and it is fine.

    `scrape_delay` is separate from the delay between letters. That one is
    politeness to the recipient; this one is politeness to a small charity's
    web server, which is about to be asked for eleven pages in a row. Both
    default to the real value and both go to zero in tests, because a suite
    that sleeps is a suite nobody runs.
    """
    host = key_for(org)
    if not host:
        return None
    listed = (org.get("email") or "").strip().lower()
    if listed and shared_inbox(listed) and (
            listed.endswith("@" + host) or listed.endswith("." + host)):
        return {"email": listed, "name": None, "tier": 1,
                "tier_name": "the address on the charity register"}
    if scrape_delay is None:
        scrape_delay = discover.POLITE_DELAY
    found = discover.scrape_site(host, session=session, delay=scrape_delay)
    shared = sorted((e for e in (found or []) if shared_inbox(e)),
                    key=lambda e: (not any(w in e.split("@")[0]
                                           for w in SERVICE_WORDS), e))
    if not shared:
        return None
    return {"email": shared[0], "name": None, "tier": 1,
            "tier_name": "their shared inbox"}


def compose_letter(org, contact) -> tuple[str, str]:
    """Subject and body. Plain, short, and asking for nothing.

    Written to be forwarded rather than answered: the thing they might
    actually do with it is paste the link into their own newsletter, so the
    link and what it does come early and the rest is the evidence that it
    is not junk. Every figure in it is one the site already publishes.
    Nothing about his age or who he works for: public copy never says.
    """
    name = (org.get("name") or "your organisation").strip()

    subject = "A free tool for the jobseekers you work with"
    body = f"""Hello,

I've been job hunting this year, and applications kept disappearing into
portals. So I started emailing a real person at each company instead, and
kept count: 171 applications, 32 replies. Across 86 of those emails, the
thing that moved the number was who received it. A named person replied 13
times out of 34; a generic info@ address 4 times out of 42.

I've turned the address-finding part into a free tool that might help the
people {name} works with. Type a company's name or paste a job advert at
{SITE}/find and it reads the advert and the company's own website, shows the
real address to write to, and can write a short letter to it. No account,
nothing to install, and it says plainly when there is nothing to find
rather than guessing an address.

The method is written up at {SITE}/playbook, and every figure, including
the bad months, is at {SITE}/numbers.

Pass it on if it's any use. If not, no reply is needed and I won't write
again.

Harry Russell
Recruited, {SITE}
"""
    return subject, body.strip()


def check(subject, body) -> list[str]:
    """The same checks a job application gets. A letter that reads as
    generated is as unwelcome to a careers adviser as to an employer."""
    problems = []
    banned = compose.banned_used(subject + " " + body)
    if banned:
        problems.append(f"banned phrases: {banned}")
    if "!" in body:
        problems.append("exclamation mark")
    if "—" in body:
        problems.append("em dash")
    for marker in ("**", "##", "- ["):
        if marker in body:
            problems.append(f"markdown: {marker!r}")
    return problems


def run(orgs, state, *, send=False, limit=None, session=None, sender=None,
        delay=POLITE_DELAY, scrape_delay=None, out=print):
    """Walk the list. Returns what happened, for the caller to print or test.

    `sender` is injected so nothing about this is exercised by pretending to
    send. A run with send=True and no sender is a programming error, caught
    before the loop rather than per letter.
    """
    if send and sender is None:
        raise RuntimeError(
            "send=True with no sender. Set OUTREACH_FROM and pass a real "
            "one; there is deliberately no default, because the default "
            "would be the mailbox the job hunt runs on.")

    done = {"sent": 0, "would_send": 0, "no_address": 0, "skipped": 0,
            "refused": 0, "letters": []}
    for org in orgs:
        if limit and (done["sent"] + done["would_send"]) >= limit:
            break
        if already_asked(state, org):
            done["skipped"] += 1
            continue

        contact = find_address(org, session=session,
                               scrape_delay=scrape_delay)
        if not contact:
            record(state, org, "no_address")
            done["no_address"] += 1
            out(f"  no address: {org.get('name')}")
            continue

        subject, body = compose_letter(org, contact)
        problems = check(subject, body)
        if problems:
            # Not recorded as asked. Nothing went out, so the organisation is
            # still a target once the letter is fixed.
            done["refused"] += 1
            out(f"  REFUSED {org.get('name')}: {problems}")
            continue

        done["letters"].append({"to": contact["email"], "tier": contact["tier"],
                                "org": org.get("name", ""), "subject": subject,
                                "body": body})
        if not send:
            done["would_send"] += 1
            out(f"  would write to {contact['email']} "
                f"({contact['tier_name']}) at {org.get('name')}")
            continue

        sender(contact["email"], subject, body)
        record(state, org, "sent", contact["email"])
        done["sent"] += 1
        out(f"  sent to {contact['email']} at {org.get('name')}")
        if delay:
            time.sleep(delay)
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--send", action="store_true",
                    help="actually send. Without it, nothing leaves.")
    ap.add_argument("--dry-run", action="store_true",
                    help="the default, stated explicitly")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--orgs", default=ORGS)
    ap.add_argument("--state", default=STATE)
    args = ap.parse_args(argv)

    orgs = load(args.orgs, [])
    if not orgs:
        print(f"no organisations in {args.orgs}. Run tools/find_orgs.py first.")
        return 1

    if args.send and not FROM_ADDRESS:
        print("OUTREACH_FROM is not set, so there is no sending identity.\n"
              "This is not a missing default - it is the decision about which\n"
              "mailbox's reputation carries this campaign, and sending it\n"
              "from the one the job hunt uses can put job applications in\n"
              "employers' spam folders. Set it deliberately.")
        return 2

    state = load(args.state, {})
    print(f"{len(orgs)} organisations, {len(state)} already approached")
    done = run(orgs, state, send=args.send, limit=args.limit,
               sender=_smtp_sender() if args.send else None)
    save(args.state, state)

    print(f"\nsent {done['sent']}, would send {done['would_send']}, "
          f"no address {done['no_address']}, already done {done['skipped']}, "
          f"refused {done['refused']}")
    if not args.send:
        print("nothing was sent. Add --send when the letters read right.")
    return 0


def _smtp_sender():
    """Built only when --send is given, so importing this file cannot send.

    Goes out over the managed mail path, which already exists and is already
    a subdomain for exactly this reason: sending reputation is per-domain, so
    mail.recruited.org.uk can be burned by a bad run and replaced, while the
    apex - the marketing site, the sign-in links that let anybody in at all -
    cannot. A marketing campaign is precisely the kind of traffic that should
    be behind that firebreak rather than in front of it.

    Two things to keep in mind and neither is solved here. The free tier
    allows 100 a day ACROSS THE ACCOUNT, shared with every letter the
    product's own users send from a Recruited address, so outreach eats a
    pool the users need. And the daily cap the app enforces for users does
    not know about this script. Keep --limit well under the ceiling.
    """
    from app import config, delivery

    if not config.managed_mail_available():
        raise RuntimeError(
            "managed mail is not configured: MANAGED_MAIL_DOMAIN and "
            "MANAGED_MAIL_KEY are both required. Refusing to fall back to "
            "any other mailbox, because the only other one here is the one "
            "the job hunt runs on.")

    def send(to, subject, body):
        delivery.send_via_smtp(
            host=config.MANAGED_MAIL_HOST, port=config.MANAGED_MAIL_PORT,
            username=config.MANAGED_MAIL_USERNAME,
            password=config.MANAGED_MAIL_KEY,
            to_email=to, subject=subject, body=body,
            from_address=FROM_ADDRESS, display_name="Harry Russell")
    return send


if __name__ == "__main__":
    sys.exit(main())
