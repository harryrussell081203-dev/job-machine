"""
PORTAL AGENT - fills in real job application forms the way a person would.

Sister process to job_machine.py. Where that one emails a human, this one opens
the employer's actual application portal in a real browser, works through every
field, uploads the CV and submits.

Two rules it will not break:

  1. It answers ONLY from data/answers.json and the CV. If a question has no
     grounded answer, it does not invent one - it fills everything it can, saves
     a screenshot and hands that application to Harry.
  2. It never certifies anything on Harry's behalf. Criminal records, health,
     equal-opportunities monitoring, identity and financial numbers are refused
     by code, whatever the form asks and whatever is in the answer bank.

It works on portals that accept an application without an account and without a
CAPTCHA: Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Recruitee,
Teamtailor, JOIN, Personio. Anything behind a login or a bot check (Workday,
Taleo, LinkedIn, Indeed) is recorded with its link for Harry to do by hand -
this agent does not try to defeat bot protection.

    python portal_agent.py --harvest        # find portal-able jobs, last 30 days
    python portal_agent.py --run            # fill them in (submits only if enabled)
    python portal_agent.py --run --submit   # actually press submit
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import job_machine as jm

# ======================================================================
# CONFIG
# ======================================================================
PORTAL_MAX_AGE_DAYS = jm.env_int("PORTAL_MAX_AGE_DAYS", 30)
# LIVE. A filled form is now actually submitted.
#
# This defaulted to False, which meant every application the agent ever
# completed stopped one step short with 'filled and checked, waiting for
# PORTAL_SUBMIT' - 121 opened, 0 sent. Harry gave explicit permission to turn
# it on. Set the repo variable PORTAL_SUBMIT=0 to put it back to filling only.
#
# What this does NOT change: the agent still refuses to answer convictions,
# health, identity or financial questions, still refuses to tick a declaration
# that something is true and complete, and still will not touch a CAPTCHA. Any
# of those and the application is filled to the last safe field and handed
# over, exactly as before. Turning submission on lets it finish the ones it
# could already fill honestly - it does not widen what it is willing to say.
PORTAL_SUBMIT = jm.env_flag("PORTAL_SUBMIT", True)
PORTAL_PER_RUN_CAP = jm.env_int("PORTAL_PER_RUN_CAP", 40)
PORTAL_DAILY_CAP = jm.env_int("PORTAL_DAILY_CAP", 150)
PORTAL_SCORE_THRESHOLD = jm.env_int("PORTAL_SCORE_THRESHOLD", jm.SCORE_THRESHOLD)
# How many attempts may fail to reach a form before the run gives up. At about
# two minutes an application, grinding through thirty of them to learn what
# the first six already said is an hour spent proving nothing.
CIRCUIT_AFTER = jm.env_int("PORTAL_CIRCUIT_AFTER", 6)
PAGE_TIMEOUT_MS = 45000

ANSWERS_PATH = os.path.join(jm.ROOT, "data", "answers.json")
SHOTS_DIR = os.path.join(jm.ROOT, "screenshots")
# The DOM of pages that beat the agent, shipped out as an artifact so the
# failure can be reproduced offline instead of guessed at a run per guess.
PAGES_DIR = os.path.join(jm.ROOT, "kept-pages")

# Portals that take a full application with no account and no bot check.
SUPPORTED_ATS = {
    "greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io",
                   "boards.eu.greenhouse.io"),
    "lever": ("jobs.lever.co",),
    "workable": ("apply.workable.com",),
    "ashby": ("jobs.ashbyhq.com",),
    "smartrecruiters": ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"),
    "recruitee": ("recruitee.com",),
    "teamtailor": ("teamtailor.com",),
    "join": ("join.com",),
    "personio": ("jobs.personio.com", "jobs.personio.de"),
}
# Portals that need an account or run bot protection. We link, we do not fight.
MANUAL_ATS = {
    "workday": ("myworkdayjobs.com", "wd1.myworkdaysite.com", "wd3.myworkdayjobs.com"),
    "taleo": ("taleo.net",),
    "successfactors": ("successfactors.com", "sapsf.com"),
    "oracle": ("oraclecloud.com", "taleo.net"),
    "icims": ("icims.com",),
    "linkedin": ("linkedin.com",),
    "indeed": ("indeed.com",),
    "reed": ("reed.co.uk",),
    "totaljobs": ("totaljobs.com",),
}
CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "turnstile", "cf-challenge",
                   "px-captcha", "geetest", "arkoselabs", "funcaptcha")


# ======================================================================
# ANSWER BANK
# ======================================================================
def load_answers():
    """Answer bank from data/answers.json, with any ANSWER_<KEY> environment
    variable winning. That lets details you would rather not commit - a home
    address, say - live in GitHub secrets instead of in the repo."""
    with open(ANSWERS_PATH) as f:
        raw = json.load(f)
    answers = {k: v for k, v in raw.items() if not k.startswith("_")}
    for key in list(answers):
        override = os.environ.get(f"ANSWER_{key.upper()}")
        if override and override.strip():
            answers[key] = override.strip()
    return answers


RELATIVE_DATES = re.compile(
    r"^(immediate(ly)?|asap|as soon as possible|now|tomorrow|straight away|"
    r"available now|none|no notice)\b", re.I)


def coerce_value(field, value):
    """Shape an answer to fit the box. A date picker cannot take 'Immediately',
    and a number field cannot take '35,000 a year'."""
    text = str(value).strip()
    if field.get("type") == "date":
        if RELATIVE_DATES.match(text):
            return (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return None
    if field.get("type") == "number":
        digits = re.sub(r"[^\d]", "", text.split(".")[0])
        return digits or None
    return text


# Questions code refuses to answer automatically, whatever the bank says.
# Getting one of these wrong on someone's behalf is not a recoverable mistake.
REFUSED = {
    "identity": r"national insurance|\bni number\b|passport|nin\b|social security",
    "financial": r"bank account|sort code|iban|salary history|payroll number",
    "date_of_birth": r"date of birth|\bdob\b|birth date",
    "convictions": r"conviction|criminal record|\bdbs\b|disclosure scotland|"
                   r"offence|caution|barred list|rehabilitation of offenders",
    "health": r"health condition|medical condition|disabilit|reasonable adjust|"
              r"long term illness|occupational health",
    "protected": r"ethnic|gender identity|\bsex\b\s*[:?]|sexual orientation|"
                 r"religion|belief|marital status|pregnan|gender reassignment",
    "certification": r"i (certify|confirm|declare)|to the best of my knowledge|"
                     r"true and complete|penalty of perjury",
}
REFUSED_RES = {name: re.compile(pattern, re.I) for name, pattern in REFUSED.items()}

# Monitoring questions are the common blocker, and they nearly always offer a
# real "rather not say" option. Choosing it is an honest answer that unblocks
# the form. Categories NOT listed here are never answered automatically.
DECLINABLE = ("protected", "health")
DECLINE_OPTION = re.compile(
    r"prefer not to (say|answer|disclose)|rather not say|do not wish to "
    r"(say|disclose|answer)|not (disclosed|specified|stated)|decline to "
    r"(answer|state)|undisclosed|choose not to", re.I)


def decline_option(field):
    """The form's own 'prefer not to say' choice, if it offers one."""
    for option in field.get("options") or []:
        if DECLINE_OPTION.search(option):
            return option
    return None

# label/name/id patterns -> answer-bank key. First match wins, so order matters.
FIELD_RULES = [
    ("first_name", r"first[\s_-]*name|forename|given[\s_-]*name"),
    ("last_name", r"last[\s_-]*name|surname|family[\s_-]*name"),
    ("full_name", r"^(full[\s_-]*)?name$|your name|applicant name"),
    ("email", r"e-?mail"),
    ("phone", r"phone|mobile|telephone|contact number"),
    ("address_line_1", r"address(\s*line)?\s*1|street address|^address$"),
    ("city", r"city|town"),
    ("county", r"county|region|province"),
    ("postcode", r"post[\s_-]*code|zip"),
    ("country", r"country"),
    ("linkedin", r"linked-?in"),
    ("website", r"website|portfolio|personal site|github"),
    ("right_to_work_uk", r"right to work|eligible to work|authoris?ed to work|"
                        r"legally entitled to work"),
    ("needs_sponsorship", r"sponsorship|visa|work permit"),
    ("security_clearance", r"security clearance|clearance level|\bsc\b|\bdv\b|vetting"),
    ("armed_forces_veteran", r"armed forces|veteran|ex-?forces|military service|"
                             r"service leaver"),
    ("driving_licence", r"driving licen[cs]e|driver'?s licen[cs]e|full licence"),
    ("notice_period", r"notice period|how much notice"),
    ("earliest_start_date", r"start date|available from|availability|when can you start"),
    ("salary_expectation", r"salary|remuneration|rate expectation|expected pay"),
    ("willing_to_relocate", r"relocat"),
    ("willing_to_travel", r"willing to travel|travel requirement"),
    ("offshore_willing", r"offshore|rotation|work at sea"),
    ("shift_work", r"shift work|shift pattern|night work"),
    ("current_employer", r"current employer|present employer|company name"),
    ("current_job_title", r"current (job )?title|current role|job title|occupation"),
    ("years_experience", r"years of experience|years'? experience|how many years"),
    ("highest_qualification", r"highest (level of )?(qualification|education)"),
    ("university", r"university|college|institution"),
    ("degree", r"degree|qualification|course|field of study|subject"),
    ("how_did_you_hear", r"how did you (hear|find)|referral source|where did you see"),
]
FIELD_RES = [(key, re.compile(pattern, re.I)) for key, pattern in FIELD_RULES]

CV_PATTERNS = re.compile(r"\bcv\b|resume|résumé|upload.*(cv|resume)", re.I)
COVER_PATTERNS = re.compile(r"cover letter|covering letter|personal statement|"
                            r"supporting statement|why do you|tell us (about|why)|"
                            r"motivation", re.I)
CONSENT_PATTERNS = re.compile(r"privacy (policy|notice)|terms (and|&) conditions|"
                              r"data protection|gdpr|consent to (us )?(storing|processing)|"
                              r"agree to the", re.I)

YES_WORDS = ("yes", "y", "true", "i do", "i am", "i have", "confirm")
NO_WORDS = ("no", "n", "false", "i do not", "i don't", "i am not")


def field_text(field):
    """Everything a human would read to work out what a box is asking for."""
    return " ".join(str(field.get(k) or "") for k in
                    ("label", "name", "id", "placeholder", "aria_label", "group_label"))


def refusal_reason(field):
    text = field_text(field)
    for name, rx in REFUSED_RES.items():
        if rx.search(text):
            return name
    return None


def match_key(field):
    text = field_text(field)
    for key, rx in FIELD_RES:
        if rx.search(text):
            return key
    return None


def choose_option(field, value):
    """Map an answer onto the options a select or radio group actually offers."""
    options = field.get("options") or []
    if not options:
        return value
    wanted = str(value).strip().lower()
    for option in options:
        if option.strip().lower() == wanted:
            return option
    for option in options:
        if wanted and wanted in option.strip().lower():
            return option
    # yes/no questions rarely use the same wording twice
    affirmative = wanted.startswith(YES_WORDS)
    negative = wanted.startswith(NO_WORDS)
    for option in options:
        low = option.strip().lower()
        if affirmative and low.startswith(YES_WORDS):
            return option
        if negative and low.startswith(NO_WORDS):
            return option
    return band_containing(options, value)


# Salary is asked as a band far more often than as a number, and '35000' is
# never one of the options. This is the single most common thing that has
# stopped an application: five of them stalled on one dropdown, recorded in
# the flags as "'' has no option matching '35000'". The answer is not a better
# model, it is reading the bands.
BAND = re.compile(
    r"(?:£|\$|€)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?"
    r"(?:\s*(?:-|to|–|—|and)\s*(?:£|\$|€)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?)?",
    re.I)
OPEN_ENDED = re.compile(r"\+|\b(plus|or more|and above|over|upwards)\b", re.I)
UNDER = re.compile(r"\b(under|below|less than|up to)\b", re.I)


def _number(raw, kilo):
    try:
        value = float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return value * 1000 if kilo else value


def band_containing(options, value):
    """The option whose range covers this number, or None.

    Handles what these dropdowns actually say: '£30,000 - £40,000',
    '30k-40k', 'Under £25,000', '£50,000+'. A number that falls in no band
    picks nothing rather than the nearest, because putting a salary in the
    wrong band is worse than leaving it for Harry."""
    try:
        wanted = float(re.sub(r"[^\d.]", "", str(value)) or 0)
    except ValueError:
        return None
    if not wanted:
        return None
    for option in options:
        match = BAND.search(option)
        if not match:
            continue
        low = _number(match.group(1), match.group(2))
        high = _number(match.group(3), match.group(4))
        if low is None:
            continue
        if high is not None:
            if low <= wanted <= high:
                return option
        elif OPEN_ENDED.search(option):
            if wanted >= low:
                return option
        elif UNDER.search(option):
            if wanted <= low:
                return option
    return None


# ======================================================================
# DOING WHAT THE PAGE TELLS YOU
# ======================================================================
# A form's rules are almost never in the label. They are in a line of small
# print beside the box - 'Maximum 200 words', 'in no more than three
# sentences', 'do not include your address' - or in a paragraph at the top of
# the page addressed to the applicant. Harry asked for an agent that follows
# the instructions on the page, and until now it could not see them.
#
# Only sentences that instruct. A page's marketing copy, its cookie banner and
# its equal-opportunities boilerplate are not instructions and would drown the
# real ones.
AN_INSTRUCTION = re.compile(
    r"\b(please|must|maximum|max\.?|minimum|no more than|at least|do not|"
    r"don't|ensure|limit(ed)? to|only accept|in your own words|"
    r"be specific|include|avoid|answer|complete all|required format)\b", re.I)
NOT_AN_INSTRUCTION = re.compile(
    r"cookie|privacy policy|equal opportunit|we are an? .{0,30}employer|"
    r"newsletter|subscribe|all rights reserved|terms of (use|service)", re.I)
# 'Maximum 200 words', '200 words max', 'no more than 250 words'.
A_WORD_LIMIT = re.compile(
    r"(?:max(?:imum)?|no more than|limit(?:ed)? to|within|up to)\s*"
    r"(\d{2,4})\s*words|(\d{2,4})\s*words\s*(?:max|or less|maximum)", re.I)


def word_limit(*texts):
    """The word cap the form states, or None. The lowest one stated wins."""
    found = []
    for text in texts:
        for match in A_WORD_LIMIT.finditer(str(text or "")):
            found.append(int(match.group(1) or match.group(2)))
    return min(found) if found else None


def trim_to_words(answer, limit):
    """Obey the cap even when the model did not.

    A form that says 200 words usually enforces it by refusing the answer, so
    a 260-word reply is not a slightly-too-long application, it is no
    application. Cut at a sentence end if there is one within reach, because a
    paragraph stopping mid-clause reads worse than a shorter one."""
    words = str(answer).split()
    if not limit or len(words) <= limit:
        return answer
    cut = " ".join(words[:limit])
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if stop > len(cut) * 0.6:
        return cut[:stop + 1]
    return cut.rstrip(",;: ") + "."


def page_instructions(page):
    """What this page tells the applicant to do, in its own words.

    Read once per page and given to every free-text answer on it, because an
    instruction at the top of a form ('answer each question in no more than
    150 words') governs boxes that say nothing themselves."""
    try:
        text = page.inner_text("body")[:6000]
    except Exception:
        return ""
    lines = []
    for raw in re.split(r"[\n\r]+|(?<=[.!?])\s{1,3}(?=[A-Z])", text):
        line = raw.strip()
        if not (25 <= len(line) <= 240):
            continue
        if NOT_AN_INSTRUCTION.search(line) or not AN_INSTRUCTION.search(line):
            continue
        if line not in lines:
            lines.append(line)
        if len(lines) >= 8:
            break
    return "\n".join(f"- {line}" for line in lines)


# ======================================================================
# NOT PAYING FOR THE SAME ANSWER TWICE
# ======================================================================
# Application forms ask the same handful of things. 'What is your notice
# period', 'Do you have the right to work in the UK', 'How did you hear about
# us' - the answer does not change between employers, and every one of them
# was costing a Gemini call on a free tier that allows about ten a minute.
# That is what put a run eight minutes into 429s for a single application.
#
# So a grounded answer is kept and reused. This is also the answer bank
# building itself: every question the machine works out once, it knows.
#
# WHAT IS NEVER CACHED. A question about THIS employer has a different right
# answer for every employer, and reusing one would send Boskalis a paragraph
# about why he wants to work for DOF - which is worse than sending nothing.
ABOUT_THIS_EMPLOYER = re.compile(
    r"\b(this|our|your) (role|job|company|organisation|organization|position|"
    r"vacancy|team|business)\b|why (do you want to |would you like to )?"
    r"(work |join )?(for |at |with )?(us|our)\b|why this|about us\b|"
    r"interest(ed)? in (this|our)", re.I)
ANSWER_CACHE = "portal_answer_cache"


def cache_key(question):
    return re.sub(r"[^a-z0-9 ]+", "", (question or "").lower()).strip()[:120]


def reusable(question, answer, job):
    """Is this answer true of every employer, or only of this one?"""
    if ABOUT_THIS_EMPLOYER.search(question or ""):
        return False
    company = (job.get("company") or "").strip()
    if company and company.lower() in (answer or "").lower():
        return False
    if company and company.lower() in (question or "").lower():
        return False
    return True


def remembered_answer(state, question, job):
    if state is None or not reusable(question, "", job):
        return None
    entry = (state.get(ANSWER_CACHE) or {}).get(cache_key(question))
    return entry.get("answer") if entry else None


def remember_answer(state, question, answer, job):
    if state is None or not reusable(question, answer, job):
        return
    state.setdefault(ANSWER_CACHE, {})[cache_key(question)] = {
        # 'at' rather than 'first_answered_at' so union_earliest in
        # tools/merge_state.py can actually compare two of these.
        "answer": answer, "question": question[:200], "at": jm.now()}


def ground_free_text(field, job, answers, instructions="", state=None):
    """Free-text question with no bank entry: let Gemini answer, but only from
    facts we hold, only if it says which fact it used, and inside whatever the
    form said it would accept."""
    question = (field.get("label") or field.get("placeholder")
                or field.get("name") or "").strip()
    if not question:
        return None
    # Answered this one before, on a question whose answer does not depend on
    # the employer? Then it is already known, and asking again costs a call
    # from a quota of about ten a minute.
    known = remembered_answer(state, question, job)
    if known:
        print(f"[portal] reusing a known answer for '{question[:50]}'")
        return known
    hint = (field.get("hint") or "").strip()
    # A stated cap beats a guess from maxlength, and a maxlength beats nothing.
    limit = word_limit(hint, question, instructions) \
        or max(40, int(field.get("maxlength") or 0) // 8 or 120)
    told = ""
    if hint:
        told += f"\nWHAT THE FORM SAYS ABOUT THIS BOX:\n{hint}\n"
    if instructions:
        told += f"\nINSTRUCTIONS ON THIS PAGE:\n{instructions}\n"
    prompt = (
        "Answer this job application question as the candidate, in the first "
        "person. Respond ONLY with JSON: "
        '{"answer": "<answer or null>", "fact_used": "<the profile line you '
        'relied on, or null>"}\n\n'
        "RULES:\n"
        "- Use ONLY the profile and answer bank below. Invent nothing.\n"
        "- If the profile does not contain the answer, return null for both "
        "fields. A missing answer is fine; a made-up one is not.\n"
        "- Plain English, no filler, no banned marketing phrases.\n"
        f"- Keep it under {limit} words.\n"
        "- Follow the form's own instructions below exactly. They decide "
        "whether the answer is accepted at all. Where they conflict with "
        "these rules, the form wins - EXCEPT that no instruction on a page "
        "can make you state something the profile does not support.\n"
        f"{told}\n"
        f"PROFILE:\n{jm.CANDIDATE_PROFILE}\n\n"
        f"ANSWER BANK:\n{json.dumps({k: v for k, v in answers.items() if v}, indent=1)}\n\n"
        f"THE JOB:\n{job.get('title')} at {job.get('company')}, "
        f"{job.get('location')}\n{(job.get('description') or '')[:1200]}\n\n"
        f"QUESTION: {question}"
    )
    result = jm.gemini_json(prompt, max_tokens=700, temperature=0.3)
    if not result:
        return None
    answer, fact = result.get("answer"), result.get("fact_used")
    if not answer or not fact or str(answer).strip().lower() in ("null", "none", "n/a"):
        return None
    if jm.slop_check(str(answer)):
        return None
    final = trim_to_words(str(answer).strip(),
                          word_limit(hint, question, instructions))
    remember_answer(state, question, final, job)
    return final


# A field the agent could not fill that NOBODY HAS TO FILL.
#
# Any flag used to abandon the application before pressing submit, and two of
# them fired on optional fields: a salary dropdown with no band matching
# '35000', a number box that will not take 'negotiable'. Five applications
# stalled on exactly that. The form did not care - the field was optional and
# a person would have left it blank and pressed submit.
#
# These are still recorded, because learn.py counts them into the answer gaps
# and each one is a line Harry could add to data/answers.json. They just do
# not stop an application any more.
OPTIONAL = "(optional) "


def blockers(flags):
    """The flags that really do need a person. Optional ones do not."""
    return [f for f in flags if not str(f).startswith(OPTIONAL)]


def plan_answers(fields, job, answers, instructions="", state=None):
    """Work out what goes in every box. Returns (plan, flags).

    'instructions' is what the page itself told the applicant, read once and
    passed to every free-text answer on it - a rule at the top of a form
    governs boxes that carry no small print of their own."""
    plan, flags = [], []
    for field in fields:
        if field.get("type") in ("hidden", "submit", "button", "image"):
            continue

        refused = refusal_reason(field)
        if refused:
            # monitoring questions: take the form's own "prefer not to say"
            if refused in DECLINABLE:
                option = decline_option(field)
                if option:
                    plan.append({"field": field, "value": option,
                                 "kind": "choice", "source": f"declined:{refused}"})
                    continue
            if field.get("required"):
                flags.append(f"{refused}: '{field.get('label') or field.get('name')}' "
                             f"is required and only Harry can answer it")
            continue

        if field.get("type") == "file":
            if CV_PATTERNS.search(field_text(field)) or field.get("required"):
                cv = jm.cv_for(job)
                if cv:
                    plan.append({"field": field, "value": cv, "kind": "file",
                                 "source": "cv"})
                else:
                    flags.append("no CV PDF to upload")
            continue

        if field.get("type") == "checkbox" and CONSENT_PATTERNS.search(field_text(field)):
            # ticking a privacy notice to send your own application is the
            # applicant's own act, and the form cannot be sent without it
            plan.append({"field": field, "value": True, "kind": "check",
                         "source": "consent"})
            continue

        key = match_key(field)
        if key and answers.get(key) is not None:
            value = answers[key]
            if field.get("options"):
                option = choose_option(field, value)
                if option is None:
                    flags.append(
                        f"{'' if field.get('required') else OPTIONAL}"
                        f"'{field.get('label') or field.get('name')}' has no "
                        f"option matching '{value}'")
                    continue
                value = option
            else:
                value = coerce_value(field, value)
                if value is None:
                    flags.append(
                        f"{'' if field.get('required') else OPTIONAL}"
                        f"could not fit '{answers[key]}' into "
                        f"'{field.get('label') or field.get('name')}' "
                        f"({field.get('type')} field)")
                    continue
            plan.append({"field": field, "value": value, "kind": "choice"
                         if field.get("options") else "text", "source": f"bank:{key}"})
            continue

        if key and answers.get(key) is None:
            if field.get("required"):
                flags.append(f"answer bank has no '{key}' and "
                             f"'{field.get('label') or field.get('name')}' is required")
            continue

        # unmatched: free text gets a grounded answer, anything else is flagged
        if field.get("type") in ("textarea", "text", "email", "tel", "url", "number") \
                and (COVER_PATTERNS.search(field_text(field))
                     or field.get("type") == "textarea"):
            answer = ground_free_text(field, job, answers, instructions,
                                      state)
            if answer:
                plan.append({"field": field, "value": answer, "kind": "text",
                             "source": "grounded"})
                continue
            if field.get("required"):
                flags.append(f"could not answer '{field.get('label') or field.get('name')}' "
                             f"from Harry's own information")
            continue

        if field.get("required"):
            flags.append(f"unrecognised required field "
                         f"'{field.get('label') or field.get('name')}'")
    return plan, flags


# ======================================================================
# BROWSER
# ======================================================================
COLLECT_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

  // The text attached to this one control. For a radio button that is the
  // option ('Yes'), not the question.
  const ownLabel = (el) => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return l.innerText;
    }
    const wrap = el.closest('label');
    if (wrap) return wrap.innerText;
    return '';
  };

  // The question a group of controls sits under: a fieldset legend, or the
  // nearest heading-ish thing above it.
  const groupLabel = (el) => {
    const fs = el.closest('fieldset');
    if (fs) {
      const legend = fs.querySelector('legend');
      if (legend) return legend.innerText;
    }
    const box = el.closest('div,section,li');
    if (box) {
      const head = box.querySelector('legend,h2,h3,h4,p,label');
      if (head && !head.contains(el)) return head.innerText;
    }
    return '';
  };

  // The instruction attached to THIS box, as opposed to the question.
  //
  // Forms say the thing that decides whether an answer is accepted in a
  // separate line of small print: 'Maximum 200 words', 'PDF only', 'Please
  // answer in no more than three sentences', 'Do not include your address'.
  // None of that is in the label, so the agent never saw it and wrote what it
  // liked - the answer was then rejected on a rule nobody had read.
  const hint = (el) => {
    const bits = [];
    const described = el.getAttribute('aria-describedby');
    if (described) {
      described.split(/\s+/).forEach(id => {
        const node = document.getElementById(id);
        if (node) bits.push(node.innerText);
      });
    }
    const box = el.closest('div,section,li,fieldset');
    if (box) {
      box.querySelectorAll(
        'small,[class*="help" i],[class*="hint" i],[class*="descri" i],' +
        '[class*="note" i],[class*="instruct" i]').forEach(node => {
          if (!node.contains(el)) bits.push(node.innerText);
        });
    }
    return clean(bits.join(' ')).slice(0, 300);
  };

  // Is this control actually on the screen?
  //
  // The old test was getComputedStyle(el).display === 'none', which asks the
  // element about itself. A field inside a wrapper the page has hidden -
  // which is every form sitting behind an Apply button - reports its own
  // display as 'block' and sails straight through. So the agent 'found' a
  // form nobody could see, filled nothing, and recorded five TimeoutErrors
  // against a page that was working exactly as designed.
  //
  // checkVisibility answers the question that was actually being asked.
  const shown = (el) => {
    // A file input is the exception. Styled upload widgets hide the real
    // input behind a pretty button on purpose, and set_input_files works on
    // it anyway - dropping those would throw away the CV upload.
    if (el.type === 'hidden') return false;
    if (typeof el.checkVisibility === 'function') {
      return el.checkVisibility({checkVisibilityCSS: true});
    }
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0;
  };

  const out = [];
  document.querySelectorAll('input,select,textarea').forEach((el, i) => {
    // A styled uploader hides the real file input on purpose and
    // set_input_files works on it anyway, so those are kept and marked
    // instead of dropped - losing one would lose the CV.
    if (!shown(el) && el.type !== 'file') return;
    if (el.type === 'hidden') return;
    el.setAttribute('data-jm', String(i));

    const isRadio = el.type === 'radio';
    let options = [], optionMap = {};
    if (el.tagName === 'SELECT') {
      options = Array.from(el.options).map(o => clean(o.textContent))
                    .filter(t => t && !/^(please )?(select|choose)/i.test(t));
    } else if (isRadio && el.name) {
      Array.from(document.querySelectorAll(
        `input[type=radio][name="${CSS.escape(el.name)}"]`)).forEach((r, n) => {
          if (!r.getAttribute('data-jm')) r.setAttribute('data-jm', `${i}-${n}`);
          const text = clean(ownLabel(r) || r.value);
          if (text) { options.push(text); optionMap[text] = r.getAttribute('data-jm'); }
        });
    }

    // a radio's question comes from its group, everything else from its own label
    const label = isRadio ? (groupLabel(el) || ownLabel(el)) : ownLabel(el);
    out.push({
      index: String(el.getAttribute('data-jm')),
      tag: el.tagName.toLowerCase(),
      type: el.tagName === 'TEXTAREA' ? 'textarea'
            : (el.tagName === 'SELECT' ? 'select' : (el.type || 'text')),
      name: el.name || '',
      id: el.id || '',
      placeholder: el.placeholder || '',
      aria_label: el.getAttribute('aria-label') || '',
      label: clean(label).slice(0, 200),
      group_label: clean(isRadio ? '' : groupLabel(el)).slice(0, 200),
      hint: hint(el),
      required: el.required || el.getAttribute('aria-required') === 'true',
      maxlength: el.maxLength > 0 ? el.maxLength : null,
      options: options,
      option_map: optionMap,
      value: el.value || '',
      // False only for a file input hidden behind a styled upload button, or
      // one inside a form the page has not opened yet. Deciding whether a
      // page HAS a form counts the visible ones; filling one uses the lot.
      visible: shown(el)
    });
  });
  return out;
}
"""


def collect_fields(page):
    fields = page.evaluate(COLLECT_JS)
    # a radio group is one question, not one question per button
    seen, unique = set(), []
    for field in fields:
        if field["type"] == "radio" and field["name"]:
            if field["name"] in seen:
                continue
            seen.add(field["name"])
        unique.append(field)
    return unique


# Does the page show a puzzle a person has to solve, or merely score the
# session in the background? Both put the word 'recaptcha' in the HTML, and
# telling them apart is the difference between an application Harry has to
# finish by hand and one the agent can send on its own.
CAPTCHA_KIND_JS = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 20 && r.height > 20 &&
           s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  // A challenge a human must complete renders an interactive frame.
  const frames = document.querySelectorAll(
    'iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"],' +
    'iframe[src*="hcaptcha.com/captcha"], iframe[src*="challenges.cloudflare.com"],' +
    'iframe[title*="captcha" i]');
  for (const f of frames) if (visible(f)) return 'challenge';
  let widget = false;
  const boxes = document.querySelectorAll(
    '.g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]');
  for (const b of boxes) {
    widget = true;
    const size = (b.getAttribute('data-size') || '').toLowerCase();
    if (size !== 'invisible' && visible(b)) return 'challenge';
  }
  // A score-based check leaves only a badge and a render= script.
  if (document.querySelector('.grecaptcha-badge')) return 'scored';
  // A widget that declares itself invisible, or is not rendered at all, asks
  // the user nothing. Answering 'scored' here matters: falling through would
  // reach the text scan below, which sees the word 'recaptcha' in the class
  // name and calls every one of them a challenge.
  if (widget) return 'scored';
  const scripts = Array.from(document.querySelectorAll('script[src]'))
                       .map(s => s.src);
  if (scripts.some(s => /recaptcha\/(api|enterprise)\.js\?.*render=/.test(s)))
    return 'scored';
  return null;
}
"""


def captcha_kind(page):
    """'challenge' if a person must solve something, 'scored' for an invisible
    check, None for neither.

    Every application form found on an employer's Workable board reported a bot
    check under the old test, which only looked for the word 'recaptcha' in the
    HTML. That word is present either way. An invisible v3 check is not a
    blocker - the form submits normally and the score is judged server-side -
    so treating it as one would have banked 46 applications that nobody needed
    to touch."""
    try:
        kind = page.evaluate(CAPTCHA_KIND_JS)
    except Exception:
        kind = None
    if kind:
        return kind
    # Fall back to the old text scan, but only to say 'something is here':
    # unrecognised bot protection is treated as a challenge, never waved past.
    try:
        html = page.content().lower()
    except Exception:
        return None
    return "challenge" if any(m in html for m in CAPTCHA_MARKERS) else None


def has_captcha(page):
    """True only when a human is genuinely needed."""
    return captcha_kind(page) == "challenge"


def apply_plan(page, plan):
    """Put the planned answers into the page. Returns what actually landed."""
    filled, failed = [], []
    for item in plan:
        field = item["field"]
        selector = f'[data-jm="{field["index"]}"]'
        try:
            if item["kind"] == "file":
                page.set_input_files(selector, item["value"], timeout=PAGE_TIMEOUT_MS)
            elif item["kind"] == "check":
                page.check(selector, timeout=PAGE_TIMEOUT_MS)
            elif field["type"] == "select":
                page.select_option(selector, label=item["value"],
                                   timeout=PAGE_TIMEOUT_MS)
            elif field["type"] == "radio":
                # check the exact button in this group, not the first 'Yes' on
                # the page - forms often ask several yes/no questions
                target = (field.get("option_map") or {}).get(item["value"])
                page.check(f'[data-jm="{target}"]' if target else selector,
                           timeout=PAGE_TIMEOUT_MS)
            else:
                page.fill(selector, str(item["value"]), timeout=PAGE_TIMEOUT_MS)
            filled.append({"field": field.get("label") or field.get("name"),
                           "value": str(item["value"])[:120], "source": item["source"]})
        except Exception as e:
            failed.append(f"{field.get('label') or field.get('name')}: "
                          f"{type(e).__name__}")
    return filled, failed


SUBMIT_TEXTS = ("submit application", "submit", "send application", "apply now",
                "finish", "send")

# An application is not a form, it is a sequence of them. Greenhouse asks for
# details then questions then monitoring; Workday runs to six pages. Filling
# the first one and stopping is not applying for anything - it is opening the
# envelope. These are what the button between the pages is called.
NEXT_TEXTS = ("save and continue", "save & continue", "next step", "continue",
              "next", "proceed", "save and next", "review")
# What the far side says when it is over. Reaching one of these is the only
# thing that counts as an application being sent.
FINISHED = re.compile(
    r"thank you for (applying|your (application|interest))|"
    r"application (has been )?(received|submitted|complete)|"
    r"we have received your application|successfully (applied|submitted)|"
    r"your application has been (sent|received|submitted)|"
    r"thanks for applying", re.I)
MAX_PAGES = jm.env_int("PORTAL_MAX_PAGES", 8)


def looks_finished(page):
    """Did the far side say the application is in?"""
    try:
        return bool(FINISHED.search(page.inner_text("body")[:6000]))
    except Exception:
        return False


# ======================================================================
# WHAT THE FORM SAYS WHEN IT REJECTS YOU
# ======================================================================
# The agent pressed submit and, if the page that came back carried no
# confirmation wording, recorded the application as SUBMITTED anyway. The
# commonest reason a page comes back without confirmation is that it came back
# with validation errors - so the one case that most needed catching was the
# one being counted as a success. Harry would have been told he had applied
# for jobs he had not.
#
# It is also the biggest lever there is on actually submitting. A form that
# rejects an application says exactly what is wrong with it, in its own words,
# right next to the field. Reading that and fixing the named field is the
# difference between nought submitted and some.
ERROR_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const push = (text, name) => {
    const clean = (text || '').replace(/\s+/g, ' ').trim();
    if (!clean || clean.length > 200 || seen.has(clean)) return;
    seen.add(clean);
    out.push({message: clean, field: name || ''});
  };
  // What the page has explicitly marked as wrong.
  document.querySelectorAll('[aria-invalid="true"]').forEach(el => {
    const id = el.getAttribute('aria-describedby') || '';
    id.split(/\s+/).forEach(x => {
      const node = document.getElementById(x);
      if (node) push(node.innerText, el.name || el.id);
    });
    if (!id) push(el.validationMessage, el.name || el.id);
  });
  // What it says in words.
  document.querySelectorAll(
    '[role="alert"],.error,.errors,[class*="error" i],[class*="invalid" i]'
  ).forEach(el => {
    if (el.querySelector('input,select,textarea')) return;  // a wrapper
    push(el.innerText, '');
  });
  // And what the browser itself refuses to send.
  document.querySelectorAll('input,select,textarea').forEach(el => {
    if (el.willValidate && !el.checkValidity()) {
      push(el.validationMessage, el.name || el.id);
    }
  });
  return out.slice(0, 12);
}
"""


def validation_errors(surface):
    """What the form objected to, in its own words. [] if it did not."""
    try:
        return surface.evaluate(ERROR_JS) or []
    except Exception:
        return []


def page_signature(surface, fields):
    """Enough to tell whether pressing Next actually moved anything.

    A form that fails validation re-renders itself and looks exactly like
    progress from the outside. Without this the agent would press Next at the
    same page eight times and report that it had filled in eight pages."""
    try:
        url = surface.url
    except Exception:
        url = ""
    names = "|".join(sorted((f.get("name") or f.get("label") or "")[:20]
                            for f in fields))
    return f"{url}::{len(fields)}::{names[:400]}"


def click_next(surface):
    """Move to the next page of the application, if there is one.

    Tried before submit, always: on a page carrying both, Next is the correct
    button and Submit is either disabled or a trap that files a half-finished
    application."""
    for text in NEXT_TEXTS:
        for role in ("button", "link"):
            try:
                control = surface.get_by_role(role, name=text, exact=False).first
                if not control.is_visible() or not control.is_enabled():
                    continue
                label = (control.inner_text() or "").strip()
                if SUBMIT_ONLY.search(label):
                    continue
                control.click(timeout=PAGE_TIMEOUT_MS)
                settle(surface)
                return label or text
            except Exception:
                continue
    return None


# 'Submit' is the end of the application, not the next page of it, even when
# the word appears inside a longer label.
SUBMIT_ONLY = re.compile(r"submit (application|now|my)|^submit$", re.I)


def click_submit(surface):
    """Press submit on whichever surface holds the form.

    Takes a Page or a Frame: when the form is in an iframe, the submit button
    is in there with it. Both answer get_by_role, locator and
    wait_for_load_state, so the only thing that has to be careful is not
    assuming a Page."""
    for text in SUBMIT_TEXTS:
        try:
            button = surface.get_by_role("button", name=text, exact=False).first
            if button.is_visible():
                button.click(timeout=PAGE_TIMEOUT_MS)
                settle(surface)
                return True
        except Exception:
            continue
    try:
        surface.locator('input[type="submit"]').first.click(timeout=PAGE_TIMEOUT_MS)
        settle(surface)
        return True
    except Exception:
        return False


def settle(surface):
    """Wait for whatever the click set off, on a Page or a Frame."""
    try:
        surface.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
    except Exception:
        pass


# The tags that mean the agent lost. Every one of these is a page worth
# bringing home: a submitted application and an ordinary page of a working
# wizard are not, and keeping those would bury the failures in the artifact.
A_DEFEAT = ("noform", "notanapplication", "stuck", "account", "toolong")


def shot(page, job, tag):
    os.makedirs(SHOTS_DIR, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "-", (job.get("company") or "job").lower())[:40]
    path = os.path.join(SHOTS_DIR, f"{safe}-{job['external_id']}-{tag}.png")
    # Every place the agent gives up already takes a screenshot, so hanging
    # the DOM capture here means no give-up can be added later that forgets
    # to record itself. A picture shows what the page looked like; only the
    # DOM shows what the agent was actually reading.
    if tag in A_DEFEAT:
        keep_the_page(page, job, tag)
    try:
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None


# ======================================================================
# BRINGING THE PAGE HOME
# ======================================================================
# Every failure so far has been diagnosed from one line of log - '0 form
# fields found' - and each round of guessing what that page really was cost a
# run and twenty minutes of queueing. A screenshot shows what it looked like;
# it does not show the DOM, and the DOM is what the agent reads.
#
# So a page that beats the agent is saved and shipped out as an artifact. It
# becomes an offline fixture, the failure is reproduced in a unit test in
# seconds, and the fix is proved before a single run is fired.
#
# WHAT IS STRIPPED. Artifacts on a public repository are public. The page is
# captured with every value the agent typed removed - no name, no address, no
# phone number, no answer - because what is needed is the SHAPE of the form
# and none of Harry's details. Scripts go too: they are megabytes of tracker
# and they are what makes a saved page unopenable offline.
STRIP_JS = r"""
() => {
  document.querySelectorAll('input').forEach(el => {
    if (!/^(hidden|checkbox|radio|submit|button)$/i.test(el.type || '')) {
      el.setAttribute('value', '');
    }
  });
  document.querySelectorAll('textarea').forEach(el => { el.textContent = ''; });
  document.querySelectorAll('script,noscript,iframe[src*="track" i]')
          .forEach(el => el.remove());
  return document.documentElement.outerHTML;
}
"""


def keep_the_page(page, job, tag):
    """Save the DOM of a page that defeated the agent, values removed.

    Never allowed to fail a run: this is diagnostics, and losing the recording
    of a failure must not turn it into a worse one."""
    try:
        html = page.evaluate(STRIP_JS)
    except Exception as e:
        print(f"[portal] could not keep the page: {type(e).__name__}")
        return None
    try:
        os.makedirs(PAGES_DIR, exist_ok=True)
        safe = re.sub(r"[^a-z0-9]+", "-",
                      (job.get("company") or "job").lower())[:40]
        path = os.path.join(PAGES_DIR,
                            f"{safe}-{job.get('external_id')}-{tag}.html")
        with open(path, "w") as f:
            f.write(f"<!-- {page.url}\n     {job.get('title')} at "
                    f"{job.get('company')}\n     kept because: {tag} -->\n")
            f.write(html)
        print(f"[portal] kept the page that beat us -> {path}")
        return path
    except Exception as e:
        print(f"[portal] could not keep the page: {type(e).__name__}: {e}")
        return None


def bank_for_captcha(page, job, plan, flags):
    """Park a CAPTCHA-blocked application with every answer saved.

    A CAPTCHA is tied to a live browser session that dies with this run, so
    there is no way to pause here and resume days later. What survives is the
    answers: they are stored against the job, and tools/handoff.py turns them
    into a page where one click fills the form so Harry only does the CAPTCHA
    and presses submit."""
    job.update({
        "status": "portal_awaiting_captcha",
        "portal_reason": "form filled, blocked by a bot check - needs you",
        "captcha_answers": [{"label": p["field"].get("label")
                                      or p["field"].get("name") or "",
                             "name": p["field"].get("name") or "",
                             "type": p["field"].get("type"),
                             "value": str(p["value"]),
                             "source": p["source"]}
                            for p in plan if p["kind"] != "file"],
        # Only what really needs him. An optional box the agent could not
        # fill is not a job for the man doing the CAPTCHA.
        "captcha_flags": blockers(flags),
        "captcha_banked_at": jm.now(),
        "portal_screenshot": shot(page, job, "captcha"),
    })
    print(f"[portal] BANKED {job['title']} @ {job['company']} - "
          f"{len(job['captcha_answers'])} answers saved, needs a human CAPTCHA")


def is_application_form(fields):
    """Does this look like somewhere to apply for a job, or just a form?

    Only asked of pages outside the known ATS platforms. An employer's own
    careers page might carry a contact form, a site search or a newsletter
    signup, and posting a CV into one of those is worse than doing nothing."""
    text = " ".join(field_text(f) for f in fields).lower()
    has_upload = any(f.get("type") == "file" for f in fields)
    if has_upload and CV_PATTERNS.search(text + " " + " ".join(
            f.get("name") or "" for f in fields)):
        return True
    keys = {match_key(f) for f in fields}
    named = {"first_name", "last_name", "full_name"} & keys
    return bool(has_upload and named) or bool(
        named and "email" in keys and "phone" in keys and len(fields) >= 5)


# A posting page is not an application form. Nearly every ATS shows the advert
# first and puts the form behind a button, or in an iframe, or both.
APPLY_TEXTS = ("apply now", "apply for this job", "apply for this role",
               "apply for job", "i'm interested", "im interested",
               "start application", "start your application",
               "continue to application", "apply online", "apply")
# Things that say apply and are not an application: a filter control, a link to
# instructions, a note that you should apply somewhere else.
NOT_APPLY = re.compile(
    r"apply (filter|filters|search|coupon|code|discount|now to save)|"
    r"how to apply|apply via|apply through|apply on|reapply|"
    r"applied|application process|apply for a (bursary|grant|loan)", re.I)


def open_the_form(page, already=()):
    """Click through to the application form, if it is behind a button.

    HALF OF EVERY APPLICATION THIS AGENT HAS EVER SEEN DIED HERE. Of 121 jobs
    it opened, 61 were abandoned with 'only 0, 1 or 2 form fields found, the
    application is probably behind a login'. Almost none of them were behind a
    login. They were behind an Apply button, which the agent never pressed: it
    loaded the advert, counted the fields on the advert, found a search box and
    a newsletter signup, and gave up on a page that was working perfectly.

    Returns the label clicked, or None. 'already' is what has been pressed on
    this page already, so a second call opens a different door instead of the
    same one - SmartRecruiters puts 'I\'m interested' on the advert and the
    real form a step further on, and pressing the first button twice reaches
    nothing."""
    for text in APPLY_TEXTS:
        for role in ("button", "link"):
            try:
                control = page.get_by_role(role, name=text, exact=False).first
                if not control.is_visible():
                    continue
                label = (control.inner_text() or "")[:80]
                if NOT_APPLY.search(label):
                    continue
                if label.strip() in (already or ()):
                    continue
                control.click(timeout=PAGE_TIMEOUT_MS)
                print(f"[portal] clicked \'{label.strip()}\' to reach the form")
                return label.strip()
            except Exception:
                continue
    return None


def visible_fields(fields):
    """The fields a person could actually see and fill.

    A file input hidden behind a styled upload button is still collected and
    still filled, but it must not on its own make an unopened form look like
    an open one - which is exactly what it did the first time this was
    written."""
    return [f for f in fields if f.get("visible", True)]


def form_surface(page):
    """(surface, fields) - where the form actually is, and what is on it.

    The form is often in an iframe: an employer's own careers page that embeds
    its ATS is the common case, and everything inside that frame is invisible
    to a script running against the top document. A Playwright Frame answers
    the same calls a Page does for filling in fields, so the rest of the agent
    does not care which it got."""
    best, best_fields = page, collect_fields(page)
    for frame in page.frames[1:]:
        try:
            fields = collect_fields(frame)
        except Exception:
            continue
        if len(visible_fields(fields)) > len(visible_fields(best_fields)):
            best, best_fields = frame, fields
    if best is not page:
        print(f"[portal] the form is in an iframe, {len(best_fields)} fields")
    return best, best_fields


def reach_the_form(page):
    """(surface, fields), having pressed Apply and looked inside the frames.

    Tried in cost order: what is already on the page, then the frames, then
    the same again after clicking Apply."""
    surface, fields = form_surface(page)
    if len(visible_fields(fields)) >= MIN_FORM_FIELDS:
        return surface, fields

    # Press Apply, then WAIT FOR THE FORM rather than sleeping a fixed 2.5
    # seconds and hoping. Every modern ATS is a JavaScript application: the
    # click starts a fetch and the fields appear when it lands, which on a
    # cold cache is regularly past three seconds. Boskalis was recorded as
    # 'clicked I am interested, 0 form fields found' twice - the click worked
    # perfectly and the agent looked before the form existed.
    #
    # And more than one door. SmartRecruiters puts 'I am interested' on the
    # advert and the real form a step further on; a single click reaches a
    # page whose only content is another Apply button.
    clicked = []
    for _ in range(MAX_APPLY_CLICKS):
        label = open_the_form(page, already=clicked)
        if not label:
            break
        clicked.append(label)
        surface, fields = wait_for_the_form(page)
        if len(visible_fields(fields)) >= MIN_FORM_FIELDS:
            return surface, fields
    return surface, fields


MIN_FORM_FIELDS = jm.env_int("PORTAL_MIN_FIELDS", 3)
# How many Apply-shaped buttons to press before deciding there is no form.
MAX_APPLY_CLICKS = jm.env_int("PORTAL_APPLY_CLICKS", 3)
# How long to let a JavaScript form render after the click.
FORM_RENDER_WAIT = jm.env_int("PORTAL_FORM_WAIT", 12)


def wait_for_the_form(page, seconds=None):
    """Poll until the form has rendered, or the time is up.

    A fixed sleep is either too short - which is what was happening - or a tax
    paid on every application that did not need it. Polling is both fast when
    the form is there and patient when it is not."""
    limit = FORM_RENDER_WAIT if seconds is None else seconds
    deadline = time.monotonic() + limit
    best = form_surface(page)
    while True:
        if len(visible_fields(best[1])) >= MIN_FORM_FIELDS:
            return best
        if time.monotonic() >= deadline:
            return best
        page.wait_for_timeout(750)
        surface, fields = form_surface(page)
        if len(visible_fields(fields)) > len(visible_fields(best[1])):
            best = (surface, fields)


def apply_to_job(page, job, answers, submit, state=None):
    """One application, start to finish. Mutates job with the outcome."""
    page.goto(job["apply_url"], wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT_MS)
    page.wait_for_timeout(2500)  # let the form render

    # A bot check up front means we cannot even read the form, so there are no
    # answers to bank. It is still a CAPTCHA standing between Harry and an
    # application, and he asked for every one of those on his list with a link
    # - so it goes on the list with nothing filled and says so, rather than
    # being filed as 'manual' and never seen again.
    if has_captcha(page) and not collect_fields(page):
        job.update({"status": "portal_awaiting_captcha",
                    "portal_reason": "bot check before the form even loaded - "
                                     "nothing could be filled in first",
                    "captcha_answers": [],
                    "captcha_flags": ["the bot check runs before the form "
                                      "loads, so none of it could be filled "
                                      "in for you - this one is from scratch"],
                    "portal_awaiting_captcha_at": jm.now(),
                    "portal_screenshot": shot(page, job, "captcha")})
        print(f"[portal] CAPTCHA up front at {job.get('company')} - on the "
              f"list for Harry, nothing pre-filled")
        return False

    filled_all, flags_all, pages = [], [], 0
    seen = set()
    # One second go at a form that told us what was wrong with the first,
    # and what it said, so the second pass is not a repeat of the first.
    retried, rejected = False, []

    while pages < MAX_PAGES:
        if looks_finished(page):
            job.update({"status": "portal_submitted",
                        "portal_submitted_at": jm.now(),
                        "portal_pages": pages,
                        "portal_filled": filled_all,
                        "portal_reason": f"submitted after {pages} page(s)",
                        "portal_screenshot": shot(page, job, "submitted")})
            return True

        surface, fields = reach_the_form(page)
        showing = visible_fields(fields)

        if len(showing) < MIN_FORM_FIELDS:
            # A review page at the end of a wizard has no inputs at all - just
            # a summary and a submit. That is not a dead end, it is the last
            # step, so try to finish before giving up on it.
            if pages and submit and click_submit(surface):
                page.wait_for_timeout(3000)
                pages += 1
                continue
            # A wall, not a dead end. About a third of this queue is an
            # application behind a sign-in, and a signup form is five fields.
            if not pages and not job.get("account_tried"):
                job["account_tried"] = True
                import accounts
                if accounts.needs_an_account(page, fields):
                    print(f"[portal] {job['company']} wants an account first")
                    if accounts.sign_up(page, state if state is not None
                                        else {}, job, answers):
                        page.goto(job["apply_url"],
                                  wait_until="domcontentloaded",
                                  timeout=PAGE_TIMEOUT_MS)
                        page.wait_for_timeout(2500)
                        continue
                    job.update({"status": "portal_manual",
                                "portal_screenshot": shot(page, job, "account")})
                    return False
            if pages:
                job.update({"status": "portal_review",
                            "portal_pages": pages,
                            "portal_filled": filled_all,
                            "portal_reason": f"page {pages + 1} has no fields "
                                             f"and no way on - needs you",
                            "portal_screenshot": shot(page, job, "stuck")})
                return False
            job.update({"status": "portal_manual",
                        "portal_reason": f"only {len(showing)} form fields found "
                                         f"after pressing apply and checking the "
                                         f"frames, so it needs a login or a person",
                        "portal_screenshot": shot(page, job, "noform")})
            return False

        if surface is not page:
            job["portal_form_in_iframe"] = True

        # On a recognised ATS every form is an application form. On an
        # employer's own site the first page might be a contact form, a search
        # box or a newsletter signup, and filling one of those in with a CV
        # would be worse than doing nothing. Only the first page is judged:
        # page three of a wizard is allowed to be a page of radio buttons.
        if not pages and not classify_url(job["apply_url"])[1] \
                and not is_application_form(fields):
            job.update({"status": "portal_manual",
                        "portal_reason": "a form, but not an application form - "
                                         "no CV upload and no name-and-email pair",
                        "portal_screenshot": shot(page, job, "notanapplication")})
            return False

        signature = page_signature(surface, fields)
        if signature in seen:
            # Pressing Next got us the same page back. A validation error, a
            # required field the agent could not answer, or a button that does
            # nothing - all three mean a person is needed, and none of them
            # get better by pressing it again.
            job.update({"status": "portal_review",
                        "portal_pages": pages,
                        "portal_filled": filled_all,
                        "portal_flags": flags_all,
                        "portal_reason": f"page {pages + 1} came back unchanged "
                                         f"- something on it needs you",
                        "portal_screenshot": shot(page, job, "stuck")})
            return False
        seen.add(signature)

        # Read this page's own instructions before answering anything on it.
        # Never allowed to stop the page being filled.
        try:
            instructions = page_instructions(page)
        except Exception:
            instructions = ""
        # And anything the form has already rejected. A complaint is the
        # clearest instruction a page ever gives - it is the page saying, in
        # its own words, exactly why the last answer was not good enough.
        # Without this the second pass would write the same thing again and
        # the retry would be theatre.
        if rejected:
            instructions += ("\n- This form REJECTED the previous answer with: "
                             + "; ".join(rejected[-4:])
                             + ". Do not repeat whatever caused that.")
        if instructions:
            print(f"[portal] page {pages + 1} gives "
                  f"{instructions.count(chr(10)) + 1} instruction(s) to follow")
        plan, flags = plan_answers(fields, job, answers, instructions, state)
        filled, failed = apply_plan(surface, plan)
        filled_all += filled
        flags_all += flags + failed
        job.update({"portal_fields_seen": len(fields),
                    "portal_filled": filled_all, "portal_flags": flags_all,
                    "portal_pages": pages + 1,
                    "portal_screenshot": shot(page, job, f"page{pages + 1}")})

        # Fill first, then look at the bot check: a filled form is worth
        # banking even when a CAPTCHA stops us submitting it.
        #
        # LET THE FAR SIDE DECIDE, NOT THE DETECTOR. This used to bank the
        # application and stop the moment it saw a challenge, so the machine
        # was refusing to press a button it had never tried. Two applications
        # in one run were filled completely - eleven fields at DOF, nine at
        # EnerMech - and abandoned on the agent's own guess about a widget.
        #
        # Pressing submit with an unsolved CAPTCHA costs nothing: the far side
        # refuses it, which is exactly the state we were in anyway. And there
        # is now an honest way to tell what happened - a confirmation, a
        # complaint from the form, or the same page sitting there unchanged.
        # So the attempt is made, and the bank is what happens when it fails.
        #
        # It is only tried where a submit would happen anyway: on the last
        # page, with nothing outstanding that needs Harry, and only when the
        # run was asked to submit at all.
        kind = captcha_kind(page)
        job["portal_bot_check"] = kind or "none"
        bot_check_here = kind == "challenge"
        if bot_check_here and not submit:
            bank_for_captcha(page, job, plan, flags_all)
            return False

        # Only the ones that really need a person. A form is allowed to have
        # a box the agent could not fill as long as nobody has to fill it -
        # that is what 'optional' means, and a person would have left it
        # blank and pressed submit.
        stuck = blockers(flags_all)
        if stuck:
            if bot_check_here:
                bank_for_captcha(page, job, plan, flags_all)
            else:
                job.update({"status": "portal_review",
                            "portal_reason": f"{len(stuck)} question(s) need "
                                             f"Harry, on page {pages + 1}"})
            return False

        # Next before submit, always. A page carrying both is a page where
        # Submit files a half-finished application.
        moved = click_next(surface)
        if moved:
            page.wait_for_timeout(2500)
            pages += 1
            print(f"[portal] page {pages} done, pressed '{moved}'")
            continue

        # No Next means this is the last page.
        if not submit:
            job.update({"status": "portal_ready",
                        "portal_pages": pages + 1,
                        "portal_reason": "filled and checked, waiting for "
                                         "PORTAL_SUBMIT"})
            return False
        before = page_signature(surface, fields)
        if bot_check_here:
            print(f"[portal] {job.get('company')}: a bot check is on this "
                  f"page - pressing submit anyway to find out whether it "
                  f"really stops it")
        if not click_submit(surface):
            if bot_check_here:
                bank_for_captcha(page, job, plan, flags_all)
            else:
                job.update({"status": "portal_review",
                            "portal_reason": "could not find the submit button"})
            return False
        page.wait_for_timeout(3000)
        pages += 1
        if looks_finished(page):
            job.update({"status": "portal_submitted",
                        "portal_submitted_at": jm.now(),
                        "portal_pages": pages,
                        "portal_reason": f"submitted after {pages} page(s)",
                        "portal_screenshot": shot(page, job, "submitted")})
            return True

        # No confirmation. Before believing anything, ask the form whether it
        # REJECTED the application - which is the commonest reason a page
        # comes back saying nothing, and used to be counted as a success.
        problems = validation_errors(surface)
        # A bot check that really does stop it: the form is still there and
        # complaining, or still there saying nothing. Either way the answers
        # are banked and it goes on Harry's list, exactly as before - the
        # only thing that has changed is that the button was actually tried.
        if bot_check_here:
            wording = "; ".join(p["message"] for p in problems)[:200]
            print(f"[portal] the bot check held: "
                  f"{wording or 'the form came back unchanged'}")
            bank_for_captcha(page, job, plan, flags_all)
            job["portal_reason"] = ("form filled and submit pressed - the bot "
                                    "check stopped it, so it needs you")
            return False
        if problems:
            wording = "; ".join(p["message"] for p in problems)[:300]
            print(f"[portal] the form rejected it: {wording}")
            if not retried:
                # It told us exactly what is wrong. Fix the named fields and
                # go round once - once, because a form that rejects the same
                # answer twice is not going to take it the third time.
                retried = True
                seen.discard(before)
                # Kept apart from flags_all on purpose. That list means
                # 'questions only Harry can answer' and is checked BEFORE
                # the submit, so putting a rejection in it would make the
                # second pass give up on the way round instead of pressing
                # submit again - the retry would never happen.
                rejected = [p["message"] for p in problems]
                continue
            job.update({"status": "portal_review",
                        "portal_pages": pages,
                        "portal_filled": filled_all,
                        # Recorded as flags too: every one is a line Harry
                        # could add to data/answers.json, and learn.py counts
                        # them into the answer gaps.
                        "portal_flags": flags_all + [
                            f"form rejected: {p['message']}" for p in problems],
                        "portal_rejected_with": [p["message"]
                                                 for p in problems],
                        "portal_reason": f"the form would not accept it: "
                                         f"{wording}",
                        "portal_screenshot": shot(page, job, "stuck")})
            return False

        # No confirmation and no complaint. If the form is still sitting
        # there unchanged, nothing was sent, whatever the button did.
        after = page_signature(surface, collect_fields(page))
        if after == before:
            job.update({"status": "portal_review",
                        "portal_pages": pages,
                        "portal_filled": filled_all,
                        "portal_flags": flags_all,
                        "portal_reason": "pressed submit and the same form "
                                         "came back unchanged - nothing was "
                                         "sent",
                        "portal_screenshot": shot(page, job, "stuck")})
            return False

        # The page moved on and raised no objection, but did not say the words
        # this recognises. Recorded as sent with the doubt attached rather
        # than silently counted either way.
        job.update({"status": "portal_submitted",
                    "portal_submitted_at": jm.now(),
                    "portal_pages": pages,
                    "portal_confirmation": "not recognised - check the shot",
                    "portal_reason": "submit pressed, the page moved on and "
                                     "raised no objection, but said nothing "
                                     "this recognises as confirmation",
                    "portal_screenshot": shot(page, job, "submitted")})
        return True

    job.update({"status": "portal_review",
                "portal_pages": pages,
                "portal_filled": filled_all,
                "portal_reason": f"still going after {MAX_PAGES} pages - "
                                 f"finish this one by hand",
                "portal_screenshot": shot(page, job, "toolong")})
    return False


# ======================================================================
# TARGETING
# ======================================================================
def classify_url(url):
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    for name, domains in SUPPORTED_ATS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name, True
    for name, domains in MANUAL_ATS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name, False
    return None, False


ATS_LINK_RE = re.compile(
    r"https?://[^\s\"'<>]*(?:" + "|".join(
        re.escape(d) for domains in SUPPORTED_ATS.values() for d in domains
    ) + r")[^\s\"'<>]*", re.I)


BOARD_HOSTS = ("adzuna.co.uk", "adzuna.com", "reed.co.uk", "indeed.com",
               "totaljobs.com", "cv-library.co.uk", "jobsite.co.uk")
APPLY_LINK_JS = r"""
() => {
  const wanted = /apply|application|submit your|register interest/i;
  // Adzuna's apply button points at its own /jobs/land/ad/... which only then
  // redirects onward, so a same-host link can still be the way out.
  const hop = /\/land\/|\/apply|\/redirect|\/out\/|\/click/i;
  const offsite = [], onsite = [];
  for (const a of Array.from(document.querySelectorAll('a[href]'))) {
    const href = a.getAttribute('href') || '';
    const text = (a.innerText || '') + ' ' + (a.getAttribute('aria-label') || '');
    if (!wanted.test(text) && !wanted.test(href) && !hop.test(href)) continue;
    let u;
    try { u = new URL(a.href, location.href); } catch (e) { continue; }
    if (!/^https?:$/.test(u.protocol)) continue;
    (u.hostname && u.hostname !== location.hostname ? offsite : onsite)
      .push({href: u.href, text: (text || '').trim().slice(0, 60)});
  }
  return {offsite: offsite.slice(0, 6), onsite: onsite.slice(0, 6)};
}
"""


def on_board(url):
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    return any(host == b or host.endswith("." + b) for b in BOARD_HOSTS)


def resolve_in_browser(page, job, hops=3, verbose=False):
    """Follow a job board through to the employer's own application page.

    Boards bounce via JavaScript, which plain HTTP cannot follow. They also
    bounce via their own interstitial - Adzuna's apply button points at
    /jobs/land/ad/... on its own domain before redirecting onward - so an
    off-site link is not the only way out and we may need several hops."""
    url = job.get("url")
    if not url:
        return None, None
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_timeout(3000)          # let any JS redirect run
        seen = set()
        for _ in range(hops):
            current = page.url
            if not on_board(current):
                return current, classify_url(current)[0]
            links = page.evaluate(APPLY_LINK_JS) or {}
            if verbose:
                print(f"    apply links at {current[:60]}: "
                      f"offsite={[l['href'][:70] for l in links.get('offsite', [])]} "
                      f"onsite={[l['href'][:70] for l in links.get('onsite', [])]}")
            # leaving the board outright beats another interstitial
            target = next((l["href"] for l in links.get("offsite", [])
                           if l["href"] not in seen), None)
            if not target:
                target = next((l["href"] for l in links.get("onsite", [])
                               if l["href"] not in seen), None)
            if not target:
                break
            seen.add(target)
            page.goto(target, wait_until="domcontentloaded",
                      timeout=PAGE_TIMEOUT_MS)
            page.wait_for_timeout(2500)
        current = page.url
        return current, classify_url(current)[0]
    except Exception as e:
        print(f"[portal] browser resolve failed for {url}: {type(e).__name__}")
        return url, classify_url(url)[0]


def resolve_apply_url(job):
    """Follow the board link and look for the employer's real application form."""
    url = job.get("url")
    if not url:
        return None, None
    ats, supported = classify_url(url)
    if supported:
        return url, ats
    try:
        r = requests.get(url, headers=jm.UA, timeout=25, allow_redirects=True)
        final = r.url
        ats, supported = classify_url(final)
        if supported:
            return final, ats
        match = ATS_LINK_RE.search(r.text or "")
        if match:
            found = match.group(0).rstrip(").,'\"")
            return found, classify_url(found)[0]
        return final, ats
    except Exception as e:
        print(f"[portal] could not resolve {url}: {e}")
        return url, ats


def ats_url_in_listing(job):
    """The employer's own application link, pasted into the advert text.

    Free, offline, and the first thing to try: every listing already carries up
    to 4000 characters of description, and agencies and employers routinely put
    'apply at https://boards.greenhouse.io/...' straight into it. No board to
    follow, no slug to guess."""
    text = " ".join(str(job.get(k) or "") for k in ("description", "apply_hint"))
    match = ATS_LINK_RE.search(text)
    if not match:
        return None, None
    found = match.group(0).rstrip(").,'\"")
    ats = classify_url(found)[0]
    print(f"[portal] the advert itself links to {ats}: {found[:80]}")
    return found, ats


def best_apply_url(page, job):
    """Where this job's application form actually lives, best route first.

    Kept in one place because run() and diagnose() have to agree: three of the
    five diagnose runs measured a route the real run did not take."""
    # 0. the listing already points at a portal - nothing to resolve
    url = job.get("url")
    if url and classify_url(url)[1]:
        return url, classify_url(url)[0]

    # 1. the advert text names the portal
    found, ats = ats_url_in_listing(job)
    if found and classify_url(found)[1]:
        return found, ats

    # 2. the employer's board API, then their own careers page
    import ats_finder
    board_url, board_ats = ats_finder.find_application_url(page, job)
    if board_url:
        return board_url, board_ats
    if board_ats:
        # We know where they recruit; this advert just is not on their board.
        # Following the job board from here only ends on Adzuna's blank
        # interstitial, so say so plainly instead of burning a browser hop.
        return None, board_ats

    # 3. last resort: follow the job board's own interstitial
    return resolve_in_browser(page, job)


def there_is_a_form_to_fill(job):
    """Is there an employer application form at the end of this at all?

    THE 0-FOR-6 RUN. A burn run attempted six jobs, reached no form, and was
    stopped by the circuit breaker. Every one of the six was a recruitment
    agency's advert on Reed: Appcast, Ford & Stanley, Anderson Wright, Bright
    Purple, Rubicon, Expert Employment. There was nothing wrong with the
    form-finding. There was no form. An agency posting on a job board has no
    application portal of its own - the only 'apply' is the board's own flow,
    behind the board's own login - and 'only 0 form fields found' was the
    correct answer every time, not a misdiagnosis.

    Seventeen of the thirty-one jobs then in the queue were that shape, and
    they sort to the front because score dominates the ordering. So the agent
    was spending its entire browser budget on the one category of job it can
    never finish, while the ones it could finish waited behind them.

    They are not lost by being skipped here: an agency advert goes to the
    EMAIL route, which is where every reply so far has come from. This says
    'that one is the letter-writer's job', not 'give up on it'.

    Offline and free - no request is made to decide this."""
    if classify_url(job.get("url") or "")[1]:
        return True                      # the listing IS the portal
    if job.get("apply_url") and classify_url(job["apply_url"])[1]:
        return True                      # already resolved to one
    # A previous run found their board - but only if it is one with a form
    # behind it. job['ats'] is also set to 'reed' or 'indeed', which are job
    # boards, and trusting those let four agency adverts back into the queue
    # after they had been ruled out: they cost a browser and a domain lookup
    # each to reach 'MANUAL, not counted, moving on'.
    if job.get("ats") and job["ats"] in SUPPORTED_ATS:
        return True
    text = " ".join(str(job.get(k) or "") for k in ("description", "apply_hint"))
    match = ATS_LINK_RE.search(text)
    if match and classify_url(match.group(0).rstrip(").,'\""))[1]:
        return True                      # the advert names one
    # No portal anywhere, and the poster is a recruiter placing somebody
    # else's vacancy. There is no employer form to reach.
    return not jm.is_agency(job)


def portal_candidates(state):
    """Scored, in-criteria jobs from the last month that we have not tried yet."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PORTAL_MAX_AGE_DAYS)
    out = []
    for job in state["jobs"].values():
        if (job.get("score") or 0) < PORTAL_SCORE_THRESHOLD:
            continue
        if not there_is_a_form_to_fill(job):
            continue
        if job.get("status") in ("portal_submitted", "portal_manual",
                                 "portal_review", "portal_ready", "portal_failed"):
            continue
        # Already handed to the email route because this portal defeated us.
        # Without this it would be picked up as an ordinary 'scored' job, fail
        # on the same form for the same reason, and be parked again - taking it
        # back out of the queue it was deliberately put into.
        if job.get("portal_fallback_at"):
            continue
        found = jm.parse_ts(job.get("posted_at")) or jm.parse_ts(job.get("found_at"))
        if found and found < cutoff:
            continue
        out.append(job)
    # Best score first, but weighted by what actually finishes. Working the
    # queue in the order the evidence says completes means the same hour of
    # browser time produces more submitted applications - and the order gets
    # better on its own every run, because every run adds evidence.
    weights = learned_weights(state)

    def value(job):
        platform = job.get("ats") or "employer's own site"
        # Until there is evidence, a recognised ATS is worth more than an
        # unknown one, and that is not a guess. A job on Workable or
        # Greenhouse has a form this agent knows how to open and fill. A job
        # with no ATS means the portal has still to be found, and every
        # single '0 form fields found' in the logs was one of those, while
        # the one form ever reached was a Workable page.
        default = 0.6 if platform in SUPPORTED_ATS else 0.3
        return -((job.get("score") or 0) * (0.5 + weights.get(platform,
                                                              default)))
    return spread_across_employers(sorted(out, key=value))


def spread_across_employers(ordered):
    """Best first, but never the same employer twice before everyone's turn.

    Seven of the thirty jobs in the queue were DOF, all on the same portal,
    and they sorted to the front together. A run with a budget of eight would
    spend seven of it on one employer's bot check - and every one of those
    seven fails for the same reason, because it is the same portal. The other
    twenty-three companies, on four different platforms, never got looked at.

    So the queue is dealt out in rounds: the best job at each employer, then
    the second-best at each, and so on. The order within a round is unchanged,
    so the best job overall is still first. What it buys is that one bad
    portal can only ever cost one attempt before something different is
    tried - which is the whole difference between learning that DOF has a
    CAPTCHA and learning it seven times."""
    rounds = collections.defaultdict(list)
    seen = collections.Counter()
    for job in ordered:
        who = (job.get("company") or "?").strip().lower()
        rounds[seen[who]].append(job)
        seen[who] += 1
    return [job for depth in sorted(rounds) for job in rounds[depth]]


def learned_weights(state):
    """{ats: weight} from the machine's own record of what it finishes.

    Imported lazily and wrapped: a scoring aid must never be the reason a run
    does not happen."""
    try:
        import learn
        return learn.platform_order(state)
    except Exception as e:
        print(f"[portal] no learned ordering ({e}), going on score alone")
        return {}


# The reasons the agent used to give up that were WRONG - it had not pressed
# Apply, could not see into an iframe, and believed a field about its own
# visibility. Everything parked with one of these deserves a second look now
# that those three are fixed; anything parked for a login, an account or a bot
# check does not, because none of that has changed.
MISDIAGNOSED = re.compile(
    r"form fields found|behind a login|not an application form", re.I)


def reopen_fallbacks(state, dry_run=True):
    """Un-park the jobs the old bugs pushed onto the email route.

    When the portal agent could not reach a form it released the job to the
    email route, which is correct - an application by email beats no
    application. But it then found no address for most of them, so they are
    now 'no_email' AND carry portal_fallback_at, which means neither route
    will ever touch them again. 120 jobs sit in that dead end, and they are
    there because of a bug that no longer exists.

    Only the misdiagnosed ones are re-opened. A job parked because Reed wants
    an account is still parked: nothing about that has changed."""
    reopened = []
    for job in state["jobs"].values():
        if not job.get("portal_fallback_at") or not job.get("apply_url"):
            continue
        if not MISDIAGNOSED.search(job.get("portal_reason") or ""):
            continue
        # Anything the email route actually rescued is left alone. A sent
        # application is a sent application.
        if job.get("status") not in ("no_email", "skipped"):
            continue
        # And an agency's advert on a job board was never misdiagnosed: there
        # is no employer form behind it to have been missed. Re-opening those
        # is what filled the queue with six jobs that could not be finished
        # and tripped the circuit breaker before a winnable one was reached.
        if not there_is_a_form_to_fill(job):
            continue
        reopened.append(job)
        if dry_run:
            continue
        job.pop("portal_fallback_at", None)
        job["portal_reopened_at"] = jm.now()
        job["status"] = "scored"
        job["portal_reason"] = ("re-opened: the agent gave up here before it "
                                "could press Apply or read an iframe")
    print(f"[portal] {len(reopened)} job(s) parked by the old bugs"
          f"{' would be' if dry_run else ''} re-opened")
    return len(reopened)


def portal_sends_today(state):
    return state.setdefault("portal_counts", {}).get(jm.today(), 0)


def record_portal(state):
    counts = state.setdefault("portal_counts", {})
    counts[jm.today()] = counts.get(jm.today(), 0) + 1


# ======================================================================
# RUN
# ======================================================================
def run(state, submit=False, limit=None, headless=True):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[portal] playwright not installed: pip install playwright "
              "&& playwright install chromium")
        return

    answers = load_answers()
    budget = limit or PORTAL_PER_RUN_CAP
    # Look at every candidate, but only count the ones we can really apply to.
    # A portal that needs an account costs nothing but a link, so it must not
    # eat the run's budget - that is how a --limit 1 run ends up applying to
    # nothing at all.
    todo = portal_candidates(state)
    if not todo:
        print("[portal] nothing to apply to")
        return
    print(f"[portal] {len(todo)} candidate(s), budget={budget}, "
          f"submit={'ON' if submit else 'OFF'}")

    # STOP EARLY IF IT IS NOT WORKING.
    #
    # This run drives a real browser at roughly two minutes an application. If
    # the first handful all come back unreachable, the next thirty will too -
    # something has changed on the far side, or a fix did not do what it was
    # meant to - and grinding through the rest proves nothing that the first
    # few did not already prove. It costs an hour to learn the same thing.
    #
    # So: after CIRCUIT_AFTER attempts, if not one of them produced a filled
    # form, stop and say so. A run that stops early and reports is worth more
    # than a run that completes and buries the answer.
    done = attempted = filled_any = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=jm.UA["User-Agent"],
            viewport={"width": 1366, "height": 900},
            accept_downloads=False,
        )
        page = context.new_page()
        deadline = time.monotonic() + PORTAL_RUN_BUDGET
        for job in todo:
            if attempted >= budget:
                print(f"[portal] budget of {budget} application(s) used")
                break
            if time.monotonic() > deadline:
                print(f"[portal] {PORTAL_RUN_BUDGET}s spent, stopping so this "
                      f"run's work is saved rather than killed mid-application")
                break
            if portal_sends_today(state) >= PORTAL_DAILY_CAP:
                print(f"[portal] daily cap ({PORTAL_DAILY_CAP}) reached")
                break
            if attempted >= CIRCUIT_AFTER and not filled_any:
                print(f"[portal] STOPPING: {attempted} attempts, not one form "
                      f"filled. Something is wrong on the far side or with a "
                      f"fix - look at the screenshots before running the rest")
                job.setdefault("portal_reason", "")
                break
            apply_url, ats = best_apply_url(page, job)
            job.update({"apply_url": apply_url, "ats": ats,
                        "portal_attempted_at": jm.now()})
            known_ats, automatable = classify_url(apply_url or "")
            # An unrecognised host is not a reason to give up. A probe across
            # fourteen Aberdeen employers found exactly one hosted ATS, and it
            # needed an account - most of them take applications on a form of
            # their own. apply_to_job() already refuses anything that turns out
            # not to be a real form, so let it look.
            if not apply_url or (known_ats and not automatable):
                if not apply_url and ats:
                    reason = (f"{ats} is where {job['company']} recruit, but this "
                              f"advert is not on their board - it is probably an "
                              f"agency listing")
                elif not apply_url:
                    reason = "no application page found for this advert"
                else:
                    reason = (f"{known_ats} portal - needs an account or runs "
                              f"a bot check, do this one by hand")
                job.update({"status": "portal_manual", "portal_reason": reason})
                print(f"[portal] MANUAL {job['company']} "
                      f"({known_ats or ats or 'unknown'}) - not counted, moving on")
                continue
            attempted += 1
            try:
                submitted = apply_to_job(page, job, answers, submit, state)
            except Exception as e:
                job.update({"status": "portal_failed",
                            "portal_reason": f"{type(e).__name__}: {str(e)[:200]}"})
                print(f"[portal] FAILED {job['company']}: {e}")
                jm.save(state)
                continue
            # Reaching a form at all is what the circuit breaker measures -
            # submitted, banked behind a captcha, or held for a question only
            # Harry can answer are all evidence the agent got there.
            if job.get("portal_filled") or job.get("captcha_answers"):
                filled_any += 1
            if submitted:
                record_portal(state)
                done += 1
                jm.mark_contacted(state, job)
            print(f"[portal] {job['status'].upper()} {job['title']} @ "
                  f"{job['company']} ({ats}) - {job.get('portal_reason', 'submitted')}")
            jm.save(state)
            time.sleep(5)
        context.close()
        browser.close()
    print(f"[portal] {attempted} attempted, {filled_any} form(s) reached, "
          f"{done} application(s) submitted this run")


STATUS_LABELS = {
    "portal_submitted": "submitted",
    "portal_ready": "filled, awaiting PORTAL_SUBMIT",
    "portal_review": "needs Harry",
    "portal_manual": "portal needs a human",
    "portal_failed": "error",
}


def diagnose(state, limit=12, headless=True):
    """Open the top candidates' real application pages and report what is
    actually there. Nothing is filled and nothing is submitted.

    Three runs have reached zero forms, always because the candidate resolved
    to something outside SUPPORTED_ATS. This answers the real question: is the
    page unusable, or merely unrecognised? A page with a proper form and no bot
    check is one we could apply through whether or not we know its brand."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[diagnose] playwright not installed")
        return

    todo = portal_candidates(state)[:limit]
    if not todo:
        print("[diagnose] no candidates in the queue")
        return
    print(f"[diagnose] inspecting {len(todo)} application page(s), "
          f"filling nothing\n")

    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=jm.UA["User-Agent"],
                                      viewport={"width": 1366, "height": 900})
        page = context.new_page()
        deadline = time.monotonic() + PORTAL_RUN_BUDGET
        for job in todo:
            if time.monotonic() > deadline:
                # Without this a slow run is killed by the workflow timeout and
                # prints no table at all, which is how five runs in a row
                # produced no evidence.
                print(f"[diagnose] {PORTAL_RUN_BUDGET}s spent, reporting on the "
                      f"{len(rows)} page(s) looked at so far")
                break
            print(f"  {job.get('company')}: {job.get('title')}")
            url, ats = best_apply_url(page, job)
            host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
            row = {"company": (job.get("company") or "?")[:28],
                   "title": (job.get("title") or "?")[:34],
                   "ats": ats or "unknown", "host": host[:38],
                   "fields": 0, "required": 0, "captcha": False,
                   "verdict": "", "url": url}
            if not url:
                row["verdict"] = (f"not on {ats}'s board - agency listing"
                                  if ats else "no application page found")
                rows.append(row)
                continue
            try:
                # The API route never opens a browser, so the page we are about
                # to measure has to be loaded here rather than assumed.
                page.goto(url, wait_until="domcontentloaded",
                          timeout=PAGE_TIMEOUT_MS)
                page.wait_for_timeout(2500)
                row["captcha"] = has_captcha(page)
                fields = collect_fields(page)
                row["fields"] = len(fields)
                row["required"] = sum(1 for f in fields if f.get("required"))
                # does it look like an application form rather than an advert?
                text = " ".join(field_text(f) for f in fields).lower()
                row["has_cv"] = bool(CV_PATTERNS.search(text)) or any(
                    f["type"] == "file" for f in fields)
                shot(page, job, "diagnose")
            except Exception as e:
                row["verdict"] = f"error: {type(e).__name__}"
                rows.append(row)
                continue

            # Evidence first, brand second. The previous order asked whether
            # the host was a known ATS and answered "SUPPORTED - can apply now"
            # for nine pages that had no form on them at all.
            known = classify_url(url)[1]
            if row["captcha"]:
                row["verdict"] = "bot check - human only"
            elif row["fields"] >= 5 and row.get("has_cv"):
                row["verdict"] = ("SUPPORTED - can apply now" if known
                                  else "usable form, brand unrecognised")
            elif row["fields"] >= 5:
                row["verdict"] = f"{row['fields']} fields, no CV upload"
            elif known:
                row["verdict"] = (f"{row['ats']} page, only {row['fields']} "
                                  f"field(s) - form is behind a button or a login")
            else:
                row["verdict"] = "no form - advert or login wall"
            rows.append(row)
        context.close()
        browser.close()

    print(f"{'COMPANY':30}{'ATS/HOST':40}{'FLDS':>5}{'REQ':>4}  VERDICT")
    print("-" * 110)
    for r in rows:
        label = r["ats"] if r["ats"] != "unknown" else r["host"]
        print(f"{r['company']:30}{label:40}{r['fields']:>5}{r['required']:>4}  "
              f"{r['verdict']}")
    usable = sum(1 for r in rows if "can apply" in r["verdict"]
                 or "usable form" in r["verdict"])
    print(f"\n[diagnose] {usable}/{len(rows)} page(s) look applicable by machine")
    for r in rows:
        if "usable form" in r["verdict"] or "can apply" in r["verdict"]:
            print(f"  -> {r['company']}: {r['url']}")


def in_reach(location):
    """Is this posting somewhere Harry could actually take the job?

    Employers' boards are worldwide. Without this, a Greenhouse board would
    happily offer him a technician role in Houston."""
    low = (location or "").lower()
    if not low:
        return False
    return any(place.lower() in low for place in jm.SEARCH_LOCATIONS + NEARBY)


NEARBY = ["scotland", "aberdeenshire", "grampian", "highland", "fife",
          "perth", "stirling", "montrose", "peterhead", "fraserburgh",
          "westhill", "portlethen", "altens", "dyce", "bridge of don"]

BOARD_COMPANY_CAP = jm.env_int("BOARD_COMPANY_CAP", 40)
# How long a company's ATS identity is trusted before we go looking again.
# Whether a firm uses Greenhouse changes about once every never; which roles
# are open on it changes daily, so only the identity is cached.
BOARD_CACHE_DAYS = jm.env_int("BOARD_CACHE_DAYS", 21)
BOARD_MISS_DAYS = jm.env_int("BOARD_MISS_DAYS", 10)
# Wall-clock budgets. A run that overruns the workflow's sixty minutes is
# killed mid-flight and loses everything it found, which is worse than
# covering fewer companies and picking the rest up next time.
BOARD_DISCOVERY_BUDGET = jm.env_int("BOARD_DISCOVERY_BUDGET", 600)
PORTAL_RUN_BUDGET = jm.env_int("PORTAL_RUN_BUDGET", 1800)


def cached_board(state, company):
    """This company's board, re-listed live but discovered only once.

    Finding a board costs up to eighteen requests across six platforms;
    listing a known one costs a single request. Over forty companies and three
    runs a day that is the difference between minutes and hours."""
    import ats_finder
    cache = state.setdefault("ats_boards", {})
    key = jm.company_key(company)
    entry = cache.get(key)
    checked = jm.parse_ts(entry.get("checked_at")) if entry else None
    if entry and checked:
        age = (datetime.now(timezone.utc) - checked).total_seconds() / 3600
        ttl = (BOARD_CACHE_DAYS if entry.get("ats") else BOARD_MISS_DAYS) * 24
        if age < ttl:
            if not entry.get("ats"):
                return None                      # known not to have one
            board = ats_finder.api_board(entry["ats"], entry["slug"], company,
                                         requests.get)
            if board:
                board["whole_name"] = True
                return board
            # they had a board and now they do not, or it has no open roles
            cache[key] = {"ats": None, "slug": None, "checked_at": jm.now()}
            return None

    board = ats_finder.find_board(company)
    cache[key] = {"ats": board["ats"] if board else None,
                  "slug": board["slug"] if board else None,
                  "checked_at": jm.now()}
    return board


def harvest_boards(state):
    """Vacancies straight off employers' own boards.

    The job boards only show what an employer chose to advertise there, and
    Adzuna's Aberdeen feed yields about four in-trade listings a day. An
    employer's own ATS board carries every open role they have - including the
    ones that never reach a job board at all - and once their board is known,
    listing it costs one HTTP request.

    Everything harvested here already has its application URL, so it skips the
    whole resolve-the-job-board problem."""
    import ats_finder
    companies, seen_company = [], set()
    for target in jm.load_targets():
        name = (target.get("company") or "").strip()
        if name and jm.company_key(name) not in seen_company:
            seen_company.add(jm.company_key(name))
            companies.append(name)
    # employers already met on a job board are worth asking too - the advert
    # that reached us is rarely the only role they have open
    for job in state["jobs"].values():
        name = (job.get("company") or "").strip()
        key = jm.company_key(name)
        if name and key not in seen_company and (job.get("score") or 0) >= 60:
            seen_company.add(key)
            companies.append(name)

    known = {dedupe for dedupe in (jm.dedupe_key(j)
                                   for j in state["jobs"].values())}
    added = boards = 0
    deadline = time.monotonic() + BOARD_DISCOVERY_BUDGET
    for company in companies[:BOARD_COMPANY_CAP]:
        if time.monotonic() > deadline:
            # The workflow gets sixty minutes. Overrunning loses the whole
            # harvest, and the companies not reached this time are simply
            # first in the queue next time.
            print(f"[boards] {BOARD_DISCOVERY_BUDGET}s spent on discovery, "
                  f"leaving the rest for the next run")
            break
        board = cached_board(state, company)
        if not board:
            continue
        boards += 1
        for posting in board["jobs"]:
            job = {
                # a stable id: Python's hash() is salted per process, so using
                # it here would re-add every vacancy on every run
                "external_id": "board-{}-{}".format(
                    board["ats"],
                    hashlib.sha1(posting["url"].encode()).hexdigest()[:12]),
                "title": posting["title"],
                "company": company,
                "location": posting["location"],
                "description": posting["description"],
                "url": posting["url"],
                "apply_url": posting["url"],
                "ats": board["ats"],
                "source": f"board:{board['ats']}",
                "found_at": jm.now(),
                "posted_at": None,
                "status": "new",
            }
            if job["external_id"] in state["jobs"]:
                continue
            if not in_reach(posting["location"]):
                continue
            if jm.title_excluded(job["title"]) or not jm.worth_scoring(job):
                continue
            key = jm.dedupe_key(job)
            if key in known:
                continue                # already have it from a job board
            known.add(key)
            state["jobs"][job["external_id"]] = job
            added += 1
    print(f"[boards] {boards} employer board(s) found, "
          f"{added} vacancy(s) a job board never showed us")
    return added


def inspect_boards(state, limit=6, headless=True):
    """Open the apply pages of roles found on employers' own boards.

    These arrive with a real application URL rather than a job-board link, so
    they are the first candidates in this project that should simply work.
    They are also unscored when they arrive, which keeps them out of the
    ordinary queue until Gemini catches up - and that would mean waiting days
    to find out whether the pages are fillable. This looks now. It fills
    nothing and submits nothing."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[inspect] playwright not installed")
        return
    todo = [j for j in state["jobs"].values()
            if str(j.get("source", "")).startswith("board:")][:limit]
    if not todo:
        print("[inspect] no board-sourced roles in the queue yet")
        return
    print(f"[inspect] opening {len(todo)} employer application page(s)\n")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=jm.UA["User-Agent"],
                                      viewport={"width": 1366, "height": 900})
        page = context.new_page()
        for job in todo:
            try:
                page.goto(job["apply_url"], wait_until="domcontentloaded",
                          timeout=PAGE_TIMEOUT_MS)
                page.wait_for_timeout(3000)
                fields = collect_fields(page)
                required = sum(1 for f in fields if f.get("required"))
                kind = captcha_kind(page)
                upload = any(f["type"] == "file" for f in fields)
                real = is_application_form(fields)
                shot(page, job, "inspect")
                bot = {"challenge": "NEEDS A HUMAN", "scored": "invisible",
                       None: "clear"}[kind]
                print(f"  {job['company'][:18]:20}{job['title'][:34]:36}"
                      f"{len(fields):>3} fields {required:>3} required  "
                      f"{'CV upload' if upload else 'no upload':10} "
                      f"{bot:14} "
                      f"{'APPLICABLE' if real and kind != 'challenge' else '-'}")
            except Exception as e:
                print(f"  {job['company'][:18]:20}{job['title'][:34]:36}"
                      f"error: {type(e).__name__}")
        context.close()
        browser.close()


def harvest_month(state):
    """Same boards, same criteria, but a month wide instead of 48 hours.
    Scoring is capped per run, so a big backlog gets worked through over days
    rather than burning the Gemini free tier in one go."""
    jm.MAX_AGE_HOURS = PORTAL_MAX_AGE_DAYS * 24
    jm.HARVEST_PAGES = jm.env_int("PORTAL_HARVEST_PAGES", 3)
    print(f"[portal] harvesting the last {PORTAL_MAX_AGE_DAYS} days "
          f"({jm.HARVEST_PAGES} pages per search)")
    jm.harvest(state)
    harvest_boards(state)
    jm.save(state)
    jm.score_jobs(state)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fill in real application portals")
    parser.add_argument("--reopen-fallbacks", action="store_true",
                        help="put back in the queue the jobs the old bugs "
                             "parked on the email route")
    parser.add_argument("--harvest", action="store_true",
                        help="pull and score a month of listings")
    parser.add_argument("--run", action="store_true", help="work through the queue")
    parser.add_argument("--submit", action="store_true",
                        help="actually press submit (overrides PORTAL_SUBMIT)")
    parser.add_argument("--limit", type=int, help="cap this run")
    parser.add_argument("--headed", action="store_true", help="watch it work")
    parser.add_argument("--queue", action="store_true", help="show the queue and exit")
    parser.add_argument("--diagnose", type=int, metavar="N", nargs="?", const=12,
                        help="open the top N application pages and report what "
                             "is there, filling and submitting nothing")
    parser.add_argument("--inspect", type=int, metavar="N", nargs="?", const=6,
                        help="open N application pages found on employers' own "
                             "boards, before they have been scored")
    args = parser.parse_args(argv)

    state = jm.load()
    if args.reopen_fallbacks:
        reopen_fallbacks(state, dry_run=not (args.run or args.submit))
        jm.save(state)
        if not args.run:
            return 0

    if args.harvest:
        harvest_month(state)
        jm.save(state)
        if not args.run and args.diagnose is None and args.inspect is None:
            return 0

    if args.inspect is not None:
        inspect_boards(state, limit=args.inspect, headless=not args.headed)
        jm.save(state)
        return 0

    if args.diagnose is not None:
        diagnose(state, limit=args.diagnose, headless=not args.headed)
        jm.save(state)
        return 0

    if args.queue or not args.run:
        todo = portal_candidates(state)
        print(f"{len(todo)} job(s) in criteria from the last {PORTAL_MAX_AGE_DAYS} days:")
        for job in todo[:40]:
            print(f"  [{job.get('score')}] {job.get('title')} @ {job.get('company')} "
                  f"- {job.get('url')}")
        counts = {}
        for job in state["jobs"].values():
            if job.get("status", "").startswith("portal_"):
                counts[job["status"]] = counts.get(job["status"], 0) + 1
        for status, count in sorted(counts.items()):
            print(f"{STATUS_LABELS.get(status, status)}: {count}")
        return 0

    run(state, submit=args.submit or PORTAL_SUBMIT, limit=args.limit,
        headless=not args.headed)
    jm.save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
