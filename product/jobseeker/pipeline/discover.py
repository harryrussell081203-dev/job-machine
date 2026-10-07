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

UA = {"User-Agent": "Mozilla/5.0 (compatible; recruited/1.0; +job search)"}

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


# ----------------------------------------------------------------------
# A wider search, for the public employer directory only.
#
# Clearbit's free autocomplete knows few UK employers: the directory builder
# read 40 companies a day and found a website for one (Booker Group, Holiday
# Inn and Bannatyne among the misses). Two more sources, both checkable:
#
#   - Wikidata's "official website" for an entity whose name is exactly the
#     company's. Free, no key, and edited by people who check.
#   - The obvious addresses (bookergroup.com, clarkcontracts.co.uk), kept
#     ONLY when the site's own title or name says it is that company. A
#     parked domain, a redirect elsewhere or a near-miss name is dropped.
#
# The sweep that writes letters keeps using find_domain alone; a wrong firm
# there costs somebody an application. Here it would cost a page naming the
# wrong site, which is why the site's own words have to agree.
# ----------------------------------------------------------------------
WIKI_UA = {"User-Agent": "recruited/1.0 (https://recruited.org.uk; "
                         "UK employer directory)"}
WIKIDATA = "https://www.wikidata.org/w/api.php"
UK = "Q145"
GUESS_TLDS = (".co.uk", ".com", ".uk", ".org.uk")
UK_TLDS = (".co.uk", ".uk", ".org.uk")
PARKED = re.compile(r"domain (is )?for sale|buy this domain|parked|"
                    r"this domain|coming soon|under construction|"
                    r"account suspended|default web page|it works!",
                    re.I)
TITLE_SPLIT = re.compile(r"\s+[|\-–—:·•]\s+|\s*\|\s*")

# Which source answered, per run, so the log says what is working.
FOUND_BY: dict[str, int] = {}


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    if "//" not in url:
        url = "https://" + url
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def from_wikidata(company: str, *, session=None) -> str | None:
    """The official website of the Wikidata entity named exactly this."""
    wanted = company_key(company)
    tokens = name_tokens(company)
    if not wanted:
        return None
    import requests
    session = session or requests
    try:
        r = session.get(WIKIDATA, params={
            "action": "wbsearchentities", "search": company, "language": "en",
            "type": "item", "limit": 7, "format": "json"},
            headers=WIKI_UA, timeout=15)
        hits = r.json().get("search", [])
        ids = [h["id"] for h in hits
               if company_key(h.get("label", "")) == wanted
               or any(company_key(a) == wanted for a in h.get("aliases", []))]
        if not ids:
            return None
        r = session.get(WIKIDATA, params={
            "action": "wbgetentities", "ids": "|".join(ids[:5]),
            "props": "claims", "format": "json"}, headers=WIKI_UA, timeout=15)
        entities = r.json().get("entities", {})
    except Exception as exc:
        print(f"[discover] wikidata '{company}': {exc}")
        return None

    def values(claims, prop):
        out = []
        for c in claims.get(prop, []):
            v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            out.append(v.get("id") if isinstance(v, dict) else v)
        return [v for v in out if v]

    for qid in ids:
        claims = (entities.get(qid) or {}).get("claims") or {}
        sites = [_host(u) for u in values(claims, "P856") if isinstance(u, str)]
        sites = [s for s in sites if contacts.plausible_domain(s)]
        if not sites:
            continue
        # One word is not a company ("Sanctuary"). Only a UK one will do.
        if len(tokens) < 2 and UK not in values(claims, "P17") \
                and not sites[0].endswith(UK_TLDS):
            continue
        return sites[0]
    return None


def guesses(company: str) -> list[str]:
    """The addresses a company called this would most likely have. Nothing
    is used unless the site itself confirms it - see site_says."""
    key = company_key(company).split()
    raw = re.sub(r"[^a-z0-9 ]", " ", (company or "").lower()
                 .replace("'", "").replace("’", "")).split()
    raw = [w for w in raw if w not in ("ltd", "limited", "plc", "llp", "the",
                                       "and", "uk")]
    stems = []
    for words in (key, raw):
        if words:
            stems += ["".join(words)]
            if len(words) > 1:
                stems += ["-".join(words)]
    stems = [s for s in dict.fromkeys(stems) if 3 <= len(s) <= 40]
    single = len(name_tokens(company)) < 2
    tlds = UK_TLDS if single else GUESS_TLDS
    return [s + t for s in stems for t in tlds]


def _names_on(page: str) -> list[str]:
    """What a home page calls itself: title, site name, schema.org name."""
    out = []
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    if m:
        out.append(m.group(1))
    for prop in ("og:site_name", "application-name", "og:title"):
        m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop)
                      + r'["\'][^>]*content=["\']([^"\']+)', page, re.I)
        if m:
            out.append(m.group(1))
    out += re.findall(r'"@type"\s*:\s*"(?:Organization|Corporation|'
                      r'LocalBusiness)"[^{}]*?"name"\s*:\s*"([^"]+)"', page)
    import html as _html
    names = []
    for text in out:
        text = _html.unescape(" ".join(text.split()))
        names.append(text)
        names += [p for p in TITLE_SPLIT.split(text) if p.strip()]
    return names


def site_says(company: str, domain: str, page: str) -> bool:
    """Does this home page name itself as the company? Exact name, or the
    same words plus nothing but 'Group', 'Ltd' and the like."""
    if not page or PARKED.search(page[:5000]):
        return False
    wanted = company_key(company)
    for name in _names_on(page):
        if company_key(name) == wanted:
            return True
        if len(name_tokens(company)) >= 2 and len(name) <= 80 \
                and domain_matches(company, name, domain):
            return True
    return False


def _resolves(domain: str) -> bool:
    import socket
    try:
        return bool(socket.getaddrinfo(domain, 443))
    except OSError:
        return False


def from_guess(company: str, *, session=None, resolves=None) -> str | None:
    import requests
    from concurrent.futures import ThreadPoolExecutor
    session = session or requests
    candidates = guesses(company)
    if not candidates:
        return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        live = [d for d, ok in zip(candidates, pool.map(
            resolves or _resolves, candidates)) if ok]
    for domain in live:
        if not contacts.plausible_domain(domain):
            continue
        for url in (f"https://www.{domain}/", f"https://{domain}/"):
            try:
                r = session.get(url, headers=UA, timeout=8)
            except Exception:
                continue
            if getattr(r, "status_code", 0) != 200:
                continue
            # A redirect to somebody else's site is not this company's site.
            if _host(getattr(r, "url", "") or url) != domain:
                break
            if site_says(company, domain, (r.text or "")[:200_000]):
                return domain
            break
    return None


# Another country's own suffix. The directory is for UK jobs, and the first
# live run matched "Vigilant Security" (an Edinburgh advert) to an Irish firm.
FOREIGN = (".ie", ".no", ".nl", ".de", ".fr", ".dk", ".se", ".fi", ".it",
           ".es", ".eu", ".au", ".nz", ".ca", ".us", ".in")


def _home_says(company: str, domain: str, session) -> bool:
    for url in (f"https://www.{domain}/", f"https://{domain}/"):
        try:
            r = session.get(url, headers=UA, timeout=8)
        except Exception:
            continue
        if getattr(r, "status_code", 0) == 200:
            return site_says(company, domain, (r.text or "")[:200_000])
    return False


def find_domain_wide(company: str, *, session=None, resolves=None) -> str | None:
    """find_domain, then Wikidata, then the obvious addresses checked
    against the site's own name. For the employer directory."""
    if not name_tokens(company):
        return None
    import requests
    web = session or requests

    def clearbit():
        domain = find_domain(company, session=session)
        # One word names nobody: "Vita Group" (Edinburgh) came back as a
        # German health software firm. Only if the site says it is them.
        if domain and len(name_tokens(company)) < 2 \
                and not _home_says(company, domain, web):
            return None
        return domain

    for source, fn in (("clearbit", clearbit),
                       ("wikidata", lambda: from_wikidata(company, session=session)),
                       ("checked", lambda: from_guess(company, session=session,
                                                      resolves=resolves))):
        domain = fn()
        if domain and domain.endswith(FOREIGN):
            continue
        if domain:
            FOUND_BY[source] = FOUND_BY.get(source, 0) + 1
            return domain
    FOUND_BY["none"] = FOUND_BY.get("none", 0) + 1
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
