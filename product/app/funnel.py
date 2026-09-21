"""One place that knows somebody moved a step forward.

Recording the event and paying whoever referred them are the same moment, and
splitting them across two call sites is how they drift apart: one gets added
to a new code path and the other does not, and the symptom is a referral
programme that pays out on some routes and not others - which reads to the
user as the offer being a lie.

So every transition goes through reached(). It writes the event, and if that
event was the first of its kind for that account, it pays the referrer.

NOTHING HERE MAY RAISE.

Every caller is in the middle of doing something that matters more than this:
saving a CV, sending a letter. A funnel that cannot be written is a gap in a
report. A funnel that throws is a person's letter not going out. The whole
module is written on that asymmetry.
"""

from __future__ import annotations

import logging

from . import db, referrals

log = logging.getLogger("recruited")


def reached(user_id: int, kind: str, *, detail: str = "",
            at: int | None = None) -> bool:
    """This account reached a stage. True if it is the first time.

    Safe to call on every send, every upload, every reply - the event table
    is unique per (user, kind), so the second call is a no-op and returns
    False. Callers do not have to know whether this is a first, which is what
    lets them call it unconditionally rather than guarding with a query that
    can race.
    """
    try:
        first = db.record_event(user_id, kind, detail=detail, at=at)
    except ValueError:
        # An unknown kind is a typo in our own code and worth seeing, but not
        # here, in front of a user mid-upload.
        log.exception("refusing to record an unknown funnel event")
        return False
    except Exception:
        log.exception("could not record %s for user %s", kind, user_id)
        return False

    if not first:
        return False
    try:
        earned = referrals.reward_for(user_id, kind)
        if earned:
            log.info("referral: %s seconds granted for %s reaching %s",
                     earned, user_id, kind)
    except Exception:
        # The stage is recorded either way. A reward that failed to pay is
        # recoverable from the events table; a stage that was never recorded
        # is not.
        log.exception("could not pay the referral for %s", kind)
    return True
