"""When should I follow up? A free public tool.

Built on the rule Recruited itself follows when it chases a letter
(followups.py): ONE nudge, a few days after, and never once three weeks have
gone by. Nothing here is a statistic about hiring - it is the rule this
product uses, stated as that - so the page says what to do and when without
inventing a number to justify it.

How somebody applied changes the answer more than the date does:

  - **Email to a person.** Follow up once, five working days on.
  - **An advert with a closing date.** Nobody reads applications until it
    closes, so the clock starts at the closing date, not the day you applied.
  - **Through an agency.** Same timing, but the person to chase is the
    recruiter, and a phone call is normal there.
  - **An Apply button or a portal.** There is nobody to follow up with. The
    useful move is to find a real person's address and send one short email,
    which is what /find is for.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .followups import AFTER_DAYS, WITHIN_DAYS

HOW = {
    "email": "I emailed a person directly",
    "closing": "The advert had a closing date",
    "agency": "Through a recruitment agency",
    "portal": "Through an Apply button or online form",
}


@dataclass
class Plan:
    how: str
    follow_up_on: dt.date | None
    stop_after: dt.date | None
    starts_from: dt.date
    who: str
    note: str


def add_working_days(start: dt.date, days: int) -> dt.date:
    """Weekends skipped. Bank holidays are not, because they differ across
    the UK and the page does not know where the employer is."""
    current, left = start, days
    while left > 0:
        current += dt.timedelta(days=1)
        if current.weekday() < 5:
            left -= 1
    return current


def plan(applied: dt.date, how: str, closing: dt.date | None = None) -> Plan:
    how = how if how in HOW else "email"
    if how == "portal":
        return Plan(how, None, None, applied, "",
                    "An application through a form goes into a system, not to "
                    "a person, so there is nobody to follow up with. Find the "
                    "hiring manager's own address and send one short email "
                    "instead.")
    start = applied
    if how == "closing" and closing and closing > applied:
        start = closing
    follow = add_working_days(start, AFTER_DAYS)
    stop = start + dt.timedelta(days=WITHIN_DAYS)
    who = ("the recruiter who has your CV" if how == "agency"
           else "the person you wrote to")
    note = {
        "email": "One short follow-up, as a reply to your original email so "
                 "they can see what it is about. If they have not answered "
                 "by the last date, move on - a nudge to something they have "
                 "forgotten is just another cold email.",
        "closing": "Applications are usually read after the closing date, so "
                   "the clock starts then rather than on the day you "
                   "applied.",
        "agency": "Agencies expect to be chased, and a phone call is normal. "
                  "Ask whether your CV has gone to the employer yet.",
    }[how]
    return Plan(how, follow, stop, start, who, note)


def message(role: str, applied: dt.date) -> str:
    """The follow-up, in the same words Recruited uses for its own."""
    role = (role or "").strip()
    about = f"the {role} role" if role else "the role"
    return (f"Hi,\n\nFollowing up on the note I sent on {applied:%A %-d %B} "
            f"about {about}. Still interested, and happy to do a short call "
            f"whenever suits.\n\nIs it worth me sending anything else over?")
