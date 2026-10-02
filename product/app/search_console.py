"""What Google's Search Console says about this site, turned into a to-do list.

Search Console reports, for every search where one of these pages appeared:
the words typed, the page shown, how often it was shown (impressions), how
often it was clicked, and where it sat in the results. On its own that is a
spreadsheet. What sites that grow from search do with it every week is a
short set of standard checks, and this runs them:

  - **Nearly there.** A search where a page sits at positions 8 to 20 is on
    the bottom of page one or on page two, where hardly anybody looks. A
    small improvement to that page (answer that exact question in its first
    paragraph, link to it from a stronger page) is the cheapest traffic
    there is, because Google already thinks the page is relevant.
  - **Seen but not clicked.** A page shown often whose click rate is well
    under what its position normally gets. The page is ranking; its title
    and description are not persuading. Rewrite those, nothing else.
  - **Questions without a page.** Searches the site appears for that no
    answer page is about. Each is somebody telling us, in their own words,
    which page to write next.
  - **Two pages, one search.** The same search landing on two pages splits
    Google's opinion between them. Pick one, and point the other at it.
  - **Rising and falling.** Pages whose clicks moved most against the 28
    days before, so a drop is noticed in a week rather than a quarter.

The weekly job (.github/workflows/search_console.yml) stores the result in
site_meta, the admin page shows it, and the Monday growth check turns the top
items into a change for Harry to approve. Nothing here edits the site.

Credentials: a Google Cloud service account, added as a user on the Search
Console property, with its JSON key in GSC_SERVICE_ACCOUNT_JSON. Read-only
scope. Dormant until that is set. Switch: SEARCH_CONSOLE_ENABLED.
"""

from __future__ import annotations

import base64
import datetime
import json
import re
import time
from urllib.parse import quote

from jobseeker import settings

from . import answers, config, db

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://www.googleapis.com/webmasters/v3/sites"
META_KEY = "search_console_report"

# Search Console's figures settle about three days behind.
LAG_DAYS = 3
WINDOW_DAYS = 28

NEARLY_THERE = (8.0, 20.0)
MIN_IMPRESSIONS = 20
# A rough click rate by position on a results page, used only to spot a page
# far below it. An internal yardstick, never shown on the public site.
TYPICAL_CTR = {1: 0.28, 2: 0.15, 3: 0.10, 4: 0.07, 5: 0.05, 6: 0.04,
               7: 0.03, 8: 0.025, 9: 0.02, 10: 0.02}
LISTED = 10

STOPWORDS = frozenset(
    "a an and are be can do does for from get how i if in is it my of on or "
    "should the to what when where which who why will with you your uk".split())


def enabled() -> bool:
    return (settings.flag("SEARCH_CONSOLE_ENABLED")
            and bool(settings.text("GSC_SERVICE_ACCOUNT_JSON")))


def site_url() -> str:
    """The property as it was verified: the URL-prefix one, with its slash."""
    return (settings.text("GSC_SITE_URL")
            or config.BASE_URL.rstrip("/") + "/")


# ----------------------------------------------------------------------
# talking to Google
# ----------------------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _assertion(account: dict, now: int) -> str:
    """A signed JWT, the service-account way of asking for a token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iss": account["client_email"], "scope": SCOPE,
              "aud": account.get("token_uri",
                                 "https://oauth2.googleapis.com/token"),
              "iat": now, "exp": now + 3600}
    signing = (_b64(json.dumps(header, separators=(",", ":")).encode()) + "."
               + _b64(json.dumps(claims, separators=(",", ":")).encode()))
    key = serialization.load_pem_private_key(
        account["private_key"].encode(), password=None)
    signature = key.sign(signing.encode(), padding.PKCS1v15(),
                         hashes.SHA256())
    return signing + "." + _b64(signature)


def _token(post, account: dict) -> str:
    r = post(account.get("token_uri", "https://oauth2.googleapis.com/token"),
             data={"grant_type":
                   "urn:ietf:params:oauth:grant-type:jwt-bearer",
                   "assertion": _assertion(account, int(time.time()))},
             timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def fetch(start: datetime.date, end: datetime.date, *, post=None) -> list:
    """Rows of {query, page, clicks, impressions, ctr, position}."""
    if post is None:
        import httpx
        post = httpx.post
    account = json.loads(settings.text("GSC_SERVICE_ACCOUNT_JSON"))
    token = _token(post, account)
    r = post(f"{API}/{quote(site_url(), safe='')}/searchAnalytics/query",
             headers={"Authorization": f"Bearer {token}"},
             json={"startDate": start.isoformat(),
                   "endDate": end.isoformat(),
                   "dimensions": ["query", "page"],
                   "rowLimit": 5000},
             timeout=60)
    r.raise_for_status()
    rows = []
    for row in r.json().get("rows", []):
        query, page = row["keys"]
        rows.append({"query": query, "page": _path(page),
                     "clicks": int(row.get("clicks", 0)),
                     "impressions": int(row.get("impressions", 0)),
                     "ctr": float(row.get("ctr", 0.0)),
                     "position": float(row.get("position", 0.0))})
    return rows


def _path(url: str) -> str:
    m = re.match(r"https?://[^/]+(/[^?#]*)?", url or "")
    return (m.group(1) if m and m.group(1) else "/") if m else url


# ----------------------------------------------------------------------
# the checks
# ----------------------------------------------------------------------
def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in STOPWORDS and len(w) > 1}


def _answer_words() -> dict[str, set[str]]:
    return {f"/answers/{a.slug}": _words(a.question + " "
                                         + a.slug.replace("-", " "))
            for a in answers.ANSWERS}


def _best_answer(query: str, pages: dict[str, set[str]]) -> tuple[str, float]:
    words = _words(query)
    best, score = "", 0.0
    for path, have in pages.items():
        if not words or not have:
            continue
        overlap = len(words & have) / len(words)
        if overlap > score:
            best, score = path, overlap
    return best, score


def analyse(rows: list, previous: list | None = None) -> dict:
    """The to-do list, from one window of rows (and the one before it)."""
    total_clicks = sum(r["clicks"] for r in rows)
    total_impr = sum(r["impressions"] for r in rows)

    nearly = sorted(
        (r for r in rows
         if NEARLY_THERE[0] <= r["position"] <= NEARLY_THERE[1]
         and r["impressions"] >= MIN_IMPRESSIONS),
        key=lambda r: -r["impressions"])[:LISTED]

    unclicked = []
    for r in rows:
        slot = round(r["position"])
        typical = TYPICAL_CTR.get(slot)
        if (typical and r["impressions"] >= MIN_IMPRESSIONS
                and r["ctr"] < typical / 2):
            unclicked.append(dict(r, typical_ctr=typical))
    unclicked.sort(key=lambda r: -r["impressions"])

    pages = _answer_words()
    by_query: dict[str, dict] = {}
    for r in rows:
        q = by_query.setdefault(r["query"], {"query": r["query"],
                                             "impressions": 0, "clicks": 0,
                                             "pages": set()})
        q["impressions"] += r["impressions"]
        q["clicks"] += r["clicks"]
        q["pages"].add(r["page"])
    unanswered = []
    for q in by_query.values():
        best, score = _best_answer(q["query"], pages)
        if score < 0.5:
            unanswered.append({"query": q["query"],
                               "impressions": q["impressions"],
                               "clicks": q["clicks"],
                               "closest": best})
    unanswered.sort(key=lambda q: -q["impressions"])

    split = sorted(
        ({"query": q["query"], "pages": sorted(q["pages"]),
          "impressions": q["impressions"]}
         for q in by_query.values()
         if len(q["pages"]) > 1 and q["impressions"] >= MIN_IMPRESSIONS),
        key=lambda q: -q["impressions"])[:LISTED]

    movers = []
    if previous is not None:
        def per_page(rs):
            out: dict[str, int] = {}
            for r in rs:
                out[r["page"]] = out.get(r["page"], 0) + r["clicks"]
            return out
        now, before = per_page(rows), per_page(previous)
        for page in set(now) | set(before):
            change = now.get(page, 0) - before.get(page, 0)
            if change:
                movers.append({"page": page, "clicks": now.get(page, 0),
                               "before": before.get(page, 0),
                               "change": change})
        movers.sort(key=lambda m: -abs(m["change"]))

    top_pages: dict[str, dict] = {}
    for r in rows:
        p = top_pages.setdefault(r["page"], {"page": r["page"], "clicks": 0,
                                             "impressions": 0})
        p["clicks"] += r["clicks"]
        p["impressions"] += r["impressions"]

    return {
        "clicks": total_clicks, "impressions": total_impr,
        "queries": len(by_query),
        "top_pages": sorted(top_pages.values(),
                            key=lambda p: (-p["clicks"],
                                           -p["impressions"]))[:LISTED],
        "nearly_there": nearly,
        "seen_not_clicked": unclicked[:LISTED],
        "unanswered": unanswered[:LISTED],
        "split": split,
        "movers": movers[:LISTED],
    }


# ----------------------------------------------------------------------
# the weekly run
# ----------------------------------------------------------------------
def run(*, today: datetime.date | None = None, fetch_rows=None) -> dict:
    if not enabled():
        return {"reason": "off"}
    today = today or datetime.date.today()
    end = today - datetime.timedelta(days=LAG_DAYS)
    start = end - datetime.timedelta(days=WINDOW_DAYS - 1)
    prev_end = start - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=WINDOW_DAYS - 1)
    get = fetch_rows or fetch
    report = analyse(get(start, end), get(prev_start, prev_end))
    report.update({"from": start.isoformat(), "to": end.isoformat(),
                   "at": int(time.time())})
    db.set_meta(META_KEY, json.dumps(report))
    return report


def latest() -> dict | None:
    raw = db.get_meta(META_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def main() -> int:
    db.init()
    report = run()
    if report.get("reason") == "off":
        print("[search-console] off: GSC_SERVICE_ACCOUNT_JSON is not set")
        return 0
    print(f"[search-console] {report['from']} to {report['to']}: "
          f"{report['impressions']} impressions, {report['clicks']} clicks, "
          f"{report['queries']} searches; "
          f"{len(report['nearly_there'])} nearly there, "
          f"{len(report['seen_not_clicked'])} seen not clicked, "
          f"{len(report['unanswered'])} without a page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
