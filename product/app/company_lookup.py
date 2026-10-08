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
    agency site has, one for candidates and one for clients."""
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
    if re.search(r"\bfor (candidates|job ?seekers)\b", text) and \
            re.search(r"\bfor (clients|employers|hiring managers)\b", text):
        return True
    return (len(re.findall(r"\bcandidates?\b", text)) >= 2
            and len(re.findall(r"\bclients?\b", text)) >= 2
            and bool(re.search(r"\b(vacancies|latest jobs|job search|"
                               r"search jobs|register (your )?cv|upload "
                               r"(your )?cv|submit (your )?cv)\b", text)))


def _read(domain: str, path: str, get) -> tuple[str, list[str], str, bool]:
    """(the page it ended up on, the addresses on it). Redirects are followed only while they stay on
    the company's own domain: a redirect elsewhere is somewhere the visitor
    could not have pointed us, and is not followed."""
    from urllib.parse import urljoin
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
                        return url, [], "", False
                    url = nxt
                    continue
                if status == 200:
                    text = r.text or ""
                    home = not path
                    return (url, discover.emails_in(text),
                            _head(text) if home else "",
                            _says_agency(text) if home else False)
                return url, [], "", False
        except Exception:
            continue
    return "", [], "", False


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
    emails: list[str] = []
    found_on: dict[str, str] = {}
    about = ""
    agency_signs = False
    if domain and (resolves or _public)(domain):
        if get is None:
            import requests
            get = requests.get
        pool = ThreadPoolExecutor(max_workers=len(PATHS))
        futures = [pool.submit(_read, domain, p, get) for p in PATHS]
        done, _ = wait(futures, timeout=OVERALL_TIMEOUT)
        pool.shutdown(wait=False, cancel_futures=True)
        raw: list[str] = []
        for f in futures:
            if f in done and not f.exception():
                page, found, head, says = f.result()
                about = about or head
                agency_signs = agency_signs or says
                raw += found
                for address in contacts.clean_emails(found, domain):
                    found_on.setdefault(address, page)
        emails = contacts.clean_emails(raw, domain)
    elif domain:
        domain = ""          # resolves somewhere private: treat as unknown

    result = {"domain": domain, "emails": emails, "found_on": found_on,
              "about": about, "agency_signs": agency_signs,
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
