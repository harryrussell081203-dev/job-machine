"""
JOB MACHINE - automated job application outreach for Harry Russell.
Single file. Runs on GitHub Actions. Costs nothing.

Pipeline:
  1. HARVEST   fresh listings (<=48h old) from Adzuna + Reed, across SEARCH_LOCATIONS
  2. SCORE     Gemini scores each listing 0-100 against the candidate profile
  3. DISCOVER  find a REAL email address - never guessed, never pattern-generated
  4. COMPOSE   role-family template filled by Gemini inside hard style rules
  5. SEND      Gmail SMTP with the CV attached, best contacts first, capped
  6. FOLLOW-UP one short nudge 4 days later if the inbox shows no reply
  7. STATE     everything in data/state.json, committed back by the workflow

TEST_MODE (default ON) runs the whole pipeline for real but redirects every
message to Harry's own inbox, prefixes the subject with the intended recipient,
leaves companies unmarked and disables follow-ups.
"""
import argparse
import email.utils
import glob
import imaplib
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import requests

try:
    import dns.resolver
except ImportError:  # pragma: no cover - dnspython is installed by the workflow
    dns = None


# ======================================================================
# CONFIG
# ======================================================================
def env_str(name, default=""):
    return (os.environ.get(name) or default).strip()


def env_int(name, default):
    try:
        return int(env_str(name) or default)
    except ValueError:
        return default


def env_flag(name, default):
    raw = env_str(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def env_list(name, default):
    raw = env_str(name)
    values = [v.strip() for v in raw.split(",") if v.strip()]
    return values or list(default)


# --- secrets (repo Settings > Secrets and variables > Actions > Secrets) ---
ADZUNA_APP_ID = env_str("ADZUNA_APP_ID")
ADZUNA_APP_KEY = env_str("ADZUNA_APP_KEY")
REED_API_KEY = env_str("REED_API_KEY")
GEMINI_API_KEY = env_str("GEMINI_API_KEY")
GMAIL_ADDRESS = env_str("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = env_str("GMAIL_APP_PASSWORD")

# --- knobs (repo Settings > Secrets and variables > Actions > Variables) ---
# LIVE by default: emails go to real employers. Set the repo variable
# TEST_MODE=1 (or export TEST_MODE=1 locally) to route everything back to
# Harry's own inbox instead.
TEST_MODE = env_flag("TEST_MODE", False)
DAILY_SEND_CAP = env_int("DAILY_SEND_CAP", 20)
PER_RUN_SEND_CAP = env_int("PER_RUN_SEND_CAP", 7)
# Outside the best send window the queue holds back, so the strongest leads
# land when they are most likely to be read. Anything genuinely fresh ignores
# this - see the comment in send_batch.
OFF_PEAK_SEND_CAP = env_int("OFF_PEAK_SEND_CAP", 3)
BRAND_NEW_HOURS = env_int("BRAND_NEW_HOURS", 14)
SEARCH_LOCATIONS = env_list(
    "SEARCH_LOCATIONS", ["Aberdeen", "Dundee", "Edinburgh", "Glasgow", "Inverness"])
SEARCH_RADIUS_MILES = env_int("SEARCH_RADIUS_MILES", 25)
SCORE_THRESHOLD = env_int("SCORE_THRESHOLD", 70)

# --- fixed tuning ---
# MAX_AGE_HOURS and HARVEST_PAGES are module-level on purpose: portal_agent.py
# widens them to sweep a month of listings without disturbing the email path.
MAX_AGE_HOURS = 48
HARVEST_PAGES = 1
FOLLOWUP_AFTER_DAYS = 4
FOLLOWUP2_AFTER_DAYS = 9          # one last nudge to that person
# Then, once, a different human at the same company. Three touches to one
# inbox capture about 93% of the replies a sequence will ever earn; a fourth
# to the same person is pestering, but a first to a new one is a new
# conversation - the one documented reason to go past three.
STAKEHOLDER_AFTER_DAYS = env_int("STAKEHOLDER_AFTER_DAYS", 16)
REPLY_AUTORESPOND = env_flag("REPLY_AUTORESPOND", True)
SPEC_PER_DAY = env_int("SPEC_PER_DAY", 2)
TARGETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "targets.json")
VETERAN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "veteran_employers.json")
SEND_INTERVAL_SECONDS = 30
FOLLOWUP_INTERVAL_SECONDS = 15
IMAP_TIMEOUT = 30          # never let a stalled inbox hang a whole run
MAX_SCORED_PER_RUN = env_int("MAX_SCORED_PER_RUN", 120)  # 12 batched calls
# Sending is the point; scoring is preparation. The workflow gets 45 minutes and
# a rate-limited scorer can eat all of it, so scoring is boxed in to leave room
# for discovery and sending.
SCORE_BUDGET_SECONDS = env_int("SCORE_BUDGET_SECONDS", 900)
MAX_DISCOVERED_PER_RUN = env_int("MAX_DISCOVERED_PER_RUN", 25)
# How old a listing can be and still be worth emailing after its application
# portal defeated us. Matches the portal agent's own window: if it was fresh
# enough to try to apply through, it is fresh enough to write to.
PORTAL_FALLBACK_MAX_AGE_DAYS = env_int("PORTAL_FALLBACK_MAX_AGE_DAYS", 30)
PRUNE_AFTER_DAYS = 45            # drop dead listings so state.json stays small
GEMINI_MODEL = "gemini-2.5-flash"

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, "data", "state.json")
CV_DIRS = [os.path.join(ROOT, "cv"), ROOT]

SEARCH_KEYWORDS = [
    "engineer technician electronics instrumentation communications workshop maintenance",
]
REED_KEYWORDS = ["engineering technician", "electronics technician",
                 "instrumentation technician", "maintenance technician",
                 "communications technician"]

# The rotational market does not live within twenty-five miles of anywhere.
# An offshore or fly-in-fly-out posting is advertised against a base, a vessel
# or a whole country, so a radius search around Aberdeen never sees it - and it
# is the market Harry's trade actually sits in, now that he can take work
# anywhere that comes with the arrangements to live it. These phrases are
# specific enough to sweep the whole UK without dragging in noise.
ROTATIONAL_KEYWORDS = [
    "offshore technician", "rotational technician", "subsea technician",
    "rov technician", "electro technical officer", "offshore electrician",
    "offshore instrument technician", "commissioning technician offshore",
    "field service engineer offshore", "marine electronics technician",
    "hydrographic survey technician", "offshore maintenance technician",
]
ROTATIONAL_WHERE = env_str("ROTATIONAL_WHERE", "United Kingdom")
ROTATIONAL_RADIUS = env_int("ROTATIONAL_RADIUS", 500)

# Titles that are never worth a Gemini call.
TITLE_EXCLUSIONS = [
    "chartered", "principal engineer", "head of", "director", "graduate scheme",
    "sales executive", "sales manager", "business development", "care assistant",
    "support worker", "hgv", "lgv", "delivery driver", "van driver", "chef",
    "waiter", "waitress", "bartender", "cleaner", "warehouse operative",
]

# Course adverts dressed up as vacancies. They turned up five at a time in the
# real queue - "Trainee Incident Response Engineer - job guarantee", "IT
# Technician No experience needed!" - all from a training provider selling a
# course, all scoring just under the bar because the trade words match. They
# are not jobs, and an email to the seller of one is a wasted approach.
NOT_A_VACANCY = re.compile(
    r"job guarantee|guaranteed job|no experience needed|"
    r"course fee|tuition|payment plan|enrol|bootcamp|"
    r"training (provider|programme|program|academy|course)|"
    r"funded training|traineeship|study (with|at) us|"
    r"once qualified we|after completing the course", re.I)


def is_course_advert(job):
    blob = f"{job.get('title') or ''} {(job.get('description') or '')[:1500]}"
    return bool(NOT_A_VACANCY.search(blob))

CANDIDATE_PROFILE = """Harry Russell, Aberdeen, Scotland.
- Ex-Royal Navy Communications & Information Specialist 2021-2023 (HMS Westminster, Type 23 frigate): secure and non-secure comms, network engineering, cryptographic material, safety-critical equipment.
- Workshop Technician at Sonardyne International 2023-2026: assembly, testing and fault diagnosis of subsea acoustic positioning systems (Ranger 2, Mini-Ranger, Solstice, USBL, Compatt) to IPC-A-610 Class 3.
- DV security clearance.
- Completed Engineering Modern Apprenticeship SCQF Level 7 (electrical / asset lifecycle & maintenance).
- Year 1 BEng Instrumentation, Measurement & Control at RGU (paused).
- Also founder of Leads2Profit, a marketing-automation business for nightlife/events venues.
- TARGET: engineering technician, electronics, instrumentation, maintenance, comms/radio, workshop, offshore/O&G, defence, forces-friendly trades, or technical roles in events & nightlife.
- WHERE HE CAN WORK: Aberdeen is home and a daily commute there is ideal, but it
  is NOT a requirement and must not be scored as one. He will take work anywhere
  in the world that comes with the arrangements to live it: offshore and
  rotational postings (2/2, 3/3, 4/4, back-to-back), fly-in fly-out, residential
  or camp accommodation, and roles that pay travel and lodging. A role that
  matches his trade in Dundee, Inverness, Perth Australia, Norway or the Gulf is
  worth as much as the same role in Aberdeen. Only mark a location down when the
  job genuinely needs him to already live somewhere he cannot get to and offers
  nothing towards it - and note that he does not drive, so a remote site with no
  transport laid on is a real problem where a rotational one is not.
- EXCLUDE: senior/chartered roles, unrelated sales/care/driving/hospitality."""

SIGNOFF = "Harry Russell / 07398 530978 / CV attached"

# Added to the prompt only for employers listed in data/veteran_employers.json.
# It asks a question rather than making a claim: Harry's service is a fact
# about Harry, and whether they run a scheme is for them to say. That way a
# wrong entry in that file can put no false statement in his name.
VETERAN_INSTRUCTION = """

THIS EMPLOYER HAS PUBLICLY COMMITTED TO THE ARMED FORCES COVENANT.
Many signatories run a guaranteed interview scheme for veterans who meet the
minimum criteria, and Harry qualifies as a veteran. So:
- Proof point 1 must be his Royal Navy service, stated plainly.
- The call to action must ASK whether they run a guaranteed interview scheme
  for veterans, or words to that effect. Ask - never state or assume that they
  do, and never mention awards, medals or scheme names.
- Everything else about the rules is unchanged."""
PHONE = "07398 530978"


# ======================================================================
# TEMPLATES
# ======================================================================
# Role-family templates plus hard style rules. Gemini fills the skeleton;
# it is not allowed to invent its own structure, and code has the last word.

BANNED = [
    "I hope this email finds you well", "passionate", "leverage", "delve",
    "seamless", "synergy", "dynamic", "thrilled", "excited to apply",
    "perfect fit", "hit the ground running", "fast-paced environment",
    "proven track record", "results-driven", "detail-oriented", "team player",
    "I am writing to", "utilize", "spearheaded", "esteemed", "keen to",
    "furthermore", "moreover",
]

STYLE_RULES = f"""HARD RULES:
- Body is 60-90 words, not counting the greeting and the sign-off.
- Short sentences. Plain English a tradesman would say out loud.
- Greet by first name if one is given, otherwise 'Hi,'.
- Line 1 names the exact role and one concrete detail from THIS listing (a product,
  site, shift pattern, piece of kit, standard, venue) that proves he read it.
- Then 2 or 3 numbered proof points, written as '1.', '2.', '3.' on their own lines.
  Each one must matter to THIS job. Use specifics and numbers. Never list everything.
- Then one line: a single easy question as the call to action.
- Sign off exactly:
Harry
{SIGNOFF}
- Confident and warm, slightly informal, no grovelling, no corporate filler.
- No exclamation marks. No markdown. No em dashes. No banned phrases.

SUBJECT RULES:
- Max 8 words, and it must name the role.
- Concrete hook: who he is or what he can do. Never 'Application for X'.
- No ALL CAPS, at most one punctuation mark."""

TEMPLATES = {
    "communications": {
        "keywords": ["comms", "communication", "radio", "network", "telecom",
                     "signal", "satellite", "rf ", "it support", "systems"],
        "subject_examples": [
            "Royal Navy comms specialist for your {role}",
            "Ran secure comms at sea - your {role}",
        ],
        "skeleton": """{greeting}

Saw your {role} listing - {one specific detail from the listing}.

1. {proof: Royal Navy comms - secure and non-secure comms, networks and crypto material on a Type 23 frigate, 2021-2023}
2. {proof: 3 years at Sonardyne fault-finding electronic kit, tie it to what this job actually needs}
3. {optional third proof only if it is relevant: DV clearance, apprenticeship, RGU study}

{one question CTA}

Harry
""" + SIGNOFF,
    },
    "electronics_technician": {
        "keywords": ["electronic", "electrical", "technician", "workshop",
                     "assembly", "test", "repair", "solder", "pcb", "bench",
                     "manufacturing"],
        "subject_examples": [
            "Sonardyne-trained tech for your {role}",
            "3 yrs subsea electronics - your {role}",
        ],
        "skeleton": """{greeting}

Your {role} listing caught my eye - {one specific detail from the listing}.

1. {proof: 3 years at Sonardyne building, testing and fault-finding subsea acoustic kit to IPC-A-610 Class 3}
2. {proof: component-level diagnosis and repair plus the test records and documentation to match}
3. {optional third proof only if relevant: Royal Navy safety-critical equipment, completed SCQF L7 apprenticeship, DV clearance}

{one question CTA}

Harry
""" + SIGNOFF,
    },
    "instrumentation_maintenance": {
        "keywords": ["instrument", "instrumentation", "maintenance", "calibration",
                     "control", "mechanical", "offshore", "oil", "gas", "subsea",
                     "rov", "plant", "asset", "reliability"],
        "subject_examples": [
            "Instrumentation apprentice-trained - your {role}",
            "Subsea kit background for your {role}",
        ],
        "skeleton": """{greeting}

Applying for your {role} - {one specific detail from the listing}.

1. {proof: 3 years testing and maintaining subsea acoustic positioning systems at Sonardyne}
2. {proof: completed Modern Apprenticeship SCQF L7 in electrical / asset lifecycle and maintenance, plus year 1 BEng Instrumentation, Measurement and Control at RGU}
3. {optional third proof only if relevant: Royal Navy shift work and pressure, DV clearance}

{one question CTA}

Harry
""" + SIGNOFF,
    },
    "events_production": {
        "keywords": ["event", "venue", "nightclub", "bar tech", "av", "audio",
                     "sound", "lighting", "production", "stage", "festival",
                     "hospitality"],
        "subject_examples": [
            "Tech who lives in your industry - {role}",
            "Comms and AV background for your {role}",
        ],
        "skeleton": """{greeting}

Your {role} role stood out - {one specific detail from the venue or listing}.

1. {proof: military-grade comms and electronics background - Royal Navy then 3 years at Sonardyne}
2. {proof: runs Leads2Profit, a marketing systems business built for nightlife and events venues, so he knows this world from the operator side}
3. {optional third proof only if relevant: hands-on with AV, rigging, cabling, fault-finding under time pressure}

{one question CTA}

Harry
""" + SIGNOFF,
    },
    "speculative": {
        # never keyword-matched; only used when code asks for it by name
        "keywords": [],
        "subject_examples": [
            "Ex-Navy subsea tech, Aberdeen - any technician roles?",
            "DV-cleared technician asking about openings",
        ],
        "skeleton": """{greeting}

Nothing advertised that I can see, so this is a speculative note. I know {company} {the one concrete detail provided about what they do}.

1. {proof: 3 years at Sonardyne building, testing and fault-finding subsea electronics to IPC-A-610 Class 3}
2. {proof: 2 years Royal Navy comms on a Type 23 frigate, DV cleared}
3. {optional: completed SCQF L7 apprenticeship, available immediately}

Any technician openings coming up this year worth a conversation?

Harry
""" + SIGNOFF,
    },
    # A consultant is not a hiring manager. They are matching a person against
    # a list of live vacancies, often several at once, and what they need is
    # the facts that let them do it: trade, location, availability, salary,
    # tickets. Research on cold outreach puts a targeted note to a named human
    # at 15-25% reply against 2-5% for a blind application, and an agency
    # consultant is the easiest named human in this market to reach.
    "agency": {
        "keywords": [],
        "subject_examples": [
            "Aberdeen tech, available now - {role}",
            "Electronics tech for your {role}",
        ],
        "skeleton": """{greeting}

Saw you are recruiting a {role} - {one specific detail from the listing}.

1. {proof: what he does - 2 years Royal Navy Communications and Information
   Specialist, 3 years at Sonardyne testing and fault-finding subsea electronics}
2. {proof: the practical facts a consultant needs - Aberdeen based, available
   immediately, looking around 35k, DV cleared}
3. {optional: one ticket or qualification that matches THIS vacancy}

{one question CTA - ask whether this one fits or whether they have something closer}

Harry
""" + SIGNOFF,
    },
    "general": {
        "keywords": [],
        "subject_examples": [
            "Ex-Navy, ex-Sonardyne - your {role}",
            "Hands-on tech for your {role} role",
        ],
        "skeleton": """{greeting}

Saw your {role} listing - {one specific detail from the listing}.

1. {proof: 2 years Royal Navy Communications and Information Specialist}
2. {proof: 3 years at Sonardyne testing and fault-finding precision electronic equipment}
3. {optional third proof only if relevant: completed electrical engineering Modern Apprenticeship, DV clearance}

{one question CTA}

Harry
""" + SIGNOFF,
    },
}


def pick_family(title, description="", company=""):
    """Route a listing to a template family. The title decides; the description
    is only a tie-breaker.

    An agency advert is answered as an agency advert whatever trade is in the
    title: the reader is a consultant matching a person to a list, not the
    manager who will supervise the work."""
    if is_agency({"company": company, "description": description}):
        return "agency"
    title_l = f" {title.lower()} "
    desc_l = description[:600].lower()
    best, best_score = "general", 0.0
    for family, tpl in TEMPLATES.items():
        score = 0.0
        for kw in tpl["keywords"]:
            if kw in title_l:
                score += 1.0
            elif kw in desc_l:
                score += 0.1
        if score > best_score:
            best, best_score = family, score
    return best


# ======================================================================
# STATE
# ======================================================================
def now():
    return datetime.now(timezone.utc).isoformat()


def today():
    return datetime.now(timezone.utc).date().isoformat()


def parse_ts(value):
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        stamp = datetime.fromisoformat(text)
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)
    else:
        state = {}
    state.setdefault("jobs", {})
    state.setdefault("companies_contacted", {})
    state.setdefault("send_counts", {})
    return state


def save(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def sends_today(state):
    return state["send_counts"].get(today(), 0)


def record_send(state):
    state["send_counts"][today()] = sends_today(state) + 1


def company_key(name):
    """Normalised company identity, so 'ACME Ltd.' and 'Acme Limited' collide."""
    text = (name or "").lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(
        r"\b(ltd|limited|plc|llp|inc|group|holdings|uk|international|"
        r"services|solutions|recruitment|the|and)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# A recruitment agency is not an employer and must not be rationed like one.
# An employer has the one job they advertised, and a second unsolicited email
# about a different role reads as pestering. An agency is *paid* to place
# people, holds dozens of roles at once, and expects to hear from candidates
# more than once - six of the fourteen firms in the last portal run were
# agencies, and under the one-email-ever rule each of them was worth exactly
# one approach forever.
AGENCY_NAME = re.compile(
    r"\brecruit|resourcing|staffing|personnel|manpower|talent|search "
    r"(and|&) selection|employment agency|\bagency\b|consultancy|"
    r"\bhays\b|matchtech|morson|randstad|adecco|manpower|reed specialist|"
    r"orion (group|electrotech)|nes fircroft|petroplan|airswift|brunel|"
    r"cammach|thorpe molloy|activate group|first achieve|contract scotland|"
    # Found by building the speculative target list out of employers the
    # machine had already seen: these all came through as 'employers' and are
    # every one of them a recruiter. Writing 'you don't seem to be advertising
    # anything' to a firm whose whole business is advertising things marks the
    # sender as not knowing who he is writing to.
    r"\bselection\b|\bappointments\b|outsource|\breed\b|morgan hunt|"
    r"anson mccade|appcast|engineering employment|\bsearch\b|"
    r"\bsolutions group\b|first military|\bplacements?\b|headhunt|"
    # Three more with no give-away word in the name at all, caught by the
    # agency register in data/agencies.json being checked against this
    # function. Every one of them is a recruiter the pipeline would otherwise
    # have filed as an employer - one email ever, and a speculative 'nothing
    # advertised that I can see' note to a firm whose business is advertising
    # things.
    r"atlas professionals|spencer ogden|rullion|simpson booth",
    re.I)
AGENCY_BODY = re.compile(
    r"our client|on behalf of (our|a) client|we are recruiting for|"
    r"my client|the successful candidate will be employed by|"
    r"acting as an (employment|recruitment) (agency|business)", re.I)

AGENCY_MAX_APPROACHES = env_int("AGENCY_MAX_APPROACHES", 4)
AGENCY_GAP_DAYS = env_int("AGENCY_GAP_DAYS", 6)


def is_agency(job):
    """Is this a recruiter placing someone else's vacancy?

    Name first, then the give-away phrases agencies are legally required to
    use in the advert itself ('acting as an employment agency', 'our client')."""
    if AGENCY_NAME.search(job.get("company") or ""):
        return True
    return bool(AGENCY_BODY.search((job.get("description") or "")[:2000]))


def contact_history(state, job):
    keys = [company_key(job.get("company"))]
    if job.get("company_domain"):
        keys.append(job["company_domain"].lower())
    for key in keys:
        entry = state["companies_contacted"].get(key)
        if entry:
            return entry
    return None


def already_contacted(state, job):
    """One email per employer, ever. Agencies get a working relationship.

    For an agency the limit is a few approaches spaced out, one per role, on
    the grounds that a consultant with a live vacancy wants to hear from a
    candidate who fits it - that is their business model, not an imposition."""
    entry = contact_history(state, job)
    if not entry:
        return False
    if not is_agency(job):
        return True
    approaches = entry.get("count", 1)
    if approaches >= AGENCY_MAX_APPROACHES:
        return True
    last = parse_ts(entry.get("at"))
    if last and (datetime.now(timezone.utc) - last).days < AGENCY_GAP_DAYS:
        return True
    # never twice about the same vacancy
    return job.get("external_id") in (entry.get("jobs") or [])


def mark_contacted(state, job):
    previous = contact_history(state, job) or {}
    entry = {"at": now(), "company": job.get("company"),
             "email": job.get("contact_email"), "job": job.get("external_id"),
             "count": (previous.get("count", 0) + 1),
             "jobs": (previous.get("jobs") or [])[-9:] + [job.get("external_id")]}
    if previous.get("first_at") or previous.get("at"):
        entry["first_at"] = previous.get("first_at") or previous["at"]
    for key in (company_key(job.get("company")),
                (job.get("company_domain") or "").lower()):
        if key:
            state["companies_contacted"][key] = entry


def prune(state):
    """Forget listings that went nowhere, so the committed state stays small.
    Sent/replied history and companies_contacted are kept forever."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_AFTER_DAYS)
    dead = ("skipped", "no_email", "compose_failed", "send_failed")
    doomed = []
    for eid, job in state["jobs"].items():
        found = parse_ts(job.get("found_at"))
        if job.get("status") in dead and found and found < cutoff:
            doomed.append(eid)
    for eid in doomed:
        del state["jobs"][eid]
    if doomed:
        print(f"[state] pruned {len(doomed)} dead listings")


# ======================================================================
# HARVEST
# ======================================================================
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-machine/1.0; +personal job search)"}
TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text):
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text or "")
    text = TAG_RE.sub(" ", text)
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&#39;", "'").replace("&quot;", '"')
                .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", text).strip()


def fresh_enough(posted, granularity="hours"):
    """True if a listing is within MAX_AGE_HOURS."""
    if posted is None:
        return True  # source gave us no date; the scorer still has to like it
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    if granularity == "hours":
        return posted >= cutoff
    # Date-only sources (Reed) give a day, not a time. A job dated D could have
    # gone up at 00:00 that day, so only accept D if the whole day sits inside
    # the window. At the 48h default that works out as today or yesterday.
    threshold = cutoff.date()
    if cutoff.time() != datetime.min.time():
        threshold += timedelta(days=1)
    return posted.date() >= threshold


def adzuna_searches():
    """(where, radius, keywords) for every sweep this run should make.

    The first is the local one: his own city and the ones near it, tight
    radius, broad trade terms. The second is the rotational sweep - the whole
    country, narrow phrases - because an offshore posting is advertised
    against a base or a vessel rather than a town, and a radius search around
    Aberdeen has never once seen one."""
    for location in SEARCH_LOCATIONS:
        for kw in SEARCH_KEYWORDS:
            yield location, SEARCH_RADIUS_MILES, kw
    for kw in ROTATIONAL_KEYWORDS:
        yield ROTATIONAL_WHERE, ROTATIONAL_RADIUS, kw


def adzuna():
    jobs = []
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        print("[harvest] adzuna: no credentials, skipping")
        return jobs
    max_days = max(1, -(-MAX_AGE_HOURS // 24))
    for location, radius, kw in adzuna_searches():
        for page in range(1, HARVEST_PAGES + 1):
            try:
                r = requests.get(
                    f"https://api.adzuna.com/v1/api/jobs/gb/search/{page}",
                    params={
                        "app_id": ADZUNA_APP_ID,
                        "app_key": ADZUNA_APP_KEY,
                        "results_per_page": 50,
                        "what_or": kw,
                        "where": location,
                        "distance": radius,
                        "max_days_old": max_days,
                        "sort_by": "date",
                        "content-type": "application/json",
                    },
                    headers=UA, timeout=30,
                )
                r.raise_for_status()
                results = r.json().get("results", [])
                for j in results:
                    posted = parse_ts(j.get("created"))
                    if not fresh_enough(posted, "hours"):
                        continue
                    jobs.append({
                        "external_id": f"adzuna_{j['id']}",
                        "source": "adzuna",
                        "title": j.get("title", "") or "",
                        "company": (j.get("company") or {}).get("display_name", ""),
                        "location": (j.get("location") or {}).get("display_name", ""),
                        "search_location": location,
                        "url": j.get("redirect_url", ""),
                        "description": strip_html(j.get("description") or "")[:4000],
                        "salary_min": j.get("salary_min"),
                        "salary_max": j.get("salary_max"),
                        "posted_at": posted.isoformat() if posted else None,
                    })
                if len(results) < 50:
                    break
            except Exception as e:
                print(f"[harvest] adzuna {location} p{page}: {e}")
                break
    return jobs


def reed_date(value):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def reed():
    jobs = []
    if not REED_API_KEY:
        print("[harvest] reed: no credentials, skipping")
        return jobs
    for location, kw, page in ((l, k, p) for l in SEARCH_LOCATIONS
                              for k in REED_KEYWORDS
                              for p in range(HARVEST_PAGES)):
        try:
            r = requests.get(
                "https://www.reed.co.uk/api/1.0/search",
                auth=(REED_API_KEY, ""),
                params={
                    "keywords": kw,
                    "locationName": location,
                    "distanceFromLocation": SEARCH_RADIUS_MILES,
                    "resultsToTake": 50,
                    "resultsToSkip": page * 50,
                    "postedByDirectEmployer": "false",
                },
                headers=UA, timeout=30,
            )
            r.raise_for_status()
            for j in r.json().get("results", []):
                posted = reed_date(j.get("date"))
                if not fresh_enough(posted, "date"):
                    continue
                jobs.append({
                    "external_id": f"reed_{j['jobId']}",
                    "source": "reed",
                    "title": j.get("jobTitle", "") or "",
                    "company": j.get("employerName", "") or "",
                    "location": j.get("locationName", "") or "",
                    "search_location": location,
                    "url": j.get("jobUrl", ""),
                    "description": strip_html(j.get("jobDescription") or "")[:4000],
                    "salary_min": j.get("minimumSalary"),
                    "salary_max": j.get("maximumSalary"),
                    "posted_at": posted.isoformat() if posted else None,
                })
        except Exception as e:
            print(f"[harvest] reed {location} '{kw}' p{page}: {e}")
    return jobs


def dedupe_key(job):
    """Same role at the same company from two boards is one opportunity."""
    title = re.sub(r"[^a-z0-9 ]", " ", (job.get("title") or "").lower())
    title = re.sub(r"\s+", " ", title).strip()
    return company_key(job.get("company")) + "|" + title


def title_excluded(title):
    low = f" {(title or '').lower()} "
    return next((x for x in TITLE_EXCLUSIONS if x in low), None)


def not_worth_applying(job):
    """A reason to bin this listing before it costs a Gemini call or a send."""
    excluded = title_excluded(job.get("title"))
    if excluded:
        return f"title excluded ({excluded})"
    if is_course_advert(job):
        return "a training course being sold, not a vacancy"
    return None


def harvest(state):
    seen = {dedupe_key(j) for j in state["jobs"].values()}
    new = dropped = 0
    for job in adzuna() + reed():
        eid = job["external_id"]
        if eid in state["jobs"]:
            continue
        key = dedupe_key(job)
        if key in seen:
            continue
        seen.add(key)
        job.update({"status": "new", "score": None, "found_at": now()})
        reason = not_worth_applying(job)
        if reason:
            job.update({"status": "skipped", "skip_reason": reason})
            dropped += 1
        else:
            new += 1
        state["jobs"][eid] = job
    print(f"[harvest] {new} new listings ({dropped} dropped as unsuitable) "
          f"across {len(SEARCH_LOCATIONS)} locations")


# ======================================================================
# SCORE
# ======================================================================
_gemini_thinking_supported = True
_gemini_last_call = 0.0
# The free tier allows roughly 10 requests a minute. Space every call out here
# rather than trusting each caller to sleep, and batch wherever possible - a
# 429 storm costs far more time than the gap does.
GEMINI_MIN_INTERVAL = float(env_int("GEMINI_MIN_INTERVAL", 7))


def _retry_delay(response, fallback):
    """Google returns the delay it wants in the error body; honour it."""
    try:
        for detail in response.json().get("error", {}).get("details", []):
            delay = detail.get("retryDelay")
            if delay and delay.endswith("s"):
                return min(90.0, float(delay[:-1]) + 1)
    except Exception:
        pass
    return fallback


def gemini(prompt, max_tokens=800, as_json=True, temperature=0.4):
    """One Gemini call. Returns the raw text, or raises."""
    global _gemini_thinking_supported, _gemini_last_call
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    wait = GEMINI_MIN_INTERVAL - (time.monotonic() - _gemini_last_call)
    if wait > 0:
        time.sleep(wait)
    _gemini_last_call = time.monotonic()
    config = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if as_json:
        config["responseMimeType"] = "application/json"
    if _gemini_thinking_supported:
        # thinking tokens come out of the same budget and we do not need them here
        config["thinkingConfig"] = {"thinkingBudget": 0}

    last = None
    for attempt in range(3):
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{GEMINI_MODEL}:generateContent",
                headers={"x-goog-api-key": GEMINI_API_KEY},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": config},
                timeout=90,
            )
            if r.status_code == 400 and "thinking" in r.text.lower():
                _gemini_thinking_supported = False
                config.pop("thinkingConfig", None)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                wait = _retry_delay(r, 15 * (attempt + 1))
                print(f"[gemini] {r.status_code}, waiting {wait:.0f}s")
                time.sleep(wait)
                _gemini_last_call = time.monotonic()
                last = RuntimeError(f"HTTP {r.status_code}")
                continue
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except Exception as e:  # network blips included
            last = e
            time.sleep(5)
    raise last or RuntimeError("gemini failed")


def gemini_json(prompt, max_tokens=800, temperature=0.4):
    try:
        raw = gemini(prompt, max_tokens, as_json=True, temperature=temperature)
    except Exception as e:
        print(f"[gemini] gave up: {e}")
        return None
    clean = raw.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-z]*|```$", "", clean.strip(), flags=re.M).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    print(f"[gemini] unparseable response: {clean[:200]}")
    return None


SCORE_BATCH = env_int("SCORE_BATCH", 10)

# Deterministic pre-filter. Gemini's free tier is the scarcest resource in the
# whole pipeline, so nothing reaches it unless a human would agree the title is
# at least in the right trade. This is cheap, offline, and cuts the AI load by
# roughly four fifths.
RELEVANT_TITLE = re.compile(
    r"technician|engineer|electr|instrument|maintenance|mechanic|fitter|"
    r"calibrat|workshop|assembl|test(er|ing)?\b|repair|subsea|offshore|rov\b|"
    r"comms?\b|communication|radio|network|telecom|satellite|avionic|"
    r"control (system|room)|automation|plc\b|hydraulic|pneumatic|"
    r"inspector|inspection|survey|operative|apprentice|technical|"
    r"fabricat|draught|cad\b|it support|helpdesk|service desk", re.I)
# A description can rescue a vague title like "Field Service Representative".
RELEVANT_BODY = re.compile(
    r"fault[- ]find|diagnos|ipc-a-610|soldering|calibration|test equipment|"
    r"instrumentation|electrical systems|electronic (assembly|equipment)|"
    r"maintenance schedule|planned preventative|subsea|offshore|"
    r"security clearance|ex-forces|service leaver", re.I)


def worth_scoring(job):
    """True if this listing is plausibly in Harry's trade at all."""
    if RELEVANT_TITLE.search(job.get("title") or ""):
        return True
    return bool(RELEVANT_BODY.search((job.get("description") or "")[:1500]))


def score_batch(batch):
    """Score several listings in one call. Returns {index: (score, reason)}.
    Batching is what keeps this inside the Gemini free tier - one call per
    listing burns the whole daily quota on a single run."""
    listings = "\n\n".join(
        f"--- LISTING {i} ---\nTitle: {j['title']}\nCompany: {j['company']}\n"
        f"Location: {j['location']}\n"
        f"Salary: {j.get('salary_min')}-{j.get('salary_max')}\n"
        f"Description: {j['description'][:900]}"
        for i, j in enumerate(batch))
    prompt = (
        f'Screen these {len(batch)} job listings for the candidate. Respond ONLY '
        'with a JSON array, one object per listing, in the same order: '
        '[{"listing": <index>, "score": <0-100>, "reason": "<one short sentence>"}]\n\n'
        f"CANDIDATE:\n{CANDIDATE_PROFILE}\n\n"
        "SCORE GUIDE: 85+ strong direct match in or near Aberdeen. "
        "70-84 good match he could clearly do. 40-69 partial. "
        "<40 poor, wrong field, or on his exclude list. "
        "Penalise roles needing a completed degree plus years of design "
        "experience, and roles outside his target list.\n\n"
        f"{listings}")
    result = gemini_json(prompt, max_tokens=180 * len(batch) + 200, temperature=0.2)
    if isinstance(result, dict):  # a lone object, or {"results": [...]}
        result = result.get("results") or result.get("listings") or [result]
    if not isinstance(result, list):
        return {}
    out = {}
    for i, entry in enumerate(result):
        if not isinstance(entry, dict):
            continue
        index = entry.get("listing", entry.get("index", i))
        try:
            index = int(index)
            score = max(0, min(100, int(float(entry.get("score", 0)))))
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(batch):
            out[index] = (score, str(entry.get("reason", ""))[:300])
    return out


def score_jobs(state):
    # spend the AI budget only on listings that are in the right trade
    pool, filtered = [], 0
    for job in state["jobs"].values():
        if job.get("status") != "new":
            continue
        if worth_scoring(job):
            pool.append(job)
        else:
            job.update({"status": "skipped", "skip_reason": "off target (pre-filter)"})
            filtered += 1
    if filtered:
        print(f"[score] pre-filter set aside {filtered} off-target listing(s) "
              f"without spending a call")
    unscored = pool[:MAX_SCORED_PER_RUN]
    passed = done = 0
    deadline = time.monotonic() + SCORE_BUDGET_SECONDS
    for start in range(0, len(unscored), SCORE_BATCH):
        # Sending is the point of this run; scoring is only preparation for it.
        # Gemini's free tier answers 429 with a retry delay of up to a minute,
        # so a dozen batches can swallow the workflow's whole allowance and
        # leave nothing for discovery and sending - a run that judges a hundred
        # listings and emails nobody. Whatever is left is judged next run.
        if time.monotonic() > deadline:
            print(f"[score] {SCORE_BUDGET_SECONDS}s spent scoring, stopping so "
                  f"there is time left to actually send something")
            break
        batch = unscored[start:start + SCORE_BATCH]
        scores = score_batch(batch)
        if not scores:
            print(f"[score] batch at {start} returned nothing, leaving it for "
                  f"the next run")
            break  # quota or outage: stop, do not burn the rest of the budget
        for i, job in enumerate(batch):
            if i not in scores:
                continue
            job["score"], job["score_reason"] = scores[i]
            done += 1
            if job["score"] >= SCORE_THRESHOLD:
                job["status"] = "scored"
                passed += 1
            else:
                job["status"] = "skipped"
                job["skip_reason"] = f"score {job['score']}"
    print(f"[score] scored {done} in {-(-done // SCORE_BATCH)} call(s), "
          f"{passed} passed >= {SCORE_THRESHOLD}")


# ======================================================================
# DISCOVER - real addresses only
# ======================================================================
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
BAD_PREFIXES = ("noreply", "no-reply", "donotreply", "postmaster", "abuse",
                "privacy", "unsubscribe", "webmaster", "marketing", "newsletter",
                "example", "test", "email", "name", "your", "user", "someone",
                "firstname", "yourname", "sentry", "wordpress", "hostmaster")
BAD_DOMAINS = ("example.com", "example.org", "domain.com", "yourcompany.com",
               "sentry.io", "wixpress.com", "godaddy.com", "squarespace.com",
               "gmail.com", "googlemail.com", "hotmail.com", "outlook.com",
               "yahoo.com", "icloud.com", "aol.com", "live.com")
HIRING_PREFIXES = ("careers", "career", "jobs", "job", "recruitment", "recruiting",
                   "recruit", "hr", "talent", "vacancies", "vacancy", "apply",
                   "people", "hiring", "workforce")
GENERIC_PREFIXES = ("info", "office", "enquiries", "enquiry", "inquiries", "admin",
                    "hello", "contact", "mail", "reception", "accounts", "general",
                    "sales", "support", "team")

TIER_NAMES = {3: "named person", 2: "hiring inbox", 1: "generic inbox"}
COMMON_WORDS = {"help", "team", "here", "click", "more", "news", "home", "main",
                "shop", "data", "site", "post", "west", "east", "north", "south"}


VOWELS = "aeiouy"
ONSETS = {"ch", "sh", "th", "ph", "wh", "br", "cr", "dr", "fr", "gr", "pr", "tr",
          "bl", "cl", "fl", "gl", "pl", "sl", "sm", "sn", "sp", "st", "sc", "sk",
          "sw", "tw", "kn", "wr", "rh", "kh", "gw", "vl", "dw", "qu"}
LONG_ONSETS = {"chr", "thr", "shr", "spr", "str", "scr", "phr", "sch", "sph"}


def plausible_first_name(word):
    """Can we greet someone with this? 'jane' and 'chris' yes, 'jsmith' no -
    getting the name wrong is worse than a plain 'Hi,'."""
    if len(word) < 3 or not word.isalpha():
        return False
    if word[1] in VOWELS:
        return True
    if word[:3] in LONG_ONSETS:
        return True
    return word[:2] in ONSETS and word[2] in VOWELS


# A role word anywhere in the local part means a shared inbox, not a person.
# 'mysupporthr@' slipped through a startswith check and got greeted by name.
ROLE_WORDS = ("hr", "recruit", "career", "job", "vacanc", "hiring", "talent",
              "support", "admin", "info", "sales", "team", "help", "service",
              "enquir", "inquir", "office", "apply", "contact", "payroll",
              "finance", "invoice", "account", "marketing", "press", "media",
              "legal", "privacy", "people", "reception", "general", "hello",
              "workforce", "resourcing", "personnel",
              # Departments that are plainly not a person once you look, but
              # were being greeted by name: 'Dear Fundraise', 'Dear Library',
              # 'Dear Corporate'. Every one of these is a real inbox at a real
              # organisation this project has written to.
              "fundrais", "donat", "legac", "volunteer", "member", "librar",
              "event", "corporate", "training", "course", "admission",
              "alumni", "booking", "ticket", "shop", "retail", "estates",
              "facilit", "procure", "supplier", "tender", "compliance",
              "quality", "safety", "hse", "communit", "partnership",
              "sponsor", "welfare", "advice", "referral")

# A place is not a person. 'canada@matchtech.com' was greeted 'Dear Canada'
# and 'africa.recruitment@enermech.com' was greeted 'Dear Africa' - both real
# applications, both to the wrong continent's office of the right company.
# These are regional inboxes, which makes them shared inboxes twice over.
PLACE_WORDS = ("canada", "america", "americas", "africa", "asia", "europe",
               "emea", "apac", "middleeast", "nordics", "benelux", "iberia",
               "usa", "uk", "eu", "india", "china", "japan", "brazil", "mexico",
               "australia", "singapore", "malaysia", "norway", "netherlands",
               "germany", "france", "spain", "italy", "poland", "romania",
               "houston", "dubai", "aberdeen", "london", "glasgow", "edinburgh",
               "manchester", "bristol", "leeds", "cardiff", "belfast",
               "scotland", "england", "wales", "ireland", "north", "south",
               "east", "west", "global", "international", "worldwide")

# Of those, the ones that are a reason to prefer an address rather than to be
# wary of it. The Aberdeen office of a company is the right desk to write to.
HOME_PLACES = ("aberdeen", "scotland", "glasgow", "edinburgh", "uk")


ROLE_WORDS_LONG = tuple(w for w in ROLE_WORDS if len(w) >= 5)
ROLE_WORDS_SHORT = tuple(w for w in ROLE_WORDS if len(w) < 5)


def place_segments(local):
    """The parts of an address that name somewhere rather than someone.

    Whole segments, matched exactly. A prefix rule looks tidier and is wrong:
    'frances' starts with 'france', and a woman called Frances is not a
    regional office. Plurals are listed rather than derived for the same
    reason."""
    return [segment for segment in re.split(r"[._\-0-9]+", local)
            if segment in PLACE_WORDS]


def is_place(local):
    return bool(place_segments(local))


def is_home_place(local):
    """A regional inbox Harry would actually want: the Aberdeen office beats
    the Houston one, and both beat a person who is not there."""
    return any(segment.startswith(HOME_PLACES) for segment in place_segments(local))


def has_role_word(local):
    """Short words need a boundary: 'hr' sits inside 'chris', so matching it
    anywhere would refuse to greet a man called Chris Brown."""
    if is_place(local):
        return True
    if any(word in local for word in ROLE_WORDS_LONG):
        return True
    for segment in (s for s in re.split(r"[._\-0-9]+", local) if s):
        if any(segment.startswith(w) or segment.endswith(w)
               for w in ROLE_WORDS_SHORT):
            return True
    return False


def is_personal(local):
    """True only if the local part belongs to an individual rather than a shared
    inbox: jane.smith, j.smith and jane all qualify, careers and info do not."""
    if has_role_word(local):
        return False
    if local in GENERIC_PREFIXES or local in HIRING_PREFIXES:
        return False
    if local.startswith(BAD_PREFIXES):
        return False
    parts = [p for p in re.split(r"[._\-]", local) if p]
    if not parts or not all(p.isalpha() for p in parts):
        return False
    if len(parts) >= 2:
        return all(len(p) >= 1 for p in parts) and any(len(p) >= 3 for p in parts)
    single = parts[0]
    return 3 <= len(single) <= 12 and single not in COMMON_WORDS


def name_from_email(local):
    """First name to greet with, or None. Only ever taken from the address as
    written - nothing is inferred or invented."""
    parts = [p for p in re.split(r"[._\-]", local) if p]
    if not parts or len(parts[0]) <= 2 or not parts[0].isalpha():
        return None  # 'j.smith' is an initial, not a name we can greet with
    if len(parts) == 1 and not plausible_first_name(parts[0]):
        return None  # 'jsmith' is a surname with an initial stuck on the front
    return parts[0].capitalize()


def classify(address):
    """(tier, first_name). 3 named person > 2 hiring inbox > 1 generic > 0 unusable."""
    local = address.split("@")[0].lower()
    if is_personal(local):
        return 3, name_from_email(local)
    # a hiring word anywhere counts - 'mysupporthr' is still an HR inbox
    if local.startswith(HIRING_PREFIXES) or any(w in local for w in HIRING_PREFIXES):
        return 2, None
    if local.startswith(GENERIC_PREFIXES) or any(w in local for w in GENERIC_PREFIXES):
        return 1, None
    # A regional inbox is a real desk, just not a person's. 'aberdeen@' is one
    # of the better addresses at a company with an Aberdeen office; the only
    # thing that must never happen is greeting it by name.
    if is_place(local):
        return 1, None
    return 0, None


def has_mx(domain):
    if dns is None:
        print("[discover] dnspython missing, cannot MX-check")
        return False
    try:
        return len(dns.resolver.resolve(domain, "MX")) > 0
    except Exception:
        return False


def clean_emails(raw, domain=None):
    out = []
    for candidate in {x.lower().strip(" .,;:'\"<>()") for x in raw}:
        if candidate.count("@") != 1:
            continue
        local, _, host = candidate.partition("@")
        if not local or local.startswith(BAD_PREFIXES):
            continue
        if host in BAD_DOMAINS or host.endswith((".png", ".jpg", ".jpeg", ".gif",
                                                 ".webp", ".svg", ".css", ".js")):
            continue
        if domain and host != domain and not host.endswith("." + domain):
            continue
        out.append(candidate)
    return sorted(out)


# Harry is job-hunting in Scotland. A domain on a far-flung ccTLD is a sign
# Clearbit matched a different company with a similar name, not his employer.
PLAUSIBLE_TLDS = (".com", ".co.uk", ".uk", ".org", ".org.uk", ".net", ".io",
                  ".scot", ".eu", ".ie", ".no", ".nl", ".de", ".fr", ".dk",
                  ".se", ".fi", ".it", ".es", ".ltd", ".plc", ".group", ".energy")


def plausible_domain(domain):
    return bool(domain) and domain.lower().endswith(PLAUSIBLE_TLDS)


def name_tokens(name):
    return {t for t in company_key(name).split() if t}


# Words a company can legitimately bolt onto its own name in a domain.
# 'sanctuary' -> 'sanctuarygroup.co.uk' is the same company; 'sanctuary' ->
# 'sanctuaryclothing.com' is a clothing brand in California.
DOMAIN_SUFFIXES = ("group", "uk", "ltd", "limited", "plc", "co", "com",
                   "global", "int", "international", "energy", "services",
                   "eng", "engineering", "tech", "technologies", "online")


def domain_matches_company(company, domain):
    """Could this domain plausibly belong to this company?

    A last check before anything is sent, because the state file is merged
    across runs and a record written before a matching bug was fixed comes
    back with the bad domain still on it. Clearing those by hand did not hold:
    the merge saw main's 'ready' as further along than my corrected
    'no_email' and restored it. A guard at the point of sending does not care
    what the file says.

    Only single-word company names are policed, because that is where the
    ambiguity is - 'Sanctuary' matched Sanctuary Clothing, 'Wood' matched
    Woodforest, 'Stork' matched stork24.eu. A multi-word name is specific
    enough to trust, and gating it would throw away real matches like
    Northern Lighthouse Board at nlb.org.uk."""
    if not company or not domain:
        return True
    tokens = name_tokens(company)
    if len(tokens) != 1:
        return True
    token = next(iter(tokens))
    root = re.split(r"[.]", domain.lower().strip())[0]
    if root == token:
        return True
    if not root.startswith(token):
        return False
    rest = root[len(token):].strip("-_")
    if rest in DOMAIN_SUFFIXES:
        return True
    # Or a word from the company's own name. company_key strips 'recruitment',
    # 'group', 'solutions' and the like, so 'Canmore Recruitment' reduces to
    # the single token 'canmore' - and canmorerecruitment.com is obviously
    # theirs. Checking the remainder against the words actually in the name
    # keeps that, while still refusing 'clothing' for Sanctuary.
    own_words = set(re.sub(r"[^a-z0-9 ]", " ", (company or "").lower()).split())
    return rest in own_words


def find_domain(company):
    """Clearbit autocomplete: free, no key.

    Only a confident match is accepted. Matching on a raw substring is what put
    a note meant for Wood plc into the inbox of Woodforest National Bank, so
    every word of the company name now has to appear as a whole word in the
    match, and the domain has to sit on a plausible TLD."""
    wanted = company_key(company)
    wanted_tokens = name_tokens(company)
    if not wanted_tokens:
        return None
    try:
        r = requests.get("https://autocomplete.clearbit.com/v1/companies/suggest",
                         params={"query": company}, headers=UA, timeout=15)
        r.raise_for_status()
        hits = r.json()
        if not isinstance(hits, list):
            return None
        for hit in hits:
            domain = hit.get("domain")
            if not domain or not plausible_domain(domain):
                continue
            hit_key = company_key(hit.get("name", ""))
            if hit_key == wanted:
                return domain
            # A one-word company name is not enough to identify anybody. The
            # subset rule below is satisfied by ANY firm containing that word,
            # which matched the housing association 'Sanctuary' to Sanctuary
            # Clothing in California - and to a named individual there, so an
            # application was one run away from landing in a stranger's inbox
            # at an unrelated company on another continent. Same family of
            # mistake as Wood and Woodforest National Bank, through a
            # different door. For a single-token name, nothing but an exact
            # match will do.
            if len(wanted_tokens) < 2:
                continue
            # every word Harry's listing gave us must be a whole word in the
            # match - 'wood' must not match 'woodforest'
            if wanted_tokens <= name_tokens(hit.get("name", "")):
                return domain
        print(f"[discover] no confident domain for '{company}'")
    except Exception as e:
        print(f"[discover] clearbit '{company}': {e}")
    return None


SCRAPE_PATHS = ("", "/contact", "/contact-us", "/careers", "/jobs",
                "/join-us", "/about", "/about-us", "/team", "/our-team", "/people")


def scrape_site(domain):
    raw = []
    for path in SCRAPE_PATHS:
        for scheme in ("https", "http"):
            try:
                r = requests.get(f"{scheme}://{domain}{path}", headers=UA, timeout=12)
                if r.status_code == 200:
                    raw += EMAIL_RE.findall(r.text)
                    raw += [m.replace("%40", "@") for m in
                            re.findall(r"mailto:([^\"'?>\s]+)", r.text)]
                break
            except Exception:
                continue
        time.sleep(0.5)  # be polite to small company sites
    return clean_emails(raw, domain)


def fetch_listing_text(job):
    """Full advert text, where the source gives us a clean way to get it."""
    try:
        if job["source"] == "reed" and REED_API_KEY:
            jid = job["external_id"].split("_", 1)[1]
            r = requests.get(f"https://www.reed.co.uk/api/1.0/jobs/{jid}",
                             auth=(REED_API_KEY, ""), headers=UA, timeout=20)
            if r.ok:
                return strip_html(r.json().get("jobDescription") or "")
        elif job.get("url"):
            r = requests.get(job["url"], headers=UA, timeout=20)
            if r.ok:
                return strip_html(r.text)
    except Exception as e:
        print(f"[discover] listing fetch failed: {e}")
    return ""


def best_email(candidates):
    """(email, first_name, tier) - highest tier wins. Never invents anything."""
    best = (None, None, 0)
    for address in candidates:
        tier, name = classify(address)
        if tier > best[2]:
            best = (address, name, tier)
    return best


def ranked_emails(candidates):
    """Every real address found for this employer, best first.

    Outreach research is clear that a second, different person at the same
    company is worth reaching when the first goes quiet - it is the one thing
    that justifies going past three touches. These are addresses already found
    and already real; keeping them costs nothing and saves discovering them
    again weeks later."""
    seen, out = set(), []
    for address in candidates:
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        tier, name = classify(address)
        if tier >= 1:
            out.append({"email": address, "name": name, "tier": tier})
    return sorted(out, key=lambda c: -c["tier"])


def discover(state):
    todo = [j for j in state["jobs"].values()
            if j["status"] == "scored"][:MAX_DISCOVERED_PER_RUN]
    found = 0
    for job in todo:
        # 1) addresses printed in the advert itself - directly tied to this job
        text = job.get("description", "") + " " + fetch_listing_text(job)
        job["listing_text_len"] = len(text)
        found_here = clean_emails(EMAIL_RE.findall(text))
        email_addr, name, tier = best_email(found_here)
        ranked = ranked_emails(found_here)
        method = "listing"

        # 2) the company's own website
        if not email_addr:
            company = (job.get("company") or "").strip()
            if not company:
                job.update({"status": "skipped", "skip_reason": "no company name"})
                continue
            domain = find_domain(company)
            if not domain:
                job.update({"status": "no_email", "skip_reason": "no domain found"})
                continue
            job["company_domain"] = domain
            if not has_mx(domain):
                job.update({"status": "no_email", "skip_reason": "domain has no MX"})
                continue
            scraped = scrape_site(domain)
            email_addr, name, tier = best_email(scraped)
            ranked = ranked_emails(scraped)
            method = "scraped"

        # 3) nothing real found -> do not send. No guessing, ever.
        if not email_addr or tier < 1:
            job.update({"status": "no_email", "skip_reason": "no real address found"})
            continue
        if not has_mx(email_addr.split("@")[1]):
            job.update({"status": "no_email", "skip_reason": "address domain has no MX"})
            continue

        job.update({"contact_email": email_addr, "contact_name": name,
                    "email_method": method, "email_tier": tier, "status": "ready",
                    "other_contacts": [c for c in ranked
                                       if c["email"].lower() != email_addr.lower()][:3]})
        found += 1
        print(f"[discover] {job['company']} -> {name or email_addr} <{email_addr}> "
              f"({TIER_NAMES.get(tier)}, via {method})")
    print(f"[discover] {found}/{len(todo)} listings got a real address")


# ======================================================================
# COMPOSE
# ======================================================================
BANNED_RES = [(b, re.compile(r"\b" + re.escape(b.lower()) + r"\b")) for b in BANNED]
STOPWORDS = {"the", "and", "for", "with", "role", "jobs", "job", "full", "time",
             "part", "permanent", "contract", "based", "new", "our", "your"}


def slop_check(text):
    low = text.lower()
    return [b for b, rx in BANNED_RES if rx.search(low)]


def normalise(text):
    """Code owns punctuation and formatting; the model only supplies words."""
    text = (text or "").replace("—", " - ").replace("–", "-")
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[*_#`]+", "", text)
    text = re.sub(r"!+", ".", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


SIGNOFF_LINE_RE = re.compile(
    r"^(harry\b|best\b|kind regards|regards|thanks|cheers|sincerely|yours\b)",
    re.I)


def strip_signoff(body):
    """Remove whatever sign-off the model produced so code can add the real one.
    Cuts from the first sign-off marker in the last few lines to the end, so
    'Best wishes,\\nH' goes entirely."""
    lines = body.rstrip().split("\n")
    cut = None
    for i in range(max(0, len(lines) - 5), len(lines)):
        line = lines[i].strip()
        if SIGNOFF_LINE_RE.match(line) or PHONE in line or "cv attached" in line.lower():
            cut = i
            break
    if cut is not None:
        lines = lines[:cut]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).rstrip()


def assemble(body, greeting):
    """Force the exact greeting and the exact sign-off around the model's copy."""
    body = normalise(body)
    lines = body.split("\n")
    if lines and (lines[0].lower().startswith("hi") or lines[0].lower().startswith("hello")
                  or lines[0].lower().startswith("dear")):
        lines = lines[1:]
    core = strip_signoff("\n".join(lines).strip())
    return f"{greeting}\n\n{core}\n\nHarry\n{SIGNOFF}", core


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9'/-]+", text))


def role_tokens(title):
    return [t for t in re.findall(r"[a-z]+", (title or "").lower())
            if len(t) >= 4 and t not in STOPWORDS]


def subject_names_role(subject, title):
    low = subject.lower()
    tokens = role_tokens(title)
    if not tokens:
        return True
    return any(t[:5] in low for t in tokens)


def email_problems(subject, core, job):
    """Everything code refuses to send. Returned as feedback for the retry."""
    problems = []
    banned = slop_check(subject + " " + core)
    if banned:
        problems.append(f"banned phrases used: {banned}")

    words = subject.split()
    if len(words) > 8:
        problems.append(f"subject is {len(words)} words, max is 8")
    if "application for" in subject.lower():
        problems.append("subject must not say 'Application for'")
    if not subject_names_role(subject, job.get("title", "")):
        problems.append(f"subject must name the role ({job.get('title')})")
    if subject.isupper():
        problems.append("subject must not be ALL CAPS")

    if job.get("source") != "speculative":
        first_line = next((l for l in core.split("\n") if l.strip()), "")
        if not subject_names_role(first_line, job.get("title", "")):
            problems.append(f"the first line must name the exact role "
                            f"({job.get('title')}) plus one detail from the listing")

    count = word_count(core)
    if not 60 <= count <= 90:
        problems.append(f"body is {count} words, must be 60-90 "
                        f"(excluding greeting and sign-off)")
    numbered = re.findall(r"^\s*(\d)[.)]\s+\S", core, re.M)
    if len(numbered) < 2:
        problems.append("body must contain 2 or 3 numbered proof points "
                        "on their own lines, starting '1.' and '2.'")
    if len(numbered) > 3:
        problems.append("body must have at most 3 numbered proof points")
    if core.count("?") != 1:
        problems.append("body must end with exactly one question as the CTA")
    return problems


def plain_email(job):
    """A hand-written application, with no model in the loop.

    Gemini's free tier has a daily ceiling and this project is meant to cost
    nothing to run, so hitting that ceiling is a normal Tuesday rather than an
    exception. When it happened, every send stopped: the composer returned
    nothing, the listing was marked compose_failed, and a matched job with a
    real verified address went nowhere - because a rate limit on a free API
    had been allowed to become a hard dependency for applying to a job.

    This is deliberately plainer than the composed version. It is not trying
    to beat it; it is trying to beat not sending. The applied-note already
    works this way for the same reason."""
    greeting = f"Hi {job['contact_name']}," if job.get("contact_name") else "Hi,"
    title = (job.get("title") or "technician").strip()
    company = (job.get("company") or "").strip()
    where = f" at {company}" if company else ""
    ask = ("Do you run a guaranteed interview scheme for veterans who meet the "
           "criteria? If so I would like to be considered under it."
           if veteran_friendly(job) else
           "Would it be worth a short call?")
    body = (
        f"{greeting}\n\n"
        f"I am applying for the {title} role{where}.\n\n"
        f"1. Three years at Sonardyne building, testing and fault-finding "
        f"subsea acoustic electronics to IPC-A-610 Class 3.\n"
        f"2. Two years Royal Navy Communications and Information Specialist "
        f"on a Type 23 frigate. DV cleared.\n"
        f"3. SCQF Level 7 engineering apprenticeship, available immediately, "
        f"and happy with rotational or offshore work.\n\n"
        f"{ask}\n\n"
        f"Harry\n{SIGNOFF}"
    )
    subject = f"{title} - application" if not company else \
        f"{title} at {company} - application"
    return {"subject": subject[:120], "body": body, "family": "plain"}


def build_email(job, attempts=3):
    family = ("speculative" if job.get("source") == "speculative"
              else pick_family(job["title"], job.get("description", ""),
                               job.get("company", "")))
    job["template_family"] = family
    tpl = TEMPLATES[family]
    greeting = f"Hi {job['contact_name']}," if job.get("contact_name") else "Hi,"
    base = (
        'Write a cold job-application email by filling the skeleton below. '
        'Respond ONLY with JSON: {"subject": "...", "body": "..."}\n\n'
        f"{STYLE_RULES}\n\n"
        f"BANNED PHRASES (never use any of these): {'; '.join(BANNED)}\n\n"
        f"GREETING TO USE EXACTLY: {greeting}\n"
        f"SUBJECT EXAMPLES (match this energy, adapt to the listing): "
        f"{tpl['subject_examples']}\n\n"
        "SKELETON - keep this structure, replace every {placeholder} with real "
        "copy, drop the third proof point if it does not help THIS job:\n"
        f"{tpl['skeleton']}\n\n"
        f"CANDIDATE FACTS (for accuracy only, never dump them all in):\n"
        f"{CANDIDATE_PROFILE}\n\n"
        f"THE JOB:\nTitle: {job['title']}\nCompany: {job['company']}\n"
        f"Location: {job['location']}\n"
        f"Recipient: {job.get('contact_name') or 'unknown, a shared hiring inbox'}\n"
        f"Listing: {job['description'][:2000]}"
        + (VETERAN_INSTRUCTION if veteran_friendly(job) else "")
    )
    prompt = base
    best = None
    for attempt in range(attempts):
        content = gemini_json(prompt, max_tokens=900, temperature=0.6)
        if not content or not content.get("subject") or not content.get("body"):
            prompt = base  # bad shape, start clean
            continue
        subject = normalise(str(content["subject"])).split("\n")[0].strip(' "')
        body, core = assemble(str(content["body"]), greeting)
        problems = email_problems(subject, core, job)
        if not problems:
            return {"subject": subject, "body": body, "family": family}
        print(f"[compose] attempt {attempt + 1} rejected: {problems}")
        if best is None or len(problems) < len(best[0]):
            best = (problems, subject, body)
        prompt = (base + "\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED:\n- "
                  + "\n- ".join(problems)
                  + "\nRewrite it, fixing every point. Keep everything else.")
    if best:
        job["compose_problems"] = best[0]
    return None


# ======================================================================
# SEND
# ======================================================================
def cv_path():
    for directory in CV_DIRS:
        pdfs = sorted(glob.glob(os.path.join(directory, "*.pdf")))
        if pdfs:
            return pdfs[0]
    return None


def cv_for(job):
    """The CV to attach for this job: tailored to its role family when the
    tailoring pipeline is available, the master PDF otherwise."""
    try:
        import cv_tailor
        family = pick_family(job.get("title", ""), job.get("description", ""),
                             job.get("company", ""))
        tailored = cv_tailor.build(family)
        if tailored:
            return tailored
    except Exception as e:
        print(f"[cv] tailoring unavailable: {e}")
    return cv_path()


def send_email(to_addr, subject, body, attach_cv=True, headers=None,
               cv_file=None, attachments=None):
    """Returns the Message-ID so follow-ups can thread onto the original.

    `attachments` is for anything that is not the CV: a list of
    (filename, maintype, subtype, bytes). The handoff page uses it to put a
    working, fully filled-in application page in Harry's own inbox, where a
    desktop can open it - a build artifact cannot do that."""
    msg = EmailMessage()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = email.utils.make_msgid(domain="gmail.com")
    msg["Date"] = email.utils.formatdate(localtime=True)
    for key, value in (headers or {}).items():
        if value:
            msg[key] = value
    msg.set_content(body)
    cv = cv_file or cv_path()
    if attach_cv and cv:
        with open(cv, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                               filename="Harry_Russell_CV.pdf")
    for name, maintype, subtype, payload in (attachments or []):
        msg.add_attachment(payload, maintype=maintype, subtype=subtype,
                           filename=name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.send_message(msg)
    return msg["Message-ID"]


def run_sends(state, dry_run=False, already_sent=0):
    """Send the queued applications. Returns how many have gone out this run.

    'already_sent' carries the count across calls, because this stage runs
    more than once per run - once before the slow stages so a queued
    application cannot be stranded by the workflow timeout, once after so
    anything discovery has just found an address for still goes today. A cap
    that reset between the two would quietly be a cap of fourteen."""
    if not cv_path():
        print("[send] no CV PDF in cv/ or repo root - refusing to send")
        return already_sent
    if not dry_run and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        print("[send] no Gmail credentials - refusing to send")
        return already_sent

    # freshest first within equal tier+score - being an early applicant on a new
    # listing is worth more than being late on a stale one
    ready = sorted([j for j in state["jobs"].values() if j["status"] == "ready"],
                   key=lambda j: (j.get("posted_at") or j.get("found_at") or ""),
                   reverse=True)
    # An employer running a guaranteed interview scheme converts an application
    # into an interview by policy rather than by persuasion, so they go first
    # whatever else is in the queue.
    ready.sort(key=lambda j: (-int(veteran_friendly(j)),
                              -(j.get("email_tier") or 0), -(j.get("score") or 0)))

    budget = PER_RUN_SEND_CAP if in_peak_window() else OFF_PEAK_SEND_CAP
    if not in_peak_window():
        print(f"[send] outside the Tuesday-Thursday 9-11am window, holding all "
              f"but {budget} back for it (fresh listings still go now)")
    sent = already_sent
    for job in ready:
        # The per-run cap is absolute: it is what keeps a burst of new listings
        # from putting twenty emails through one Gmail session.
        if sent >= PER_RUN_SEND_CAP:
            print(f"[send] per-run cap ({PER_RUN_SEND_CAP}) reached")
            break
        # Under that, two findings pull against each other. Applying within
        # 24-48 hours produces two to three times the interviews; a Tuesday-to-
        # Thursday, 9-11am send gets the best open and reply rates. Speed is
        # measured in days and timing in hours, so speed wins for anything
        # genuinely fresh and timing decides the rest of the queue.
        if sent >= budget and not (brand_new(job) or already_waited(job)):
            print(f"[send] {sent} sent, holding the rest for the window")
            break
        if sends_today(state) >= DAILY_SEND_CAP:
            print(f"[send] daily cap ({DAILY_SEND_CAP}) reached")
            break
        if not TEST_MODE and already_contacted(state, job):
            job.update({"status": "skipped", "skip_reason": "company already contacted"})
            continue
        # Last line of defence against a misdirected application. Records are
        # merged across runs, so one written before a matching bug was fixed
        # comes back carrying the bad domain - two applications for Sanctuary
        # the housing association were queued to a named person at Sanctuary
        # Clothing in California.
        if not domain_matches_company(job.get("company"),
                                      job.get("company_domain")):
            job.update({"status": "no_email", "contact_email": None,
                        "contact_name": None, "company_domain": None,
                        "skip_reason": "domain does not belong to this company"})
            print(f"[send] REFUSED {job.get('company')} -> "
                  f"{job.get('company_domain')}: not the same company")
            continue

        content = build_email(job)
        if not content:
            # A rate limit on a free API is not a reason not to apply for a
            # job. Fall back to the hand-written letter rather than dropping a
            # matched listing that already has a real, verified address.
            content = plain_email(job)
            print(f"[compose] model unavailable, sending the plain letter for "
                  f"{job['title']} @ {job['company']}")
        if not content:
            job["status"] = "compose_failed"
            print(f"[compose] gave up on {job['title']} @ {job['company']}")
            continue

        real_to = job["contact_email"]
        to_addr = GMAIL_ADDRESS if TEST_MODE else real_to
        subject = (f"[TEST -> {real_to}] {content['subject']}"
                   if TEST_MODE else content["subject"])

        if dry_run:
            print(f"\n--- DRY RUN -> {to_addr}\nSubject: {subject}\n\n{content['body']}\n")
            job.update({"status": "ready", "draft_subject": subject,
                        "draft_body": content["body"]})
            sent += 1
            continue

        try:
            message_id = send_email(to_addr, subject, content["body"],
                                    cv_file=cv_for(job))
        except Exception as e:
            job.update({"status": "send_failed", "send_error": str(e)[:200]})
            print(f"[send] failed {job['company']}: {e}")
            continue

        job.update({
            "status": "test_sent" if TEST_MODE else "sent",
            "sent_at": now(),
            "sent_to": to_addr,
            "sent_subject": subject,
            "sent_body": content["body"],
            "message_id": message_id,
            "template_family": content["family"],
        })
        record_send(state)
        if not TEST_MODE:
            mark_contacted(state, job)
        sent += 1
        label = "TEST" if TEST_MODE else "LIVE"
        print(f"[send] {label} {job['title']} @ {job['company']} -> {to_addr}")
        save(state)
        if sent < PER_RUN_SEND_CAP:
            time.sleep(SEND_INTERVAL_SECONDS)
    print(f"[send] {sent} message(s) this run, {sends_today(state)} today")
    return sent


def run_applied_notes(state):
    """The double-tap: after the portal agent submits an application, the named
    person we found gets a short heads-up so it does not sit unread in an ATS.
    Deterministic copy - no model in the loop for these."""
    todo = [j for j in state["jobs"].values()
            if j.get("status") == "portal_submitted"
            and j.get("contact_email") and (j.get("email_tier") or 0) >= 3
            and not j.get("applied_note_sent_at")]
    for job in todo:
        if sends_today(state) >= DAILY_SEND_CAP:
            break
        name = job.get("contact_name")
        body = (
            f"Hi {name}," if name else "Hi,") + (
            f"\n\nI have just applied for your {job['title']} role through the "
            f"online portal and wanted to reach out directly as well. Quick "
            f"background: 3 years testing and fault-finding subsea electronics at "
            f"Sonardyne to IPC-A-610 Class 3, Royal Navy communications before "
            f"that, DV cleared and available immediately.\n\n"
            f"If the application is worth a closer look, when suits a quick call?"
            f"\n\nHarry\n{SIGNOFF}")
        real_to = job["contact_email"]
        to_addr = GMAIL_ADDRESS if TEST_MODE else real_to
        subject = f"Just applied - {job['title']}"
        if TEST_MODE:
            subject = f"[TEST -> {real_to}] {subject}"
        try:
            message_id = send_email(to_addr, subject, body, cv_file=cv_for(job))
            job.update({"applied_note_sent_at": now(), "message_id": message_id,
                        "sent_at": job.get("sent_at") or now(),
                        "sent_subject": subject, "sent_body": body})
            record_send(state)
            print(f"[note] applied-note -> {to_addr} ({job['company']})")
            save(state)
            time.sleep(SEND_INTERVAL_SECONDS)
        except Exception as e:
            print(f"[note] failed {job['company']}: {e}")


# ======================================================================
# SPECULATIVE - the hidden market: employers with no advert up
# ======================================================================
_VETERAN_KEYS = None


def veteran_employers():
    """Normalised names of employers who have committed to the Armed Forces
    Covenant. Read once and kept."""
    global _VETERAN_KEYS
    if _VETERAN_KEYS is None:
        try:
            with open(VETERAN_PATH) as f:
                entries = json.load(f).get("employers", [])
            _VETERAN_KEYS = {company_key(e.get("company")) for e in entries
                             if e.get("company")}
        except Exception:
            _VETERAN_KEYS = set()
    return _VETERAN_KEYS


def veteran_friendly(job):
    """Has this employer publicly committed to the Armed Forces Covenant?

    Many signatories run a guaranteed interview scheme: a veteran who meets the
    minimum criteria for a role is interviewed. Harry served two years as a
    Royal Navy Communications and Information Specialist, so where such a
    scheme exists he qualifies for it - which is worth more than any number of
    extra cold emails, because it converts an application into an interview by
    policy rather than by persuasion.

    Nothing in the code ever tells an employer they hold an award. The email
    asks whether they run a scheme, which is true whoever reads it."""
    key = company_key(job.get("company"))
    if not key:
        return False
    known = veteran_employers()
    if key in known:
        return True
    # 'Babcock International Group' should match 'Babcock International'
    return any(k and (key.startswith(k + " ") or k.startswith(key + " "))
               for k in known)


def load_targets():
    try:
        with open(TARGETS_PATH) as f:
            return json.load(f).get("targets", [])
    except Exception:
        return []


def grow_targets(state):
    """Add employers the machine has seen advertising this trade to the
    speculative list. Imported here so job_machine stays importable by the
    tool itself without a circular import at module load."""
    import spec_targets
    try:
        with open(TARGETS_PATH) as f:
            data = json.load(f)
    except OSError:
        return 0
    existing = data.get("targets", [])
    found = spec_targets.candidates(state, existing)
    if not found:
        print("[targets] no new employers with evidence this run")
        return 0
    data["targets"] = existing + found
    with open(TARGETS_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"[targets] {len(found)} employer(s) added, "
          f"{len(data['targets'])} on the speculative list")
    return len(found)


def spec_sends_today(state):
    return state.setdefault("spec_counts", {}).get(today(), 0)


def speculative(state):
    """Two short notes a day to curated employers who are not advertising.
    Real addresses only, one per company ever - same rules as everything else."""
    targets = load_targets()
    done = state.setdefault("spec_done", {})
    sent = 0
    for target in targets:
        if spec_sends_today(state) >= SPEC_PER_DAY:
            break
        if sends_today(state) >= DAILY_SEND_CAP:
            break
        company = target["company"]
        key = company_key(company)
        if key in done or key in state["companies_contacted"]:
            continue

        domain = find_domain(company)
        if not domain or not has_mx(domain):
            done[key] = "no domain or MX"
            continue
        addr, name, tier = best_email(scrape_site(domain))
        if not addr or tier < 1 or not has_mx(addr.split("@")[1]):
            done[key] = "no real address"
            continue

        job = {
            "external_id": f"spec_{key.replace(' ', '_')}",
            "source": "speculative", "title": "Engineering Technician",
            "company": company, "location": "Aberdeen / Scotland",
            "company_domain": domain, "url": "",
            "description": f"Speculative approach. What they do: {target['note']}.",
            "score": None, "found_at": now(),
            "contact_email": addr, "contact_name": name,
            "email_method": "scraped", "email_tier": tier, "status": "ready",
        }
        content = build_email(job)
        if not content:
            done[key] = "compose failed"
            continue
        real_to = addr
        to_addr = GMAIL_ADDRESS if TEST_MODE else real_to
        subject = (f"[TEST -> {real_to}] {content['subject']}"
                   if TEST_MODE else content["subject"])
        try:
            message_id = send_email(to_addr, subject, content["body"],
                                    cv_file=cv_for(job))
        except Exception as e:
            print(f"[spec] failed {company}: {e}")
            continue
        job.update({"status": "test_sent" if TEST_MODE else "spec_sent",
                    "sent_at": now(), "sent_to": to_addr, "sent_subject": subject,
                    "sent_body": content["body"], "message_id": message_id})
        state["jobs"][job["external_id"]] = job
        done[key] = "sent"
        record_send(state)
        state["spec_counts"][today()] = spec_sends_today(state) + 1
        if not TEST_MODE:
            mark_contacted(state, job)
        sent += 1
        print(f"[spec] {'TEST' if TEST_MODE else 'LIVE'} {company} -> {to_addr}")
        save(state)
        time.sleep(SEND_INTERVAL_SECONDS)
    print(f"[spec] {sent} speculative note(s) this run")


# ======================================================================
# REPLY WATCH - answer interview invitations in minutes, not days
# ======================================================================
REPLY_STATUSES = ("sent", "spec_sent", "portal_submitted")

AUTOREPLY_BODY = (
    "{greeting}\n\n"
    "Thanks for getting back to me about the {title} role. Happy to meet "
    "whenever suits - I am free any day this week, tomorrow included, and "
    "flexible on time. If a call is easier my number is 07398 530978.\n\n"
    "Which day works best for you?\n\n"
    "Harry\nHarry Russell / 07398 530978")

# The same reply, for a posting that is offshore or on a rotation. Mobility is
# the first thing an operator screens for on those, and leaving them to ask is
# a wasted exchange when the answer is an unqualified yes.
AUTOREPLY_ROTATIONAL = (
    "{greeting}\n\n"
    "Thanks for getting back to me about the {title} role. Happy to meet "
    "whenever suits - I am free any day this week, tomorrow included, and "
    "flexible on time. If a call is easier my number is 07398 530978.\n\n"
    "To save you asking: I am fine with rotation and with travelling for it, "
    "onshore or offshore, UK or overseas. Available immediately, and I hold DV "
    "clearance.\n\n"
    "Which day works best for you?\n\n"
    "Harry\nHarry Russell / 07398 530978")

# How many agency registration letters an ordinary pipeline run may send.
# Small on purpose: the module's own cooldown is a month per agency, so this
# is only the pacing for the handful that come due on the same day.
AGENCY_PIPELINE_PER_RUN = env_int("AGENCY_PIPELINE_PER_RUN", 2)


def agency_refresh(state):
    """Keep Harry current on the recruitment agencies' own databases.

    Imported here rather than at the top because agency_outreach imports this
    module. Almost every run finds nobody due and costs one lookup in the
    state file; see agency_outreach.py for why this is the one route allowed
    to write to the same firm more than once."""
    import agency_outreach
    sent = agency_outreach.run(state, send=True, limit=AGENCY_PIPELINE_PER_RUN)
    # The offshore tickets are the one thing keeping him out of the market
    # this whole pipeline searches, so the question rides along with the
    # registration letters rather than waiting for somebody to fire it.
    sent += agency_outreach.run_tickets(state, send=True, limit=2)
    return sent


ROTATIONAL_ADVERT = re.compile(
    r"offshore|rotation|rota\b|\d\s*[/:]\s*\d\s*(week|rotation)?|back[- ]to[- ]back|"
    r"fly[- ]in|fifo|vessel|rig\b|platform\b|swing|trip[- ]based|"
    r"field[- ]based overseas|expat", re.I)


def rotational(job):
    """Is this posting offshore or on a rotation?"""
    blob = " ".join(str(job.get(k) or "") for k in
                    ("title", "location", "description"))[:3000]
    return bool(ROTATIONAL_ADVERT.search(blob))


def autoreply_body(job):
    return AUTOREPLY_ROTATIONAL if rotational(job) else AUTOREPLY_BODY


def _message_text(msg):
    """Plain text of an email, minus the quoted trail."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode("utf-8", "ignore")
                break
    else:
        payload = msg.get_payload(decode=True)
        body = payload.decode("utf-8", "ignore") if payload else ""
    lines = [l for l in body.splitlines() if not l.strip().startswith(">")]
    return "\n".join(lines).strip()


def fetch_latest_reply(conn, addr, since):
    """Newest inbox message from this address after `since`, or None."""
    import email as email_mod
    status, data = conn.search(
        None, "FROM", f'"{addr}"', "SINCE", since.strftime("%d-%b-%Y"))
    if status != "OK" or not data or not data[0].split():
        return None
    status, msgdata = conn.fetch(data[0].split()[-1], "(RFC822)")
    if status != "OK":
        return None
    msg = email_mod.message_from_bytes(msgdata[0][1])
    return {"subject": msg.get("Subject", ""),
            "message_id": msg.get("Message-ID"),
            "text": _message_text(msg)[:3000]}


def classify_reply(job, reply):
    result = gemini_json(
        'Classify this reply to a job application. Respond ONLY with JSON: '
        '{"category": "<interview_invite|question|rejection|auto_acknowledgement'
        '|other>", "summary": "<one sentence>"}\n\n'
        "interview_invite = they want to meet, call, or arrange a time.\n"
        "question = they asked the candidate something that needs a real answer.\n"
        "rejection = a no.\n"
        "auto_acknowledgement = automated receipt, no human involved.\n\n"
        f"THE APPLICATION: {job.get('title')} at {job.get('company')}\n\n"
        f"THEIR REPLY:\n{reply['text'][:1500]}", max_tokens=200, temperature=0.1)
    if not result or result.get("category") not in (
            "interview_invite", "question", "rejection",
            "auto_acknowledgement", "other"):
        return None, None
    return result["category"], str(result.get("summary", ""))[:200]


def alert_harry(job, reply, category, extra=""):
    """A loud, labelled email to Harry's own inbox the moment a human replies."""
    label = {"interview_invite": "INTERVIEW", "question": "NEEDS YOUR ANSWER",
             "rejection": "rejection", None: "REPLY"}.get(category, "REPLY")
    body = (f"{job.get('company')} replied about the {job.get('title')} role.\n"
            f"From: {job.get('contact_email')}\n"
            f"Category: {category or 'unclassified'}\n\n"
            f"---- their message ----\n{reply['text'][:2500]}\n----\n\n{extra}")
    send_email(GMAIL_ADDRESS,
               f"[job-machine {label}] {job.get('company')} - {job.get('title')}",
               body, attach_cv=False)


def text_back(state):
    """Text the people who asked to speak to him, in working hours.

    Imported here rather than at the top because sms imports this module.
    Does nothing at all without an httpSMS key."""
    import sms
    return sms.run_followups(state)


def harvest_contact_number(state, job, reply, category):
    """Keep the phone number out of a human reply, and text the good news.

    A consultant's direct line only ever appears in one place: the signature
    of a reply they wrote themselves. It is worth more than the email address
    - most of these people would rather take a call - and it is thrown away on
    every run that does not do this.

    Texting is deliberately one-way here. Harry gets a text the minute an
    interview invitation lands; nobody else gets one from this function."""
    try:
        import sms
        numbers = sms.numbers_from_signature(reply.get("text", ""))
    except Exception as e:
        # This runs inside the reply loop, which is the one stage that must
        # never fall over: it is what sends the availability reply to an
        # interview invitation within minutes.
        print(f"[sms] harvest unavailable: {e}")
        return
    if numbers:
        entry = sms.remember(state, company_key(job.get("company")), numbers[0],
                             name=job.get("contact_name") or job.get("company"),
                             source=f"signature of {job.get('contact_email')}")
        for extra_number in numbers[1:3]:
            sms.remember(state, company_key(job.get("company")), extra_number)
        kind = "mobile" if sms.is_mobile(numbers[0]) else "direct line"
        print(f"[sms] {job.get('company')}: {kind} {numbers[0]} "
              f"({len(entry['numbers'])} known)")
        mobile = next((n for n in entry["numbers"] if sms.is_mobile(n)), None)
        if mobile:
            job["contact_mobile"] = mobile

    # Somebody asking to be phoned is the strongest signal in this inbox and
    # the easiest one to miss, because it arrives looking like every other
    # email. It is also the only thing that makes an outbound text obviously
    # welcome rather than merely tolerable, so it is recorded either way and
    # the texting stage decides what to do about it in working hours.
    if sms.wants_a_word(reply.get("text", "")):
        job["wants_a_word"] = True
        print(f"[sms] {job.get('company')} asked to speak - "
              f"{numbers[0] if numbers else job.get('contact_email')}")
        if numbers and sms.configured():
            sms.alert_harry(sms.call_back_alert(
                job.get("contact_name"), numbers[0], job.get("company")))

    if category == "interview_invite" and sms.configured():
        sms.alert_harry(sms.interview_alert(job), urgent=True)


def check_replies(state):
    """Scan the inbox for replies from anyone we contacted. Interview invites
    get an availability reply within minutes; everything human gets an alert."""
    if TEST_MODE:
        print("[replies] TEST_MODE, nothing was really sent, skipping")
        return
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        return
    watch = [j for j in state["jobs"].values()
             if j.get("status") in REPLY_STATUSES and j.get("contact_email")
             and j.get("sent_at") and not j.get("reply_handled_at")]
    if not watch:
        print("[replies] nothing awaiting a reply")
        return
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", timeout=IMAP_TIMEOUT)
        conn.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        conn.select("INBOX")
    except Exception as e:
        print(f"[replies] imap error: {e}")
        return
    handled = 0
    try:
        for job in watch:
            sent_at = parse_ts(job["sent_at"])
            reply = None
            try:
                reply = fetch_latest_reply(conn, job["contact_email"], sent_at)
            except Exception as e:
                print(f"[replies] fetch {job['contact_email']}: {e}")
            if not reply or not reply["text"]:
                continue

            category, summary = classify_reply(job, reply)
            job.update({"status": "replied", "replied_at": now(),
                        "reply_category": category or "unclassified",
                        "reply_summary": summary,
                        "reply_excerpt": reply["text"][:500],
                        "reply_handled_at": now()})
            handled += 1
            extra = ""
            if category == "interview_invite" and REPLY_AUTORESPOND \
                    and not job.get("auto_replied_at"):
                greeting = (f"Hi {job['contact_name']},"
                            if job.get("contact_name") else "Hi,")
                body = autoreply_body(job).format(
                    greeting=greeting, title=job.get("title", "advertised"))
                subject = reply["subject"] or job.get("sent_subject") or ""
                if not subject.lower().startswith("re:"):
                    subject = f"Re: {subject}"
                try:
                    send_email(job["contact_email"], subject, body,
                               attach_cv=False,
                               headers={"In-Reply-To": reply.get("message_id"),
                                        "References": reply.get("message_id")})
                    job["auto_replied_at"] = now()
                    extra = ("An availability reply has already been sent for "
                             "you. Jump in personally as soon as you can.")
                    print(f"[replies] AUTO-REPLIED to {job['company']}")
                except Exception as e:
                    extra = f"Auto-reply failed ({e}) - answer this yourself."
            elif category in (None, "question", "other"):
                extra = "No automatic reply was sent - this one needs you."
            if category != "auto_acknowledgement":
                try:
                    alert_harry(job, reply, category, extra)
                except Exception as e:
                    print(f"[replies] alert failed: {e}")
            # A human wrote back, so their signature is here and it is the
            # only place a real direct line ever comes from. File it, and if
            # this was an interview invitation put it on his phone now rather
            # than in the digest at 22:00.
            try:
                harvest_contact_number(state, job, reply, category)
            except Exception as e:
                print(f"[sms] harvest failed: {e}")
            save(state)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    print(f"[replies] {handled} new repl{'y' if handled == 1 else 'ies'} handled")


# ======================================================================
# DAILY SUMMARY - one digest a day at 22:00 UK time
# ======================================================================
SUMMARY_HOUR_UK = 22
SUMMARY_TZ = "Europe/London"


def uk_now():
    """Local UK time. Falls back to a BST approximation if tzdata is missing."""
    utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo(SUMMARY_TZ))
    except Exception:
        # BST runs from the last Sunday in March to the last Sunday in October
        def last_sunday(month):
            day = 31
            while True:
                candidate = datetime(utc.year, month, day, 1, tzinfo=timezone.utc)
                if candidate.weekday() == 6:
                    return candidate
                day -= 1
        bst = last_sunday(3) <= utc < last_sunday(10)
        return utc + timedelta(hours=1) if bst else utc


def in_peak_window(when=None):
    """Tuesday to Thursday, 9-11am UK - the window cold-outreach studies
    consistently find gets the highest open and reply rates. Monday's inbox is
    a backlog and Friday's reader has checked out."""
    now_uk = when or uk_now()
    return now_uk.weekday() in (1, 2, 3) and 9 <= now_uk.hour < 12


def already_waited(job):
    """Has this job been held back once already?

    The off-peak hold trades a few hours for a better open rate, and that is a
    good trade for a listing that will still be there this afternoon. It is not
    a good trade twice. A job that reached the queue through the portal
    fallback has been sitting for days - it was found, judged, matched, and
    then parked because its application form could not be driven. Holding it
    again for a timing window is how eighty-six of the best-matched roles in
    the file would go out at three a run."""
    return bool(job.get("portal_fallback_at"))


def brand_new(job, hours=None):
    """Posted so recently that being early beats being well-timed."""
    posted = parse_ts(job.get("posted_at")) or parse_ts(job.get("found_at"))
    if not posted:
        return False
    age = (datetime.now(timezone.utc) - posted).total_seconds() / 3600
    return age <= (hours if hours is not None else BRAND_NEW_HOURS)


def summary_due(force=False):
    """The workflow fires at 21:00 and 22:00 UTC; exactly one of those is 22:00
    in the UK, whichever way the clocks are set."""
    return force or uk_now().hour == SUMMARY_HOUR_UK


def summary_window(state):
    since = parse_ts(state.get("last_summary_at"))
    floor = datetime.now(timezone.utc) - timedelta(hours=24)
    return min(since, floor) if since else floor


def collect_summary(state, since):
    """What happened since the last digest."""
    def after(value):
        stamp = parse_ts(value)
        return stamp is not None and stamp >= since

    jobs = list(state["jobs"].values())
    applications = sorted(
        [j for j in jobs if after(j.get("sent_at"))],
        key=lambda j: -(j.get("score") or 0))
    portal = [j for j in jobs if after(j.get("portal_attempted_at"))]
    return {
        "applications": applications,
        "portal_submitted": [j for j in portal if j.get("status") == "portal_submitted"],
        "portal_review": [j for j in portal if j.get("status") in
                          ("portal_review", "portal_ready")],
        "portal_manual": [j for j in portal if j.get("status") == "portal_manual"],
        "followups": [j for j in jobs if after(j.get("followup_sent_at"))],
        "replies": [j for j in jobs if after(j.get("replied_at"))],
        "found": [j for j in jobs if after(j.get("found_at"))],
        "no_email": [j for j in jobs if after(j.get("found_at"))
                     and j.get("status") == "no_email"],
        "queued": [j for j in jobs if j.get("status") == "ready"],
        "waiting": [j for j in jobs if j.get("status") == "sent"
                    and not j.get("followup_sent_at")],
        "lifetime": sum(1 for j in jobs
                        if j.get("status") in ("sent", "replied")
                        or j.get("followup_sent_at")),
        "call_list": call_list_rows(state),
        "awaiting_captcha": awaiting_captcha(state),
        "answer_gaps": answer_gaps(state),
    }


def answer_gaps(state):
    """The questions that have stopped an application, worst first.

    Each one is a line in data/answers.json and ten seconds of Harry's time,
    and each one unblocks an application that is otherwise finished."""
    try:
        import learn
        return learn.unanswered_questions(state)[:5]
    except Exception:
        return []


def awaiting_captcha(state):
    """Applications filled in completely and stopped by a bot check.

    The most valuable thing in the file and the easiest to lose track of, so
    it goes in the digest as well as in the daily text - the text carries one
    link, this carries all of them."""
    try:
        from tools import handoff
        return handoff.pending(state)[:10]
    except Exception:
        return []


def call_list_rows(state):
    """Everyone who has written back and left a number, for the digest.

    A consultant's direct line is the most useful thing this machine ever
    finds and it cannot be automated any further than this: nobody should
    automate a phone call. Putting it in the digest is the whole point."""
    try:
        import sms
        return sms.call_list(state)[:12]
    except Exception:
        return []


def esc(text):
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


# The outbound side of this project writes to employers. This is the other
# direction: places where a recruiter finds Harry. Registering is a one-off he
# does himself, but CV databases rank by how recently a CV was touched, so the
# ranking decays every week whether he does anything or not - which is the one
# part worth a standing reminder.
INBOUND_TASKS = [
    ("RightJob - Forces Employment Charity",
     "https://www.rfea.org.uk/jobseekers/",
     "free to veterans, and they work with around 8,000 employers who "
     "specifically want to hire ex-forces. If you do one thing on this list, "
     "this is it. Set its alerts to this inbox and the machine reads them."),
    ("CV-Library", "https://www.cv-library.co.uk/",
     "refresh the CV so you rank top of recruiter searches"),
    ("Totaljobs / Jobsite", "https://www.totaljobs.com/",
     "same - recruiters search by last updated"),
    ("Oil and Gas Job Search", "https://www.oilandgasjobsearch.com/",
     "the Aberdeen energy market searches here"),
    ("Energy Jobline", "https://www.energyjobline.com/",
     "offshore and renewables recruiters"),
    ("Reed", "https://www.reed.co.uk/",
     "keep the profile live even though we use their API"),
    ("LinkedIn - Open to Work",
     "https://www.linkedin.com/",
     "set it to recruiters only; they filter on it"),
]


def inbound_reminder(when=None):
    """A short weekly list of the places recruiters go looking, or None.

    Sunday evening, because it is a ten minute job and Monday is when
    recruiters start searching."""
    now_uk = when or uk_now()
    if now_uk.weekday() != 6:
        return None, None
    text = ["Recruiters search CV databases by how recently a CV was touched,",
            "so ten minutes here puts you at the top of next week's searches.",
            ""]
    items = []
    for name, url, why in INBOUND_TASKS:
        text.append(f"  {name} - {why}")
        text.append(f"      {url}")
        items.append(f"<li><b>{esc(name)}</b> - {esc(why)}<br>"
                     f'<a href="{esc(url)}">{esc(url)}</a></li>')
    html = ("<h2>Inbound - 10 minutes, once a week</h2>"
            "<p class=m>Recruiters search CV databases by how recently a CV was "
            "touched, so ten minutes here puts you at the top of next week's "
            "searches.</p><ul>" + "".join(items) + "</ul>")
    return text, html


def summary_bodies(data):
    """(subject, plain text, html) for the digest."""
    apps = data["applications"]
    tests = [j for j in apps if j.get("status") == "test_sent"]
    day = uk_now().strftime("%A %d %B")

    if not apps:
        subject = f"Job machine: nothing sent today ({day})"
    elif tests:
        subject = f"Job machine: {len(apps)} TEST email(s) ({day})"
    else:
        subject = f"Job machine: {len(apps)} application(s) sent ({day})"

    lines = [subject, "=" * len(subject), ""]
    rows = []
    for job in apps:
        address = job.get("contact_email") or "unknown"
        who = job.get("contact_name")
        target = f"{who} <{address}>" if who else address
        tier = TIER_NAMES.get(job.get("email_tier"), "unknown")
        flag = " [TEST]" if job.get("status") == "test_sent" else ""
        lines += [
            f"{job.get('title')} - {job.get('company')}{flag}",
            f"  {job.get('location')} | score {job.get('score')} | "
            f"to {target} ({tier})",
            f"  Subject: {job.get('sent_subject')}",
            f"  Listing: {job.get('url') or 'n/a'}",
            "",
        ]
        rows.append(
            f"<tr><td><b>{esc(job.get('title'))}</b><br>"
            f"<span class=m>{esc(job.get('company'))}{esc(flag)} - "
            f"{esc(job.get('location'))}</span></td>"
            f"<td>{esc(job.get('score'))}</td>"
            f"<td>{esc(who) + '<br>' if who else ''}"
            f"<span class=m>{esc(address)}<br>{esc(tier)}</span></td>"
            f"<td>{esc(job.get('sent_subject'))}</td></tr>")

    if not apps:
        lines.append("No emails went out in the last 24 hours.\n")

    if data.get("answer_gaps"):
        lines += ["", "QUESTIONS THAT STOPPED AN APPLICATION", "-" * 37,
                  "Each of these is one line in data/answers.json, and each "
                  "one unblocks an application that is otherwise finished.", ""]
        for gap in data["answer_gaps"]:
            lines.append(f"  {gap['times']}x  {gap['question'][:70]}")

    if data.get("awaiting_captcha"):
        lines += ["", "FILLED IN, WAITING ON A CAPTCHA", "-" * 30,
                  "Every answer is worked out and saved. Open the link, do the "
                  "puzzle, press submit. The handoff artifact on the latest "
                  "portal run has a one-click prefill for each of these.", ""]
        for job in data["awaiting_captcha"]:
            lines.append(f"  {(job.get('title') or '?')[:40]:42} "
                         f"{(job.get('company') or '?')[:24]}")
            lines.append(f"      {job.get('apply_url') or ''}")

    if data.get("call_list"):
        lines += ["", "PEOPLE TO RING", "-" * 14,
                  "Numbers out of the signatures of people who wrote back. A "
                  "call beats everything else in this email.", ""]
        for row in data["call_list"]:
            kind = "mobile" if row["mobile"] else "direct line"
            lines.append(f"  {row['name'][:34]:36} {row['number']}  ({kind})")

    inbound_text, inbound_html = inbound_reminder()
    if inbound_text:
        lines += ["", "INBOUND - 10 MINUTES, ONCE A WEEK", "-" * 33] + inbound_text

    portal_html = ""
    if data.get("portal_submitted") or data.get("portal_review") or \
            data.get("portal_manual"):
        lines += ["", "APPLICATION PORTALS", "-" * 19]
        blocks = [
            ("Submitted in full", data.get("portal_submitted", [])),
            ("Filled in, waiting on you", data.get("portal_review", [])),
            ("Portal needs doing by hand", data.get("portal_manual", [])),
        ]
        html_blocks = []
        for heading, jobs_in_block in blocks:
            if not jobs_in_block:
                continue
            lines.append(f"{heading}:")
            items = []
            for job in jobs_in_block:
                why = job.get("portal_reason", "")
                lines.append(f"  {job.get('title')} - {job.get('company')}"
                             f"{' (' + why + ')' if why else ''}")
                if job.get("portal_flags"):
                    for flag in job["portal_flags"][:3]:
                        lines.append(f"      needs you: {flag}")
                link = job.get("apply_url") or job.get("url") or ""
                items.append(
                    f"<li><b>{esc(job.get('title'))}</b> - {esc(job.get('company'))}"
                    + (f'<br><span class=m>{esc(why)}</span>' if why else "")
                    + (f'<br><a href="{esc(link)}">open the form</a>' if link else "")
                    + "</li>")
            html_blocks.append(f"<h3>{esc(heading)}</h3><ul>{''.join(items)}</ul>")
        portal_html = "<h2>Application portals</h2>" + "".join(html_blocks)

    extras = []
    if data["replies"]:
        extras.append("Replies received: " + ", ".join(
            f"{j.get('company')} ({j.get('reply_category', 'reply')}"
            + (", auto-replied with availability" if j.get("auto_replied_at") else "")
            + ")" for j in data["replies"]))
    if data["followups"]:
        extras.append("Follow-ups sent: " + ", ".join(
            j.get("company", "?") for j in data["followups"]))
    extras += [
        f"New listings found: {len(data['found'])}",
        f"Found but no real email address: {len(data['no_email'])}",
        f"Queued and ready to send: {len(data['queued'])}",
        f"Awaiting a reply (follow-up due in 4 days): {len(data['waiting'])}",
        f"Applications sent all time: {data['lifetime']}",
    ]
    lines += ["-" * 40] + extras

    table = ("<table><thead><tr><th>Role</th><th>Score</th><th>Sent to</th>"
             "<th>Subject line</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table>") if rows else (
        "<p>No applications went out in the last 24 hours.</p>")
    html = f"""<html><body style="font-family:-apple-system,Segoe UI,Arial,sans-serif;
color:#111;line-height:1.45">
<style>
table{{border-collapse:collapse;width:100%;max-width:720px;font-size:14px}}
th,td{{border-bottom:1px solid #ddd;padding:8px 6px;text-align:left;vertical-align:top}}
th{{background:#f4f4f4;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.m{{color:#666;font-size:12px}}
ul{{font-size:14px;padding-left:18px}}
</style>
<h2 style="margin:0 0 4px">{esc(subject)}</h2>
<p class=m style="margin:0 0 14px">Everything sent in the last 24 hours.</p>
{table}
{portal_html}
{inbound_html or ''}
<ul>{''.join(f'<li>{esc(x)}</li>' for x in extras)}</ul>
</body></html>"""
    return subject, "\n".join(lines), html


def send_summary(state, force=False):
    if not summary_due(force):
        print(f"[summary] not 22:00 in the UK yet (local {uk_now():%H:%M}), skipping")
        return
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        print("[summary] no Gmail credentials")
        return

    since = summary_window(state)
    subject, text, html = summary_bodies(collect_summary(state, since))
    msg = EmailMessage()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = GMAIL_ADDRESS
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain="gmail.com")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.send_message(msg)
    state["last_summary_at"] = now()
    print(f"[summary] sent: {subject}")


# ======================================================================
# FOLLOW-UP
# ======================================================================
def has_reply_from(addr):
    """True/False, or None when the inbox could not be checked."""
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=IMAP_TIMEOUT)
        m.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        try:
            m.select("INBOX")
            status, data = m.search(None, "FROM", f'"{addr}"')
            if status != "OK":
                return None
            return bool(data and data[0].split())
        finally:
            try:
                m.logout()
            except Exception:
                pass
    except Exception as e:
        print(f"[followup] imap error: {e}")
        return None


def next_stakeholder(job):
    """The next real person at this company we have not written to yet."""
    used = {(job.get("contact_email") or "").lower(),
            (job.get("stakeholder_email") or "").lower()}
    for contact in job.get("other_contacts") or []:
        if contact.get("email") and contact["email"].lower() not in used:
            return contact
    return None


def run_followups(state):
    if TEST_MODE:
        print("[followup] disabled in TEST_MODE")
        return
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        return
    done = 0
    for job in state["jobs"].values():
        if job.get("status") != "sent":
            continue
        sent_at = parse_ts(job.get("sent_at"))
        if not sent_at:
            continue
        age = datetime.now(timezone.utc) - sent_at

        # which nudge is due, if any
        if not job.get("followup_sent_at"):
            if age < timedelta(days=FOLLOWUP_AFTER_DAYS):
                continue
            which = 1
        elif not job.get("followup2_sent_at"):
            if age < timedelta(days=FOLLOWUP2_AFTER_DAYS):
                continue
            which = 2
        elif not job.get("stakeholder_sent_at") and next_stakeholder(job):
            # Three touches to one person capture about 93% of the replies a
            # sequence will ever get, and a fourth to the same inbox is
            # pestering. A fourth to a *different* person at the same company
            # is the one documented exception - it is a new conversation, not
            # a fourth ask.
            if age < timedelta(days=STAKEHOLDER_AFTER_DAYS):
                continue
            which = 3
        else:
            continue  # said everything there is to say; silence is the polite option

        replied = has_reply_from(job["contact_email"])
        if replied is None:
            continue  # could not check: leave it alone, try again next run
        if replied:
            job.update({"status": "replied", "replied_at": now()})
            print(f"[followup] reply from {job['company']} - leaving it alone")
            continue

        target = job["contact_email"]
        target_name = job.get("contact_name")
        if which == 3:
            other = next_stakeholder(job)
            target, target_name = other["email"], other.get("name")

        greeting = f"Hi {target_name}," if target_name else "Hi,"
        if which == 1:
            body = (
                f"{greeting}\n\n"
                f"Following up on the note I sent {sent_at.strftime('%A')} about the "
                f"{job['title']} role. Still interested, and happy to come in for a chat "
                f"or do a short call whenever suits.\n\n"
                f"Is it worth me sending anything else over?\n\n"
                f"Harry\n{SIGNOFF.replace(' / CV attached', '')}"
            )
        elif which == 3:
            body = (
                f"{greeting}\n\n"
                f"I wrote to a colleague about the {job['title']} role a couple of "
                f"weeks back and I suspect it landed at a busy moment. Ex-Royal Navy "
                f"comms, three years at Sonardyne on subsea electronics, Aberdeen "
                f"based and free to start now.\n\n"
                f"Is that role still open, or is there someone better placed for me "
                f"to speak to?\n\n"
                f"Harry\n{SIGNOFF}"
            )
        else:
            body = (
                f"{greeting}\n\n"
                f"Last note from me on the {job['title']} role - I know inboxes get "
                f"buried. Still interested and available immediately if it is live. "
                f"If the timing is wrong, no bother at all.\n\n"
                f"Worth keeping my CV on file for the next opening?\n\n"
                f"Harry\n{SIGNOFF.replace(' / CV attached', '')}"
            )
        subject = job.get("sent_subject") or job["title"]
        if which == 3:
            # A fresh conversation with a new person, so no Re: and no
            # threading headers - and the CV goes with it, because this
            # reader has never seen it.
            headers, attach = {}, True
        else:
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            headers = {"In-Reply-To": job.get("message_id"),
                       "References": job.get("message_id")}
            attach = False
        try:
            send_email(target, subject, body, attach_cv=attach, headers=headers)
            stamp = {1: "followup_sent_at", 2: "followup2_sent_at",
                     3: "stakeholder_sent_at"}[which]
            job[stamp] = now()
            job[{1: "followup_body", 2: "followup2_body",
                 3: "stakeholder_body"}[which]] = body
            if which == 3:
                job["stakeholder_email"] = target
            done += 1
            print(f"[followup] {'second contact' if which == 3 else f'nudge {which}'}"
                  f" -> {job['company']} <{target}>")
            save(state)
            time.sleep(FOLLOWUP_INTERVAL_SECONDS)
        except Exception as e:
            print(f"[followup] failed {job['company']}: {e}")
    print(f"[followup] {done} sent")


# ======================================================================
# MAIN
# ======================================================================
STATUSES = ("new", "scored", "ready", "sent", "spec_sent", "test_sent",
            "replied", "no_email", "compose_failed", "send_failed", "skipped")


def harvest_from_inbox(state):
    """Adverts out of job-alert email. Imported here rather than at the top so
    that a problem in the alert reader can never stop the main pipeline."""
    import alert_harvest
    if alert_harvest.harvest_alerts(state):
        alert_harvest.enrich(state)


def portal_fallback(state, max_age_days=PORTAL_FALLBACK_MAX_AGE_DAYS):
    """Put jobs whose application form defeated us back on the email route.

    'portal_manual' is a terminal status. The email route only looks at
    'scored', and the portal agent explicitly excludes anything already marked
    portal_manual so it does not retry it. Nothing else reads it. So a listing
    that reached it was found, judged, matched - and then quietly dropped.

    Sixty-eight had collected there, and they were not the dregs: Oceaneering,
    Survitec, Trescal, Dron & Dickson, Konecranes, scoring 88 to 90 in exactly
    Harry's trade in Aberdeen. Two thirds of them were parked for a reason that
    has nothing to do with the job - the portal wanted an account, or ran a bot
    check, or rendered its form in a way the agent could not read.

    Being unable to drive somebody's web form is not a reason not to apply to
    them. This puts those listings back on the ordinary route: find a real,
    MX-checked address at the employer and write to a human. That route is not
    speculative - it is the one every application that has actually gone out
    used.

    Once each, marked so it cannot loop. A job that comes back from that route
    with no real address has genuinely run out of options and stays where it
    lands. The send caps, the company-dedupe and the agency limits all still
    apply, because this only re-enters the queue - it does not send anything."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    woken = 0
    for job in state["jobs"].values():
        if job.get("status") not in ("portal_manual", "portal_review"):
            continue
        if job.get("portal_fallback_at"):
            continue
        # A form we could not fill is worth an email. A stale advert is not.
        found = parse_ts(job.get("posted_at")) or parse_ts(job.get("found_at"))
        if found and found < cutoff:
            continue
        job.update({"status": "scored", "portal_fallback_at": now()})
        woken += 1
    print(f"[fallback] {woken} listing(s) whose portal we could not drive "
          f"put back on the email route")
    return woken


def rescore(state, floor=55):
    """Re-open listings that were judged under an out-of-date profile.

    A score is a judgement against CANDIDATE_PROFILE at the moment it was made,
    so changing that profile silently invalidates every score already in the
    file. When the profile said 'Aberdeen strongly preferred', fifty-two
    listings that matched Harry's trade exactly were marked down to 65 and
    binned for being in Dundee, Inverness or Perth - including an
    Electro-Technical Officer post that is about as close to his Navy trade as
    an advert gets. Correcting the profile without re-opening those would leave
    every one of them in the bin."""
    woken = 0
    for job in state["jobs"].values():
        if job.get("status") != "skipped":
            continue
        # only listings the scorer rejected, never ones the title filter or the
        # pre-filter threw out - those were not judgement calls
        if not str(job.get("skip_reason") or "").startswith("score "):
            continue
        if (job.get("score") or 0) < floor:
            continue
        job.pop("score", None)
        job.pop("score_reason", None)
        job.update({"status": "new", "skip_reason": None, "rescored_at": now()})
        woken += 1
    print(f"[rescore] {woken} listing(s) put back in the queue to be judged "
          f"against the current profile")
    return woken


def stage(name, fn, *args):
    """Run a stage, swallowing its failure so the rest of the run continues.

    Returns whatever the stage returned, or None if it blew up - the send
    stage uses that to carry its running total into its second call."""
    try:
        return fn(*args)
    except Exception as e:
        print(f"[{name}] STAGE FAILED: {type(e).__name__}: {e}")
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Job outreach pipeline")
    parser.add_argument("--dry-run", action="store_true",
                        help="compose everything but send nothing")
    parser.add_argument("--skip-harvest", action="store_true")
    parser.add_argument("--rescore", type=int, nargs="?", const=55, metavar="FLOOR",
                        help="put listings skipped for a low score back in the "
                             "queue, so a change to the candidate profile is "
                             "applied to what was already judged under the old one")
    parser.add_argument("--summary", action="store_true",
                        help="send the daily digest instead of running the pipeline")
    parser.add_argument("--force", action="store_true",
                        help="with --summary, send it whatever the UK clock says")
    parser.add_argument("--replies", action="store_true",
                        help="only check the inbox and handle replies (fast)")
    args = parser.parse_args(argv)

    if args.summary:
        state = load()
        send_summary(state, force=args.force)
        save(state)
        return 0

    if args.replies:
        state = load()
        stage("replies", check_replies, state)
        save(state)
        return 0

    print(f"=== job-machine {now()} ===")
    print(f"TEST_MODE={'ON (nothing reaches an employer)' if TEST_MODE else 'OFF (LIVE)'} "
          f"caps={PER_RUN_SEND_CAP}/run {DAILY_SEND_CAP}/day "
          f"locations={','.join(SEARCH_LOCATIONS)} cv={cv_path() or 'MISSING'}")

    state = load()
    if args.rescore is not None:
        stage("rescore", rescore, state, args.rescore)
        save(state)
    # Before anything that touches the network. This stage needs nothing but
    # the state file, and it is what puts the best-matched listings in the file
    # back in the queue - so it must not sit behind a stage that can hang. It
    # did, once, and a run died in advert-reading with eighty-six of them still
    # parked.
    stage("fallback", portal_fallback, state)
    save(state)
    if not args.skip_harvest:
        stage("harvest", harvest, state)
        # Job-alert email from Harry's own inbox. The boards that carry most of
        # this market - CV-Library, Totaljobs, s1jobs, Oil and Gas Job Search -
        # have no free API, but every one of them will email him alerts.
        stage("alerts", harvest_from_inbox, state)
        save(state)
    # Anything already holding a real address goes now, before the two slow
    # stages get a chance to spend the run. Sending a queued application takes
    # seconds; scoring and address discovery are the parts that hit the
    # forty-five minute ceiling, and they did - a run released ninety-nine
    # parked listings, found real addresses for six of them, and was killed
    # before it sent one, leaving them 'ready' with nothing to show.
    sent = stage("send", run_sends, state, args.dry_run) or 0
    save(state)
    stage("score", score_jobs, state)
    save(state)
    stage("discover", discover, state)
    save(state)
    # And again for whatever discovery just found an address for, carrying the
    # count so the per-run cap stays a per-run cap.
    stage("send", run_sends, state, args.dry_run, sent)
    save(state)
    if not args.dry_run:
        stage("notes", run_applied_notes, state)
        save(state)
        # Grow the target list from employers this run has just seen, before
        # writing to any of them. Every company that advertised a role the
        # scorer rated in-trade is an employer of that trade in this market -
        # evidence, rather than my guess about who operates in Aberdeen.
        stage("targets", grow_targets, state)
        stage("speculative", speculative, state)
        save(state)
        # The agencies' own databases, which are searched by consultants
        # against roles that were never advertised. Nearly always a no-op:
        # each agency has a month-long cooldown of its own.
        stage("agencies", agency_refresh, state)
        save(state)
        stage("followup", run_followups, state)
        save(state)
        stage("replies", check_replies, state)
        # After the replies, because that is where a request to speak is
        # found, and as its own stage because a reply that lands at nine at
        # night should be answered in the morning rather than at nine at
        # night. sms.may_text holds it until working hours; this runs often
        # enough to pick it up when they arrive.
        stage("sms", text_back, state)
    prune(state)
    save(state)

    jobs = list(state["jobs"].values())
    print("\n=== SUMMARY ===")
    for s in STATUSES:
        count = sum(1 for j in jobs if j.get("status") == s)
        if count:
            print(f"{s}: {count}")
    print(f"total tracked: {len(jobs)} | companies contacted ever: "
          f"{len(state['companies_contacted'])} | sent today: {sends_today(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
