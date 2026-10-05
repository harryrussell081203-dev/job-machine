"""Write the letter on /find, with no account.

The product asked for a sign-in, six answers, a pay floor and then a mailbox
before it did anything for anybody, and nine of the first ten people to sign
up stopped before the first answer. So the first useful thing now happens on
the free page: it finds the address, and then, if the person wants, writes
the 60 to 90 word letter to it from two lines about themselves. They send it
from their own email with one tap. No account, nothing kept.

The rules are the product's rules:

  - **Only what they said.** The proof points come from what the person
    typed and nowhere else. A letter that claims a ticket they don't hold
    gets found out at the interview.
  - **The house style.** 60 to 90 words, one detail from the advert if there
    is one, one question, no banned phrases, no exclamation marks. The same
    banned list every letter the product sends is held to.
  - **Rationed.** A model call costs quota the paying users need, so it is
    limited per visitor and for the whole site.

Switch: FIND_LETTER_ENABLED (default on).
"""

from __future__ import annotations

import json
import re

from jobseeker import settings
from jobseeker.pipeline import compose

MAX_ABOUT = 600
MAX_ADVERT = 2500
WORDS = (50, 110)        # the 60-90 target, with room for the model's count


def enabled() -> bool:
    return settings.flag("FIND_LETTER_ENABLED")


def prompt(*, job: str, company: str, about: str, advert: str,
           feedback: list[str] | None = None) -> str:
    retry = ""
    if feedback:
        retry = ("\nYour last attempt was rejected. Fix all of these:\n"
                 + "\n".join(f"- {f}" for f in feedback))
    return (
        "Write one short job application email from a UK jobseeker. Respond "
        'ONLY with JSON: {"subject": "<the job title, at most 8 words, never '
        '\'Application for\'>", "body": "<the letter>"}\n\n'
        f"THE JOB: {job or 'not given; infer it from the advert if there is one'}\n"
        f"THE COMPANY: {company or 'not given'}\n"
        f"THE ADVERT (may be empty): {advert[:MAX_ADVERT]}\n\n"
        f"WHAT THE PERSON HAS DONE, IN THEIR OWN WORDS:\n{about}\n\n"
        "RULES, all mandatory:\n"
        "- 60 to 90 words in the body\n"
        "- no greeting and no sign-off; both are added for you\n"
        "- the first line names the job. If the advert gives a concrete "
        "detail only this employer would write (a site, a team, a piece of "
        "kit), mention one. Never quote pay, hours or shift patterns back\n"
        "- then two short proof points taken ONLY from what the person wrote "
        "above. Never add a qualification, number, employer or skill they "
        "did not mention\n"
        "- then exactly one question, and nothing after it\n"
        "- plain English, the way a person would say it out loud. No "
        "markdown, no em dashes, no exclamation marks\n"
        f"- never use: {', '.join(compose.BANNED[:12])}, or similar filler\n"
        f"{retry}")


def problems(subject: str, body: str) -> list[str]:
    found = []
    words = len(body.split())
    if not WORDS[0] <= words <= WORDS[1]:
        found.append(f"the body is {words} words; it must be 60 to 90")
    banned = compose.banned_used(f"{subject} {body}")
    if banned:
        found.append(f"uses banned phrases: {', '.join(banned)}")
    if "!" in body:
        found.append("has an exclamation mark")
    if "—" in body or "–" in body:
        found.append("has a long dash")
    if body.count("?") != 1:
        found.append("must ask exactly one question")
    if not subject.strip():
        found.append("has no subject")
    return found


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", compose.normalise(text or "")).strip()


def write(*, job: str, company: str, about: str, advert: str = "",
          greet: str = "", name: str = "", phone: str = "", ai,
          attempts: int = 2) -> dict | None:
    """{"subject", "body"} ready to send, or None if no draft passed."""
    about = (about or "").strip()[:MAX_ABOUT]
    if not about:
        return None
    feedback: list[str] = []
    for _ in range(attempts):
        raw = ai(prompt(job=job.strip()[:80], company=company.strip()[:80],
                        about=about, advert=advert or "", feedback=feedback))
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                feedback = ["the reply was not valid JSON"]
                continue
        if not isinstance(raw, dict):
            feedback = ["the reply was not a JSON object"]
            continue
        subject = _clean(str(raw.get("subject", "")))[:120]
        core = _clean(str(raw.get("body", "")))
        feedback = problems(subject, core)
        if feedback:
            continue
        greeting = f"Hi {greet.strip().title()}," if greet.strip() else "Hi,"
        signoff = "\n".join(x for x in (name.strip()[:60], phone.strip()[:30])
                            if x)
        body = f"{greeting}\n\n{core}\n\n{signoff}".rstrip()
        return {"subject": subject, "body": body}
    return None
