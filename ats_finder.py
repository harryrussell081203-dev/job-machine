"""
Find an employer's real application form without going through a job board.

The job boards are a dead end for automation. Adzuna's outbound page renders
blank to a headless browser, so following a listing through never arrives
anywhere. Employers who use a hosted ATS have a public board of their own, and
that board is reachable directly - so go at it from the company name instead.

Two routes, cheapest first:

  1. Probe the hosted ATS platforms for the company's slug. Greenhouse, Lever,
     Workable and the rest all have predictable board URLs, so a handful of
     HTTP GETs settles whether a company has one.
  2. Failing that, open the company's own site, find its careers page, and look
     for a link to whichever ATS it uses.

Then match the specific advert on that board by title.
"""
import re

import requests

import job_machine as jm

TIMEOUT = 12

# Board URL patterns per platform. {slug} is the company's own identifier.
ATS_PROBES = (
    ("greenhouse", "https://boards.greenhouse.io/{slug}"),
    ("greenhouse", "https://job-boards.greenhouse.io/{slug}"),
    ("lever", "https://jobs.lever.co/{slug}"),
    ("workable", "https://apply.workable.com/{slug}/"),
    ("ashby", "https://jobs.ashbyhq.com/{slug}"),
    ("smartrecruiters", "https://careers.smartrecruiters.com/{slug}"),
    ("recruitee", "https://{slug}.recruitee.com/"),
    ("teamtailor", "https://{slug}.teamtailor.com/jobs"),
    ("personio", "https://{slug}.jobs.personio.com/"),
)

# Words that are not part of a company's ATS slug.
SLUG_NOISE = ("ltd", "limited", "plc", "llp", "inc", "uk", "group", "holdings",
              "international", "services", "solutions", "recruitment", "energy",
              "the", "and", "co", "company")

CAREERS_LINK_RE = re.compile(r"career|job|vacanc|join[- ]us|work[- ](for|with)[- ]us",
                             re.I)
ATS_URL_RE = re.compile(
    r"https?://[^\s\"'<>]*(?:" + "|".join(
        re.escape(d) for domains in
        __import__("portal_agent").SUPPORTED_ATS.values() for d in domains
    ) + r")[^\s\"'<>)]*", re.I)


def slug_candidates(company):
    """Plausible ATS slugs for a company name, most likely first.
    'Dron & Dickson Ltd' -> dronanddickson, drondickson, dron-dickson, dron."""
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
    if len(words) > 1:
        out.append(words[0])                       # many firms use just the first word
        out.append("".join(words[:2]))
    seen, unique = set(), []
    for slug in out:
        if slug and slug not in seen and 2 < len(slug) <= 40:
            seen.add(slug)
            unique.append(slug)
    return unique[:4]


NOT_A_BOARD = re.compile(
    r"page not found|404|no longer available|doesn'?t exist|does not exist|"
    r"account (is )?(suspended|closed)|no open (roles|positions|jobs)", re.I)


def looks_like_a_board(text, ats, company):
    """A 200 is not enough, and neither is a long page.

    apply.workable.com serves a bot-check page for any slug at all, so the
    first version of this probe matched fourteen unrelated companies to
    Workable. A real board has to name the company it belongs to."""
    if not text or len(text) < 400:
        return False
    if NOT_A_BOARD.search(text[:4000]):
        return False
    low = text.lower()
    # a bot check is not a board, whatever else is on the page
    import portal_agent
    if any(marker in low for marker in portal_agent.CAPTCHA_MARKERS):
        return False
    # the company's own name must appear, or this is someone else's board
    words = [w for w in re.findall(r"[a-z0-9]+", (company or "").lower())
             if w not in SLUG_NOISE and len(w) > 2]
    if not words:
        return False
    return any(w in low for w in words)


def probe_ats(company, session=None):
    """(ats, board_url) if this company has a public ATS board, else None."""
    get = (session or requests).get
    for slug in slug_candidates(company):
        for ats, pattern in ATS_PROBES:
            url = pattern.format(slug=slug)
            try:
                r = get(url, headers=jm.UA, timeout=TIMEOUT, allow_redirects=True)
            except Exception:
                continue
            if r.status_code == 200 and looks_like_a_board(r.text, ats, company):
                print(f"[ats] {company} -> {ats} board at {url}")
                return ats, r.url
    return None


def careers_page_ats(domain, session=None):
    """Open the company's own site and look for the ATS its careers page links to."""
    if not domain:
        return None
    get = (session or requests).get
    pages = [f"https://{domain}", f"https://{domain}/careers",
             f"https://{domain}/jobs", f"https://{domain}/vacancies",
             f"https://{domain}/careers/vacancies"]
    seen_links = []
    for url in pages:
        try:
            r = get(url, headers=jm.UA, timeout=TIMEOUT, allow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200 or not r.text:
            continue
        match = ATS_URL_RE.search(r.text)
        if match:
            found = match.group(0).rstrip(").,'\"")
            import portal_agent
            ats = portal_agent.classify_url(found)[0]
            print(f"[ats] {domain} careers page links to {ats}: {found}")
            return ats, found
        # remember internal careers links to try on the next pass
        for href in re.findall(r'href=["\']([^"\']+)["\']', r.text):
            if CAREERS_LINK_RE.search(href) and href.startswith(("/", "http")):
                full = href if href.startswith("http") else f"https://{domain}{href}"
                if full not in pages and full not in seen_links:
                    seen_links.append(full)
    for url in seen_links[:4]:
        try:
            r = get(url, headers=jm.UA, timeout=TIMEOUT, allow_redirects=True)
            match = ATS_URL_RE.search(r.text or "")
            if match:
                found = match.group(0).rstrip(").,'\"")
                import portal_agent
                return portal_agent.classify_url(found)[0], found
        except Exception:
            continue
    return None


TITLE_NOISE = {"the", "and", "for", "with", "job", "role", "vacancy", "new",
               "full", "part", "time", "permanent", "contract", "temporary",
               "senior", "junior", "based", "uk", "ltd"}


def title_tokens(title):
    return {t for t in re.findall(r"[a-z]+", (title or "").lower())
            if len(t) > 3 and t not in TITLE_NOISE}


def match_job_on_board(page, board_url, title, min_overlap=0.5):
    """Find the advert for this title on an ATS board. Returns its URL or None.

    A board lists every open role, so the right one has to be picked by title
    rather than assumed - applying to the wrong vacancy is worse than not
    applying."""
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

    best, best_score = None, 0.0
    for link in links or []:
        got = title_tokens(link.get("text"))
        if not got:
            continue
        score = len(wanted & got) / len(wanted)
        if score > best_score:
            best, best_score = link, score
    if best and best_score >= min_overlap:
        print(f"[ats] matched '{title}' to '{best['text']}' ({best_score:.0%})")
        return best["href"]
    print(f"[ats] no advert on the board matches '{title}' "
          f"(best {best_score:.0%})")
    return None


def find_application_url(page, job, session=None):
    """The employer's real application page for this job, or None.

    Deliberately does not touch the job board: that route ends on a blank
    interstitial, proven over three runs."""
    company = (job.get("company") or "").strip()
    if not company:
        return None, None

    found = probe_ats(company, session=session)
    if not found and job.get("company_domain"):
        found = careers_page_ats(job["company_domain"], session=session)
    if not found:
        domain = jm.find_domain(company)
        if domain:
            job["company_domain"] = domain
            found = careers_page_ats(domain, session=session)
    if not found:
        return None, None

    ats, board_url = found
    advert = match_job_on_board(page, board_url, job.get("title", ""))
    return (advert or board_url), ats
