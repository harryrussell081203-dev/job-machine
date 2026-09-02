"""Deciding who an address belongs to, and how good it is.

This is the highest-leverage code in the product, and it is worth saying why
before any of it. Across 86 real emails from the original machine:

    a named human      34 sent, 13 replied   38%
    a hiring inbox     10 sent,  5 replied   50%
    a generic info@    42 sent,  4 replied   10%

Reaching a person rather than a shared inbox was worth roughly four times the
reply rate. The whole of the rest of the pipeline exists to feed this file a
list of candidate addresses; this file decides which one is worth using and
whether it can be greeted by name.

Two failure modes it is built to avoid, both learned the expensive way:

  - **Greeting a shared inbox by name.** "Dear Fundraise" is worse than "Hi,".
    A role word anywhere in the local part disqualifies a greeting, not just
    at the start: `mysupporthr@` is an HR desk.
  - **Getting a real person's name wrong.** `hr` sits inside `chris`, so
    short role words are matched only at segment boundaries. A man called
    Chris Brown gets greeted properly.

Nothing here invents an address. Every candidate must have been seen written
down somewhere; the classifier only ranks what it is given.
"""

from __future__ import annotations

import re

TIER_NAMES = {3: "named person", 2: "hiring inbox", 1: "generic inbox",
              0: "unusable"}

GENERIC_PREFIXES = ("info", "office", "enquiries", "enquiry", "inquiries",
                    "admin", "hello", "contact", "mail", "reception",
                    "accounts", "general", "sales", "support", "team")

HIRING_PREFIXES = ("careers", "career", "jobs", "job", "recruitment",
                   "recruiting", "recruit", "hr", "talent", "vacancies",
                   "vacancy", "apply", "people", "hiring", "workforce")

BAD_PREFIXES = ("noreply", "no-reply", "donotreply", "postmaster", "abuse",
                "privacy", "unsubscribe", "webmaster", "marketing",
                "newsletter", "example", "test", "email", "name", "your",
                "user", "someone", "firstname", "yourname", "sentry",
                "wordpress", "hostmaster")

# Free mail hosts are excluded deliberately. An employer reachable only at a
# gmail.com address is usually a scraped personal address, and writing to it
# is the kind of thing that ends the method working for everybody.
BAD_DOMAINS = ("example.com", "example.org", "domain.com", "yourcompany.com",
               "sentry.io", "wixpress.com", "godaddy.com", "squarespace.com",
               "gmail.com", "googlemail.com", "hotmail.com", "outlook.com",
               "yahoo.com", "icloud.com", "aol.com", "live.com")

# A role word anywhere in the local part means a shared inbox, not a person.
ROLE_WORDS = (
    "hr", "recruit", "career", "job", "vacanc", "hiring", "talent", "support",
    "admin", "info", "sales", "team", "help", "service", "enquir", "inquir",
    "office", "apply", "contact", "payroll", "finance", "invoice", "account",
    "marketing", "press", "media", "legal", "privacy", "people", "reception",
    "general", "hello", "workforce", "resourcing", "personnel", "fundrais",
    "donat", "legac", "volunteer", "member", "librar", "event", "corporate",
    "training", "course", "admission", "alumni", "booking", "ticket", "shop",
    "retail", "estates", "facilit", "procure", "supplier", "tender",
    "compliance", "quality", "safety", "hse", "communit", "partnership",
    "sponsor", "welfare", "advice", "referral")
ROLE_WORDS_LONG = tuple(w for w in ROLE_WORDS if len(w) >= 5)
ROLE_WORDS_SHORT = tuple(w for w in ROLE_WORDS if len(w) < 5)

# Words that name somewhere rather than someone.
PLACE_WORDS = ("canada", "america", "americas", "africa", "asia", "europe",
               "emea", "apac", "middleeast", "nordics", "benelux", "iberia",
               "usa", "uk", "eu", "india", "china", "japan", "brazil",
               "mexico", "australia", "singapore", "malaysia", "norway",
               "netherlands", "germany", "france", "spain", "italy", "poland",
               "romania", "houston", "dubai", "aberdeen", "london", "glasgow",
               "edinburgh", "manchester", "bristol", "leeds", "cardiff",
               "belfast", "scotland", "england", "wales", "ireland", "north",
               "south", "east", "west", "global", "international", "worldwide")

COMMON_WORDS = {"help", "team", "here", "click", "more", "news", "home", "main",
                "shop", "data", "site", "post", "west", "east", "north", "south"}

VOWELS = "aeiouy"
ONSETS = {"ch", "sh", "th", "ph", "wh", "br", "cr", "dr", "fr", "gr", "pr",
          "tr", "bl", "cl", "fl", "gl", "pl", "sl", "sm", "sn", "sp", "st",
          "sc", "sk", "sw", "tw", "kn", "wr", "rh", "kh", "gw", "vl", "dw",
          "qu"}
LONG_ONSETS = {"chr", "thr", "shr", "spr", "str", "scr", "phr", "sch", "sph"}

PLAUSIBLE_TLDS = (".com", ".co.uk", ".uk", ".org", ".org.uk", ".net", ".io",
                  ".scot", ".eu", ".ie", ".no", ".nl", ".de", ".fr", ".dk",
                  ".se", ".fi", ".it", ".es", ".ltd", ".plc", ".group",
                  ".energy", ".london", ".wales")

_SPLIT = re.compile(r"[._\-]")
_SPLIT_NUM = re.compile(r"[._\-0-9]+")


def plausible_first_name(word: str) -> bool:
    """Can we greet somebody with this? 'jane' and 'chris' yes, 'jsmith' no.

    Getting a name wrong is worse than a plain 'Hi,', so this errs towards
    refusing. It works on English syllable shape: a name has a vowel in
    second position, or opens with a real consonant cluster.
    """
    if len(word) < 3 or not word.isalpha():
        return False
    if word[1] in VOWELS:
        return True
    if word[:3] in LONG_ONSETS:
        return True
    return word[:2] in ONSETS and word[2] in VOWELS


def place_segments(local: str) -> list[str]:
    """The parts of an address naming somewhere rather than someone.

    Whole segments, matched exactly. A prefix rule looks tidier and is wrong:
    'frances' starts with 'france', and a woman called Frances is not a
    regional office.
    """
    return [seg for seg in _SPLIT_NUM.split(local) if seg in PLACE_WORDS]


def is_place(local: str) -> bool:
    return bool(place_segments(local))


def is_home_place(local: str, home_places: tuple[str, ...] = ()) -> bool:
    """A regional inbox the user would actually want.

    Their own city's office beats one on another continent, and both beat a
    person who is not there. Derived from the user's own search locations
    rather than hardcoded, which is the whole difference between this and the
    machine it came from.
    """
    if not home_places:
        return False
    return any(seg.startswith(tuple(home_places)) for seg in place_segments(local))


def has_role_word(local: str) -> bool:
    """Short words need a boundary: 'hr' sits inside 'chris', so matching it
    anywhere would refuse to greet a man called Chris Brown."""
    if is_place(local):
        return True
    if any(word in local for word in ROLE_WORDS_LONG):
        return True
    for seg in (s for s in _SPLIT_NUM.split(local) if s):
        if any(seg.startswith(w) or seg.endswith(w) for w in ROLE_WORDS_SHORT):
            return True
    return False


# Departments that are real, staffed, and must never receive a job
# application. Writing to investor relations or the complaints desk about a
# vacancy is not a near miss - it is the wrong building, and it is the kind
# of thing an employer remembers about a candidate.
#
# Matched as WHOLE SEGMENTS, never as substrings. A man called Boardman is
# not the board, and Frances is not France - the same rule the place list
# already learned the hard way.
NEVER_WRITE_TO = frozenset((
    "complaint", "complaints", "investor", "investors", "investorrelations",
    "shareholder", "shareholders", "board", "trustee", "trustees",
    "governance", "audit", "ombudsman", "dispute", "disputes", "refund",
    "refunds", "billing", "feedback", "escalation", "escalations", "chair",
    "chairman", "secretary", "customer", "customers", "returns",
    "dpo", "gdpr", "foi",
))


def never_write_to(local: str) -> bool:
    return any(seg in NEVER_WRITE_TO
               for seg in re.split(r"[._\-0-9]+", local) if seg)


def is_personal(local: str) -> bool:
    """True only if the local part belongs to an individual.

    jane.smith, j.smith and jane qualify; careers and info do not.
    """
    if has_role_word(local):
        return False
    if local in GENERIC_PREFIXES or local in HIRING_PREFIXES:
        return False
    if local.startswith(BAD_PREFIXES):
        return False
    parts = [p for p in _SPLIT.split(local) if p]
    if not parts or not all(p.isalpha() for p in parts):
        return False
    if len(parts) >= 2:
        return any(len(p) >= 3 for p in parts)
    single = parts[0]
    return 3 <= len(single) <= 12 and single not in COMMON_WORDS


def name_from_email(local: str) -> str | None:
    """A first name to greet with, or None.

    Only ever taken from the address as written. Nothing is inferred and
    nothing is invented.
    """
    parts = [p for p in _SPLIT.split(local) if p]
    if not parts or len(parts[0]) <= 2 or not parts[0].isalpha():
        return None      # 'j.smith' is an initial, not a name
    if len(parts) == 1 and not plausible_first_name(parts[0]):
        return None      # 'jsmith' is a surname with an initial stuck on
    return parts[0].capitalize()


def classify(address: str) -> tuple[int, str | None]:
    """(tier, first_name). 3 named > 2 hiring > 1 generic > 0 unusable."""
    local = address.split("@")[0].lower()
    if never_write_to(local):
        return 0, None
    if is_personal(local):
        return 3, name_from_email(local)
    # A hiring word anywhere counts: 'mysupporthr' is still an HR inbox.
    if local.startswith(HIRING_PREFIXES) or any(w in local for w in HIRING_PREFIXES):
        return 2, None
    if local.startswith(GENERIC_PREFIXES) or any(w in local for w in GENERIC_PREFIXES):
        return 1, None
    # A regional inbox is a real desk, just not a person's. The one thing that
    # must never happen is greeting it by name.
    if is_place(local):
        return 1, None
    return 0, None


def clean_emails(raw, domain: str | None = None) -> list[str]:
    """Filter scraped strings down to addresses worth considering."""
    out = []
    for candidate in {x.lower().strip(" .,;:'\"<>()") for x in raw}:
        if candidate.count("@") != 1:
            continue
        local, _, host = candidate.partition("@")
        if not local or local.startswith(BAD_PREFIXES):
            continue
        if host in BAD_DOMAINS or host.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")):
            continue
        if domain and host != domain and not host.endswith("." + domain):
            continue
        out.append(candidate)
    return sorted(out)


def plausible_domain(domain: str) -> bool:
    return bool(domain) and domain.lower().endswith(PLAUSIBLE_TLDS)


def rank(candidates, home_places: tuple[str, ...] = ()) -> list[dict]:
    """Every usable address, best first.

    Sorted by tier, then by whether it is the user's own region. Ties keep
    their input order, so an address printed in the advert - which the caller
    passes first, and which replied at 67% - stays ahead of a scraped one of
    the same tier.
    """
    out = []
    for address in candidates:
        tier, name = classify(address)
        if tier < 1:
            continue
        local = address.split("@")[0].lower()
        out.append({"email": address, "name": name, "tier": tier,
                    "tier_name": TIER_NAMES[tier],
                    "home": is_home_place(local, home_places)})
    out.sort(key=lambda c: (-c["tier"], not c["home"]))
    return out


def best(candidates, home_places: tuple[str, ...] = ()) -> dict | None:
    ranked = rank(candidates, home_places)
    return ranked[0] if ranked else None


def home_places_from(profile) -> tuple[str, ...]:
    """The user's own patch, lowercased, for the regional-inbox preference."""
    places = [p.split(",")[0].strip().lower()
              for p in (list(profile.locations) + [profile.location]) if p]
    return tuple(sorted({p for p in places if p}))
