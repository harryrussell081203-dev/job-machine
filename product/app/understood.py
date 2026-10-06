"""Say back what somebody told us, in plain words, so they can see it landed.

The sign-up asks for as little as the machine can run on. That only feels
safe to the person if every answer comes straight back to them as something
the system now knows: "£12.50 an hour or more", "Leeds, and 25 miles
around", "Letters signed Sam Example". A form that swallows answers silently
feels like a form; one that answers back feels like it is listening.

Everything here reads the same six-or-so strings the quick start takes, and
says nothing it was not told. The pay wording comes from the same parser
that decides the floor, so the sentence on the screen and the number the
machine filters on can never disagree.
"""

from __future__ import annotations

import math
import re

# What "the least you will work for" means when somebody types one number.
# Below this it is an hourly rate; at or above it, a salary. Nobody earns
# £200 an hour in this market and nobody's salary is £199 a year, so one box
# can take either and the machine can tell which.
HOURLY_BELOW = 200

# The six things the machine cannot start without, plus the two only the
# person may type (a model never fills in a name or a phone number).
WORK = ("target_roles", "location", "last_title", "last_org", "min_pay")
CONTACT = ("name", "phone")
FIELDS = WORK + CONTACT

QUESTION = {
    "target_roles": "What job are you after?",
    "location": "Where do you live?",
    "last_title": "Your last job",
    "last_org": "Who it was with",
    "min_pay": "The least you would work for",
    "name": "Your name",
    "phone": "Your phone number",
}


def _pay_value(raw: str) -> float:
    """The number somebody meant, before any rounding; 0 when unreadable."""
    text = (raw or "").lower().replace("£", "").replace(",", "").strip()
    for suffix in ("per hour", "an hour", "/hr", "/h", "p/h", "ph", "per year",
                   "a year", "p.a.", "pa"):
        text = text.replace(suffix, "").strip()
    thousands = text.endswith("k")
    text = text.rstrip("k").strip()
    try:
        value = float(text)
    except ValueError:
        return 0
    if thousands:
        value *= 1000
    return value if value > 0 else 0


def parse_pay(raw: str) -> tuple[int, int]:
    """One free-text pay box -> (min_salary_annual, min_rate_hourly).

    Accepts what people actually type on a phone: "25000", "£25,000", "25k",
    "25k a year", "12.50". Returns (0, 0) for anything unreadable, which the
    Profile then refuses with its own message - so a bad value is caught by
    the same rule as every other route, rather than by a second copy of it
    here.
    """
    value = _pay_value(raw)
    if not value:
        return 0, 0
    # Rounded UP. This is a floor, and rounding a floor down quietly accepts
    # work below what the person said - "12.50" stored as 12 lets through a
    # £12.00 job they told us they would not take.
    if value < HOURLY_BELOW:
        return 0, math.ceil(value)
    return math.ceil(value), 0


def pay_words(raw: str) -> str:
    """"£12.50 an hour or more", or "" when it cannot be read.

    Shows what they typed (12.50), not the rounded floor (13), because the
    rounding is ours and a person reading back "£13" would think we misread
    them. The filter still uses the rounded-up figure.
    """
    value = _pay_value(raw)
    if not value:
        return ""
    if value < HOURLY_BELOW:
        shown = f"{value:.2f}".removesuffix(".00")
        return f"£{shown} an hour or more"
    return f"£{round(value):,} a year or more"


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def playback(answers: dict, *, radius: int = 25) -> list[dict]:
    """Each answer given, as a line the person can recognise.

    [{"key": "location", "label": "Where", "text": "Leeds, and 25 miles
    around"}, ...] in the order the questions are asked. Unanswered (or
    unreadable) ones are left out; missing() says what those are.
    """
    a = {k: _clean(answers.get(k)) for k in FIELDS}
    out = []

    def add(key, label, text):
        if text:
            out.append({"key": key, "label": label, "text": text})

    roles = [r.strip() for r in a["target_roles"].split(",") if r.strip()]
    add("target_roles", "Looking for", ", ".join(roles))
    add("location", "Where",
        f"{a['location']}, and {radius} miles around" if a["location"] else "")
    if a["last_title"] and a["last_org"]:
        add("last_job", "Your last job", f"{a['last_title']} at {a['last_org']}")
    add("min_pay", "Pay", pay_words(a["min_pay"]))
    add("name", "Letters signed", a["name"])
    add("phone", "Employers can ring", a["phone"])
    return out


def missing(answers: dict, keys=FIELDS) -> list[str]:
    """The questions still to answer, in order. Pay counts as missing when it
    cannot be read, because an unreadable floor is no floor."""
    a = {k: _clean(answers.get(k)) for k in FIELDS}
    out = []
    for k in keys:
        if k == "min_pay":
            if not pay_words(a["min_pay"]):
                out.append(k)
        elif not a[k]:
            out.append(k)
    return out


def headline(answers: dict) -> str:
    """One sentence for the top of a screen: what it is about to do."""
    a = {k: _clean(answers.get(k)) for k in FIELDS}
    roles = [r.strip() for r in a["target_roles"].split(",") if r.strip()]
    if not roles:
        return ""
    what = roles[0] if len(roles) == 1 else ", ".join(roles[:-1]) + " or " + roles[-1]
    where = f" around {a['location']}" if a["location"] else ""
    return f"{what} work{where}"


# What a stranger can make this site email to any address. /start sends a
# sign-in link to whatever address is typed, so anything quoted from the
# answers into that email is text somebody else may have chosen. Plain words
# only, and short: no links, no digits, nothing that reads as an offer.
_SAFE = re.compile(r"[A-Za-z][A-Za-z &'./-]{0,88}[A-Za-z]")


def for_email(answers: dict) -> str:
    """The headline, if it is safe to put in an email; "" otherwise."""
    line = headline(answers)
    words = line.replace(",", "")
    # A full stop is allowed for "St. Albans", never touching a letter on
    # both sides as in a web address.
    if not _SAFE.fullmatch(words) or re.search(r"\.\S", words):
        return ""
    return line
