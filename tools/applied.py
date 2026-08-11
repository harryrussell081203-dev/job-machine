"""
The applications Harry finished himself.

A CAPTCHA is the one step the machine will not do, so every filled application
ends up on his list with the answers banked and a link. When he does one, that
has to get back into the record - otherwise the list nags him about it
tomorrow, and the machine's own count of what has been applied for is wrong in
the direction that matters.

TWO WAYS IN, AND THE SECOND IS THE POINT

    python tools/applied.py --scan          read the inbox and work it out
    python tools/applied.py --mark DOF      he said so
    python tools/applied.py --list          what is still waiting

--scan is the one that should do the work. An employer that takes an
application sends a confirmation within minutes - 'thank you for applying',
'we have received your application' - and that email is proof of exactly the
thing the machine cannot otherwise know. It is already reading the inbox for
replies and verification links, so this costs nothing new.

WHY IT IS RECORDED AS SUBMITTED BUT NOT AS THE AGENT'S WORK

The status becomes portal_submitted, because that is the truth about the JOB:
the application is in, and every count of 'how many have we applied for' should
say so. But finished_by_hand_at is set alongside it, and learn.py does not
count those towards a platform's finish rate - the agent did not finish it,
Harry did, and a platform that always needs a human should not be ranked as
though the machine sails through it.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_machine as jm  # noqa: E402
from tools import handoff  # noqa: E402

DONE = "captcha_done_at"
BY_HAND = "finished_by_hand_at"

# What an employer's own confirmation says. Deliberately narrow: an agency
# newsletter saying 'thanks for your interest in our jobs' is not a receipt for
# an application, and marking a job done off one would take it off the list
# without it ever having been applied for - the one failure here that costs a
# job rather than a minute.
A_RECEIPT = re.compile(
    r"thank you for (your )?(applying|application|your interest in the role)|"
    r"we have received your application|your application (has been )?"
    r"(been )?(received|submitted|sent)|application received|"
    r"received your application|thanks for applying", re.I)

# HARRY SAYING HE HAS DONE THEM.
#
# The whole hand-back has to be one action on a desktop, because that is where
# a CAPTCHA gets solved and that is where he already is when he finishes. He
# does not have a terminal open and should not need one.
#
# So: he replies to the morning email. 'done' clears the list. 'done DOF,
# T-Tech' clears those two. Nothing else in the inbox can trigger it - the
# message has to come FROM his own address, which is the same address the list
# was sent to, so nobody else can mark his applications as sent.
HE_SAYS_DONE = re.compile(
    r"^\s*(all\s+)?(done|did|finished|completed|complete|sent|applied)\b", re.I)
# The email that started the thread. Kept narrow so a reply to some other
# job-machine mail cannot clear the CAPTCHA list.
#
# The second half is the one-click hand-back. Every application on the page has
# a "Mark this one done" link and the page has one for the lot; they are
# mailto: links with the whole message already written, so finishing is a click
# and Send rather than a sentence to compose at the end of a job that is
# otherwise over. Those arrive as a FRESH message, not a reply, so matching on
# the original subject would never have seen them - hence the marker.
HANDOFF_SUBJECT = re.compile(
    r"needs? only the captcha|waiting on a captcha|\[job-machine done\]", re.I)


def waiting(state):
    return handoff.pending(state)


def mark(job, how):
    job["status"] = "portal_submitted"
    job[DONE] = jm.now()
    job[BY_HAND] = jm.now()
    job.setdefault("portal_submitted_at", jm.now())
    job["portal_reason"] = f"finished by hand ({how})"
    return job


def by_name(state, needle):
    """Everything on the list whose company or title he would call that."""
    needle = (needle or "").strip().lower()
    if not needle:
        return []
    return [j for j in waiting(state)
            if needle in (j.get("company") or "").lower()
            or needle in (j.get("title") or "").lower()]


def _confirmations(since_days=14):
    """(from, subject, body) for recent mail that reads like a receipt."""
    import email
    import imaplib
    from email.header import decode_header, make_header
    from datetime import datetime, timedelta, timezone

    if not (jm.GMAIL_ADDRESS and jm.GMAIL_APP_PASSWORD):
        print("[applied] no mailbox configured, cannot scan")
        return []
    out = []
    since = (datetime.now(timezone.utc)
             - timedelta(days=since_days)).strftime("%d-%b-%Y")
    try:
        box = imaplib.IMAP4_SSL("imap.gmail.com")
        box.login(jm.GMAIL_ADDRESS, jm.GMAIL_APP_PASSWORD)
        box.select("INBOX")
        _, data = box.search(None, f'(SINCE "{since}")')
        for num in (data[0].split() if data and data[0] else [])[-400:]:
            _, raw = box.fetch(num, "(RFC822)")
            if not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            subject = str(make_header(decode_header(msg.get("Subject") or "")))
            sender = str(msg.get("From") or "")
            try:
                body = jm._message_text(msg)
            except Exception:
                body = ""
            out.append((sender, subject, body or ""))
        box.logout()
    except Exception as e:
        print(f"[applied] could not read the inbox: {type(e).__name__}: {e}")
    return out


def named_in(text, jobs):
    """The jobs he named in his reply, or all of them if he named none.

    'done' means the list. 'done DOF, T-Tech' means those two. Naming one is
    the exception, not the rule - the point of this is that finishing the lot
    costs one word."""
    body = str(text or "")
    # Only the first line matters. Everything after it is the quoted email he
    # replied to, which contains every company name on the list.
    first = body.strip().splitlines()[0] if body.strip() else ""
    named = [j for j in jobs
             if (j.get("company") or "").strip().lower() in first.lower()
             and len((j.get("company") or "").strip()) >= 3]
    return named or list(jobs)


def read_his_reply(state, mail):
    """Mark what he says he has done. Returns how many."""
    jobs = waiting(state)
    if not jobs:
        return 0
    me = (jm.GMAIL_ADDRESS or "").lower()
    done = 0
    for sender, subject, body in mail:
        if me not in (sender or "").lower():
            continue
        if not HANDOFF_SUBJECT.search(subject or ""):
            continue
        if not HE_SAYS_DONE.search((body or "").strip()):
            continue
        for job in named_in(body, jobs):
            if job.get(DONE):
                continue
            mark(job, "he replied to say he had done it")
            print(f"[applied] {job.get('company')} - {job.get('title')} "
                  f"marked done from his reply")
            done += 1
    return done


def scan(state, since_days=14):
    """Mark anything the employer has confirmed receiving."""
    jobs = waiting(state)
    if not jobs:
        print("[applied] nothing waiting on a CAPTCHA")
        return 0
    mail = _confirmations(since_days)
    if not mail:
        return 0
    # What he has told us himself comes first, and is the whole point of the
    # loop: fill, hand over, he replies 'done', the machine believes him.
    done = read_his_reply(state, mail)
    jobs = waiting(state)
    for job in jobs:
        company = (job.get("company") or "").strip().lower()
        if len(company) < 3:
            continue
        for sender, subject, body in mail:
            haystack = f"{sender}\n{subject}\n{body[:3000]}"
            if company not in haystack.lower():
                continue
            if not A_RECEIPT.search(f"{subject}\n{body[:3000]}"):
                continue
            mark(job, f"confirmation email: {subject[:70]}")
            print(f"[applied] {job.get('company')} confirmed receiving it "
                  f"- off the list")
            done += 1
            break
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description="Applications Harry finished")
    ap.add_argument("--mark", help="company or title he has done")
    ap.add_argument("--all", action="store_true",
                    help="he has cleared the whole list")
    ap.add_argument("--scan", action="store_true",
                    help="read the inbox and mark what was confirmed")
    ap.add_argument("--list", action="store_true", help="what is still waiting")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args(argv)
    state = jm.load()

    if args.list or not (args.mark or args.all or args.scan):
        jobs = waiting(state)
        print(f"[applied] {len(jobs)} waiting on a CAPTCHA")
        for job in jobs:
            filled = len(job.get("captcha_answers") or [])
            print(f"    {job.get('company')} - {job.get('title')} "
                  f"({filled} answer(s) ready)")
            print(f"      {job.get('apply_url')}")
        return 0

    changed = 0
    if args.all:
        for job in waiting(state):
            mark(job, "he cleared the whole list")
            changed += 1
        print(f"[applied] {changed} marked done")
    elif args.mark:
        hits = by_name(state, args.mark)
        if not hits:
            print(f"[applied] nothing on the list matches {args.mark!r}")
            for job in waiting(state):
                print(f"    on the list: {job.get('company')} - "
                      f"{job.get('title')}")
            return 1
        for job in hits:
            mark(job, "he said so")
            print(f"[applied] {job.get('company')} - {job.get('title')} "
                  f"marked done")
            changed += 1
    if args.scan:
        changed += scan(state, args.days)

    if changed:
        jm.save(state)
        print(f"[applied] {changed} application(s) recorded as submitted")
    else:
        print("[applied] nothing to record")
    return 0


if __name__ == "__main__":
    sys.exit(main())
