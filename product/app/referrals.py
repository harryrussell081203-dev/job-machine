"""Users bringing users, and what they get for it.

WHY THE REWARD IS NOT MORE SENDING.

The obvious reward for a product that sends letters is a higher daily limit,
and it is the wrong one twice over.

It is not what anybody wants. Nobody's problem is that they cannot send
enough - this product exists because four hundred Easy Apply applications got
auto-rejected, and the whole argument is that twenty careful letters beat four
hundred careless ones. A reward of "send more" contradicts the pitch on the
landing page.

And it is actively harmful. Users send from their own Gmail. Raising somebody's
daily volume as a prize pushes their personal mailbox toward limits and toward
a spam reputation they will still have long after they have stopped using this.
A referral programme must not be able to damage the thing it is rewarding.

SO THE REWARD IS THE THING WE SELL.

A free month. It costs nothing to give - the marginal cost of an account is a
few database rows - and it is real money to somebody out of work, which is
precisely who this is for. It needs no new concept in the code either:
db.is_paid already honours paid_until, so a reward is that column moving.

TWO TIERS, BECAUSE ONE WOULD NEVER PAY OUT.

Of the ten accounts that exist, three have uploaded a CV and one has connected
a mailbox - and that one is Harry. A single reward gated on the referred
person becoming active would be a promise that almost never comes true, which
is worse than offering nothing: it teaches people the offer is decoration.

    they sign up and upload a CV    ->  a month
    they connect a mailbox          ->  another month

The first is reachable today. The second is deliberately pinned to activation,
the step that is actually broken, so the programme pays out most for bringing
people who get as far as using it.

WHAT STOPS THIS BEING GAMED.

Nothing here pays out for a signup alone, which is the only thing somebody can
manufacture in bulk from one keyboard. Uploading a CV and connecting a working
mailbox are both work, and the second needs a real mailbox nobody else holds.
A cap on top of that, because a limit that is never reached costs nothing and
a missing limit is found by the first person who looks for it.
"""

from __future__ import annotations

import hashlib
import secrets

from . import config, db

# Thirty days, in seconds. Not a calendar month: a month whose length depends
# on when you were referred is a support question, and the difference is worth
# nothing to anybody.
MONTH = 30 * 24 * 3600

# What each step is worth. Keyed by the event on the REFERRED person that
# triggers it, so adding a tier is adding a line here rather than a branch
# somewhere.
REWARDS = {
    "onboarded": MONTH,            # signed up and uploaded a CV
    "first_email_sent": MONTH,     # connected a mailbox and actually sent
}

# The most anybody can earn, ever. A year is far beyond what a real user will
# reach and cheap to honour if they do; without it, a mistake in the trigger
# path is unbounded free access rather than a bad week.
MAX_MONTHS = 12

# Unambiguous in speech and when typed badly: no 0/O, no 1/l/I. The whole
# point of a referral code is that somebody can say it out loud.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LENGTH = 7


def make_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def code_for(user_id: int) -> str:
    """This user's code, made on first use rather than at sign-up.

    On demand because a code that exists is a code that can be in circulation,
    and the only ones worth having in circulation are the ones somebody asked
    for. It also means the column is empty for every account that has never
    opened the referral screen, which is a useful thing to be able to count.
    """
    user = db.get_user(user_id)
    if user is None:
        return ""
    existing = (user["referral_code"] or "").strip()
    if existing:
        return existing
    for _ in range(8):
        code = make_code()
        if db.claim_referral_code(user_id, code):
            return code
    # Eight collisions against a 31^7 space means something is wrong with the
    # randomness, not that we were unlucky. Fall back to something derived and
    # certainly unique rather than looping forever.
    return "u" + hashlib.sha256(
        f"{user_id}:{config.SECRET_KEY}".encode()).hexdigest()[:CODE_LENGTH]


def link_for(user_id: int) -> str:
    code = code_for(user_id)
    base = (config.BASE_URL or "").rstrip("/")
    return f"{base}/?ref={code}" if code else ""


def credit_signup(new_user_id: int, code: str) -> bool:
    """Attach a new account to whoever referred it. Pays nothing yet.

    Returns whether a referrer was found and recorded.

    Nothing is paid for a signup because a signup is the one step somebody can
    manufacture in bulk. This only records the relationship; the rewards are
    paid by reward_for below, when the referred person does something that
    takes work.
    """
    code = (code or "").strip().lower()
    if not code:
        return False
    referrer = db.user_by_referral_code(code)
    if referrer is None:
        return False
    # Referring yourself is not a thing. Cheap to check and the first thing
    # anybody tries.
    if int(referrer["id"]) == int(new_user_id):
        return False
    return db.set_referrer(new_user_id, int(referrer["id"]))


def reward_for(user_id: int, kind: str) -> int:
    """Pay the person who referred this user, if this step earns anything.

    Returns the seconds of free access granted, 0 for nothing. Called AFTER
    the event has been recorded on the referred user, and safe to call every
    time: the referred_user event is unique on (referrer, kind, referee), so
    a second call for the same step pays nothing.
    """
    seconds = REWARDS.get(kind)
    if not seconds:
        return 0
    user = db.get_user(user_id)
    if user is None:
        return 0
    try:
        referrer_id = int(user["referred_by"] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return 0
    if not referrer_id:
        return 0

    # The ledger and the payment in that order, and the ledger is what makes
    # this idempotent. Paying first and recording second would double the
    # reward on any retry - a race here costs real money in free access, and
    # this is the cheap way to make it impossible rather than unlikely.
    first_time = db.record_event(referrer_id, db.REFERRED_USER,
                                 ref=f"{user_id}:{kind}", detail=kind)
    if not first_time:
        return 0
    if db.referral_months_earned(referrer_id) > MAX_MONTHS:
        # Recorded, deliberately, and not paid. The event is the true history
        # of what happened; the cap is a decision about what we pay for it,
        # and rolling back the record would hide that the cap was ever hit.
        return 0
    db.extend_paid_until(referrer_id, seconds)
    return seconds
