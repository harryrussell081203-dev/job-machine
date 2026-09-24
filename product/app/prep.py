"""Getting ready for the conversation a letter earned.

The interview helper is the part of AI Apply its users single out: likely
questions for the role, with an answer shaped in the STAR way. It is also the
part most likely to get somebody into trouble, because a model asked for a
strong answer will happily invent one - a project that never happened, a
ticket never held - and the candidate then has to defend it across a table.

So the rules here are the same ones every letter follows:

  - **Only their own history.** Every outline has to name which job or
    qualification on their profile it draws on. If nothing fits, it says so
    and suggests how to answer honestly, rather than filling the gap.
  - **Never what they must not claim.** The profile's never_claim list goes
    into the prompt word for word, and anything that uses those words anyway
    is thrown out rather than shown.
  - **Built once, then kept.** It is made when the user asks and stored on
    the application, so opening it again costs nothing and does not change.
"""

from __future__ import annotations

import json
import re

QUESTIONS = 6
ASK_THEM = 3


def build_prompt(draft, profile: dict) -> str:
    history = "\n".join(
        f"- {h.get('title', '')} at {h.get('org', '')}: {h.get('detail', '')}"
        for h in profile.get("history") or [])
    quals = "\n".join(f"- {q}" for q in profile.get("qualifications") or [])
    never = "\n".join(f"- {n}" for n in profile.get("never_claim") or [])
    return f"""You are helping a candidate in the UK prepare for a job interview.

THE ROLE
Title: {draft['job_title']}
Employer: {draft['company']}
Location: {draft['location'] or 'not stated'}
Why it matched them: {draft['score_reason'] or 'not recorded'}

THE LETTER THEY SENT
{draft['body']}

THEIR REAL HISTORY - the only facts you may use
{history or '- none given'}

QUALIFICATIONS
{quals or '- none given'}

NEVER CLAIM, SUGGEST OR IMPLY ANY OF THESE
{never or '- nothing listed'}

Write the {QUESTIONS} questions this employer is most likely to ask for this
role, and for each an answer outline in the STAR shape (situation, task,
action, result), in plain UK English, as short notes rather than a script.

Rules:
- Every outline must come from ONE item in their real history or
  qualifications, and must name it in "draws_on" exactly as written above.
- Never invent a project, number, employer, certificate or result. If their
  history has nothing that fits a question, set "draws_on" to "" and make the
  outline an honest way to say so and show how they would approach it.
- Include at least one question about why they want this role, and one about
  a time something went wrong.

Then {ASK_THEM} good questions for the candidate to ask them.

Reply with JSON only:
{{"questions": [{{"q": "...", "why_they_ask": "...", "draws_on": "...",
  "outline": "S: ... T: ... A: ... R: ..."}}], "ask_them": ["..."]}}"""


def _allowed_sources(profile: dict) -> list[str]:
    out = []
    for h in profile.get("history") or []:
        out += [str(h.get("title") or ""), str(h.get("org") or "")]
    out += [str(q) for q in profile.get("qualifications") or []]
    return [s.strip().lower() for s in out if s and s.strip()]


def _forbidden(profile: dict) -> list[str]:
    """Distinctive words from each never_claim line. A whole sentence would
    never match, so the checkable part is its first few words."""
    words = []
    for line in profile.get("never_claim") or []:
        head = re.split(r"[.;:(]", str(line))[0].strip().lower()
        if len(head) >= 6:
            words.append(" ".join(head.split()[:4]))
    return words


def parse(raw: str, profile: dict) -> dict | None:
    """The model's answer, cleaned, or None if there is nothing usable."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", str(raw or ""), re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None

    sources = _allowed_sources(profile)
    forbidden = _forbidden(profile)
    questions = []
    for item in data.get("questions") or []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or "").strip()
        outline = str(item.get("outline") or "").strip()
        draws = str(item.get("draws_on") or "").strip()
        if not q or not outline:
            continue
        text = f"{q} {outline}".lower()
        if any(f in text for f in forbidden):
            continue
        # An outline that claims a source they do not have is an invented
        # history, which is the one thing this must never hand them.
        if draws and not any(s and (s in draws.lower() or draws.lower() in s)
                             for s in sources):
            continue
        questions.append({"q": q,
                          "why": str(item.get("why_they_ask") or "").strip(),
                          "draws_on": draws, "outline": outline})
    ask = [str(a).strip() for a in data.get("ask_them") or [] if str(a).strip()]
    if not questions:
        return None
    return {"questions": questions[:QUESTIONS], "ask_them": ask[:ASK_THEM]}


def prepare(draft, profile: dict, ai) -> dict | None:
    raw = ai(build_prompt(draft, profile))
    return parse(raw, profile)
