"""
JOB MACHINE - fully automated job application outreach.
Single file. Runs on GitHub Actions. Costs nothing.

Pipeline: harvest fresh listings -> AI score vs Harry's profile -> find REAL
email addresses (never guessed, named people preferred) -> write personalised
email from a role-specific template -> send from Gmail with CV attached ->
follow up after 4 days if no reply.
"""
import glob, imaplib, json, os, re, smtplib, time
from datetime import datetime, timezone
from email.message import EmailMessage

import requests
import dns.resolver


# ======================================================================
# CONFIG
# ======================================================================
import os

# --- secrets (set in GitHub repo Settings > Secrets and variables > Actions) ---
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
REED_API_KEY = os.environ.get("REED_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")          # harryrussell081203@gmail.com
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")  # Google App Password (needs 2FA on)

# --- tuning ---
SCORE_THRESHOLD = 70
DAILY_SEND_CAP = 20        # across the whole day
PER_RUN_SEND_CAP = 7       # workflow runs 3x/weekday
FOLLOWUP_AFTER_DAYS = 4
SEARCH_LOCATION = "Aberdeen"
SEARCH_RADIUS_MILES = 40
MAX_DAYS_OLD = 2

SEARCH_KEYWORDS = [
    "engineer technician electronics instrumentation communications workshop maintenance",
]
REED_KEYWORDS = ["engineer", "technician", "electronics"]

GEMINI_MODEL = "gemini-2.5-flash"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "state.json")
CV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cv")

CANDIDATE_PROFILE = """Harry Russell, Aberdeen, Scotland.
- Ex-Royal Navy Communications & Information Specialist (HMS Westminster, Type 23 frigate): secure/non-secure comms, network engineering, cryptographic material, safety-critical equipment, high-pressure ops.
- ~3 years Workshop Technician at Sonardyne International: assembly, testing, fault diagnosis and repair of subsea acoustic/electronic equipment; quality control; technical documentation.
- Modern Apprenticeship SCQF Level 7 (electrical engineering / asset lifecycle & maintenance). Year 1 of BEng Instrumentation, Measurement & Control at RGU (paused).
- Also founder of Leads2Profit: marketing automation business (n8n, AI tooling, Meta ads, CRM) for nightlife/events venues.
- TARGET ROLES: engineering technician, electronics, instrumentation, maintenance, communications/radio, workshop, offshore/O&G technical, defence, forces-friendly trades; or technical/AV/production roles in events & nightlife.
- LOCATION: Aberdeen and surrounds strongly preferred. Relocation only for clearly strong roles.
- EXCLUDE: senior/chartered engineer roles requiring a completed degree + years of design experience, unrelated sales/care/driving/hospitality roles."""


# ======================================================================
# TEMPLATES
# ======================================================================
# Copy system: role-family templates + hard style rules.
# Gemini fills the skeleton; it is NOT allowed to freestyle structure.

BANNED = [
    "I hope this email finds you well", "I hope this finds you well", "passionate",
    "leverage", "delve", "seamless", "synergy", "dynamic", "keen to", "thrilled",
    "excited to apply", "perfect fit", "hit the ground running", "fast-paced environment",
    "proven track record", "results-driven", "detail-oriented", "team player",
    "furthermore", "moreover", "in today's", "I am writing to", "esteemed",
    "utilize", "spearheaded", "honed", "align", "resonate",
]

STYLE_RULES = """HARD RULES:
- 60-90 words body. Short sentences. Plain English a mate would say out loud.
- Greet by first name if a person's name is given, otherwise 'Hi,'.
- Line 1 names the exact role and one concrete detail from THIS listing or company (a product, site, shift pattern, kit, venue - something proving he read it).
- 2-3 proof lines: specific, with numbers. Never list everything - only what THIS job cares about.
- One-line CTA: a single easy question ('Worth a quick call this week?' / 'When suits for a chat?').
- Sign off: Harry, then 'Harry Russell / 07398 530978 / CV attached'.
- Confident and warm, slightly informal, zero grovelling, zero corporate filler.
- NEVER use any banned phrase. No exclamation marks. No markdown. No em dashes.

SUBJECT RULES:
- Max 8 words. Must reference the actual role or listing detail.
- Concrete + a hook: who he is or what he can do, not 'Application for X'.
- No clickbait lies, no ALL CAPS, at most one punctuation mark."""

TEMPLATES = {
    "communications": {
        "keywords": ["comms", "communication", "radio", "network", "telecom", "signal", "it support", "systems"],
        "subject_examples": [
            "Royal Navy comms specialist for your {role}",
            "Ran secure comms at sea - your {role}",
        ],
        "skeleton": """{greeting}

Saw your {role} listing - {one specific detail from the listing/company}.

I spent 2 years as a Communications & Information Specialist in the Royal Navy, running secure and non-secure comms and network troubleshooting on a frontline frigate, including cryptographic material where mistakes weren't an option. Since then, 3 years of hands-on electronics testing and fault-finding at Sonardyne.

{CTA question}

Harry
Harry Russell / 07398 530978 / CV attached""",
    },
    "electronics_technician": {
        "keywords": ["electronic", "technician", "workshop", "assembly", "test", "repair", "solder", "pcb", "electrical"],
        "subject_examples": [
            "Sonardyne-trained tech for your {role}",
            "3 yrs subsea electronics - your {role} role",
        ],
        "skeleton": """{greeting}

Your {role} listing caught my eye - {one specific detail from the listing/company}.

I've spent 3 years at Sonardyne assembling, testing and fault-finding subsea acoustic and electronic kit to IPC-A-610 Class 3, plus component-level repair and the documentation to match. Before that, the Royal Navy taught me to look after safety-critical equipment properly.

{CTA question}

Harry
Harry Russell / 07398 530978 / CV attached""",
    },
    "instrumentation_maintenance": {
        "keywords": ["instrument", "maintenance", "calibration", "control", "mechanical", "offshore", "oil", "gas", "subsea", "rov"],
        "subject_examples": [
            "Instrumentation apprentice-trained - your {role}",
            "Subsea kit background for your {role}",
        ],
        "skeleton": """{greeting}

Applying for your {role} - {one specific detail from the listing/company}.

My background is exactly this space: 3 years testing and maintaining subsea acoustic positioning systems at Sonardyne, a completed Modern Apprenticeship (SCQF L7, electrical/asset lifecycle), and first-year BEng in Instrumentation, Measurement & Control at RGU. Navy before that, so shift work and pressure are normal.

{CTA question}

Harry
Harry Russell / 07398 530978 / CV attached""",
    },
    "events_production": {
        "keywords": ["event", "venue", "nightclub", "av ", "audio", "sound", "lighting", "production", "stage", "hospitality tech"],
        "subject_examples": [
            "Tech who lives in your industry - {role}",
            "Comms + AV background for your {role}",
        ],
        "skeleton": """{greeting}

Your {role} role stood out - {one specific detail from the venue/company}.

I'm a rare mix for this industry: military-grade comms and electronics experience (Royal Navy, then 3 years at Sonardyne), and I run a marketing systems business built exclusively for nightlife and events venues, so I know how this world actually operates on both sides of the desk.

{CTA question}

Harry
Harry Russell / 07398 530978 / CV attached""",
    },
    "general": {
        "keywords": [],
        "subject_examples": [
            "Ex-Navy, ex-Sonardyne - your {role}",
            "Hands-on tech for your {role} role",
        ],
        "skeleton": """{greeting}

Saw your {role} listing - {one specific detail from the listing/company}.

Quick background: 2 years Royal Navy as a Communications & Information Specialist, 3 years at Sonardyne testing and fault-finding precision electronic equipment, and a completed electrical engineering Modern Apprenticeship. I learn systems fast and turn up reliable.

{CTA question}

Harry
Harry Russell / 07398 530978 / CV attached""",
    },
}

def pick_family(title, description=""):
    text = f"{title} {description[:500]}".lower()
    best, best_hits = "general", 0
    for fam, t in TEMPLATES.items():
        hits = sum(1 for k in t["keywords"] if k in text)
        if hits > best_hits:
            best, best_hits = fam, hits
    return best


# ======================================================================
# STATE
# ======================================================================
import json, os
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

def today():
    return datetime.now(timezone.utc).date().isoformat()

def load():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"jobs": {}, "companies_contacted": {}, "send_counts": {}}

def save(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=1)

def sends_today(state):
    return state["send_counts"].get(today(), 0)

def record_send(state):
    state["send_counts"][today()] = sends_today(state) + 1


# ======================================================================
# HARVEST
# ======================================================================
import requests

UA = {"User-Agent": "Mozilla/5.0 (job-machine)"}

def adzuna():
    jobs = []
    for kw in SEARCH_KEYWORDS:
        try:
            r = requests.get(
                "https://api.adzuna.com/v1/api/jobs/gb/search/1",
                params={
                    "app_id": ADZUNA_APP_ID,
                    "app_key": ADZUNA_APP_KEY,
                    "results_per_page": 50,
                    "what_or": kw,
                    "where": SEARCH_LOCATION,
                    "distance": SEARCH_RADIUS_MILES,
                    "max_days_old": MAX_DAYS_OLD,
                    "sort_by": "date",
                },
                timeout=30,
            )
            for j in r.json().get("results", []):
                jobs.append({
                    "external_id": f"adzuna_{j['id']}",
                    "source": "adzuna",
                    "title": j.get("title", ""),
                    "company": (j.get("company") or {}).get("display_name", ""),
                    "location": (j.get("location") or {}).get("display_name", ""),
                    "url": j.get("redirect_url", ""),
                    "description": (j.get("description") or "")[:4000],
                    "salary_min": j.get("salary_min"),
                    "salary_max": j.get("salary_max"),
                })
        except Exception as e:
            print(f"[harvest] adzuna error: {e}")
    return jobs

def reed():
    jobs = []
    for kw in REED_KEYWORDS:
        try:
            r = requests.get(
                "https://www.reed.co.uk/api/1.0/search",
                auth=(REED_API_KEY, ""),
                params={
                    "keywords": kw,
                    "locationName": SEARCH_LOCATION,
                    "distanceFromLocation": SEARCH_RADIUS_MILES,
                    "resultsToTake": 50,
                },
                timeout=30,
            )
            for j in r.json().get("results", []):
                jobs.append({
                    "external_id": f"reed_{j['jobId']}",
                    "source": "reed",
                    "title": j.get("jobTitle", ""),
                    "company": j.get("employerName", ""),
                    "location": j.get("locationName", ""),
                    "url": j.get("jobUrl", ""),
                    "description": (j.get("jobDescription") or "")[:4000],
                    "salary_min": j.get("minimumSalary"),
                    "salary_max": j.get("maximumSalary"),
                })
        except Exception as e:
            print(f"[harvest] reed error: {e}")
    return jobs

def harvest(state):
    new = 0
    for job in adzuna() + reed():
        eid = job["external_id"]
        if eid in state["jobs"]:
            continue
        job.update({"status": "new", "score": None, "found_at": now()})
        state["jobs"][eid] = job
        new += 1
    print(f"[harvest] {new} new listings")


# ======================================================================
# SCORE
# ======================================================================
import json, time, requests

def gemini(prompt, max_tokens=300):
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]

def gemini_json(prompt, max_tokens=300, retries=2):
    for _ in range(retries + 1):
        try:
            raw = gemini(prompt, max_tokens)
            clean = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(clean)
        except Exception as e:
            print(f"[gemini] retrying: {e}")
            time.sleep(5)
    return None

def score_jobs(state):
    unscored = [j for j in state["jobs"].values() if j["status"] == "new"][:40]
    for job in unscored:
        prompt = (
            'Screen this job listing for the candidate. Respond ONLY with JSON: '
            '{"score": <0-100>, "reason": "<one sentence>"}\n\n'
            f"CANDIDATE:\n{CANDIDATE_PROFILE}\n\n"
            "SCORE GUIDE: 85+ strong direct match in/near Aberdeen. 70-84 good match. "
            "40-69 partial. <40 poor/excluded.\n\n"
            f"LISTING:\nTitle: {job['title']}\nCompany: {job['company']}\n"
            f"Location: {job['location']}\nSalary: {job.get('salary_min')}-{job.get('salary_max')}\n"
            f"Description: {job['description'][:2500]}"
        )
        result = gemini_json(prompt)
        if result is None:
            continue
        job["score"] = max(0, min(100, int(result.get("score", 0))))
        job["score_reason"] = str(result.get("reason", ""))[:300]
        job["status"] = "scored" if job["score"] >= SCORE_THRESHOLD else "skipped"
        time.sleep(4)  # stay inside free-tier rate limits
    print(f"[score] scored {len(unscored)} listings")


# ======================================================================
# DISCOVER
# ======================================================================
import re, requests
import dns.resolver

UA = {"User-Agent": "Mozilla/5.0 (compatible; JobApply/1.0)"}
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
BAD_PREFIXES = ("noreply", "no-reply", "donotreply", "postmaster", "abuse",
                "privacy", "unsubscribe", "support", "sales", "webmaster",
                "example", "test", "email", "name", "your")
HIRING_PREFIXES = ("careers", "jobs", "recruitment", "recruiting", "hr",
                   "talent", "vacancies", "apply", "people")
GENERIC_PREFIXES = ("info", "office", "enquiries", "admin", "hello", "contact",
                    "mail", "reception", "accounts") + HIRING_PREFIXES

def is_personal(local):
    """Looks like a real person: firstname.lastname / f.lastname / plain first name."""
    if local in GENERIC_PREFIXES or local.startswith(BAD_PREFIXES):
        return False
    parts = [p for p in re.split(r"[._-]", local) if p]
    if not all(p.isalpha() for p in parts):
        return False
    if len(parts) >= 2 and all(len(p) >= 1 for p in parts):
        return True
    return len(parts) == 1 and 3 <= len(parts[0]) <= 12  # plain first name

def name_from_email(local):
    parts = [p for p in re.split(r"[._-]", local) if len(p) > 1 and p.isalpha()]
    return parts[0].capitalize() if parts else None

def classify(email):
    local = email.split("@")[0]
    if is_personal(local):
        return 3, name_from_email(local)   # named person - best
    if local.startswith(HIRING_PREFIXES):
        return 2, None                     # hiring inbox
    if local in GENERIC_PREFIXES:
        return 1, None                     # generic real inbox
    return 0, None

def has_mx(domain):
    try:
        return len(dns.resolver.resolve(domain, "MX")) > 0
    except Exception:
        return False

def clean_emails(raw, domain=None):
    out = []
    for e in set(x.lower().strip(".") for x in raw):
        local = e.split("@")[0]
        if local.startswith(BAD_PREFIXES):
            continue
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        if domain and not e.endswith("@" + domain) and not e.endswith("." + domain):
            continue
        out.append(e)
    return out

def find_domain(company):
    try:
        r = requests.get("https://autocomplete.clearbit.com/v1/companies/suggest",
                         params={"query": company}, headers=UA, timeout=15)
        for hit in r.json():
            if hit.get("domain"):
                return hit["domain"]
    except Exception:
        pass
    return None

def scrape_site(domain):
    raw = []
    for path in ("", "/contact", "/contact-us", "/careers", "/jobs",
                 "/join-us", "/about", "/about-us", "/team", "/our-team", "/people"):
        for scheme in ("https", "http"):
            try:
                r = requests.get(f"{scheme}://{domain}{path}", headers=UA, timeout=12)
                raw += EMAIL_RE.findall(r.text)
                break
            except Exception:
                continue
    return clean_emails(raw, domain)

def best_email(candidates):
    """Return (email, contact_name, tier) - highest tier wins. Never invents anything."""
    best = (None, None, -1)
    for e in candidates:
        tier, name = classify(e)
        if tier > best[2]:
            best = (e, name, tier)
    return best

def discover(state):
    todo = [j for j in state["jobs"].values() if j["status"] == "scored"][:15]
    for job in todo:
        # 1) emails printed inside the listing itself - directly tied to the job
        listing_emails = clean_emails(EMAIL_RE.findall(job.get("description", "")))
        email, name, tier = best_email(listing_emails)
        method = "listing"

        # 2) company website scrape
        if not email:
            company = (job.get("company") or "").strip()
            if not company:
                job["status"], job["skip_reason"] = "skipped", "no company name"
                continue
            domain = find_domain(company)
            if not domain or not has_mx(domain):
                job["status"] = "no_email"
                continue
            job["company_domain"] = domain
            email, name, tier = best_email(scrape_site(domain))
            method = "scraped"

        # 3) nothing real found -> do NOT send. No guessing, ever.
        if not email or not has_mx(email.split("@")[1]):
            job["status"] = "no_email"
            continue

        job.update({"contact_email": email, "contact_name": name,
                    "email_method": method, "email_tier": tier, "status": "ready"})
        who = name or email
        print(f"[discover] {job['company']} -> {who} <{email}> tier={tier} via {method}")
    print(f"[discover] processed {len(todo)}")


# ======================================================================
# COMPOSE_SEND
# ======================================================================
import glob, imaplib, os, smtplib, time
from datetime import datetime, timezone
from email.message import EmailMessage

def cv_path():
    pdfs = glob.glob(os.path.join(CV_DIR, "*.pdf"))
    return pdfs[0] if pdfs else None

def slop_check(text):
    low = text.lower()
    return [b for b in BANNED if b.lower() in low]

def build_email(job):
    family = pick_family(job["title"], job.get("description", ""))
    t = TEMPLATES[family]
    greeting = f"Hi {job['contact_name']}," if job.get("contact_name") else "Hi,"
    prompt = (
        'Fill this cold-email skeleton for the job below. Respond ONLY with JSON: '
        '{"subject": "...", "body": "..."}\n\n'
        f"{STYLE_RULES}\n\n"
        f"BANNED PHRASES (never use any): {', '.join(BANNED)}\n\n"
        f"GREETING TO USE EXACTLY: {greeting}\n"
        f"SUBJECT EXAMPLES (match this energy, adapt to the listing): {t['subject_examples']}\n\n"
        f"SKELETON (keep its structure, replace the {{placeholders}}, tighten wording to fit the job):\n{t['skeleton']}\n\n"
        f"CANDIDATE (for accuracy only, do not dump it all in):\n{CANDIDATE_PROFILE}\n\n"
        f"JOB:\nTitle: {job['title']}\nCompany: {job['company']}\nLocation: {job['location']}\n"
        f"Recipient: {job.get('contact_name') or 'unknown - generic hiring inbox'}\n"
        f"Description: {job['description'][:2000]}"
    )
    for _ in range(2):
        content = gemini_json(prompt, max_tokens=600)
        if not content or "subject" not in content or "body" not in content:
            continue
        bad = slop_check(content["subject"] + " " + content["body"])
        if bad:
            prompt += f"\n\nPREVIOUS ATTEMPT USED BANNED PHRASES {bad}. Rewrite without them."
            continue
        if len(content["subject"].split()) > 10:
            content["subject"] = " ".join(content["subject"].split()[:8])
        return content
    return None

def send_email(to_addr, subject, body, attach_cv=True):
    msg = EmailMessage()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    cv = cv_path()
    if attach_cv and cv:
        with open(cv, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                               filename="Harry_Russell_CV.pdf")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.send_message(msg)

def run_sends(state):
    if not cv_path():
        print("[send] NO CV PDF IN cv/ FOLDER - not sending")
        return
    ready = sorted(
        [j for j in state["jobs"].values() if j["status"] == "ready"],
        key=lambda j: (-(j.get("email_tier") or 0), -(j.get("score") or 0)),
    )
    sent = 0
    for job in ready:
        if sent >= PER_RUN_SEND_CAP or sends_today(state) >= DAILY_SEND_CAP:
            break
        ckey = job["company"].lower().strip()
        if ckey in state["companies_contacted"]:
            job["status"] = "skipped"
            job["skip_reason"] = "company already contacted"
            continue
        content = build_email(job)
        if not content or "subject" not in content:
            continue
        try:
            send_email(job["contact_email"], content["subject"], content["body"])
            job.update({"status": "sent", "sent_at": now(),
                        "sent_subject": content["subject"]})
            state["companies_contacted"][ckey] = now()
            record_send(state)
            sent += 1
            print(f"[send] {job['title']} @ {job['company']} -> {job['contact_email']}")
            time.sleep(30)
        except Exception as e:
            print(f"[send] failed {job['company']}: {e}")
    print(f"[send] sent {sent}")

def has_reply_from(addr):
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com")
        m.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        m.select("INBOX")
        _, data = m.search(None, "FROM", f'"{addr}"')
        m.logout()
        return bool(data[0].split())
    except Exception as e:
        print(f"[followup] imap error: {e}")
        return True  # fail safe: assume replied, don't follow up

def run_followups(state):
    for job in state["jobs"].values():
        if job["status"] != "sent" or job.get("followup_sent_at"):
            continue
        sent_at = datetime.fromisoformat(job["sent_at"])
        age = (datetime.now(timezone.utc) - sent_at).days
        if age < FOLLOWUP_AFTER_DAYS:
            continue
        if has_reply_from(job["contact_email"]):
            job["status"] = "replied"
            continue
        body = (
            f"Hi,\n\nJust following up on my application for the {job['title']} role "
            f"I sent over on {sent_at.strftime('%A')}. Still very interested - happy to come in "
            "for a chat any time that suits.\n\nBest,\nHarry Russell\n07398 530978"
        )
        try:
            send_email(job["contact_email"], f"Re: {job.get('sent_subject', job['title'])}",
                       body, attach_cv=False)
            job["followup_sent_at"] = now()
            print(f"[followup] {job['company']}")
            time.sleep(15)
        except Exception as e:
            print(f"[followup] failed {job['company']}: {e}")


# ======================================================================
# MAIN
# ======================================================================
def main():
    state = load()
    harvest(state)
    save(state)
    score_jobs(state)
    save(state)
    discover(state)
    save(state)
    run_sends(state)
    save(state)
    run_followups(state)
    save(state)
    jobs = state["jobs"].values()
    print("\n=== SUMMARY ===")
    for s in ("new", "scored", "ready", "sent", "replied", "no_email", "skipped"):
        print(f"{s}: {sum(1 for j in jobs if j['status'] == s)}")

if __name__ == "__main__":
    main()
