"""Is this advert from the employer, or from a recruitment agency?

It matters for who gets written to and what the letter says. An employer
has one job to fill; an agency has many, and the recruiter who reads the
letter can put the same person forward for this role and the next three.
Some people want only one kind, so the sweep can be told.

Neither board says so in its search results, so this reads the signals the
adverts carry themselves:

  - **The disclosure.** UK agencies are required to say whether they act as
    an "employment agency" or an "employment business", and most adverts end
    with exactly that sentence. It is the strongest signal there is.
  - **"Our client".** An agency writes about the employer in the third
    person, because it is not them.
  - **The name.** "Recruitment", "Resourcing", "Personnel", "Staffing" and
    the large household names.

A wrong guess costs little: the listing is still a real job with a real
address, it is only filed under the other heading.
"""

from __future__ import annotations

import re

AGENCY = "agency"
EMPLOYER = "employer"

DISCLOSURE = re.compile(
    r"acting as an? employment (agency|business)|"
    r"(is|are) an? (employment|recruitment) (agency|business)|"
    r"recruitment (agency|consultancy) (acting|working) on behalf|"
    r"on behalf of (our|my|a) client|\bour client\b|\bmy client\b|"
    r"\bclient of ours\b", re.I)

NAME_WORDS = re.compile(
    r"\b(recruitment|recruiting|recruiters?|resourcing|personnel|staffing|"
    r"employment (agency|services)|talent (solutions|acquisition)|"
    r"search (and|&) selection|selection (ltd|limited)|appointments|"
    r"jobs? (ltd|limited))\b", re.I)

# The large agencies, which often advertise under a bare brand name.
KNOWN = frozenset({
    "hays", "adecco", "randstad", "manpower", "pertemps", "matchtech",
    "morson", "gi group", "kelly services", "michael page", "page personnel",
    "robert walters", "harvey nash", "reed specialist recruitment",
    "blue arrow", "brook street", "office angels", "search consultancy",
    "cpl", "sanderson", "nes fircroft", "airswift", "orion group",
    "rullion", "jsm group", "cordant people", "staffline", "kenneth brian",
    "prospect resourcing", "progressive recruitment", "rise technical",
    "jac recruitment", "redline group", "service care solutions",
    "allstaff", "ernest gordon", "taskmaster", "the recruitment co",
    "adria solutions", "cobalt recruitment", "carrington west",
})


def _plain(name: str) -> str:
    name = re.sub(r"[^a-z0-9& ]", " ", (name or "").lower())
    name = re.sub(r"\b(ltd|limited|plc|llp|uk|group)\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def advertiser(company: str, description: str = "") -> str:
    """AGENCY or EMPLOYER."""
    if DISCLOSURE.search(description or ""):
        return AGENCY
    if NAME_WORDS.search(company or ""):
        return AGENCY
    plain = _plain(company)
    if plain and any(plain == k or plain.startswith(k + " ") for k in KNOWN):
        return AGENCY
    return EMPLOYER


PERMANENT = "permanent"
CONTRACT = "contract"
TEMP = "temp"

_TEMP = re.compile(r"\btemp(orary)?\b|\btemp[- ]to[- ]perm\b|\bseasonal\b|"
                   r"\bimmediate start\b.*\bweekly pay\b", re.I)
_CONTRACT = re.compile(r"\bcontract(or)?\b|\bfixed[- ]term\b|\bftc\b|"
                       r"\b\d+[- ]?(month|week)s?\b.{0,20}\b(contract|"
                       r"assignment|initially)\b|\boutside ir35\b|"
                       r"\binside ir35\b|\bday rate\b", re.I)
_PERMANENT = re.compile(r"\bpermanent\b|\bperm\b", re.I)


def contract(title: str, description: str = "", board: str = "") -> str:
    """PERMANENT, CONTRACT, TEMP, or "" when the advert doesn't say.

    The title wins over the text, and the board's own field (Adzuna has one)
    over both, except that Adzuna has no word for temporary work."""
    board = (board or "").lower()
    head = title or ""
    body = (description or "")[:2000]
    if _TEMP.search(head):
        return TEMP
    if _CONTRACT.search(head):
        return CONTRACT
    if board == "contract":
        return TEMP if _TEMP.search(body) else CONTRACT
    if board == "permanent":
        return PERMANENT
    if _PERMANENT.search(head):
        return PERMANENT
    if _TEMP.search(body):
        return TEMP
    if _CONTRACT.search(body):
        return CONTRACT
    if _PERMANENT.search(body):
        return PERMANENT
    return ""
