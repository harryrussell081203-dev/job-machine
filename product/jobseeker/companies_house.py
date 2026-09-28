"""Companies House: is the employer still trading, and who runs a small one?

The free Companies House API (a key from developer.company-information
.service.gov.uk, no charge) answers two questions this product otherwise
guesses at:

  - **Is it still a company?** Adverts outlive employers - an agency
    reposting an old listing, a firm that has since gone into liquidation. A
    dissolved company is set aside before a model is paid to read its advert.
  - **Who is the hiring manager at a small firm?** At a ten-person company
    it is very often a director, and the directors are public record. They
    are shown as NAMES ONLY, as a pointer for the person to use themselves.
    No address is ever made from a name: the rule that an address is found,
    never guessed, holds here too.

Nothing is acted on unless the search finds exactly one company whose name
matches the advert's. "Smith Engineering" matching three companies is not an
answer, and a wrong company's status or directors would be worse than none.

Every answer, including "no clear match", is cached for thirty days.

DORMANT until COMPANIES_HOUSE_API_KEY is set.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import quote

from . import settings

_LEGAL = re.compile(r"\b(ltd|limited|plc|llp|lp|cic|the)\b")


def company_key(name: str) -> str:
    """Stricter than names.company_key on purpose. That one treats words like
    "Services" and "Group" as noise, which is right for never writing to one
    employer twice and wrong here: "Acme Engineering Services" and "Acme
    Engineering" are two companies, and taking one's status or directors for
    the other's is the mistake this module must not make. Only the legal
    suffix and punctuation are dropped."""
    text = (name or "").lower().replace("&", " and ")
    text = text.replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = _LEGAL.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()

API = "https://api.company-information.service.gov.uk"
TIMEOUT = 10
CACHE_SECONDS = 30 * 86400

# Statuses that mean there is no employer left to write to. "administration"
# is deliberately not here: a firm in administration can still be trading and
# hiring.
GONE = {"dissolved", "liquidation", "converted-closed", "closed",
        "insolvency-proceedings"}

# Accounts filed as small or micro: the firms where a director is plausibly
# the person hiring.
SMALL_ACCOUNTS = {"micro-entity", "small", "total-exemption-small",
                  "total-exemption-full", "unaudited-abridged", "dormant",
                  "audit-exemption-subsidiary", "filing-exemption-subsidiary"}
MAX_DIRECTORS_SHOWN = 6

# Plugged in by the app: store.get(key) -> str | None, store.put(key, str).
store = None
_memory: dict[str, dict] = {}


def key() -> str:
    return settings.text("COMPANIES_HOUSE_API_KEY")


def enabled() -> bool:
    return bool(key())


def _get_json(path: str, get) -> dict | None:
    if get is None:
        import requests
        get = requests.get
    try:
        r = get(f"{API}{path}", auth=(key(), ""), timeout=TIMEOUT)
    except Exception as exc:
        print(f"[companies-house] unavailable: {exc}")
        raise
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"Companies House answered {r.status_code}")
    return r.json()


def _cached(name: str) -> dict | None:
    k = f"ch:{company_key(name)}"
    found = _memory.get(k)
    if found is None and store is not None:
        try:
            raw = store.get(k)
            found = json.loads(raw) if raw else None
        except Exception:
            found = None
    if found and time.time() - found.get("at", 0) < CACHE_SECONDS:
        _memory[k] = found
        return found
    return None


def _remember(name: str, info: dict) -> dict:
    info = dict(info, at=int(time.time()))
    k = f"ch:{company_key(name)}"
    _memory[k] = info
    if store is not None:
        try:
            store.put(k, json.dumps(info))
        except Exception:
            pass
    return info


def lookup(name: str, *, get=None) -> dict | None:
    """{"number", "title", "status"} for the one company matching `name`, or
    {"number": ""} for no clear match. None if it could not be asked."""
    if not enabled() or not company_key(name):
        return None
    cached = _cached(name)
    if cached is not None:
        return cached
    try:
        data = _get_json(f"/search/companies?q={quote(name)}&items_per_page=10",
                         get) or {}
    except Exception:
        return None
    wanted = company_key(name)
    matches = [i for i in data.get("items") or []
               if company_key(i.get("title") or "") == wanted]
    if len(matches) != 1:
        return _remember(name, {"number": ""})
    hit = matches[0]
    return _remember(name, {"number": hit.get("company_number") or "",
                            "title": hit.get("title") or "",
                            "status": (hit.get("company_status") or "").lower()})


def gone(name: str, *, get=None) -> str:
    """The status, if Companies House says this employer no longer trades."""
    info = lookup(name, get=get)
    if info and info.get("number") and info.get("status") in GONE:
        return info["status"]
    return ""


def _person(raw: str) -> str:
    """ "SMITH, Jane Anne" -> "Jane Anne Smith"."""
    if "," in raw:
        surname, _, rest = raw.partition(",")
        return f"{rest.strip().title()} {surname.strip().title()}".strip()
    return raw.strip().title()


def directors(name: str, *, get=None) -> list[str]:
    """Current directors of a SMALL company matching `name`, or []."""
    info = lookup(name, get=get)
    if not info or not info.get("number"):
        return []
    if "directors" in info:
        return info["directors"]
    number = info["number"]
    found: list[str] = []
    try:
        profile = _get_json(f"/company/{quote(number)}", get) or {}
        kind = (((profile.get("accounts") or {}).get("last_accounts") or {})
                .get("type") or "").lower()
        if kind in SMALL_ACCOUNTS:
            officers = _get_json(
                f"/company/{quote(number)}/officers?items_per_page=50",
                get) or {}
            found = [_person(o.get("name") or "")
                     for o in officers.get("items") or []
                     if (o.get("officer_role") or "") == "director"
                     and not o.get("resigned_on") and o.get("name")]
            if len(found) > MAX_DIRECTORS_SHOWN:
                found = []      # not a small firm in the sense that matters
    except Exception:
        return []
    _remember(name, dict(info, directors=found))
    return found
