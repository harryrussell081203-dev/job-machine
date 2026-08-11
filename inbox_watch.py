"""
Watching the whole inbox, not just the replies.

WHAT WAS ALREADY THERE, AND WHAT WAS MISSING
--------------------------------------------
Two things read Harry's mail before this file existed:

  job_machine --replies   every two hours, and it is good - it classifies,
                          auto-answers an interview invitation, harvests the
                          direct line out of a signature and texts him. But it
                          only ever looks at addresses THE MACHINE WROTE TO.
                          It walks state["jobs"] and searches the inbox FROM
                          each known contact. Anyone else is invisible to it.
  morning.py              once a day at 07:45, reads the last three days of
                          real mail and puts at most two lines in a text. It
                          records nothing, so the same message is re-judged
                          every morning and never marked as dealt with.

Between them, a consultant who found Harry on a job board and wrote to him
cold - which is most of the good mail a live job hunt produces - was seen once,
briefly, the following morning, and only if it beat two other items to the
text.

He asked for the whole thing: "you should constantly monitor my emails and
tell me what needs done", and earlier, "text me every evening about the emails
I've received that day and what actions need taken FOR ALL OF MY EMAILS".

So this reads everything, keeps a register of it, and is called from all three
clocks: the two-hourly reply run, the morning brief, and the evening digest.

    python inbox_watch.py             # what is in there and what it says
    python inbox_watch.py --send      # text him anything urgent
    python inbox_watch.py --list      # everything still outstanding
    python inbox_watch.py --done KEY  # he has dealt with that one

WHY A REGISTER AND NOT JUST A READ
----------------------------------
Because "what needs done" is a question about a WEEK, not about right now. A
recruiter who asked for a Word copy of the CV on Monday is still waiting on
Thursday, and a machine that re-reads the inbox from scratch every morning
either says it again as though it were new or, worse, loses it off the bottom
of the list. Recording each message once - with what it wants and whether it
has been dealt with - is what turns a mail reader into a to-do list.

It also stops the same message being classified four times a day. Gemini's
free tier is a real constraint in this project; every entry costs one call,
once, ever.

WHAT IT WILL NOT DO
-------------------
IT NEVER REPLIES TO ANYONE. Not once, not to anybody. The reply watcher sends
an availability line to a confirmed interview invitation on a job the machine
itself applied for, and that is a narrow, chosen exception. Mail from a
stranger is Harry's to answer, and a machine answering it on his behalf could
say something he would never have said to a person he has not met.

IT ONLY EVER TEXTS HARRY'S OWN HANDSET. sms.alert_harry takes no recipient -
it sends to SMS_FROM, which is his phone - and a test holds that shut. Nothing
in this file can put a word of his private mail in front of anybody else.

IT DOES NOT INVENT THE ASK. The model is given numbered messages and must
answer with the number; an action attributed to a message that does not exist,
or to a different sender than the one that wrote it, is dropped rather than
texted. It is told to return nothing rather than guess, and a message nobody
is waiting on gets no line at all.
"""
import argparse
import email as email_mod
import hashlib
import imaplib
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

import job_machine as jm
import sms

REGISTER = "inbox"

# Two days by default. The watcher runs every two hours, so anything newer has
# already been seen; the overlap is only there so a run that failed does not
# leave a hole.
LOOKBACK_DAYS = jm.env_int("INBOX_LOOKBACK_DAYS", 2)
MAX_MESSAGES = jm.env_int("INBOX_MAX_MESSAGES", 60)
# One Gemini call covers a batch, but a huge batch gets a worse answer than a
# small one and this runs twelve times a day.
MAX_TRIAGE = jm.env_int("INBOX_MAX_TRIAGE", 10)
# How long something stays on the outstanding list. Nothing here can tell
# whether Harry answered an email - he answers from his own inbox and the
# machine never sees it - so after a fortnight it either was handled or it is
# not going to be, and a list that nags forever is a list he stops opening.
KEEP_DAYS = jm.env_int("INBOX_KEEP_DAYS", 14)
# A cap on how loud one run may be. Two texts is an alert; six is an alarm
# clock he turns off.
MAX_TEXTS = jm.env_int("INBOX_MAX_TEXTS", 2)

# Mail nobody is waiting on an answer to. Job boards, newsletters, receipts,
# and anything that announces itself as unattended. Dropped before a word of
# it is read, which is most of the volume in a job hunt.
NOT_A_PERSON = re.compile(
    r"no-?reply|do-?not-?reply|notification|newsletter|mailer|automated|"
    r"donotreply|bounce|postmaster|mailer-daemon|updates?@|info@jobs|"
    r"cv-?library|totaljobs|reed\.co\.uk|indeed|s1jobs|jobsite|linkedin|"
    r"glassdoor|adzuna|monster|jobserve|wowcher|beehiiv|alibabacloud|"
    r"villagegym|creation\.co\.uk|usebouncer|secure\.|specialoffers|"
    # Bulk-mail subdomains. Marketing goes out from email.currys.co.uk and
    # news.something.com; a person writes from the bare domain.
    r"@(email|news|mail|marketing|campaigns?|info)\.", re.I)

# And mail that is a person's address but not a person. An out-of-office is the
# machine's own letter coming back at it, and a to-do list carrying three of
# them is a to-do list he scrolls past.
NOT_WORTH_READING = re.compile(
    r"^\s*(automatic reply|auto(matic)?[- ]?response|autoreply|out of (the )?"
    r"office|undeliverable|delivery status notification)", re.I)

# What somebody wanting something from you writes. Used as the filter on what
# is worth spending a model call on, and as the whole answer when there is no
# Gemini key.
AN_ASK = re.compile(
    r"\?|can you|could you|please (send|complete|fill|confirm|let|call|reply)|"
    r"let me know|get back to me|we need|you need to|by (friday|monday|tuesday|"
    r"wednesday|thursday|the \d)|deadline|as soon as|waiting (on|for) you|"
    r"give me a (call|ring)|complete the (form|below)|send (me|us|a|your)|"
    r"when are you|what time|confirm", re.I)

# The categories, and how loudly each one is treated. An interview invitation
# from a stranger is the most valuable message this system will ever see and
# the easiest to lose, because it arrives looking like every other email.
CATEGORIES = ("interview", "call", "question", "document", "information")
WEIGHT = {"interview": 0, "call": 0, "question": 1, "document": 1,
          "information": 3}
# What goes on the phone the moment it lands rather than waiting for six
# o'clock. Deliberately short: everything else can wait for the digest.
LOUD = ("interview", "call")


def header_text(raw):
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or ""


def address_of(sender):
    return (re.findall(r"[\w.+-]+@[\w.-]+", sender or "") or [""])[0].lower()


def key_for(msg, sender, subject):
    """A stable id for one message.

    Message-ID when there is one, which is nearly always. The hash fallback is
    for the handful of senders whose mailer omits it - without it those would
    be re-classified, re-texted and re-listed on every single run."""
    mid = (msg.get("Message-ID") or "").strip()
    if mid:
        return mid[:120]
    blob = f"{sender}|{subject}|{msg.get('Date', '')}"
    return "h-" + hashlib.sha1(blob.encode("utf-8", "ignore")).hexdigest()[:16]


def fetch_recent(days=None, limit=None):
    """[{key, who, address, subject, body, at}] - real mail from real people."""
    if not (jm.GMAIL_ADDRESS and jm.GMAIL_APP_PASSWORD):
        print("[inbox] no Gmail credentials, nothing to read")
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days or LOOKBACK_DAYS)
    out = []
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", timeout=jm.IMAP_TIMEOUT)
        conn.login(jm.GMAIL_ADDRESS, jm.GMAIL_APP_PASSWORD)
        conn.select("INBOX")
    except Exception as e:
        print(f"[inbox] could not open the inbox: {e}")
        return []
    try:
        status, data = conn.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        if status != "OK" or not data or not data[0].split():
            return []
        for num in reversed(data[0].split()[-300:]):
            if len(out) >= (limit or MAX_MESSAGES):
                break
            try:
                status, msgdata = conn.fetch(num, "(RFC822)")
                if status != "OK":
                    continue
                msg = email_mod.message_from_bytes(msgdata[0][1])
            except Exception:
                continue
            sender = header_text(msg.get("From", ""))
            if NOT_A_PERSON.search(sender):
                continue
            address = address_of(sender)
            if not address or address == (jm.GMAIL_ADDRESS or "").lower():
                continue
            subject = header_text(msg.get("Subject", ""))
            if NOT_WORTH_READING.search(subject):
                continue
            try:
                when = parsedate_to_datetime(msg.get("Date", ""))
            except Exception:
                when = None
            out.append({
                "key": key_for(msg, sender, subject),
                "who": re.sub(r"<.*", "", sender).strip().strip('"') or address,
                "address": address,
                "subject": subject,
                "body": jm._message_text(msg)[:1500],
                "at": (when or datetime.now(timezone.utc)).astimezone(
                    timezone.utc).isoformat(timespec="seconds"),
            })
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out


def asks_something(subject, body):
    """Does this read like somebody waiting on Harry?"""
    return bool(AN_ASK.search(f"{subject}\n{sms.own_words(body)}"))


def register(state):
    return state.setdefault(REGISTER, {})


def tracked_addresses(state):
    """Every address the machine has already written to.

    Not a reason to ignore the message - it is a reason not to text about it
    twice, and not to list it in the evening digest under a bare name when the
    reply line above already gives the company and the role.

    THE JOB RECORDS ARE NOT THE WHOLE STORY, and the first version of this
    assumed they were. It walked state["jobs"] only, and on the first live run
    it reported nought out of thirty-two messages as tracked - in an inbox that
    included Cammach answering the offshore-tickets letter and TMM confirming a
    meeting, both of which the machine itself had started. The agency letters,
    the speculative letters, the charities and the asking-around letters are
    each recorded in a register of their own, and none of them is a job."""
    out = set()
    for job in state.get("jobs", {}).values():
        for field in ("contact_email", "stakeholder_email"):
            if job.get(field):
                out.add(job[field].lower())
        for contact in job.get("other_contacts") or []:
            if contact.get("email"):
                out.add(contact["email"].lower())
    for name in ("companies_contacted", "agency_registered", "support_asked",
                 "spec_done", "asked_around"):
        for entry in (state.get(name) or {}).values():
            if not isinstance(entry, dict):
                continue          # spec_done stores a reason string, not a record
            if entry.get("email"):
                out.add(str(entry["email"]).lower())
            for address in entry.get("emails") or []:
                out.add(str(address).lower())
    return out


# Mailbox providers, where two addresses sharing a domain means nothing at all.
SHARED_DOMAINS = ("gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
                  "yahoo.com", "yahoo.co.uk", "icloud.com", "live.com",
                  "aol.com", "btinternet.com", "sky.com", "me.com")


def tracked_domains(state):
    """The firms already in a conversation with him, by domain.

    A consultant almost never replies from the address on the 'contact us'
    page. The machine wrote to recruitment@wearecammach.com and Louise Young
    answered from l.young@wearecammach.com - the same conversation, a different
    mailbox, and matching on the address alone misses every one of them.

    Not applied to the free mail providers, where a shared domain is a
    coincidence rather than a company."""
    out = set()
    for address in tracked_addresses(state):
        domain = address.partition("@")[2]
        if domain and domain not in SHARED_DOMAINS:
            out.add(domain)
    return out


def is_tracked(address, addresses, domains):
    address = (address or "").lower()
    return address in addresses or address.partition("@")[2] in domains


# ======================================================================
# READING IT
# ======================================================================
def triage(messages):
    """{key: {category, do}} - what each message wants, if anything.

    The messages are NUMBERED and the model must answer with the number. That
    is the whole grounding trick: a name can be half-matched and argued about,
    an index either points at a message that exists or it does not. An answer
    about message 7 of 4, or one that names a different sender than the one who
    wrote number 3, is dropped without ever reaching a text."""
    if not messages:
        return {}
    if not jm.GEMINI_API_KEY or jm.gemini_exhausted():
        # No key, or the budget is spent. Fall back to the regex: it cannot say
        # what somebody wants, but it can say that somebody wants something,
        # which beats silently dropping real mail. Only the messages that read
        # as asks - the batch also carries mail nobody is waiting on, and
        # labelling that a question would fill the list with nothing.
        return {m["key"]: {"category": "question",
                           "do": f"read {m['subject'][:50]}".strip()}
                for m in messages
                if asks_something(m["subject"], m["body"])}
    blob = "\n\n".join(
        f"MESSAGE {i}\nFROM: {m['who']} <{m['address']}>\n"
        f"SUBJECT: {m['subject']}\n{sms.own_words(m['body'])[:600]}"
        for i, m in enumerate(messages, 1))
    result = jm.gemini_json(
        "You are triaging the inbox of Harry Russell, an electrical and "
        "instrumentation technician in Aberdeen who is job hunting - he wants "
        "offshore rotational work - and also runs a small business.\n\n"
        "For each numbered message, decide what HE has to do about it.\n\n"
        'Return ONLY JSON: {"items": [{"n": <message number>, '
        '"who": "<the sender\'s name, copied exactly>", '
        '"category": "<interview|call|question|document|information>", '
        '"do": "<the one concrete thing he must do, max 12 words, '
        'imperative>"}]}\n\n'
        "CATEGORIES:\n"
        "interview = they want to meet him, interview him, or arrange a time.\n"
        "call = they have asked him to ring them, or to be rung.\n"
        "question = they asked him something that needs a real answer.\n"
        "document = they want a form, a CV, a certificate or a detail sent.\n"
        "information = worth him knowing, nobody is waiting on him.\n\n"
        "RULES:\n"
        "- Leave a message out entirely if nobody is waiting on him and it is "
        "not worth knowing. An empty list is a correct answer.\n"
        "- Quote what they actually asked for. Never invent a deadline, a "
        "time, a role or a detail that is not in the message.\n"
        "- 'do' is something HE does, not something he waits for.\n"
        "- Copy the sender's name exactly as given.\n\n"
        f"{blob}", max_tokens=700, temperature=0.1)
    items = (result or {}).get("items") or []
    out = {}
    for item in items:
        try:
            n = int(item.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(messages):
            continue
        message = messages[n - 1]
        who = str(item.get("who", "")).strip().lower()
        real = message["who"].lower()
        # It may only speak about the person who really wrote that message.
        if who and not (who in real or real in who
                        or who in message["address"]):
            print(f"[inbox] dropped an action attributed to '{item.get('who')}' "
                  f"- message {n} is from {message['who']}")
            continue
        category = str(item.get("category", "")).strip().lower()
        do = str(item.get("do", "")).strip().rstrip(".")
        if category not in CATEGORIES or not do:
            continue
        out[message["key"]] = {"category": category, "do": do[:90]}
    return out


def scan(state, days=None, messages=None, send=False):
    """Read the inbox, record what is new, text anything urgent.

    Returns the entries added this run."""
    messages = fetch_recent(days) if messages is None else messages
    known = register(state)
    fresh = [m for m in messages if m["key"] not in known]
    if not fresh:
        print(f"[inbox] {len(messages)} read, nothing new")
        prune(state)
        return []
    # What somebody is plausibly waiting on goes to the front of the queue, and
    # the rest of the budget is spent on the remainder - a recruiter's "we have
    # submitted your CV" carries no question mark and is still worth a line.
    #
    # THE CAP PACES THE WORK, IT DOES NOT DISCARD IT. The first version recorded
    # every fresh message and only triaged the first ten, so on a busy morning
    # the eleventh was filed as seen, never read, and never looked at again.
    # Anything past the cap is simply left for the next run, which is two hours
    # away.
    asking = [m for m in fresh if asks_something(m["subject"], m["body"])]
    rest = [m for m in fresh if m not in asking]
    batch = (asking + rest)[:MAX_TRIAGE]
    read = triage(batch)
    addresses, domains = tracked_addresses(state), tracked_domains(state)
    added = []
    for message in batch:
        verdict = read.get(message["key"])
        entry = {"at": message["at"], "seen_at": jm.now(),
                 "who": message["who"], "address": message["address"],
                 "subject": message["subject"][:140],
                 "tracked": is_tracked(message["address"], addresses, domains)}
        if verdict:
            entry.update(verdict)
        known[message["key"]] = entry
        if verdict:
            added.append((message["key"], entry))
    added.sort(key=lambda kv: WEIGHT.get(kv[1].get("category"), 9))
    for key, entry in added:
        print(f"[inbox] {entry.get('category', 'seen'):12} {entry['who']}: "
              f"{entry.get('do', entry['subject'])}")
    shout(state, added, send=send)
    prune(state)
    waiting = len(fresh) - len(batch)
    print(f"[inbox] {len(messages)} read, {len(fresh)} new, {len(batch)} "
          f"triaged, {len(added)} needing something"
          + (f", {waiting} left for the next run" if waiting else ""))
    return added


def shout(state, added, send=False):
    """Put the urgent ones on his phone now, not at six o'clock.

    Only ever texts about somebody the reply watcher is NOT already covering -
    a tracked contact's interview invitation is texted by check_replies within
    minutes and a second text about the same email is noise."""
    loud = [(k, e) for k, e in added
            if e.get("category") in LOUD and not e.get("tracked")
            and not e.get("texted_at")]
    if not loud:
        return 0
    sent = 0
    for key, entry in loud[:MAX_TEXTS]:
        body = (f"{entry['who']} "
                f"{'wants to interview you' if entry['category'] == 'interview' else 'asked you to call'}"
                f" - {entry['do']}. In your inbox now.")
        if not send:
            print(f"[inbox] would text: {body}")
            continue
        if not sms.alert_harry(body, urgent=True):
            continue
        register(state)[key]["texted_at"] = jm.now()
        sent += 1
    return sent


# ======================================================================
# WHAT IS STILL OWED
# ======================================================================
def outstanding(state, days=None, include_information=False):
    """[(key, entry)] - everything somebody is still waiting on, worst first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days or KEEP_DAYS)
    out = []
    for key, entry in register(state).items():
        if entry.get("done_at") or not entry.get("category"):
            continue
        if entry["category"] == "information" and not include_information:
            continue
        when = jm.parse_ts(entry.get("at")) or jm.parse_ts(entry.get("seen_at"))
        if when and when < cutoff:
            continue
        out.append((key, entry))
    out.sort(key=lambda kv: (WEIGHT.get(kv[1]["category"], 9),
                             kv[1].get("at") or ""))
    return out


def arrived_today(state):
    """[(key, entry)] - what came in today, for the evening digest."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    out = []
    for key, entry in register(state).items():
        when = jm.parse_ts(entry.get("at")) or jm.parse_ts(entry.get("seen_at"))
        if not when or when < since or not entry.get("category"):
            continue
        out.append((key, entry))
    out.sort(key=lambda kv: WEIGHT.get(kv[1]["category"], 9))
    return out


def line_for(entry):
    """One line for a text: who, then what he has to do."""
    return f"{entry['who']} - {entry.get('do') or entry.get('subject', '')}"


def mark_done(state, key):
    entry = register(state).get(key)
    if not entry:
        return False
    entry["done_at"] = jm.now()
    return True


def prune(state):
    """Forget what is long past. The register is a to-do list, not an archive.

    Twice KEEP_DAYS, not KEEP_DAYS: an entry has to outlive the outstanding
    window by a clear margin, otherwise a message would drop off the list one
    day and be rediscovered as brand new the next time it is still in the
    IMAP lookback."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS * 2)
    known = register(state)
    for key in [k for k, e in known.items()
                if (jm.parse_ts(e.get("seen_at")) or
                    jm.parse_ts(e.get("at")) or
                    datetime.now(timezone.utc)) < cutoff]:
        del known[key]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Watch the whole inbox")
    ap.add_argument("--send", action="store_true",
                    help="really text him about the urgent ones")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--list", action="store_true",
                    help="everything still outstanding, read nothing new")
    ap.add_argument("--done", metavar="KEY", help="he has dealt with that one")
    ap.add_argument("--forget-unread", action="store_true",
                    help="drop entries filed as seen but never actually read")
    args = ap.parse_args(argv)
    state = jm.load()
    if args.forget_unread:
        # A one-off, for the first live run. That version recorded every fresh
        # message and triaged only the first ten, so twenty-one real emails -
        # Cammach answering the offshore-tickets letter among them - were filed
        # as seen, never read, and would never be looked at again. Forgetting
        # them puts them back in front of the fixed watcher.
        gone = [k for k, e in register(state).items() if not e.get("category")]
        for key in gone:
            del register(state)[key]
        jm.save(state)
        print(f"[inbox] forgot {len(gone)} message(s) that were never read")
        return 0
    if args.done:
        if mark_done(state, args.done):
            jm.save(state)
            print(f"[inbox] marked done: {args.done}")
        else:
            print(f"[inbox] no such message: {args.done}")
        return 0
    if not args.list:
        scan(state, days=args.days, send=args.send)
        jm.save(state)
    items = outstanding(state)
    if not items:
        print("[inbox] nobody is waiting on you")
        return 0
    print(f"\n[inbox] {len(items)} outstanding:")
    for key, entry in items:
        print(f"  {entry['category']:12} {line_for(entry)}")
        print(f"  {'':12} {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
