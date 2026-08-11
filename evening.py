"""
What came in today, and what to do about it.

THE PROBLEM THIS SOLVES, IN HARRY'S WORDS
-----------------------------------------
"a lot of people just call me randomly as a result of the system working and I
have to figure out on the fly who's calling and what it's about and what the
role is."

That is the machine's own success arriving as an ambush. A consultant rings
about a role Harry has never read, from a company he was written to on
Tuesday, and he has thirty seconds to sound like a candidate rather than
somebody who does not recognise the name. The information exists - the machine
wrote the letter - it just was not in his hand when the phone went.

So: one text every evening, covering every email that arrived that day, who it
was from, what it is about, and what he needs to do. Read once at six o'clock,
and tomorrow's callers are people he is expecting.

    python evening.py            # what would be sent
    python evening.py --send     # send it

WHY EVENING AND NOT MORNING
The morning text is about what to DO today; this is about what CAME IN. Sent
at the end of the day it covers the whole day, and it primes him for the calls
that come the following morning, which is when recruiters ring.

WHY IT NAMES THE ROLE AND THE COMPANY BEFORE ANYTHING ELSE
Because that is the question he cannot answer on the phone. Everything else in
the line is secondary to 'who is this and what job'.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import job_machine as jm
import sms

SENT_ON = "evening_sent_on"
# The hour, UK time, this is meant to go. The workflow fires either side of it
# and this decides which firing is really the right one, the same trick the
# morning brief uses for British Summer Time.
EVENING_HOUR_UK = jm.env_int("EVENING_HOUR_UK", 18)
MAX_LINES = jm.env_int("EVENING_MAX_LINES", 8)

# Mail that is not a person and needs no action. A digest that lists these is a
# digest he stops reading.
NOT_WORTH_A_LINE = re.compile(
    r"no-?reply|do-?not-?reply|newsletter|unsubscribe|notification|"
    r"jobs? alert|job alert|new jobs|daily digest|password|verify your|"
    r"security alert|receipt|invoice|statement", re.I)


def today_uk():
    return jm.uk_now().date().isoformat()


def is_time(force=False):
    return force or jm.uk_now().hour == EVENING_HOUR_UK


def replies_today(state):
    """Everything that came back today, from the machine's own record.

    Read off state rather than the inbox: the reply watcher already reads the
    mailbox every two hours and writes what it found, including which job it
    was about - which is exactly the context the inbox alone cannot give."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    out = []
    for job in state.get("jobs", {}).values():
        when = jm.parse_ts(job.get("replied_at"))
        if not when or when < since:
            continue
        out.append(job)
    return sorted(out, key=lambda j: j.get("replied_at") or "", reverse=True)


# What he should do, in the order it matters. An interview invitation is not
# the same kind of thing as a rejection and must never be summarised the same
# way.
def action_for(job):
    kind = (job.get("reply_category") or "").lower()
    if kind in ("interview", "interview_invite"):
        return "WANTS TO INTERVIEW YOU - reply today"
    if kind in ("question", "needs_info"):
        return "asked you a question - needs an answer"
    if kind in ("rejection", "no"):
        return "no thanks"
    if kind == "auto_acknowledgement":
        return "automatic acknowledgement, nothing to do"
    return "read it and decide"


def rank(job):
    kind = (job.get("reply_category") or "").lower()
    order = {"interview": 0, "interview_invite": 0, "question": 1,
             "needs_info": 1, "": 2, "rejection": 4, "no": 4,
             "auto_acknowledgement": 5}
    return order.get(kind, 2)


def line_for(job):
    """One line: who, what role, what to do. In that order, deliberately."""
    company = (job.get("company") or "someone").strip()
    title = (job.get("title") or "").strip()
    role = f" re {title}" if title else ""
    return f"{company}{role} - {action_for(job)}"


# ======================================================================
# THE REST OF THE MAIL
# ======================================================================
# The first version of this covered replies only - mail from a company the
# machine had itself written to. That is not what he asked for. His words were
# "text me every evening about the emails I've received that day and what
# actions need taken FOR ALL OF MY EMAILS", and in a live job hunt a good half
# of the useful mail is from somebody who found him rather than the other way
# round: a consultant off a job board, the Job Centre, the Forces Employment
# Charity. None of it appeared here.
#
# inbox_watch keeps the register of that mail and what each message wants. This
# reads it. Anything from an address the reply watcher already covers is left
# out, because the reply line above already names that company and the role -
# which is more than the inbox alone can say.
def other_mail(state):
    """[(key, entry)] - today's mail from people the job records do not cover."""
    try:
        import inbox_watch
        return [(k, e) for k, e in inbox_watch.arrived_today(state)
                if not e.get("tracked")
                and not NOT_WORTH_A_LINE.search(
                    f"{e.get('subject', '')} {e.get('address', '')}")]
    except Exception as e:
        # This reads the register, not the mailbox - the workflow tops it up
        # just before this runs. Wrapped anyway: a problem in the newer half
        # must never cost him the reply digest, which needs no network at all.
        print(f"[evening] inbox register unavailable, carrying on: {e}")
        return []


def line_for_mail(entry):
    return f"{entry['who']} - {entry.get('do') or entry.get('subject', '')}"


def compose(state):
    """The whole text, or None if there is nothing worth sending."""
    replies = [j for j in replies_today(state)
               if not NOT_WORTH_A_LINE.search(job_subject(j))]
    mail = other_mail(state)
    if not replies and not mail:
        return None
    replies.sort(key=rank)
    lines = [line_for(j) for j in replies[:MAX_LINES]]
    # Whatever room is left goes to the rest of the inbox, urgent first.
    room = max(0, MAX_LINES - len(lines))
    mail_lines = [line_for_mail(e) for _, e in mail[:room]]
    more = (len(replies) - len(lines)) + (len(mail) - len(mail_lines))
    urgent = sum(1 for j in replies if rank(j) == 0)
    urgent += sum(1 for _, e in mail if e.get("category") == "interview")
    counts = []
    if replies:
        counts.append(f"{len(replies)} repl{'y' if len(replies) == 1 else 'ies'}")
    if mail:
        counts.append(f"{len(mail)} other")
    head = " and ".join(counts) + " today"
    if urgent:
        head += f" - {urgent} wants to interview you"
    body = head + ":\n" + "\n".join(f"- {l}" for l in lines + mail_lines)
    if more > 0:
        body += f"\n- and {more} more in your inbox"
    older = still_waiting(state, exclude={k for k, _ in mail})
    if older:
        # Otherwise something asked for on Monday goes silent by Wednesday: it
        # is not new enough for this text and never beats an interview
        # invitation to the two lines in the morning one.
        body += (f"\n{older} older one{'' if older == 1 else 's'} still "
                 f"waiting on you.")
    # The point of the whole thing: he should recognise tomorrow's caller.
    body += "\nIf one of these rings tomorrow, that is who it is."
    return body


def still_waiting(state, exclude=()):
    """How many earlier days' messages nobody has had an answer to."""
    try:
        import inbox_watch
        return len([k for k, _ in inbox_watch.outstanding(state)
                    if k not in exclude])
    except Exception:
        return 0


def job_subject(job):
    return " ".join(str(job.get(k) or "") for k in
                    ("reply_subject", "reply_from", "sent_subject"))


def run(state, dry_run=True, force=False):
    if not is_time(force):
        print(f"[evening] not {EVENING_HOUR_UK}:00 UK yet, nothing sent")
        return False
    if state.get(SENT_ON) == today_uk() and not force:
        print("[evening] already sent today")
        return False
    text = compose(state)
    if not text:
        print("[evening] nothing came in today worth a text")
        return False
    print(f"--- evening text ---\n{text}\n--------------------")
    if dry_run:
        return False
    if not sms.alert_harry(text):
        print("[evening] could not text it")
        return False
    state[SENT_ON] = today_uk()
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tonight's inbox digest")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="ignore the clock and the once-a-day guard")
    args = ap.parse_args(argv)
    state = jm.load()
    if run(state, dry_run=not args.send, force=args.force) and args.send:
        jm.save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
