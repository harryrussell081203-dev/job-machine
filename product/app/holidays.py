"""No letters to employers on a bank holiday.

A letter that lands on a bank holiday sits under a day's worth of others by
the time anybody reads it. Nothing is lost by holding it: the draft stays
due, and the next sweep on a working day sends it.

The dates are GOV.UK's own (https://www.gov.uk/bank-holidays.json, Open
Government Licence), fetched at most once a day and kept in site_meta. The
three divisions have different holidays - Scotland has 2 January and St
Andrew's Day, and no Easter Monday - so the right one is worked out from
where the person lives (postcodes.io gives the country; see
jobseeker/geo.py). BANK_HOLIDAY_DIVISION is the fallback when that cannot
be told, and defaults to Scotland.

If the dates cannot be fetched and nothing is cached, letters go. Sending on
a bank holiday is a small cost; not sending for a week because GOV.UK was
slow once is a large one.

Switch: BANK_HOLIDAYS (default on).
"""

from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

from jobseeker import geo, settings

from . import db

SOURCE = "https://www.gov.uk/bank-holidays.json"
DIVISIONS = ("england-and-wales", "scotland", "northern-ireland")
_UK = ZoneInfo("Europe/London")
_CACHE_KEY = "bank_holidays"
_BY_COUNTRY = {"scotland": "scotland", "england": "england-and-wales",
               "wales": "england-and-wales",
               "northern ireland": "northern-ireland"}
NAMES = {"england-and-wales": "England and Wales", "scotland": "Scotland",
         "northern-ireland": "Northern Ireland"}


def enabled() -> bool:
    return settings.flag("BANK_HOLIDAYS")


def fallback_division() -> str:
    chosen = settings.text("BANK_HOLIDAY_DIVISION", "scotland").lower()
    return chosen if chosen in DIVISIONS else "scotland"


def uk_today(now: dt.datetime | None = None) -> dt.date:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(_UK).date()


def _parse(payload: dict) -> dict[str, set[str]]:
    out = {}
    for division in DIVISIONS:
        events = (payload.get(division) or {}).get("events") or []
        out[division] = {e.get("date") for e in events
                         if isinstance(e, dict) and e.get("date")}
    return out


def dates(*, today: dt.date | None = None, get=None) -> dict[str, set[str]]:
    """Every bank holiday date by division, from the cache if it was fetched
    today, otherwise from GOV.UK. {} if neither can be had."""
    today = today or uk_today()
    cached = db.get_meta(_CACHE_KEY)
    saved = None
    if cached:
        try:
            saved = json.loads(cached)
        except ValueError:
            saved = None
    if saved and saved.get("fetched") == today.isoformat():
        return _parse(saved.get("data") or {})

    if get is None:
        import requests
        get = requests.get
    try:
        r = get(SOURCE, timeout=15)
        if r.status_code == 200:
            data = r.json()
            db.set_meta(_CACHE_KEY, json.dumps(
                {"fetched": today.isoformat(), "data": data}))
            return _parse(data)
        print(f"[holidays] GOV.UK answered {r.status_code}")
    except Exception as exc:
        print(f"[holidays] could not fetch bank holidays: {exc}")
    # Yesterday's copy is still right for today: the list covers years.
    return _parse(saved.get("data") or {}) if saved else {}


def division_for(user_id: int, *, get=None) -> str:
    profile = db.load_profile(user_id) or {}
    where = geo.locate(profile.get("location") or "", get=get)
    country = ((where or {}).get("country") or "").strip().lower()
    return _BY_COUNTRY.get(country, fallback_division())


def holiday_today(user_id: int, *, now: dt.datetime | None = None,
                  get=None) -> str:
    """The division's name if today is a bank holiday there, else ""."""
    if not enabled():
        return ""
    today = uk_today(now)
    division = division_for(user_id, get=get)
    if today.isoformat() in dates(today=today, get=get).get(division, set()):
        return NAMES[division]
    return ""
