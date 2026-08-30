"""Finding a real address to write to, and refusing to invent one.

The single rule: **no address is ever guessed.** Not from a pattern that
worked at another company, not from a first name and a domain. Every address
must have been seen written down somewhere, or the listing gets no letter.

That rule costs applications - 516 of the original's listings ended here with
nothing sent - and it is still right. A guessed address bounces, and bounces
are what destroy the sending reputation that gets the next thirty letters
delivered.

Where addresses come from, in the order they are tried:

  1. **The advert itself.** 9 of these, 6 replies - 67%, the best source
     there is, and almost nobody uses it because everybody clicks Apply.
  2. **The company's own website**, once its domain is confidently matched.

Matching a company to a domain is where this gets dangerous, and the rules in
names.py exist because it has already gone wrong three times. Read the
comments there before loosening anything here.
"""

from __future__ import annotations

import re
import time

from ..names import CORPORATE_WORDS, DOMAIN_SUFFIXES, company_key, name_tokens
from . import contacts

UA = {"User-Agent": "Mozilla/5.0 (compatible; job-machine/1.0; +job search)"}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
MAILTO_RE = re.compile(r"mailto:([^\"'?>\s]+)")

# Where a small company actually puts an address.
SCRAPE_PATHS = ("", "/contact", "/contact-us", "/careers", "/jobs",
                "/join-us", "/about", "/about-us", "/team", "/our-team",
                "/people")

POLITE_DELAY = 0.5      # small sites do not deserve a hammering


def emails_in(text: str) -> list[str]:
    """Every address written down in a page or an advert."""
    found = EMAIL_RE.findall(text or "")
    found += [m.replace("%40", "@") for m in MAILTO_RE.findall(text or "")]
    return found


def domain_matches(company: str, hit_name: str, domain: str) -> bool:
    """Is this autocomplete hit really the company in the advert?

    Every rule here is a bug that shipped. See names.py for the three
    applications that went to the wrong firm before these existed.
    """
    if not contacts.plausible_domain(domain):
        return False

    wanted_key = company_key(company)
    if not wanted_key:
        return False
    if company_key(hit_name) == wanted_key:
        return True

    wanted = name_tokens(company)
    # A one-word company name identifies nobody. 'Sanctuary' the housing
    # association matched Sanctuary Clothing in California, and a named person
    # there. For a single token, nothing but an exact match will do.
    if len(wanted) < 2:
        return False

    hit = name_tokens(hit_name)
    # Whole words only: 'wood' must not match inside 'woodforest'.
    if not wanted <= hit:
        return False

    # Subset alone is not enough. 'Grace May' is a subset of 'Grace and May
    # Home', which is a furniture shop. What the match ADDS decides: a
    # corporate suffix means the same firm, a real word means a different one.
    return (hit - wanted) <= CORPORATE_WORDS


def find_domain(company: str, *, session=None) -> str | None:
    """The company's own domain, or None. Clearbit autocomplete: free, no key."""
    if not name_tokens(company):
        return None
    import requests
    session = session or requests
    try:
        r = session.get("https://autocomplete.clearbit.com/v1/companies/suggest",
                        params={"query": company}, headers=UA, timeout=15)
        r.raise_for_status()
        hits = r.json()
    except Exception as exc:
        print(f"[discover] clearbit '{company}': {exc}")
        return None

    if not isinstance(hits, list):
        return None
    for hit in hits:
        domain = (hit.get("domain") or "").lower()
        if domain and domain_matches(company, hit.get("name", ""), domain):
            return domain
    print(f"[discover] no confident domain for '{company}'")
    return None


def scrape_site(domain: str, *, session=None, paths=SCRAPE_PATHS,
                delay: float = POLITE_DELAY) -> list[str]:
    """Addresses written on the company's own pages."""
    import requests
    session = session or requests
    raw: list[str] = []
    for path in paths:
        for scheme in ("https", "http"):
            try:
                r = session.get(f"{scheme}://{domain}{path}", headers=UA,
                                timeout=12)
                if getattr(r, "status_code", 0) == 200:
                    raw += emails_in(r.text)
                break
            except Exception:
                continue
        if delay:
            time.sleep(delay)
    return contacts.clean_emails(raw, domain)


def discover(listing, profile, *, session=None, delay: float = POLITE_DELAY) -> dict | None:
    """The best real address for this listing, or None.

    Returns the contact dict compose() expects: email, name, tier, tier_name.
    None means no letter is written, which is a correct outcome and by far the
    most common one.
    """
    home = contacts.home_places_from(profile)

    # 1. The advert. Best odds of anything, and ordering matters: rank() is
    #    stable within a tier, so an address printed in the advert stays ahead
    #    of a scraped one the classifier rates the same.
    from_advert = contacts.clean_emails(emails_in(listing.description or ""))

    # 2. The company's own site.
    from_site: list[str] = []
    domain = find_domain(listing.company, session=session)
    if domain:
        from_site = scrape_site(domain, session=session, delay=delay)

    candidates = from_advert + [e for e in from_site if e not in from_advert]
    best = contacts.best(candidates, home)
    if not best:
        return None

    best["source"] = "listing" if best["email"] in from_advert else "scraped"
    best["domain"] = domain
    return best
