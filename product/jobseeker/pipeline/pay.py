"""Pay, as the advert states it, for the job card.

The boards' salary fields are empty on most adverts, and the figure is often
sitting in the text instead: "£32,000 - £36,000 per annum", "£18.50 per
hour", "up to £40k". This reads that, so a card can say what the job pays
rather than "Salary not listed" when the advert did list it.

DISPLAY ONLY. The pay floor in scoring.py reads the boards' fields and never
this, on purpose: a misread here costs a wrong line on a card, but a misread
fed to the floor ("£1,000 welcome bonus" taken as a salary) would silently
throw away a good job. So this is conservative - a figure needs a range, a
unit ("per hour", "a year"), or a salary word next to it - and it skips
anything that is plainly a bonus or an allowance.

Switch: SALARY_FROM_TEXT (default on).
"""

from __future__ import annotations

import re

from .. import settings

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{1,2})?)\s*(k)?"
_UNIT = (r"(per\s+hour|an\s+hour|p/?h\b|ph\b|hourly|per\s+annum|p\.?a\.?\b|"
         r"a\s+year|per\s+year|annually|per\s+day|a\s+day|daily|"
         r"per\s+week|a\s+week|weekly)")
RANGE = re.compile(
    r"£\s*" + _NUM + r"\s*(?:-|–|—|to)\s*£?\s*" + _NUM + r"(?:\s*" + _UNIT + r")?",
    re.I)
SINGLE_WITH_UNIT = re.compile(r"£\s*" + _NUM + r"\s*" + _UNIT, re.I)
SINGLE = re.compile(r"£\s*" + _NUM, re.I)
_SALARY_WORDS = re.compile(
    r"(salary|pay|paying|earn|up\s+to|ote|circa|c\.|basic|rate|package|from)"
    r"\W*$", re.I)
_NOT_PAY = re.compile(
    r"bonus|allowance|relocation|sign[- ]?on|welcome|referral|golden|"
    r"voucher|discount|expenses|budget|turnover|million|investment", re.I)


def enabled() -> bool:
    return settings.flag("SALARY_FROM_TEXT")


def _amount(number: str, k: str | None) -> float:
    value = float(number.replace(",", ""))
    return value * 1000 if k else value


def _unit(word: str | None, amount: float) -> str:
    w = (word or "").lower().replace(" ", "")
    if w in ("perhour", "anhour", "ph", "p/h", "hourly"):
        return "hour"
    if w in ("perday", "aday", "daily"):
        return "day"
    if w in ("perweek", "aweek", "weekly"):
        return "week"
    if w:
        return "year"
    # No unit given: judged by size, the same way scoring.stated_pay does.
    if amount < 100:
        return "hour"
    if amount <= 2000:
        return "day"
    return "year"


def _plausible(low: float, high: float, unit: str) -> bool:
    bounds = {"hour": (6, 250), "day": (60, 2500), "week": (200, 10000),
              "year": (10000, 400000)}
    lo, hi = bounds[unit]
    return lo <= low <= high <= hi


def _is_bonus(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 40):end + 40]
    return bool(_NOT_PAY.search(window))


def from_text(text: str) -> tuple[float, float, str] | None:
    """(low, high, unit) for the first pay figure the advert states, or None."""
    if not enabled() or not text:
        return None
    for m in RANGE.finditer(text):
        if _is_bonus(text, m.start(), m.end()):
            continue
        low, high = _amount(m.group(1), m.group(2)), _amount(m.group(3), m.group(4))
        # "£30 - 35k": the k on the second applies to both.
        if m.group(4) and not m.group(2) and low < 1000:
            low *= 1000
        unit = _unit(m.group(5), high)
        if low <= high and _plausible(low, high, unit):
            return low, high, unit
    for m in SINGLE_WITH_UNIT.finditer(text):
        if _is_bonus(text, m.start(), m.end()):
            continue
        value = _amount(m.group(1), m.group(2))
        unit = _unit(m.group(3), value)
        if _plausible(value, value, unit):
            return value, value, unit
    for m in SINGLE.finditer(text):
        if _is_bonus(text, m.start(), m.end()):
            continue
        before = text[max(0, m.start() - 25):m.start()]
        if not _SALARY_WORDS.search(before):
            continue
        value = _amount(m.group(1), m.group(2))
        if _plausible(value, value, "year"):
            return value, value, "year"
    return None


def _money(value: float) -> str:
    if value == int(value):
        return f"£{int(value):,}"
    return f"£{value:,.2f}"


def describe(low: float, high: float, unit: str) -> str:
    """"£32,000 to £36,000 a year", "£18.50 an hour"."""
    per = {"hour": "an hour", "day": "a day", "week": "a week",
           "year": "a year"}[unit]
    if low == high:
        return f"{_money(high)} {per}"
    return f"{_money(low)} to {_money(high)} {per}"
