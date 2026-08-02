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
PORTAL_SUBMIT = jm.env_flag("PORTAL_SUBMIT", False)
PORTAL_PER_RUN_CAP = jm.env_int("PORTAL_PER_RUN_CAP", 10)
PORTAL_DAILY_CAP = jm.env_int("PORTAL_DAILY_CAP", 25)
PORTAL_SCORE_THRESHOLD = jm.env_int("PORTAL_SCORE_THRESHOLD", jm.SCORE_THRESHOLD)
PAGE_TIMEOUT_MS = 45000

ANSWERS_PATH = os.path.join(jm.ROOT, "data", "answers.json")
SHOTS_DIR = os.path.join(jm.ROOT, "screenshots")

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
    return None


def ground_free_text(field, job, answers):
    """Free-text question with no bank entry: let Gemini answer, but only from
    facts we hold, and only if it says which fact it used."""
    question = (field.get("label") or field.get("placeholder")
                or field.get("name") or "").strip()
    if not question:
        return None
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
        f"- Keep it under {max(40, int(field.get('maxlength') or 0) // 8 or 120)} words.\n\n"
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
    return str(answer).strip()


def plan_answers(fields, job, answers):
    """Work out what goes in every box. Returns (plan, flags)."""
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
                    flags.append(f"'{field.get('label') or field.get('name')}' has no "
                                 f"option matching '{value}'")
                    continue
                value = option
            else:
                value = coerce_value(field, value)
                if value is None:
                    flags.append(f"could not fit '{answers[key]}' into "
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
            answer = ground_free_text(field, job, answers)
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

  const out = [];
  document.querySelectorAll('input,select,textarea').forEach((el, i) => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return;
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
      required: el.required || el.getAttribute('aria-required') === 'true',
      maxlength: el.maxLength > 0 ? el.maxLength : null,
      options: options,
      option_map: optionMap,
      value: el.value || ''
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


def has_captcha(page):
    try:
        html = page.content().lower()
    except Exception:
        return False
    return any(marker in html for marker in CAPTCHA_MARKERS)


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


def click_submit(page):
    for text in SUBMIT_TEXTS:
        try:
            button = page.get_by_role("button", name=text, exact=False).first
            if button.is_visible():
                button.click(timeout=PAGE_TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                return True
        except Exception:
            continue
    try:
        page.locator('input[type="submit"]').first.click(timeout=PAGE_TIMEOUT_MS)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
        return True
    except Exception:
        return False


def shot(page, job, tag):
    os.makedirs(SHOTS_DIR, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "-", (job.get("company") or "job").lower())[:40]
    path = os.path.join(SHOTS_DIR, f"{safe}-{job['external_id']}-{tag}.png")
    try:
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None


def apply_to_job(page, job, answers, submit):
    """One application, start to finish. Mutates job with the outcome."""
    page.goto(job["apply_url"], wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT_MS)
    page.wait_for_timeout(2500)  # let the form render

    if has_captcha(page):
        job.update({"status": "portal_manual",
                    "portal_reason": "captcha or bot check on the form",
                    "portal_screenshot": shot(page, job, "captcha")})
        return False

    fields = collect_fields(page)
    if len(fields) < 3:
        job.update({"status": "portal_manual",
                    "portal_reason": f"only {len(fields)} form fields found, "
                                     f"the application is probably behind a login",
                    "portal_screenshot": shot(page, job, "noform")})
        return False

    plan, flags = plan_answers(fields, job, answers)
    filled, failed = apply_plan(page, plan)
    flags += failed
    job.update({"portal_fields_seen": len(fields), "portal_filled": filled,
                "portal_flags": flags,
                "portal_screenshot": shot(page, job, "filled")})

    if flags:
        job.update({"status": "portal_review",
                    "portal_reason": f"{len(flags)} question(s) need Harry"})
        return False
    if not submit:
        job.update({"status": "portal_ready",
                    "portal_reason": "filled and checked, waiting for PORTAL_SUBMIT"})
        return False

    if not click_submit(page):
        job.update({"status": "portal_review",
                    "portal_reason": "could not find the submit button"})
        return False

    page.wait_for_timeout(3000)
    job.update({"status": "portal_submitted", "portal_submitted_at": jm.now(),
                "portal_screenshot": shot(page, job, "submitted")})
    return True


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
  const links = Array.from(document.querySelectorAll('a[href]'));
  // an explicit apply link that leaves this site is what we are after
  for (const a of links) {
    const text = (a.innerText || '') + ' ' + (a.getAttribute('aria-label') || '');
    if (!wanted.test(text) && !wanted.test(a.getAttribute('href') || '')) continue;
    try {
      const u = new URL(a.href, location.href);
      if (u.hostname && u.hostname !== location.hostname) return u.href;
    } catch (e) { /* skip malformed */ }
  }
  return null;
}
"""


def on_board(url):
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    return any(host == b or host.endswith("." + b) for b in BOARD_HOSTS)


def resolve_in_browser(page, job):
    """Follow a job board through to the employer's own page.

    Adzuna and friends bounce via JavaScript, which plain HTTP cannot follow -
    that is why every candidate looked like 'www.adzuna.co.uk' with an 11-field
    search form. The browser is already open, so use it."""
    url = job.get("url")
    if not url:
        return None, None
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_timeout(3000)          # let any JS redirect run
        current = page.url
        if on_board(current):
            target = page.evaluate(APPLY_LINK_JS)
            if target:
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


def portal_candidates(state):
    """Scored, in-criteria jobs from the last month that we have not tried yet."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PORTAL_MAX_AGE_DAYS)
    out = []
    for job in state["jobs"].values():
        if (job.get("score") or 0) < PORTAL_SCORE_THRESHOLD:
            continue
        if job.get("status") in ("portal_submitted", "portal_manual",
                                 "portal_review", "portal_ready", "portal_failed"):
            continue
        found = jm.parse_ts(job.get("posted_at")) or jm.parse_ts(job.get("found_at"))
        if found and found < cutoff:
            continue
        out.append(job)
    return sorted(out, key=lambda j: -(j.get("score") or 0))


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

    done = attempted = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=jm.UA["User-Agent"],
            viewport={"width": 1366, "height": 900},
            accept_downloads=False,
        )
        page = context.new_page()
        for job in todo:
            if attempted >= budget:
                print(f"[portal] budget of {budget} application(s) used")
                break
            if portal_sends_today(state) >= PORTAL_DAILY_CAP:
                print(f"[portal] daily cap ({PORTAL_DAILY_CAP}) reached")
                break
            apply_url, ats = resolve_in_browser(page, job)
            job.update({"apply_url": apply_url, "ats": ats,
                        "portal_attempted_at": jm.now()})
            if not apply_url or not classify_url(apply_url)[1]:
                job.update({"status": "portal_manual",
                            "portal_reason": f"{ats or 'unknown'} portal - needs an "
                                             f"account or runs a bot check, do this one "
                                             f"by hand"})
                print(f"[portal] MANUAL {job['company']} ({ats or 'unknown'}) "
                      f"- not counted, moving on")
                continue
            attempted += 1
            try:
                submitted = apply_to_job(page, job, answers, submit)
            except Exception as e:
                job.update({"status": "portal_failed",
                            "portal_reason": f"{type(e).__name__}: {str(e)[:200]}"})
                print(f"[portal] FAILED {job['company']}: {e}")
                jm.save(state)
                continue
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
    print(f"[portal] {done} application(s) submitted this run")


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
        for job in todo:
            url, ats = resolve_in_browser(page, job)
            host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
            row = {"company": (job.get("company") or "?")[:28],
                   "title": (job.get("title") or "?")[:34],
                   "ats": ats or "unknown", "host": host[:38],
                   "fields": 0, "required": 0, "captcha": False,
                   "verdict": "", "url": url}
            try:
                row["captcha"] = has_captcha(page)   # already on the page
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

            if row["captcha"]:
                row["verdict"] = "bot check - human only"
            elif classify_url(url)[1]:
                row["verdict"] = "SUPPORTED - can apply now"
            elif row["fields"] >= 5 and row.get("has_cv"):
                row["verdict"] = "usable form, brand unrecognised"
            elif row["fields"] >= 5:
                row["verdict"] = f"{row['fields']} fields, no CV upload"
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


def harvest_month(state):
    """Same boards, same criteria, but a month wide instead of 48 hours.
    Scoring is capped per run, so a big backlog gets worked through over days
    rather than burning the Gemini free tier in one go."""
    jm.MAX_AGE_HOURS = PORTAL_MAX_AGE_DAYS * 24
    jm.HARVEST_PAGES = jm.env_int("PORTAL_HARVEST_PAGES", 3)
    print(f"[portal] harvesting the last {PORTAL_MAX_AGE_DAYS} days "
          f"({jm.HARVEST_PAGES} pages per search)")
    jm.harvest(state)
    jm.save(state)
    jm.score_jobs(state)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fill in real application portals")
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
    args = parser.parse_args(argv)

    state = jm.load()
    if args.harvest:
        harvest_month(state)
        jm.save(state)
        if not args.run and args.diagnose is None:
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
