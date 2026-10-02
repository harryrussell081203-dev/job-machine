"""Fill the setup questions in from one sentence the person writes.

"I've driven forklifts at Tesco in Leeds for three years and want warehouse
or driving work, at least £12 an hour" answers four of the six questions.
Somebody on a phone would rather type that once than find four boxes, so a
model reads it and fills the boxes in, and the person checks them and
presses Start looking themselves. Nothing is saved from here.

Two rules the prompt states and the code enforces anyway:

  - **Only what they said.** A box the sentence doesn't answer stays empty.
    A guessed employer or pay floor would end up in a letter.
  - **Never the name or the phone number.** Those are typed by the person,
    always, because a model getting one digit wrong sends every letter with
    the wrong number on it.
"""

from __future__ import annotations

import json
import re

from jobseeker.profile import _not_a_job_title, _not_a_place

MAX_TEXT = 600
FIELDS = ("target_roles", "location", "last_title", "last_org", "min_pay")


def prompt(text: str) -> str:
    return (
        "A UK jobseeker described what they want in their own words. Fill in "
        "the fields below ONLY from what they actually said. If they did not "
        "say something, use an empty string. Never guess an employer, a place "
        "or a pay figure.\n\n"
        "Respond ONLY with JSON:\n"
        '{"target_roles": "<job titles an employer would advertise, comma '
        'separated, e.g. warehouse operative, forklift driver>", '
        '"location": "<the town or city they live in or want to work in>", '
        '"last_title": "<their most recent job title>", '
        '"last_org": "<who that job was with>", '
        '"min_pay": "<the least they would accept, as a number: a yearly '
        'salary like 24000 or an hourly rate like 12.50>"}\n\n'
        f"THEIR WORDS:\n{text}")


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:120]


def read(text: str, ai) -> dict:
    """The boxes it could fill, checked; {} when it could fill none.
    Raises whatever the model raises."""
    text = (text or "").strip()[:MAX_TEXT]
    if not text:
        return {}
    raw = ai(prompt(text))
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return {}
    out = {k: _clean(raw.get(k)) for k in FIELDS}

    roles = [r.strip() for r in out["target_roles"].split(",") if r.strip()]
    out["target_roles"] = ", ".join(r for r in roles if not _not_a_job_title(r))
    if out["location"] and _not_a_place(out["location"]):
        out["location"] = ""
    if out["last_title"] and _not_a_job_title(out["last_title"]):
        out["last_title"] = ""
    pay = re.sub(r"[£,\s]", "", out["min_pay"]).lower()
    thousands = pay.endswith("k")
    pay = pay.rstrip("k")
    if not re.fullmatch(r"\d+(\.\d+)?", pay):
        pay = ""
    elif thousands:
        pay = str(int(float(pay) * 1000))
    out["min_pay"] = pay
    return {k: v for k, v in out.items() if v}
