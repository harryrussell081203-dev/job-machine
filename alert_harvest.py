"""
Harvest jobs out of Harry's own inbox.

WHY THIS EXISTS
---------------
Adzuna and Reed have APIs, and between them they yield a median of about four
in-trade Aberdeen listings a day. The boards that carry the rest of this market
- CV-Library, Totaljobs, s1jobs, Oil and Gas Job Search, Rigzone, employers'
own career pages - have no free API at all.

Every one of them will email you job alerts.

So the widest, cheapest source of listings is not an API and not a scraper: it
is the inbox those alerts already land in. Harry sets the alerts up once by
hand, on the sites, logged in as himself. From then on this reads his own mail
over the same IMAP connection the reply-watcher already uses, pulls the job
links out of the alert emails, and feeds them into the ordinary pipeline.

WHAT THIS DELIBERATELY IS NOT
-----------------------------
It does not log into Indeed, LinkedIn or any other site, and it never will.
Those platforms ban accounts for automated access, their bot detection is good,
and a banned account is a real loss to a real job search. Reading your own
email is not automating their site - the alert was sent to you, addressed to
you, because you asked for it.

SETTING IT UP (one evening, once)
---------------------------------
On each site: search for the roles and area you want, then save the search as a
DAILY email alert to the Gmail address the machine uses. That is the whole
integration. Every alert that arrives from then on is harvested automatically.
"""
import email as email_mod
import imaplib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import job_machine as jm

# Reading adverts is the slowest thing in the pipeline and it sits in front of
# everything that matters. One run fetched 88 of them one at a time, took
# forty-four minutes over it, and was killed by the workflow timeout before it
# discovered a single address or sent a single application - the whole run
# spent on the one stage whose output nothing was waiting for.
#
# So: a wall clock, and several at once. Whatever is not read this run is
# still 'new' and is read by the next one.
ENRICH_BUDGET_SECONDS = jm.env_int("ENRICH_BUDGET_SECONDS", 240)
ENRICH_WORKERS = jm.env_int("ENRICH_WORKERS", 8)
ENRICH_TIMEOUT = jm.env_int("ENRICH_TIMEOUT", 12)

# Senders whose mail is a job alert worth reading. Matched against the From
# header, so a personal email from someone at one of these firms is not
# mistaken for an alert - alerts come from no-reply style addresses.
ALERT_SENDERS = (
    "indeed.com", "reed.co.uk", "cv-library.co.uk", "totaljobs.com",
    "s1jobs.com", "oilandgasjobsearch.com", "rigzone.com", "jobsite.co.uk",
    "linkedin.com", "glassdoor", "adzuna", "monster.co.uk", "jobserve.com",
    "technojobs.co.uk", "myjobscotland", "energyjobline.com", "google.com",
    "workable.com", "greenhouse.io", "lever.co", "smartrecruiters.com",
    "ashbyhq.com", "teamtailor.com",
)

# A link that goes to a specific advert rather than to a login page, an
# unsubscribe form or the board's homepage.
JOB_LINK = re.compile(
    r"https?://[^\s\"'<>\)\]]+(?:/job|/jobs/|/vacanc|/viewjob|/job-detail|"
    r"/careers/|/o/|/j/|jk=|jobId=|jobid=|/position)[^\s\"'<>\)\]]*", re.I)
JUNK_LINK = re.compile(
    r"unsubscribe|/login|/signin|/register|/account|/settings|/preferences|"
    r"privacy|/help|/support|manage-alerts|email-preferences", re.I)

MAX_ALERTS_PER_RUN = jm.env_int("ALERT_MAX_PER_RUN", 40)
MAX_LINKS_PER_ALERT = jm.env_int("ALERT_MAX_LINKS", 25)
ALERT_LOOKBACK_DAYS = jm.env_int("ALERT_LOOKBACK_DAYS", 3)


def is_alert(from_header):
    low = (from_header or "").lower()
    return any(sender in low for sender in ALERT_SENDERS)


def job_links(text):
    """Advert links from one alert email, deduplicated, junk removed."""
    seen, out = set(), []
    for url in JOB_LINK.findall(text or ""):
        url = url.rstrip(".,);'\"")
        if JUNK_LINK.search(url):
            continue
        # alert links carry tracking junk; the path is what identifies the job
        key = re.sub(r"[?&](utm_|src|from|token|sig|hl|mid|eid)[^&]*", "", url)
        key = key.split("?")[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= MAX_LINKS_PER_ALERT:
            break
    return out


def board_of(url):
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    host = re.sub(r"^(www|uk|jobs|apply|careers)\.", "", host)
    return host or "unknown"


def alert_id(url):
    """A stable id for a listing found in an alert, so the same advert arriving
    in tomorrow's alert as well does not become a second job."""
    import hashlib
    key = re.sub(r"[?#].*$", "", url or "").rstrip("/").lower()
    return "alert-" + hashlib.sha1(key.encode()).hexdigest()[:12]


def fetch_alerts(conn, days=None):
    """[(from, subject, text, date)] for recent alert mail."""
    since = datetime.now(timezone.utc) - timedelta(
        days=days if days is not None else ALERT_LOOKBACK_DAYS)
    status, data = conn.search(None, "SINCE", since.strftime("%d-%b-%Y"))
    if status != "OK" or not data or not data[0].split():
        return []
    out = []
    for num in reversed(data[0].split()[-400:]):
        if len(out) >= MAX_ALERTS_PER_RUN:
            break
        try:
            status, msgdata = conn.fetch(num, "(RFC822)")
            if status != "OK":
                continue
            msg = email_mod.message_from_bytes(msgdata[0][1])
        except Exception:
            continue
        sender = msg.get("From", "")
        if not is_alert(sender):
            continue
        try:
            when = parsedate_to_datetime(msg.get("Date", ""))
        except Exception:
            when = None
        out.append((sender, msg.get("Subject", ""), _text_of(msg), when))
    return out


def _text_of(msg):
    """Alert emails are usually HTML only, so take whichever part exists."""
    chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    chunks.append(payload.decode("utf-8", "ignore"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            chunks.append(payload.decode("utf-8", "ignore"))
    return "\n".join(chunks)


def harvest_alerts(state, days=None):
    """Pull adverts out of job-alert email and add the new ones to the queue.

    The listings arrive as little more than a URL, so they carry no title or
    company yet. jm.fetch_listing_text already opens a listing and reads it,
    and the discover stage runs that on everything - so these are added with
    what is known and filled in there, exactly like a listing from a board."""
    if not (jm.GMAIL_ADDRESS and jm.GMAIL_APP_PASSWORD):
        print("[alerts] no Gmail credentials, skipping")
        return 0
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", timeout=jm.IMAP_TIMEOUT)
        conn.login(jm.GMAIL_ADDRESS, jm.GMAIL_APP_PASSWORD)
        conn.select("INBOX")
    except Exception as e:
        print(f"[alerts] could not open the inbox: {e}")
        return 0

    added = alerts = 0
    try:
        for sender, subject, text, when in fetch_alerts(conn, days=days):
            alerts += 1
            for url in job_links(text):
                eid = alert_id(url)
                if eid in state["jobs"]:
                    continue
                board = board_of(url)
                state["jobs"][eid] = {
                    "external_id": eid,
                    "title": "",              # read from the advert at discovery
                    "company": "",
                    "location": "",
                    "description": "",
                    "url": url,
                    "source": f"alert:{board}",
                    "alert_subject": subject[:200],
                    "found_at": jm.now(),
                    "posted_at": when.isoformat() if when else None,
                    "status": "new",
                }
                added += 1
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    print(f"[alerts] {alerts} alert email(s) read, {added} new advert(s)")
    return added


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=None,
                    help="how far back to read (default 3)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be added, change nothing")
    args = ap.parse_args(argv)
    state = jm.load()
    before = len(state["jobs"])
    harvest_alerts(state, days=args.days)
    if args.dry_run:
        print(f"[alerts] dry run: {len(state['jobs']) - before} would be added")
        return 0
    jm.save(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ======================================================================
# FILLING IN WHAT AN ALERT DOES NOT TELL US
# ======================================================================
# An alert email gives a link and little else, so the title, company and
# description have to come off the advert itself. Nearly every job page in the
# world puts them in the <title> and the Open Graph tags, because that is what
# makes a link preview work when the advert is shared - so this needs no AI and
# costs one HTTP request.
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
META = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](og:title|og:site_name|og:description|'
    r'description)["\'][^>]*content=["\'](.*?)["\']', re.I | re.S)
META_REVERSED = re.compile(
    r'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:property|name)=["\']'
    r'(og:title|og:site_name|og:description|description)["\']', re.I | re.S)

# 'Instrumentation Technician - Aberdeen | Acme Subsea | CV-Library'
TITLE_SPLIT = re.compile(r"\s+[|–—-]\s+|\s+at\s+", re.I)


def page_facts(html):
    """{title, company, description} read off a job advert's own markup."""
    facts = {}
    for pattern in (META, META_REVERSED):
        for a, b in pattern.findall(html or ""):
            key, value = (a, b) if a.lower().startswith(("og:", "desc")) else (b, a)
            facts.setdefault(key.lower(), jm.strip_html(value).strip())
    title = facts.get("og:title") or ""
    if not title:
        found = TITLE_TAG.search(html or "")
        title = jm.strip_html(found.group(1)).strip() if found else ""
    company = facts.get("og:site_name") or ""
    description = facts.get("og:description") or facts.get("description") or ""
    return {"title": title, "company": company, "description": description}


BOARD_NAMES = re.compile(
    r"cv-?library|totaljobs|reed\.co\.uk|indeed|s1jobs|jobsite|rigzone|"
    r"oil\s*and\s*gas|energyjobline|linkedin|glassdoor|adzuna|monster|"
    r"jobserve|technojobs|myjobscotland|workable|greenhouse|lever|"
    r"smartrecruiters|ashby|teamtailor|apply now|job search|careers", re.I)


def split_title(raw, board_host=""):
    """('Instrumentation Technician', 'Acme Subsea') from a page title.

    The board's own name turns up in nearly every one of these, and taking it
    as the employer would put the wrong company on the email - the exact
    mistake that has already sent two of these to the wrong firm."""
    parts = [p.strip() for p in TITLE_SPLIT.split(raw or "") if p.strip()]
    parts = [p for p in parts if not BOARD_NAMES.search(p)]
    if not parts:
        return "", ""
    title = parts[0]
    company = ""
    for part in parts[1:]:
        # a location is not a company
        if re.fullmatch(r"[A-Za-z .'-]{2,30}", part) and not re.search(
                r"\b(aberdeen|dundee|edinburgh|glasgow|inverness|scotland|uk|"
                r"aberdeenshire|permanent|contract|full[ -]time|part[ -]time)\b",
                part, re.I):
            company = part
            break
    return title[:140], company[:120]


def enrich_one(job, html):
    """Read one advert. Returns True if it named both the role and the firm.

    Anything that cannot be read is dropped rather than guessed at: a listing
    with no company name cannot be emailed to anyone, and inventing one is how
    an application reaches the wrong firm."""
    if not html:
        job.update({"status": "skipped", "skip_reason": "advert unreadable"})
        return False
    facts = page_facts(html)
    title, company = split_title(facts["title"], board_of(job["url"]))
    company = company or (facts["company"]
                          if not BOARD_NAMES.search(facts["company"] or "")
                          else "")
    if not title or not company:
        job.update({"status": "skipped",
                    "skip_reason": "advert did not name the role and employer"})
        return False
    job.update({
        "title": title,
        "company": company,
        "description": (facts["description"] + "\n" + jm.strip_html(html))[:4000],
        "location": job.get("location") or "",
    })
    return True


def enrich(state, limit=None):
    """Open the adverts found in alerts and read their title and company.

    Fetched several at a time and under a wall clock. One run read 88 of them
    one at a time, spent forty-four minutes doing it, and was killed by the
    workflow timeout before it discovered a single address or sent a single
    application. Whatever is not read this run keeps its 'new' status and is
    read by the next one - a slow job board costs a delay, never the run."""
    todo = [j for j in state["jobs"].values()
            if str(j.get("source", "")).startswith("alert:")
            and j.get("status") == "new" and not j.get("title")]
    if limit:
        todo = todo[:limit]
    if not todo:
        return 0
    print(f"[alerts] reading {len(todo)} advert(s) for their title and company")
    started = time.monotonic()
    filled = dropped = skipped = 0

    def read(job):
        try:
            r = jm.requests.get(job["url"], headers=jm.UA, timeout=ENRICH_TIMEOUT,
                                allow_redirects=True)
            return r.text if r.ok else ""
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
        pages = pool.map(read, todo)
        for job, html in zip(todo, pages):
            if time.monotonic() - started > ENRICH_BUDGET_SECONDS:
                skipped = len(todo) - filled - dropped
                print(f"[alerts] {ENRICH_BUDGET_SECONDS}s budget reached, "
                      f"leaving {skipped} for the next run")
                break
            if enrich_one(job, html):
                filled += 1
            else:
                dropped += 1
    print(f"[alerts] {filled} advert(s) read, {dropped} unreadable and dropped")
    return filled
