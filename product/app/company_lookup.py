"""Find a real address from a company name alone, for the free /find page.

Pasting an advert means finding the advert, copying all of it and pasting
it, and most people give up before the paste. Typing an employer's name is
one step. This does what the sweep does for every listing (find the
company's own website, read the pages where firms print their addresses) but
sized for somebody waiting on a page:

  - the domain comes from the same confident-match lookup the sweep uses
    (discover.find_domain), never from what the visitor typed, so nobody
    can point this server at a site of their choosing
  - that domain must resolve to a public address, never an internal one
  - seven pages at most, fetched at once, 5 seconds each, 20 overall
  - each company's answer is kept for a week, so a small firm's site is
    read once however many people look it up
  - addresses are only ever ones written on the company's own pages. If
    there are none, the answer is "none", and nothing is guessed
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor, wait

from jobseeker.names import company_key
from jobseeker.pipeline import contacts, discover

PATHS = ("", "/contact", "/contact-us", "/careers", "/jobs", "/about", "/team")
PAGE_TIMEOUT = 5
OVERALL_TIMEOUT = 20
CACHE_SECONDS = 7 * 86400


def _public(domain: str) -> bool:
    """Every address the domain resolves to is on the public internet."""
    try:
        infos = socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def _same_site(url: str, domain: str) -> bool:
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme in ("http", "https") and host in (domain,
                                                          "www." + domain)


def _head(page: str) -> str:
    """The home page's title and description: what the firm says it is."""
    import html as _html
    import re
    page = page[:100_000]
    bits = re.findall(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)[:1]
    for name in ("description", "og:description"):
        n = re.escape(name)
        bits += (re.findall(r'<meta[^>]+(?:name|property)=["\']' + n
                            + r'["\'][^>]*content=["\']([^"\']*)', page, re.I)
                 or re.findall(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*'
                               r'(?:name|property)=["\']' + n + r'["\']', page, re.I))[:1]
    return _html.unescape(" ".join(" ".join(bits).split()))[:600]


def _says_agency(page: str) -> bool:
    """Does the home page read like a recruitment agency's? Their titles
    often give nothing away ("Pioneering People", "Cathcart Technology"),
    but the page does: what they call themselves, or the two doors every
    agency site has, one for candidates and one for employers."""
    import re
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page[:300_000],
                  flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text).lower()
    if re.search(r"\brecruitment (agency|agencies|consultancy|consultants?|"
                 r"specialists?|company|business|firm|partner|solutions|"
                 r"services)\b|\bemployment (agency|business)\b|"
                 r"\bspecialist recruit|\bexecutive search\b|\bheadhunt|"
                 r"\bsupply (teachers?|staff)\b|\bstaffing (agency|solutions|"
                 r"services)\b", text):
        return True
    # Counting "candidates" and "clients" is not enough: a care provider
    # calls the people it looks after clients, and talks to candidates on
    # the same page (Keystone Care was taken down on 8 October that way).
    # The two labelled doors are the agency's tell.
    return bool(re.search(r"\bfor (candidates|job ?seekers)\b", text)
                and re.search(r"\bfor (employers|hiring managers|"
                              r"companies|businesses)\b", text))


def _read(domain: str, path: str, get) -> dict:
    """One page: where it ended up, the addresses on it, and what kind of
    answer it was (see learning.kind_of_response). Redirects are followed
    only while they stay on the company's own domain: a redirect elsewhere
    is somewhere the visitor could not have pointed us, and is not
    followed."""
    from urllib.parse import urljoin
    from . import learning
    home = not path
    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}{path}"
        try:
            for _ in range(3):
                r = get(url, headers=discover.UA, timeout=PAGE_TIMEOUT,
                        allow_redirects=False)
                status = getattr(r, "status_code", 0)
                if status in (301, 302, 303, 307, 308):
                    nxt = urljoin(url, (r.headers or {}).get("location", ""))
                    if not _same_site(nxt, domain):
                        return {"url": url, "emails": [], "head": "",
                                "agency": False, "kind": learning.AWAY}
                    url = nxt
                    continue
                if status == 200:
                    text = r.text or ""
                    found = discover.emails_in(text)
                    return {"url": url, "emails": found,
                            "head": _head(text) if home else "",
                            "agency": _says_agency(text) if home else False,
                            "kind": learning.kind_of_response(
                                200, bool(contacts.clean_emails(found, domain)))}
                return {"url": url, "emails": [], "head": "", "agency": False,
                        "kind": learning.kind_of_response(status, False)}
        except Exception:
            continue
    return {"url": "", "emails": [], "head": "", "agency": False,
            "kind": learning.ERROR}


def lookup(company: str, *, get=None, find_domain=None, resolves=None,
           store=None, retry_unknown: bool = False,
           fresh: bool = False) -> dict:
    """{"domain": str, "emails": [..], "found_on": {email: page url},
    "about": the home page's title and description};
    domain "" when the company's own site could not be identified with
    confidence. A fresh answer is also offered to the public employer
    directory (directory.py), which keeps only shared role inboxes.

    retry_unknown: a kept answer of "no website found" is not trusted. The
    directory builder searches harder than /find does, so a miss there is
    worth another look. fresh: ignore any kept answer (a re-check after
    the publishing rules change)."""
    key = f"find:{company_key(company)}"
    if store is not None and not fresh:
        try:
            saved = store.get(key)
            if saved:
                data = json.loads(saved)
                if time.time() - data.get("at", 0) < CACHE_SECONDS \
                        and (data.get("domain") or not retry_unknown):
                    return data
        except Exception:
            pass

    domain = (find_domain or discover.find_domain)(company) or ""
    found_by = discover.LAST_FOUND_BY.pop(company_key(company), "") \
        or ("clearbit" if domain and not find_domain else "")
    emails: list[str] = []
    found_on: dict[str, str] = {}
    about = ""
    agency_signs = False
    tried: dict[str, str] = {}
    from . import learning
    if domain and learning.resting(domain):
        # It refused us or did not answer recently. Not "no address": the
        # page it may already have in the directory is left as it is.
        tried = {"": learning.REFUSED}
    elif domain and (resolves or _public)(domain):
        if get is None:
            import requests
            get = requests.get
        paths = [p for p in PATHS if (p or "/") not in learning.skip(domain)]
        pool = ThreadPoolExecutor(max_workers=len(paths) or 1)
        futures = {p: pool.submit(_read, domain, p, get) for p in paths}
        done, _ = wait(futures.values(), timeout=OVERALL_TIMEOUT)
        pool.shutdown(wait=False, cancel_futures=True)
        raw: list[str] = []
        for path, f in futures.items():
            if f not in done or f.exception():
                tried[path] = learning.ERROR
                continue
            page = f.result()
            tried[path] = page["kind"]
            about = about or page["head"]
            agency_signs = agency_signs or page["agency"]
            raw += page["emails"]
            for address in contacts.clean_emails(page["emails"], domain):
                found_on.setdefault(address, page["url"])
        emails = contacts.clean_emails(raw, domain)
        learning.remember(domain, tried, found_by=found_by,
                          agency=agency_signs)
    elif domain:
        domain = ""          # resolves somewhere private: treat as unknown

    result = {"domain": domain, "emails": emails, "found_on": found_on,
              "about": about, "agency_signs": agency_signs,
              # The site was there but nothing answered: an outage or a
              # refusal, which says nothing about whether its address went.
              "unreachable": bool(domain and tried
                                  and not learning.reachable(tried)),
              "at": int(time.time())}
    if store is not None:
        try:
            store.put(key, json.dumps(result))
        except Exception:
            pass
        try:
            from . import directory
            directory.record(company, result)
        except Exception:
            pass
    return result
