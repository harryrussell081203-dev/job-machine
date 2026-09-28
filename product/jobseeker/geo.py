"""How far away a job is, from postcodes.io - free, no key, open data.

Two uses: every job card says "31 miles from you", and a listing the boards
leaked from well outside the person's travel radius is set aside before a
model is paid to read it. The boards search "within 25 miles of Aberdeen",
but their idea of where a job is can be a head office two hundred miles off.

Lookups:

  - a full postcode ("AB10 1XG")  -> /postcodes/{postcode}
  - an outward code ("AB10")      -> /outcodes/{code}
  - anything else, a town         -> /places?q={town}
  - Adzuna often gives coordinates itself, and those are used first

Every answer, including "could not find it", is remembered: in this process,
and through `store` in the app's database across runs, so the same town is
asked about once, not six times a day.

A place that cannot be found is never a reason to hide a job. Unknown
distance means the job is shown, without a distance on it.

Switch: DISTANCE (default on).
"""

from __future__ import annotations

import json
import math
import re
from urllib.parse import quote

from . import settings

API = "https://api.postcodes.io"
TIMEOUT = 10

FULL_POSTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$")
OUTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$")
# Somewhere in a free-text location, e.g. "Dyce, Aberdeen AB21 0BH".
POSTCODE_IN_TEXT = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b")

# Plugged in by the app: store.get(key) -> str | None, store.put(key, str).
store = None
_memory: dict[str, dict | None] = {}


def enabled() -> bool:
    return settings.flag("DISTANCE")


def _normalise(place: str) -> str:
    return re.sub(r"\s+", " ", (place or "").strip()).upper()


def _query(place: str) -> tuple[str, str]:
    """(kind, value) to ask postcodes.io about."""
    upper = _normalise(place)
    found = POSTCODE_IN_TEXT.search(upper)
    if found:
        return "postcode", found.group(1).replace(" ", "")
    if OUTCODE.match(upper):
        return "outcode", upper
    # "Westhill, Aberdeenshire" -> "Westhill": the first part is the town.
    town = (place or "").split(",")[0].strip()
    return "place", town


def _ask(kind: str, value: str, get) -> dict | None:
    if kind == "postcode":
        url = f"{API}/postcodes/{quote(value)}"
    elif kind == "outcode":
        url = f"{API}/outcodes/{quote(value)}"
    else:
        url = f"{API}/places?q={quote(value)}&limit=1"
    try:
        r = get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        result = r.json().get("result")
    except Exception as exc:
        print(f"[geo] postcodes.io unavailable for {value!r}: {exc}")
        raise
    if isinstance(result, list):
        result = result[0] if result else None
    if not isinstance(result, dict):
        return None
    lat, lon = result.get("latitude"), result.get("longitude")
    if lat is None or lon is None:
        return None
    country = result.get("country")
    if isinstance(country, list):
        country = country[0] if country else ""
    return {"lat": float(lat), "lon": float(lon), "country": country or ""}


def locate(place: str, *, get=None) -> dict | None:
    """{"lat", "lon", "country"} for a place, or None if it cannot be found.
    A network failure is not remembered, so it is asked again next time."""
    if not (place or "").strip():
        return None
    kind, value = _query(place)
    if not value:
        return None
    key = f"geo:{kind}:{value.upper()}"
    if key in _memory:
        return _memory[key]
    if store is not None:
        try:
            saved = store.get(key)
        except Exception:
            saved = None
        if saved:
            found = json.loads(saved) or None
            _memory[key] = found
            return found
    if get is None:
        import requests
        get = requests.get
    try:
        found = _ask(kind, value, get)
    except Exception:
        return None
    _memory[key] = found
    if store is not None:
        try:
            store.put(key, json.dumps(found))
        except Exception:
            pass
    return found


def miles(a: dict, b: dict) -> float:
    """Straight-line distance. Not the drive, and the card says so by saying
    nothing more precise than whole miles."""
    r = 3958.8
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp = p2 - p1
    dl = math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def where_listing_is(listing, *, get=None) -> dict | None:
    lat = getattr(listing, "latitude", None)
    lon = getattr(listing, "longitude", None)
    if lat is not None and lon is not None:
        return {"lat": float(lat), "lon": float(lon), "country": ""}
    return locate(getattr(listing, "location", "") or "", get=get)


def slack(radius: int) -> float:
    """How far past the radius a job may be before it is set aside. The boards
    measure from a town's centre and this from a postcode or a town's centre,
    so a job at the edge must not be lost to the difference."""
    return max(5.0, radius * 0.1)
