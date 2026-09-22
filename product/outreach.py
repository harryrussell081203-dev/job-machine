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
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from jobseeker.pipeline import compose, contacts, discover  # noqa: E402
from app import study  # noqa: E402  - the figures, from their one home

ORGS = os.path.join(HERE, "data", "orgs.json")
STATE = os.path.join(HERE, "data", "outreach_state.json")

# The personal job machine's own registers. Organisations in these have
# already been written to by Harry's job hunt - about HIS job hunt - and a
# second letter from the product would break "one approach each, ever" and
# make two machines that must not interfere write to the same inbox. See
# excluded_hosts().
PERSONAL_REGISTERS = tuple(
    os.path.join(os.path.dirname(HERE), "data", f"{name}.json")
    for name in ("support_orgs", "networking_targets", "agencies"))

# No default. See the docstring: picking one for him is picking which mailbox
# carries the risk, and that is his call, not this file's.
FROM_ADDRESS = os.environ.get("OUTREACH_FROM", "")

# Every link in the letter is tagged, so a signup that came from one of these
# letters is counted as one. Without it, the only channel that can actually be
# run from here would be invisible in the one report meant to judge channels.
UTM = "utm_source=orgs&utm_campaign=letter1"

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


def _host(value: str) -> str:
    host = (value or "").lower().strip().split("//", 1)[-1].split("/", 1)[0]
    return host[4:] if host.startswith("www.") else host


def excluded_hosts(paths=PERSONAL_REGISTERS) -> set[str]:
    """Every site the personal job machine has its own business with.

    Read from the machine's registers rather than copied into a list here,
    because a copy is a second list to forget to update - and the failure it
    guards against is a charity getting a letter about Harry's job hunt one
    week and a sales pitch for his product the next.

    A register that cannot be read EXCLUDES NOTHING and says so loudly
    rather than failing open silently: the caller treats an empty set from a
    missing file as a reason to stop. See run().
    """
    hosts = set()
    for path in paths:
        data = load(path, None)
        if not isinstance(data, dict):
            raise RuntimeError(
                f"cannot read {path} - refusing to write to anybody while "
                "the list of organisations the job hunt already knows is "
                "unreadable")
        for key, rows in data.items():
            if key.startswith("_") or not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for field in ("domain", "site", "website"):
                    host = _host(row.get(field, ""))
                    if host:
                        hosts.add(host)
    return hosts


def is_excluded(org, hosts) -> bool:
    """Same site, or a subdomain of one, counts as the same organisation."""
    host = key_for(org)
    return any(host == h or host.endswith("." + h) or h.endswith("." + host)
               for h in hosts)


def record(state, org, outcome, address=""):
    state[key_for(org)] = {"name": org.get("name", ""), "at": now(),
                           "outcome": outcome, "address": address}


def find_address(org, *, session=None, scrape_delay=None):
    """A real published address on their own site, or None.

    Exactly the discovery the product sells, pointed at the organisation
    rather than at an employer. None is the common answer and it is fine.

    `scrape_delay` is separate from the delay between letters. That one is
    politeness to the recipient; this one is politeness to a small charity's
    web server, which is about to be asked for eleven pages in a row. Both
    default to the real value and both go to zero in tests, because a suite
    that sleeps is a suite nobody runs.
    """
    host = key_for(org)
    if not host:
        return None
    if scrape_delay is None:
        scrape_delay = discover.POLITE_DELAY
    # Council services rarely keep their address at /contact. The target list
    # can name the page it is really on, which is still reading their own
    # site - it only tells the scraper where to look, never what to find.
    paths = tuple(discover.SCRAPE_PATHS) + tuple(org.get("pages") or ())
    found = discover.scrape_site(host, session=session, paths=paths,
                                 delay=scrape_delay)
    return contacts.best(found)


def _rate_line() -> tuple[tuple[int, int], tuple[int, int]]:
    """(sent, came back) for a named person and for a generic inbox.

    From study.py, never typed here. This letter used to carry its own copy
    of the figures, and this project has already been caught quoting three
    different numbers for the same thing. A stranger checking the letter
    against /numbers must find the same counts.
    """
    named = next(r for r in study.BY_RECIPIENT if r[0].startswith("A named"))
    generic = next(r for r in study.BY_RECIPIENT
                   if r[0].startswith("A generic"))
    return (named[1], named[2]), (generic[1], generic[2])


def compose_letter(org, contact) -> tuple[str, str]:
    """Subject and body. Plain, short, and asking for nothing.

    Written to be forwarded rather than answered: the thing they might
    actually do with it is paste the link into their own newsletter, so the
    link and what it does come early and the rest is the evidence that it is
    not junk.

    What it deliberately does NOT say: Harry's age. The whole job machine is
    built never to volunteer it, and an earlier draft of this letter opened
    with it.

    "Came back" rather than "replied", because it is the study's own
    definition: every message that arrived, autoresponders included. The
    live counter on /numbers excludes those, and a reader who checks should
    find the difference stated rather than smoothed over.
    """
    name = (org.get("name") or "your organisation").strip()
    greeting = f"Hello {contact['name']}," if contact.get("name") else "Hello,"
    (n_sent, n_back), (g_sent, g_back) = _rate_line()

    subject = "A free tool for the jobseekers you work with"
    body = f"""{greeting}

I am job hunting in Aberdeen. My applications kept vanishing into portals,
so I started emailing a real person at each company instead and logging what
came back. Over the first {study.SENT} emails, the thing that made the
difference was who received it: a named person came back {n_back} times out
of {n_sent}, a generic info@ inbox {g_back} times out of {g_sent}.

Finding that named person is the slow part, so I turned it into a free tool.
Paste a job advert into {SITE}/find?{UTM} and it reads the advert and the
company's own website, then tells you whether there is a real person worth
writing to. No account and nothing to install, and when there is nobody to
find it says so rather than guessing an address.

I thought it might be useful to the people you support at {name}. The method is
written up free at {SITE}/playbook?{UTM}, and every figure, including the
weeks that went badly, is at {SITE}/numbers?{UTM}.

Pass it on if it helps. If it does not, no reply needed and I will not write
again.

Harry Russell
Recruited
{FROM_ADDRESS}
"""
    # The site is already linked three times above, each one tagged. A bare
    # link in the signature would be the one untagged way in.
    return subject, _reflow(body.strip())


def _reflow(body: str) -> str:
    """One line per paragraph; the signature keeps its line breaks.

    Written wrapped at 78 columns so it reads in source, but sent that way it
    arrives with hard breaks mid-sentence, and the long tagged links make
    every one of them ragged on a phone. Mail clients wrap plain text
    themselves - the only thing a hard break adds is the mess.
    """
    paragraphs = body.split("\n\n")
    flowed = [" ".join(p.split()) for p in paragraphs[:-1]]
    return "\n\n".join(flowed + [paragraphs[-1]])


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
        delay=POLITE_DELAY, scrape_delay=None, out=print, excluded=None):
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
            "refused": 0, "excluded": 0, "letters": []}
    hosts = excluded_hosts() if excluded is None else excluded
    for org in orgs:
        if limit and (done["sent"] + done["would_send"]) >= limit:
            break
        if already_asked(state, org):
            done["skipped"] += 1
            continue
        if is_excluded(org, hosts):
            # Not recorded as asked: the product never wrote to them. The job
            # hunt did, which is precisely why the product must not.
            done["excluded"] += 1
            out(f"  excluded, the job hunt already knows them: {org.get('name')}")
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


def preview(done, path):
    """The letters as markdown, for the Actions run summary.

    This is the human review. Every scheduled run writes it, sending or not,
    so the first live batch is never the first time anybody has read the
    letters - and every one after it can be checked in the same place.
    """
    lines = [f"## Outreach: {done['sent']} sent, {done['would_send']} would "
             f"send, {done['no_address']} with no address, "
             f"{done['excluded']} excluded, {done['skipped']} already done", ""]
    for letter in done["letters"]:
        lines += [f"### {letter['org']}",
                  f"**To:** `{letter['to']}` ({letter['tier']})  ",
                  f"**Subject:** {letter['subject']}", "", "```",
                  letter["body"], "```", ""]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--send", action="store_true",
                    help="actually send. Without it, nothing leaves.")
    ap.add_argument("--dry-run", action="store_true",
                    help="the default, stated explicitly")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--orgs", default=ORGS)
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"),
                    help="append the letters as markdown here")
    args = ap.parse_args(argv)

    orgs = load(args.orgs, [])
    if not orgs:
        print(f"no organisations in {args.orgs}. Run tools/find_orgs.py first.")
        return 1

    sender = None
    if args.send:
        try:
            sender = _smtp_sender()
        except RuntimeError as exc:
            print(f"not sending: {exc}")
            return 2

    state = load(args.state, {})
    print(f"{len(orgs)} organisations, {len(state)} already approached")
    done = run(orgs, state, send=args.send, limit=args.limit, sender=sender)

    # Only a real send writes the state. A preview that saved it would record
    # every "no address" as final on a rehearsal, and the scheduled preview
    # runs every weekday - it would quietly retire the list before a single
    # letter had been approved.
    if args.send:
        save(args.state, state)
    if args.summary:
        preview(done, args.summary)

    print(f"\nsent {done['sent']}, would send {done['would_send']}, "
          f"no address {done['no_address']}, excluded {done['excluded']}, "
          f"already done {done['skipped']}, refused {done['refused']}")
    if not args.send:
        print("nothing was sent. Add --send when the letters read right.")
    return 0


def _smtp_sender():
    """A mailbox of its own, and never the one the job hunt runs on.

    WHY NOT THE MANAGED MAIL PATH, which is what this used to use. That goes
    through Resend, and Resend's rules - like those of every mail provider of
    its kind - forbid unsolicited email. A letter to a council employability
    team is unsolicited however courteous it is. Breaking that would risk the
    account every user of the product sends through, for a marketing
    campaign. It also shared their 100-a-day allowance.

    So this is an ordinary mailbox - a separate free Gmail is the intended
    one - configured entirely by environment, with no defaults:

        OUTREACH_FROM           the address letters come from
        OUTREACH_SMTP_USER      the login, usually the same address
        OUTREACH_SMTP_PASSWORD  an app password for it
        OUTREACH_SMTP_HOST      default smtp.gmail.com
        OUTREACH_SMTP_PORT      default 465

    And it REFUSES the job hunt's mailbox by comparison, not by trust. If the
    login or the From address is GMAIL_ADDRESS - the account the job machine
    applies from - it stops. Two machines that must not interfere cannot be
    allowed to by a copy-and-pasted secret.
    """
    user = os.environ.get("OUTREACH_SMTP_USER", "").strip()
    password = os.environ.get("OUTREACH_SMTP_PASSWORD", "").strip()
    host = os.environ.get("OUTREACH_SMTP_HOST", "").strip() or "smtp.gmail.com"
    port = int(os.environ.get("OUTREACH_SMTP_PORT", "") or 465)
    sender_address = FROM_ADDRESS.strip()

    missing = [name for name, value in (("OUTREACH_FROM", sender_address),
                                        ("OUTREACH_SMTP_USER", user),
                                        ("OUTREACH_SMTP_PASSWORD", password))
               if not value]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} not set, so there is no "
                           "sending identity. There is deliberately no "
                           "default.")

    job_hunt = os.environ.get("GMAIL_ADDRESS", "").strip().lower()
    if job_hunt and job_hunt in (user.lower(), sender_address.lower()):
        raise RuntimeError(
            "that is the mailbox the job hunt applies from. A campaign that "
            "tips its reputation puts job applications in employers' spam "
            "folders. Use a separate address.")

    from app import delivery

    def send(to, subject, body):
        delivery.send_via_smtp(
            host=host, port=port, username=user, password=password,
            to_email=to, subject=subject, body=body,
            from_address=sender_address, display_name="Harry Russell")
    return send


if __name__ == "__main__":
    sys.exit(main())
