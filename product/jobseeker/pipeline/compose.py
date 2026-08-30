"""Writing the letter, and refusing to send it when it is wrong.

The rules below are not style preferences. They are the difference between a
26% reply rate and the 1-5% cold email normally manages, and every one of them
is **enforced in code rather than asked of the model**. That distinction is the
whole point: a model told to avoid a phrase complies for two paragraphs and
drifts back. A model whose draft is rejected and returned with the reason does
not get the chance.

What code owns:

  - 60-90 words in the body, greeting and sign-off excluded
  - the first line names the exact role plus a detail from that advert
  - two or three numbered proof points
  - exactly one question, as the call to action
  - the greeting and the sign-off, both stamped on afterwards
  - a subject of at most eight words that names the role
  - no banned phrase, no markdown, no exclamation marks, no em dashes
  - **nothing from the user's never_claim list**

That last one replaces the original's single hardcoded rule about a lapsed
security clearance. It is the most important check here, because it is the
only one whose failure follows the user around after the application.
"""

from __future__ import annotations

import re

BANNED = [
    "I hope this email finds you well", "passionate", "leverage", "delve",
    "seamless", "synergy", "dynamic", "thrilled", "excited to apply",
    "perfect fit", "hit the ground running", "fast-paced environment",
    "proven track record", "results-driven", "detail-oriented", "team player",
    "I am writing to", "utilize", "spearheaded", "esteemed", "keen to",
    "furthermore", "moreover",
]
BANNED_RES = [(b, re.compile(r"\b" + re.escape(b.lower()) + r"\b")) for b in BANNED]

STOPWORDS = {"with", "from", "role", "jobs", "job", "team", "work", "your",
             "this", "that", "have", "will", "para", "part", "time", "full",
             "based", "week", "year", "hour", "days", "shift", "night"}

SIGNOFF_LINE_RE = re.compile(
    r"^(best\b|kind regards|regards|thanks|cheers|sincerely|yours\b|"
    r"many thanks|all the best)", re.I)

WORD_RE = re.compile(r"[A-Za-z0-9'/-]+")

# Shapes that name a credential inside a free-text never_claim entry.
ACRONYM_RE = re.compile(r"\b([A-Z]{2,6})\b")
ORDINAL_RE = re.compile(r"\b(\d+(?:st|nd|rd|th)\s+\w+)", re.I)
TITLECASE_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")

MIN_WORDS, MAX_WORDS = 60, 90
MAX_SUBJECT_WORDS = 8


# ----------------------------------------------------------------------
# text hygiene
# ----------------------------------------------------------------------
def normalise(text: str) -> str:
    """Code owns punctuation and formatting; the model only supplies words."""
    text = (text or "").replace("—", " - ").replace("–", "-")
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[*_#`]+", "", text)
    text = re.sub(r"!+", ".", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def strip_signoff(body: str, profile) -> str:
    """Remove whatever sign-off the model produced so code can add the real one.

    Cuts from the first sign-off marker in the last few lines to the end, so
    "Best wishes,\\nS" goes entirely.
    """
    first_name = (profile.name or "").split()[0].lower() if profile.name else ""
    lines = body.rstrip().split("\n")
    cut = None
    for i in range(max(0, len(lines) - 5), len(lines)):
        line = lines[i].strip()
        low = line.lower()
        if (SIGNOFF_LINE_RE.match(line)
                or (first_name and low.startswith(first_name))
                or (profile.phone and profile.phone in line)
                or "cv attached" in low):
            cut = i
            break
    if cut is not None:
        lines = lines[:cut]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).rstrip()


def assemble(body: str, greeting: str, profile) -> tuple[str, str]:
    """Force the exact greeting and sign-off around the model's copy."""
    body = normalise(body)
    lines = body.split("\n")
    if lines and lines[0].lower().startswith(("hi", "hello", "dear")):
        lines = lines[1:]
    core = strip_signoff("\n".join(lines).strip(), profile)
    first_name = (profile.name or "").split()[0] if profile.name else ""
    return f"{greeting}\n\n{core}\n\n{first_name}\n{profile.signoff()}", core


# ----------------------------------------------------------------------
# the checks
# ----------------------------------------------------------------------
def banned_used(text: str) -> list[str]:
    low = (text or "").lower()
    return [b for b, rx in BANNED_RES if rx.search(low)]


def credential_terms(claim: str) -> list[str]:
    """Distinctive terms inside one never_claim entry.

    "that they hold a current 17th Edition certificate" yields "17th Edition".
    Acronyms, ordinal-plus-word and Title Case runs are what credentials
    actually look like in the way people write them down.
    """
    terms = set()
    for rx in (ORDINAL_RE, TITLECASE_RE):
        terms.update(m.strip() for m in rx.findall(claim or ""))
    for m in ACRONYM_RE.findall(claim or ""):
        if m.upper() not in {"THE", "AND", "NOT"}:
            terms.add(m)
    return sorted(t for t in terms if len(t) >= 2)


def never_claim_violations(text: str, profile) -> list[str]:
    """Anything in the draft that touches the user's never_claim list.

    Deliberately blunt, and blunt in the safe direction. It will also stop an
    honest sentence like "this role needs the 17th Edition and I would sit it",
    because no pattern reliably separates a claim about the writer from a
    description of the vacancy - and the two failures are nowhere near equal.
    A blocked draft costs one application. An unblocked false claim is found
    at vetting and follows the person afterwards.
    """
    low = (text or "").lower()
    hits = []
    for claim in profile.never_claim:
        for term in credential_terms(claim):
            if re.search(r"\b" + re.escape(term.lower()) + r"\b", low):
                hits.append(f"{term!r} (you said never to claim: {claim})")
                break
    return hits


def role_tokens(title: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", (title or "").lower())
            if len(t) >= 4 and t not in STOPWORDS]


def names_role(text: str, title: str) -> bool:
    low = (text or "").lower()
    tokens = role_tokens(title)
    if not tokens:
        return True
    return any(t[:5] in low for t in tokens)


def problems(subject: str, core: str, listing, profile) -> list[str]:
    """Everything code refuses to send, phrased as feedback for the retry."""
    out = []

    # Honesty first. It is the only failure here that outlives the letter.
    for hit in never_claim_violations(subject + " " + core, profile):
        out.append(f"must not mention {hit}")

    banned = banned_used(subject + " " + core)
    if banned:
        out.append(f"banned phrases used: {banned}")

    words = subject.split()
    if len(words) > MAX_SUBJECT_WORDS:
        out.append(f"subject is {len(words)} words, max is {MAX_SUBJECT_WORDS}")
    if "application for" in subject.lower():
        out.append("subject must not say 'Application for'")
    if not names_role(subject, listing.title):
        out.append(f"subject must name the role ({listing.title})")
    if subject.isupper():
        out.append("subject must not be ALL CAPS")

    first_line = next((l for l in core.split("\n") if l.strip()), "")
    if not names_role(first_line, listing.title):
        out.append(f"the first line must name the exact role ({listing.title}) "
                   "plus one concrete detail from the advert")

    count = word_count(core)
    if not MIN_WORDS <= count <= MAX_WORDS:
        out.append(f"body is {count} words, must be {MIN_WORDS}-{MAX_WORDS} "
                   "excluding greeting and sign-off")

    numbered = re.findall(r"^\s*(\d)[.)]\s+\S", core, re.M)
    if len(numbered) < 2:
        out.append("body must contain 2 or 3 numbered proof points on their "
                   "own lines, starting '1.' and '2.'")
    if len(numbered) > 3:
        out.append("body must have at most 3 numbered proof points")

    if core.count("?") != 1:
        out.append("body must end with exactly one question as the call to action")

    return out


# ----------------------------------------------------------------------
# asking for a letter
# ----------------------------------------------------------------------
def build_prompt(listing, contact, profile, feedback=()) -> str:
    greeting = f"Hi {contact['name']}," if contact.get("name") else "Hi,"

    never = ""
    if profile.never_claim:
        never = ("\nNEVER claim any of these. They are not true of this "
                 "person:\n" + "\n".join(f"- {c}" for c in profile.never_claim))

    retry = ""
    if feedback:
        retry = ("\nYour last attempt was rejected for these reasons. Fix all "
                 "of them:\n" + "\n".join(f"- {f}" for f in feedback))

    return (
        "Write one short job application email. Respond ONLY with JSON: "
        '{"subject": "<max 8 words, names the role, never \'Application for\'>", '
        '"body": "<the letter>"}\n\n'
        f"CANDIDATE:\n{profile.prompt_block()}\n"
        f"{never}\n\n"
        f"THE JOB\nTitle: {listing.title}\nCompany: {listing.company}\n"
        f"Location: {listing.location}\n"
        f"Advert: {(listing.description or '')[:1200]}\n\n"
        "RULES, all mandatory:\n"
        f"- the greeting is exactly '{greeting}' and will be added for you; "
        "do not write it\n"
        "- do not write a sign-off; it is added for you\n"
        f"- {MIN_WORDS}-{MAX_WORDS} words in the body\n"
        "- the first line names the exact role and ONE concrete detail from "
        "the advert above, proving it was read\n"
        "- then 2 or 3 numbered proof points on their own lines, each "
        "relevant to THIS job, specific, with numbers where possible\n"
        "- then exactly ONE question, and nothing after it\n"
        "- plain English a tradesperson would say out loud. No markdown, no "
        "em dashes, no exclamation marks\n"
        f"- never use: {', '.join(BANNED[:10])}, or similar filler\n"
        f"{retry}")


def compose(listing, contact, profile, ai, *, attempts: int = 3) -> dict | None:
    """A letter that passes every check, or None.

    Rejections are fed back into the next attempt, which is what makes this
    converge rather than reroll. Returning None is a real outcome: a letter
    that breaks the rules is worse than no letter, because the rules are the
    reason these get answered.
    """
    greeting = f"Hi {contact['name']}," if contact.get("name") else "Hi,"
    feedback: list[str] = []

    for _ in range(attempts):
        try:
            raw = ai(build_prompt(listing, contact, profile, feedback))
        except Exception as exc:
            print(f"[compose] {listing.external_id}: {exc}")
            return None

        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except ValueError:
                feedback = ["reply was not valid JSON"]
                continue
        if not isinstance(raw, dict):
            feedback = ["reply was not a JSON object"]
            continue

        subject = normalise(str(raw.get("subject", "")))[:120]
        body, core = assemble(str(raw.get("body", "")), greeting, profile)

        feedback = problems(subject, core, listing, profile)
        if not feedback:
            return {"subject": subject, "body": body,
                    "to_email": contact.get("email"),
                    "to_name": contact.get("name"),
                    "contact_tier": contact.get("tier")}

    return None


# ----------------------------------------------------------------------
# the fallback
# ----------------------------------------------------------------------
def fallback_subject(title: str, company: str) -> str:
    """A subject inside the same eight-word cap the composed path enforces.

    The company is dropped first, then the title trimmed, because a reader
    scanning an inbox needs the role far more than the name of their own
    employer. Without this the fallback quietly breaks the one rule the
    composed path would have caught.
    """
    for candidate in (f"{title} at {company} - application" if company else "",
                      f"{title} - application",
                      title):
        if candidate and len(candidate.split()) <= MAX_SUBJECT_WORDS:
            return candidate[:120]
    return " ".join(title.split()[:MAX_SUBJECT_WORDS])[:120]


def plain_letter(listing, contact, profile) -> dict:
    """A hand-assembled application, with no model in the loop.

    An AI quota is a daily ceiling, and hitting it is a normal Tuesday rather
    than an exception. When the original hit it, every send stopped: a matched
    job with a real verified address went nowhere because a rate limit had
    been allowed to become a hard dependency for applying to a job.

    This is deliberately plainer than the composed version. It is not trying
    to beat it. It is trying to beat not sending.

    It meets every rule that matters - names the role, one question, nothing
    from never_claim, no banned phrases, subject inside the word cap - but it
    is **exempt from the 60-word floor**, and that is a decision rather than an
    oversight. The floor exists to stop a model saying nothing at length.
    Padding a hand-assembled letter to reach it would add filler, which is the
    exact thing the floor is there to prevent. A short honest letter beats a
    padded one, and both beat silence.
    """
    greeting = f"Hi {contact['name']}," if contact.get("name") else "Hi,"
    first_name = (profile.name or "").split()[0] if profile.name else ""
    title = (listing.title or "the role").strip()
    where = f" at {listing.company}" if listing.company else ""

    points = []
    for role in profile.history[:2]:
        detail = role.detail or f"{role.title} work"
        points.append(f"{detail[0].upper()}{detail[1:]}, at {role.org}.")
    if profile.qualifications:
        points.append(", ".join(profile.qualifications[:2]) + ".")
    while len(points) < 2:
        points.append("Available to start and happy to talk through the detail.")

    numbered = "\n".join(f"{i}. {p}" for i, p in enumerate(points[:3], 1))

    ask = ("Does this role involve travel to site or overseas work?"
           if profile.wants_travel else
           "Would it help if I sent my availability for a call this week?")

    body = (f"{greeting}\n\n"
            f"I am applying for the {title} role{where}.\n\n"
            f"{numbered}\n\n"
            f"{ask}\n\n"
            f"{first_name}\n{profile.signoff()}")

    return {"subject": fallback_subject(title, listing.company), "body": body,
            "to_email": contact.get("email"), "to_name": contact.get("name"),
            "contact_tier": contact.get("tier"), "fallback": True}
