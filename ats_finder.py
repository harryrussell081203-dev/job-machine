"""
Find an employer's real application form without going through a job board.

The job boards are a dead end for automation: Adzuna's outbound page renders
blank to a headless browser, so following a listing through never arrives
anywhere. Employers who use a hosted ATS have a public board of their own, and
that board is reachable directly - so go at it from the company name instead.

WHY THIS ASKS APIs RATHER THAN PAGES
------------------------------------
The first version of this module guessed a slug and fetched the board's HTML.
Every one of those platforms answers 200 for a slug that does not exist -
apply.workable.com serves a bot check, careers.smartrecruiters.com serves a
generic search page - so a 200 proved nothing. A diagnose run matched fourteen
unrelated companies to Workable, and after that was patched, matched
'Fisher House, PO Box 4' and 'Morson Edge' to AECOM's adverts on
SmartRecruiters and sent 'Future Engineering' to recruitee.com's marketing
homepage. Nine rows of that table read "SUPPORTED - can apply now" while
finding zero form fields.

Every one of these platforms also publishes a free, unauthenticated JSON API
that 404s honestly for a company that is not on it, and returns the real list
of open roles with their real application URLs when it is. That removes the
guesswork in both directions: existence is proven, and the advert is matched
against actual posting titles rather than whatever text a scraper found in a
page's navigation.

Two routes, cheapest first:

  1. Ask each platform's board API for the company's slug.
  2. Failing that, open the company's own site, find its careers page, and take
     the ATS link it points at - which is authoritative, no guessing involved.

Platforms with no public API (Teamtailor, JOIN, Personio) are reachable only by
route 2, deliberately: a guessed slug on those cannot be verified.
"""
import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import job_machine as jm
import portal_agent

TIMEOUT = 12

# Words that are not part of a company's ATS slug.
SLUG_NOISE = ("ltd", "limited", "plc", "llp", "inc", "uk", "group", "holdings",
              "international", "services", "solutions", "recruitment", "energy",
              "the", "and", "co", "company")


# ======================================================================
# SLUGS
# ======================================================================
def slug_candidates(company, whole_name_only=False):
    """Plausible ATS slugs for a company name, most likely first.
    'Dron & Dickson Ltd' -> dronanddickson, drondickson, dron-dickson, dron.

    whole_name_only drops the abbreviations. A slug built from every word can
    only belong to this company; 'dron' or 'speedy' could belong to anyone,
    so those are accepted on stronger evidence (see slug_plan)."""
    text = re.sub(r"&", " and ", (company or "").lower())
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in SLUG_NOISE]
    if not words:
        return []
    joined = "".join(words)
    out = [joined, "-".join(words)]
    # 'Dron & Dickson' is as likely to be dronanddickson as drondickson
    with_and = [w for w in re.findall(r"[a-z0-9]+", text)
                if w not in SLUG_NOISE or w == "and"]
    if with_and != words:
        out.append("".join(with_and))
    if len(words) > 1 and not whole_name_only:
        out.append(words[0])                       # many firms use just the first word
        out.append("".join(words[:2]))
    seen, unique = set(), []
    for slug in out:
        if slug and slug not in seen and 2 < len(slug) <= 40:
            seen.add(slug)
            unique.append(slug)
    return unique[:4]


def slug_plan(company):
    """The slugs worth asking about, in order.

    Whole-name slugs only. A probe run asked about abbreviations too, and
    'future' - from 'Future Engineering Recruitment Ltd' - returned five real
    postings belonging to an unrelated AI company. Across fourteen companies
    the abbreviations found one wrong board and no right ones, and a wrong
    board means an application sent to a company that never advertised."""
    return [(s, True) for s in slug_candidates(company, whole_name_only=True)]


def slug_forms(ats, slug):
    """The spellings of a slug this platform might want.

    SmartRecruiters' company identifier is case-sensitive - their own posting
    URLs read jobs.smartrecruiters.com/AECOM2/... - so a lowercase slug gets a
    polite 200 with zero postings even for a company that really is on there."""
    if ats != "smartrecruiters":
        return [slug]
    words = [w for w in re.split(r"[-_]", slug) if w]
    return list(dict.fromkeys([slug,
                               "".join(w.capitalize() for w in words),
                               slug.capitalize()]))


def significant_words(name):
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower())
            if w not in SLUG_NOISE and len(w) > 2}


def names_agree(board_name, company):
    """Does the account name the API reports belong to the company we asked for?

    Only used when a platform tells us the name - it is the cheapest way to
    catch a slug that happens to belong to somebody else."""
    a, b = significant_words(board_name), significant_words(company)
    if not a or not b:
        return True            # nothing to disagree with
    return bool(a & b)


# ======================================================================
# BOARD APIs
# ======================================================================
def _place(value):
    """Every platform spells a location differently. Flatten it to text."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [value.get(k) for k in ("name", "city", "region", "state",
                                        "country", "countryCode")]
        return ", ".join(p for p in parts if isinstance(p, str) and p)
    return ""


def _posting(title, url, where="", text=""):
    return {"title": title, "url": url, "location": _place(where),
            "description": jm.strip_html(text or "")[:4000]}


def _greenhouse(data, slug):
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    return None, [_posting(j.get("title"), j.get("absolute_url"),
                           j.get("location"), j.get("content"))
                  for j in data["jobs"] if isinstance(j, dict)]


def _lever(data, slug):
    if not isinstance(data, list):
        return None
    return None, [_posting(j.get("text"), j.get("applyUrl") or j.get("hostedUrl"),
                           (j.get("categories") or {}).get("location"),
                           j.get("descriptionPlain") or j.get("description"))
                  for j in data if isinstance(j, dict)]


def _ashby(data, slug):
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    return None, [_posting(j.get("title"), j.get("applyUrl") or j.get("jobUrl"),
                           j.get("location"),
                           j.get("descriptionPlain") or j.get("descriptionHtml"))
                  for j in data["jobs"] if isinstance(j, dict)]


def _smartrecruiters(data, slug):
    if not isinstance(data, dict) or "content" not in data:
        return None
    name, jobs = None, []
    for posting in data["content"]:
        if not isinstance(posting, dict):
            continue
        company = posting.get("company") or {}
        name = name or company.get("name")
        ident = company.get("identifier") or slug
        if posting.get("id"):
            jobs.append(_posting(
                posting.get("name"),
                f"https://jobs.smartrecruiters.com/{ident}/{posting['id']}",
                posting.get("location")))
    return name, jobs


def _workable(data, slug):
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    jobs = [_posting(j.get("title"),
                     j.get("application_url") or j.get("url") or j.get("shortlink"),
                     j.get("location") or j.get("city"), j.get("description"))
            for j in data["jobs"] if isinstance(j, dict)]
    return data.get("name"), jobs


def _recruitee(data, slug):
    if not isinstance(data, dict) or "offers" not in data:
        return None
    return None, [_posting(o.get("title"),
                           o.get("careers_apply_url") or o.get("careers_url"),
                           o.get("location") or o.get("city"), o.get("description"))
                  for o in data["offers"] if isinstance(o, dict)]


# Free, unauthenticated, and honest about a company that is not on the platform.
API_BOARDS = (
    ("greenhouse",
     "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
     "https://job-boards.greenhouse.io/{slug}", _greenhouse),
    ("lever",
     "https://api.lever.co/v0/postings/{slug}?mode=json",
     "https://jobs.lever.co/{slug}", _lever),
    ("ashby",
     "https://api.ashbyhq.com/posting-api/job-board/{slug}",
     "https://jobs.ashbyhq.com/{slug}", _ashby),
    ("smartrecruiters",
     "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
     "https://careers.smartrecruiters.com/{slug}", _smartrecruiters),
    ("workable",
     "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
     "https://apply.workable.com/{slug}/", _workable),
    ("recruitee",
     "https://{slug}.recruitee.com/api/offers/",
     "https://{slug}.recruitee.com/", _recruitee),
)


def api_board(ats, slug, company, get):
    """The company's board on this platform, or None. Never a maybe.

    None covers every failure equally: a 404, a redirect to somebody's
    marketing site, a payload we do not recognise, an empty board. Guessing
    is what produced nine bogus 'can apply now' verdicts.

    The empty-board check is load-bearing rather than tidy-minded. A probe run
    showed SmartRecruiters answering 200 for every slug it was given and
    Workable answering 200 while echoing the slug back as the account name -
    'Baker', 'ORION', 'fisher'. Both are catch-alls; what neither can fake is a
    list of real postings."""
    spec = next((s for s in API_BOARDS if s[0] == ats), None)
    if not spec:
        return None
    _, api_url, board_pattern, parse = spec
    for form in slug_forms(ats, slug):
        r = None
        for attempt in range(2):
            try:
                r = get(api_url.format(slug=form), headers=jm.UA,
                        timeout=TIMEOUT, allow_redirects=True)
            except Exception:
                return None
            if getattr(r, "status_code", 0) != 429:
                break
            time.sleep(2 + attempt * 3)      # Workable rate-limits a long run
        if getattr(r, "status_code", 0) != 200:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        parsed = parse(data, form)
        if not parsed:
            continue
        board_name, raw = parsed
        jobs = [p for p in raw if p.get("title") and p.get("url")]
        if not jobs:
            continue                   # a board with no open roles is no use
        if board_name and not names_agree(board_name, company):
            print(f"[ats] {company}: {ats}/{form} belongs to "
                  f"'{board_name}', skipping")
            continue
        return {"ats": ats, "slug": form, "name": board_name, "jobs": jobs,
                "url": board_pattern.format(slug=form)}
    return None


def find_board(company, session=None):
    """The company's real ATS board, or None.

    The six platforms are six different hosts, so they are asked at the same
    time - serially this was the slowest thing in the run, and it happens for
    every company on every pass. Slugs stay sequential: the first one usually
    answers, and firing them all would multiply the load on platforms that
    already rate-limit."""
    get = (session or requests).get
    for slug, whole_name in slug_plan(company):
        with ThreadPoolExecutor(max_workers=len(API_BOARDS)) as pool:
            found = {ats: pool.submit(api_board, ats, slug, company, get)
                     for ats, _, _, _ in API_BOARDS}
            results = {}
            for ats, future in found.items():
                try:
                    results[ats] = future.result()
                except Exception:
                    results[ats] = None
        # decided in API_BOARDS order, not in whichever order they answered
        for ats, _, _, _ in API_BOARDS:
            board = results.get(ats)
            if board:
                board["whole_name"] = whole_name
                print(f"[ats] {company} -> {ats} board '{board['slug']}' "
                      f"({len(board['jobs'])} open role(s))")
                return board
    return None


def probe_ats(company, session=None):
    """(ats, board_url) if this company has a public ATS board, else None."""
    board = find_board(company, session=session)
    return (board["ats"], board["url"]) if board else None


# ======================================================================
# THE COMPANY'S OWN CAREERS PAGE
# ======================================================================
CAREERS_LINK_RE = re.compile(r"career|job|vacanc|join[- ]us|work[- ](for|with)[- ]us",
                             re.I)
_ATS_DOMAINS = [d for domains in portal_agent.SUPPORTED_ATS.values() for d in domains]
# Workday and the rest cannot be automated, but a direct link still beats
# handing Harry a job-board advert to hunt through.
_ATS_DOMAINS += [d for domains in portal_agent.MANUAL_ATS.values() for d in domains
                 if d not in ("linkedin.com", "indeed.com", "reed.co.uk",
                              "totaljobs.com")]
ATS_URL_RE = re.compile(
    r"https?://[^\s\"'<>]*(?:" + "|".join(re.escape(d) for d in _ATS_DOMAINS)
    + r")[^\s\"'<>)]*", re.I)


def careers_page_ats(domain, session=None):
    """Open the company's own site and find where their applications go.

    Returns (ats, url). A link on the employer's own careers page is
    authoritative - unlike a guessed slug, it is the employer telling us. When
    they run no hosted ATS at all, this returns (None, their own careers page),
    which is not a failure: a probe run showed most Aberdeen employers taking
    applications on a form of their own, and a form is a form."""
    if not domain:
        return None
    get = (session or requests).get
    pages = [f"https://{domain}", f"https://{domain}/careers",
             f"https://{domain}/jobs", f"https://{domain}/vacancies",
             f"https://{domain}/careers/vacancies"]
    seen_links, own_page, reachable = [], None, False
    for url in pages:
        try:
            r = get(url, headers=jm.UA, timeout=TIMEOUT, allow_redirects=True)
        except Exception:
            continue
        if getattr(r, "status_code", 0) != 200 or not r.text:
            continue
        reachable = True
        match = ATS_URL_RE.search(r.text)
        if match:
            found = match.group(0).rstrip(").,'\"")
            ats = portal_agent.classify_url(found)[0]
            print(f"[ats] {domain} careers page links to {ats}: {found}")
            return ats, found
        if own_page is None and url != f"https://{domain}":
            own_page = getattr(r, "url", url) or url   # a real careers page
        # remember internal careers links to try on the next pass
        for href in re.findall(r'href=["\']([^"\']+)["\']', r.text):
            if CAREERS_LINK_RE.search(href) and href.startswith(("/", "http")):
                full = href if href.startswith("http") else f"https://{domain}{href}"
                if full not in pages and full not in seen_links:
                    seen_links.append(full)
    for url in seen_links[:4]:
        try:
            r = get(url, headers=jm.UA, timeout=TIMEOUT, allow_redirects=True)
        except Exception:
            continue
        if getattr(r, "status_code", 0) != 200:
            continue
        reachable = True
        match = ATS_URL_RE.search(r.text or "")
        if match:
            found = match.group(0).rstrip(").,'\"")
            ats = portal_agent.classify_url(found)[0]
            print(f"[ats] {domain} careers page links to {ats}: {found}")
            return ats, found
        own_page = own_page or getattr(r, "url", url) or url
    if own_page:
        print(f"[ats] {domain} runs no hosted ATS - their own page: {own_page}")
        return None, own_page
    if not reachable:
        print(f"[ats] {domain} could not be read at all")
    return None


BOARD_SLUG_RES = (
    re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I),
    re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I),
    re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I),
    re.compile(r"smartrecruiters\.com/([a-z0-9_-]+)", re.I),
    re.compile(r"apply\.workable\.com/([a-z0-9_-]+)", re.I),
    re.compile(r"https?://([a-z0-9-]+)\.recruitee\.com", re.I),
)


def slug_from_board_url(url):
    """The company identifier inside an ATS URL, so the API can list its roles."""
    for pattern in BOARD_SLUG_RES:
        m = pattern.search(url or "")
        if m and m.group(1).lower() not in ("www", "jobs", "careers", "embed"):
            return m.group(1)
    return None


# ======================================================================
# MATCHING THE ADVERT
# ======================================================================
TITLE_NOISE = {"the", "and", "for", "with", "job", "role", "vacancy", "new",
               "full", "part", "time", "permanent", "contract", "temporary",
               "senior", "junior", "based", "uk", "ltd"}


def title_tokens(title):
    return {t for t in re.findall(r"[a-z]+", (title or "").lower())
            if len(t) > 3 and t not in TITLE_NOISE}


def match_posting(jobs, title, min_overlap=0.5):
    """The board's own posting for this advert, or None.

    Applying to the wrong vacancy is worse than not applying, so a weak match
    is refused rather than rounded up."""
    wanted = title_tokens(title)
    if not wanted:
        return None
    best, best_score = None, 0.0
    for job in jobs or []:
        got = title_tokens(job.get("title"))
        if not got:
            continue
        score = len(wanted & got) / len(wanted)
        if score > best_score:
            best, best_score = job, score
    if best and best_score >= min_overlap:
        print(f"[ats] matched '{title}' to '{best['title']}' ({best_score:.0%})")
        return best["url"]
    print(f"[ats] no posting matches '{title}' (best {best_score:.0%})")
    return None


def match_job_on_board(page, board_url, title, min_overlap=0.5):
    """Same job, done in a browser, for a board with no API (Teamtailor, JOIN,
    Personio). Only ever reached from a link on the employer's own site."""
    wanted = title_tokens(title)
    if not wanted:
        return None
    try:
        page.goto(board_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        links = page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]'))"
            ".map(a => ({href: a.href, text: (a.innerText||'').trim()}))"
            ".filter(l => l.text.length > 3 && l.text.length < 120)")
    except Exception as e:
        print(f"[ats] could not read board {board_url}: {type(e).__name__}")
        return None
    jobs = [{"title": l.get("text"), "url": l.get("href")} for l in links or []]
    return match_posting(jobs, title, min_overlap=min_overlap)


# ======================================================================
# THE ROUTE
# ======================================================================
def own_site_application(page, careers_url, title):
    """Walk an employer's own careers page through to this vacancy's form.

    A careers page is an index, not a form: the vacancy is one click in and
    the form is often one more after that. Composed of the two pieces that
    already work - find the link whose text matches the advert, then follow
    whatever the page calls 'apply'."""
    if page is None or not careers_url:
        return None
    vacancy = match_job_on_board(page, careers_url, title)
    if not vacancy:
        return None
    try:
        page.goto(vacancy, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"[ats] could not open {vacancy[:70]}: {type(e).__name__}")
        return None
    links = page.evaluate(portal_agent.APPLY_LINK_JS) or {}
    target = next((l["href"] for l in
                   list(links.get("offsite") or []) + list(links.get("onsite") or [])
                   if l.get("href") != vacancy), None)
    if not target:
        return vacancy                     # the form may be on the advert itself
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        print(f"[ats] followed '{title}' to {page.url[:80]}")
        return page.url
    except Exception:
        return vacancy


def find_application_url(page, job, session=None):
    """The employer's real application page for this job, or None.

    Returns (url, ats). A None url with an ats name means we know where the
    employer recruits but this particular advert is not applyable by machine -
    which is a different thing from not knowing, and is reported differently."""
    company = (job.get("company") or "").strip()
    title = job.get("title", "")
    if not company:
        return None, None

    board = find_board(company, session=session)
    if board:
        # A slug built from the whole company name can only be theirs. An
        # abbreviation ('dron', 'speedy') could be anyone's, so it has to earn
        # it with a much closer title match before we believe the board.
        threshold = 0.5 if board.get("whole_name") else 0.8
        url = match_posting(board["jobs"], title, min_overlap=threshold)
        if url:
            return url, board["ats"]
        if board.get("whole_name"):
            # their board is real, this advert simply is not on it
            return None, board["ats"]

    domain = job.get("company_domain") or jm.find_domain(company)
    if not domain:
        return None, None
    job["company_domain"] = domain
    found = careers_page_ats(domain, session=session)
    if not found:
        return None, None
    ats, url = found

    slug = slug_from_board_url(url)
    if slug:
        board = api_board(ats, slug, company, (session or requests).get)
        if board:
            matched = match_posting(board["jobs"], title)
            if matched:
                return matched, ats
            return None, ats
    if page is not None and ats in ("teamtailor", "join", "personio"):
        matched = match_job_on_board(page, url, title)
        if matched:
            return matched, ats
    if ats is None:
        # their own site: the careers page is an index, walk it to the vacancy
        walked = own_site_application(page, url, title)
        if walked:
            return walked, None
    return url, ats


# ======================================================================
# GROUND TRUTH
# ======================================================================
def check(companies):
    """Ask the APIs about real companies and print exactly what came back.

    Run this on a runner - the whole point is that a 200 from these platforms
    used to mean nothing, so the claim 'the API 404s honestly' is one to verify
    rather than assume."""
    for company in companies:
        company, _, title = company.partition("|")
        print(f"\n=== {company.strip()} ===")
        for slug, whole in slug_plan(company.strip()):
            for ats, api_url, _, parse in API_BOARDS:
                url = api_url.format(slug=slug)
                try:
                    r = requests.get(url, headers=jm.UA, timeout=TIMEOUT,
                                     allow_redirects=True)
                except Exception as e:
                    print(f"  {ats:16} {slug:24} ERR {type(e).__name__}")
                    continue
                note = ""
                if r.status_code == 200:
                    try:
                        parsed = parse(r.json(), slug)
                    except Exception as e:
                        parsed, note = None, f"unparseable ({type(e).__name__})"
                    if parsed:
                        name, jobs = parsed
                        note = f"name={name!r} jobs={len(jobs)}"
                        if jobs:
                            note += f" first={jobs[0][0]!r}"
                    elif not note:
                        note = "200 but not a board payload"
                print(f"  {ats:16} {slug:24} {r.status_code} {note}")
        # Route 2 matters more than route 1 for this queue: most of these
        # firms are agencies or run their own site, so the question is what
        # their careers page links to.
        company = company.strip()
        domain = jm.find_domain(company)
        print(f"  {'careers page':16} {domain or '(no domain found)':24} "
              f"{careers_page_ats(domain) if domain else ''}")
        if title.strip():
            board = find_board(company)
            if board:
                print(f"  -> would apply to: "
                      f"{match_posting(board['jobs'], title.strip())}")


def check_queue(limit):
    """Probe the companies actually sitting in the portal queue.

    Runs in seconds with no browser and no Gemini, so the API claims can be
    verified on a runner before a full portal run is trusted."""
    state = jm.load()
    todo = portal_agent.portal_candidates(state)[:limit]
    if not todo:
        print("[check] no candidates in the queue")
        return
    check([f"{j.get('company', '')}|{j.get('title', '')}" for j in todo])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", nargs="+", metavar="COMPANY[|TITLE]",
                    help="probe the board APIs for these companies and report")
    ap.add_argument("--check-queue", type=int, metavar="N",
                    help="probe the top N companies in the portal queue")
    args = ap.parse_args(argv)
    if args.check:
        check(args.check)
        return 0
    if args.check_queue:
        check_queue(args.check_queue)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
