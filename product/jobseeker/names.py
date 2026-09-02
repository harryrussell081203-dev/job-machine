"""Deciding when two company names are the same company.

Used for two jobs that both hurt real people when they go wrong:

  - **"one email per employer, ever."** Too loose and somebody gets the same
    pitch twice from the same stranger.
  - **matching a company to its website.** Too loose and the application goes
    to an entirely different firm.

The two want opposite errors, so they get different functions. `company_key`
over-matches on purpose: a false collision costs one unsent email, a miss
costs somebody an unwanted second one. `name_tokens` feeds the domain match,
which is where being too generous has already sent mail to the wrong company
on another continent - see CORPORATE_WORDS below.
"""

from __future__ import annotations

import re

# Words that do not distinguish one company from another. Stripped anywhere in
# the name, not only at the end: "Ltd Acme Group Services" and "Acme" are the
# same firm, and an endswith rule catches neither.
NOISE = re.compile(
    r"\b(ltd|limited|plc|llp|inc|incorporated|group|holdings|uk|"
    r"international|services|solutions|recruitment|the|and)\b")

# Words a company can legitimately add to its own name without becoming a
# different company. Anything outside this list is a business, not a suffix.
#
# The distinction is not academic. "Grace May", a recruiter, matched "Grace and
# May Home" and an IT support application went to a home furnishings shop. The
# extra word is what tells you which it is: "Baker Hughes Company" adds
# "company" and is the same firm; "Grace and May Home" adds "home" and is not.
CORPORATE_WORDS = {
    "company", "co", "corp", "corporation", "incorporated", "llc", "lp",
    "partners", "partnership", "associates", "consulting", "consultancy",
    "technologies", "technology", "systems", "engineering", "industries",
    "industrial", "energy", "marine", "offshore", "subsea", "global",
    "worldwide", "europe", "emea", "scotland", "north", "sea", "gb",
    "britain", "british", "group", "holdings", "plc", "ltd", "limited",
}

# Words a company can bolt onto its own name in a domain. "sanctuary" ->
# "sanctuarygroup.co.uk" is the same housing association; "sanctuary" ->
# "sanctuaryclothing.com" is a clothing brand in California, and an
# application came within one run of landing there.
DOMAIN_SUFFIXES = ("group", "uk", "ltd", "limited", "plc", "co", "com",
                   "global", "int", "international", "energy", "services",
                   "eng", "engineering", "tech", "technologies", "online")


def company_key(name: str) -> str:
    """Normalised identity, so 'ACME Ltd.' and 'Acme Limited' collide.

    Spaces are kept: name_tokens needs them, and they cost nothing here.
    """
    text = (name or "").lower()
    # Apostrophes are dropped rather than spaced. "O'Brien" and "OBrien" are
    # one firm, and splitting them into "o brien" leaves them apart - which
    # would let the same employer be written to twice. Common enough in UK
    # trade names (O'Neill, O'Connor, D'Arcy) to be worth the special case.
    text = text.replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def name_tokens(name: str) -> set[str]:
    """The distinguishing words of a company name."""
    return {t for t in company_key(name).split() if t}


def same_company(a: str, b: str) -> bool:
    return bool(company_key(a)) and company_key(a) == company_key(b)
