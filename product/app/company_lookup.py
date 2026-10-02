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


def _read(domain: str, path: str, get) -> tuple[str, list[str]]:
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
                        return url, []
                    url = nxt
                    continue
                if status == 200:
                    return url, discover.emails_in(r.text or "")
                return url, []
        except Exception:
            continue
    return "", []


def lookup(company: str, *, get=None, find_domain=None, resolves=None,
           store=None) -> dict:
    """{"domain": str, "emails": [..], "found_on": {email: page url}};
    domain "" when the company's own site could not be identified with
    confidence. A fresh answer is also offered to the public employer
    directory (directory.py), which keeps only shared role inboxes."""
    key = f"find:{company_key(company)}"
    if store is not None:
        try:
            saved = store.get(key)
            if saved:
                data = json.loads(saved)
                if time.time() - data.get("at", 0) < CACHE_SECONDS:
                    return data
        except Exception:
            pass

    domain = (find_domain or discover.find_domain)(company) or ""
    emails: list[str] = []
    found_on: dict[str, str] = {}
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
                page, found = f.result()
                raw += found
                for address in contacts.clean_emails(found, domain):
                    found_on.setdefault(address, page)
        emails = contacts.clean_emails(raw, domain)
    elif domain:
        domain = ""          # resolves somewhere private: treat as unknown

    result = {"domain": domain, "emails": emails, "found_on": found_on,
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
