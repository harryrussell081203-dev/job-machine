"""
The morning text: the two or three things most worth doing today.

WHAT MAKES THIS DIFFERENT FROM ADVICE
-------------------------------------
Anyone can send a daily motivational text. This one is only allowed to say
things that are TRUE OF TODAY, and it gets them from two places:

  THE STATE FILE   who replied, who asked to be phoned, what is filled in and
                   waiting, what needs an answer only Harry can give. This is
                   live work, and it always outranks anything standing.
  data/goals.json  what he is actually trying to do, written down, with the
                   source of each line recorded. Without that file the machine
                   would be guessing at his life, and a guessed 'most
                   productive action' is worse than no text at all.

THE RANKING, AND WHY IT IS THIS WAY
-----------------------------------
  1. Somebody is waiting on him. A consultant who asked for a call, an
     interview invitation, a question only he can answer. Nothing he could
     start today beats finishing something somebody else has already begun.
  2. Work that is nearly done. An application filled in and stopped by a bot
     check is minutes from being sent; starting a new one instead is worse
     value in every direction.
  3. One standing goal, rotated. Not the list - one. A text that reads as
     five things you are failing at gets swiped away, and the whole point is
     that it does not.

WHAT IT WILL NOT DO
-------------------
It never reads the inbox directly and never summarises personal
correspondence. It works from the job-search state file and a file of goals
Harry can edit, so it cannot accidentally put something private into a text
message. If a goal in that file is wrong, the fix is to edit the file.

    python morning.py --dry-run    # print today's text, send nothing
    python morning.py --send       # send it
"""
import argparse
import json
import os
import sys

import job_machine as jm
import sms

GOALS_PATH = os.path.join(jm.ROOT, "data", "goals.json")
SENT_ON = "morning_sent_on"
GOAL_ROTATION = "morning_goal_rotation"
# 07:45 UK. Early enough that a call before ten is still possible, late enough
# that he is awake for it.
MORNING_HOUR_UK = jm.env_int("MORNING_HOUR_UK", 7)
# How long something stays on the list before the text stops mentioning it.
#
# Nothing here can tell whether Harry answered an email - he answers from his
# own inbox and the machine never sees it. So an item he has already dealt
# with would otherwise be repeated every morning forever, and the fastest way
# to make a daily text worthless is to have it nag about something that is
# done. After a week, either it was handled or it is not going to be, and
# either way the text has said its piece.
CHASE_DAYS = jm.env_int("MORNING_CHASE_DAYS", 7)


def load_goals():
    try:
        with open(GOALS_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[morning] cannot read goals: {e}")
        return []
    goals = [g for g in data.get("goals", []) if g.get("active", True)]
    return sorted(goals, key=lambda g: g.get("priority", 99))


def due_today(when=None):
    """Is it the morning slot in the UK right now?

    GitHub cron is UTC and does not shift with British Summer Time, so the
    workflow fires at both 06:45 and 07:45 UTC and this decides which one is
    really 07:45 in Aberdeen - the same trick the nightly digest uses."""
    now = when or jm.uk_now()
    return now.hour == MORNING_HOUR_UK


def still_fresh(job, field="replied_at"):
    """Did this land recently enough to still be worth chasing?"""
    when = jm.parse_ts(job.get(field))
    if not when:
        return True
    from datetime import datetime, timezone
    return (datetime.now(timezone.utc) - when).days < CHASE_DAYS


def waiting_on_harry(state):
    """Live work, most urgent first. Every line names something real."""
    out = []
    jobs = [j for j in state.get("jobs", {}).values() if still_fresh(j)]

    for job in jobs:
        if job.get("wants_a_word") and not job.get("call_made_at"):
            who = job.get("contact_name") or job.get("company") or "someone"
            number = job.get("contact_mobile") or ""
            entry = state.get("contact_numbers", {}).get(
                jm.company_key(job.get("company")), {})
            number = number or next(iter(entry.get("numbers", [])), "")
            out.append({"kind": "call",
                        "text": f"Ring {who}{' on ' + number if number else ''}"
                                f" - they asked you to",
                        "weight": 1})

    interviews = [j for j in jobs if j.get("reply_category") == "interview_invite"
                  and not j.get("interview_replied_at")]
    for job in interviews[:2]:
        out.append({"kind": "interview",
                    "text": f"Reply properly to {job.get('company')} about the "
                            f"interview - the machine only sent availability",
                    "weight": 0})

    questions = [j for j in jobs if j.get("reply_category") == "question"
                 and not j.get("answered_at")]
    if questions:
        first = questions[0]
        more = f" (+{len(questions) - 1} more)" if len(questions) > 1 else ""
        out.append({"kind": "question",
                    "text": f"Answer {first.get('company')} - they asked you "
                            f"something the machine will not answer{more}",
                    "weight": 2})

    captcha = sms.waiting_on_a_captcha(state)
    if captcha:
        out.append({"kind": "captcha",
                    "text": f"{len(captcha)} application"
                            f"{'' if len(captcha) == 1 else 's'} need only the "
                            f"captcha - the page is in your email",
                    "weight": 3})

    review = [j for j in jobs if j.get("status") == "portal_review"]
    if review:
        out.append({"kind": "review",
                    "text": f"{len(review)} application"
                            f"{'' if len(review) == 1 else 's'} stopped on a "
                            f"question only you can answer",
                    "weight": 4})

    return [item for item in sorted(out, key=lambda i: i["weight"])]


def todays_goal(state, goals, when=None):
    """One standing goal, rotated so it is a different one each day.

    A goal pinned to a weekday takes that day. Otherwise the rotation moves on
    every time a morning text goes out, which means it does not matter how
    many days are missed - it never repeats the same one twice running."""
    if not goals:
        return None
    now = when or jm.uk_now()
    for goal in goals:
        if goal.get("weekday") is not None and goal["weekday"] == now.weekday():
            return goal
    rotating = [g for g in goals if g.get("weekday") is None]
    if not rotating:
        return None
    index = state.get(GOAL_ROTATION, 0) % len(rotating)
    return rotating[index]


def advance_rotation(state, goals):
    rotating = [g for g in goals if g.get("weekday") is None]
    if rotating:
        state[GOAL_ROTATION] = (state.get(GOAL_ROTATION, 0) + 1) % len(rotating)


def compose(state, goals, when=None):
    """The text. Live work first, then one standing goal, then nothing else."""
    live = waiting_on_harry(state)
    lines = [item["text"] for item in live[:2]]
    goal = todays_goal(state, goals, when)
    if goal and len(lines) < 3:
        lines.append(goal.get("next_action") or goal.get("goal"))
    if not lines:
        return ""
    numbered = " ".join(f"{i}. {line}." for i, line in enumerate(lines, 1))
    return f"Today: {numbered}"[:320]


def run(state, send=False, force=False, when=None):
    if not force and not due_today(when):
        print(f"[morning] not {MORNING_HOUR_UK:02d}:00 in the UK, skipping")
        return 0
    if not force and state.get(SENT_ON) == jm.today():
        print("[morning] already sent today")
        return 0
    goals = load_goals()
    body = compose(state, goals, when)
    if not body:
        print("[morning] nothing worth saying today")
        return 0
    if not send:
        print(f"[morning] would send ({len(body)} chars):\n  {body}")
        return 1
    if not sms.configured():
        print("[morning] no httpSMS key")
        return 0
    if not sms.alert_harry(body, urgent=True):
        return 0
    state[SENT_ON] = jm.today()
    advance_rotation(state, goals)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="the default")
    ap.add_argument("--force", action="store_true",
                    help="ignore the clock and the once-a-day guard")
    args = ap.parse_args(argv)
    state = jm.load()
    run(state, send=args.send and not args.dry_run, force=args.force)
    jm.save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
