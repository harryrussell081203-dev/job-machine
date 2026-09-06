"""Judging a listing against one person's search.

The rule that shapes everything here: **the question is not "could they do
this job" but "is this better than what they have".** For somebody already in
work, a perfect trade match at their current salary is worth nothing, and a
scorer that does not know their salary cannot tell the difference.

The original's rubric was written in prose about one man - in work on £30,000,
Aberdeen neutral, penalise chartered roles. All of that is now derived from
the profile, so the same code scores a Sheffield fitter and a Bristol lab
technician correctly without either of them editing a prompt.

Two guards sit in front of the AI, because an AI call costs money and quota:

  - a deterministic pay floor, which rejects only listings that STATE their
    pay and state it too low
  - batching, so one call covers a dozen listings rather than one

The AI itself is injected as a callable. That keeps the network out of the
tests and lets the caller decide about retries, rate limits and which model.
"""

from __future__ import annotations

import json

BATCH_SIZE = 12
DEFAULT_THRESHOLD = 70


def stated_pay(listing) -> tuple[float | None, str | None]:
    """(amount, unit) from a listing's salary fields, or (None, None).

    Units are guessed from the size of the number, because the boards do not
    say which they mean and the same field carries all three. The case that
    makes this necessary is real: £30.81 is an hourly rate, and read as a
    salary it is thirty-one pounds a year - the best-paid thing in the queue,
    thrown out as the worst.
    """
    values = [v for v in (getattr(listing, "salary_max", None),
                          getattr(listing, "salary_min", None))
              if isinstance(v, (int, float)) and v > 0]
    if not values:
        return None, None

    # The top of an advertised range, so a listing is only rejected when even
    # its best case is too little.
    amount = float(max(values))

    # 100 rather than 200, because the hour/day band genuinely overlaps and
    # the two readings are not equally likely. A day rate of £120 is common
    # and poor; an hourly rate of £120 would be a quarter of a million a year
    # and does not exist in these trades. Guessing "hour" up there would pass
    # every bad day rate in the market.
    if amount < 100:
        return amount, "hour"
    if amount <= 2000:
        return amount, "day"
    return amount, "year"


def pays_enough(listing, profile) -> bool:
    """False only when a listing STATES its pay and that pay is too low.

    Silence has to pass. Most adverts never print a figure, the whole contract
    market quotes on application, and a filter treating "unstated" as "too
    little" would delete the best-paid half of the market to save a few AI
    calls.
    """
    amount, unit = stated_pay(listing)
    if amount is None:
        return True

    hourly = profile.min_rate_hourly
    annual = profile.min_salary_annual
    if unit == "hour":
        return amount >= hourly if hourly else True
    if unit == "day":
        return amount >= hourly * 8 if hourly else True
    return amount >= annual if annual else True


def score_guide(profile) -> str:
    """The rubric, written from this person's actual position."""
    lines = []

    if profile.situation == "employed" and profile.current_salary:
        earn = f"GBP {profile.current_salary:,}"
        lines.append(
            f"They are IN WORK on {earn} a year, so the question is not "
            f"'could they do this' but 'is this better than what they have'.")
        top = (f"85+  clearly better paid than {earn}")
    else:
        lines.append(
            "They are looking for work, so a solid match in their trade at or "
            "above their floor is a good result.")
        top = "85+  a strong match in their trade, at or above their floor"

    wants = []
    if profile.wants_travel:
        wants.append("states overseas travel, field service abroad, client "
                     "sites in other countries, or a rotational pattern")
    if profile.wants_contract:
        wants.append("is contract, day rate, or a shift pattern they said "
                     "they want")
    if wants:
        top += ", AND/OR " + ", or ".join(wants)
    lines.append(top + ".")

    lines.append(
        "Read pay in whatever unit the advert uses - GBP 30 an hour is roughly "
        "GBP 60,000 a year, not GBP 30. Day rates and shift allowances count.")
    lines.append("70-84 a clear step up in pay or responsibility in their trade.")
    lines.append("40-69 partial match, or a step up they could not obviously make.")

    floor = []
    if profile.min_salary_annual:
        floor.append(f"GBP {profile.min_salary_annual:,} a year")
    if profile.min_rate_hourly:
        floor.append(f"GBP {profile.min_rate_hourly} an hour")
    lines.append(
        "<40  pays below " + (" or ".join(floor) if floor else "their floor")
        + " however well the trade fits, or is the wrong field entirely.")

    # Their own patch is neutral, not a bonus. They already live there; a
    # local job is not better for being local, and rewarding it quietly
    # buries the better-paid one an hour away.
    if profile.locations:
        where = ", ".join(profile.locations)
        lines.append(
            f"{where} are NEUTRAL - not a bonus and not a penalty. They live "
            "and work there already. Do not reward a listing for being local.")

    if profile.exclude_titles:
        lines.append("They have ruled out: " + ", ".join(profile.exclude_titles) + ".")

    lines.append(
        "Do NOT penalise senior, lead or principal titles in their trade - "
        "those are a step up, which is the point.")

    return "\n".join(lines)


def build_prompt(batch, profile) -> str:
    listings = "\n\n".join(
        f"--- LISTING {i} ---\n"
        f"Title: {l.title}\n"
        f"Company: {l.company}\n"
        f"Location: {l.location}\n"
        f"Salary: {l.salary_min}-{l.salary_max}\n"
        f"Description: {(l.description or '')[:900]}"
        for i, l in enumerate(batch))

    return (
        f"Screen these {len(batch)} job listings for the candidate. Respond "
        "ONLY with a JSON array, one object per listing, in the same order: "
        '[{"listing": <index>, "score": <0-100>, "reason": "<one short sentence>"}]\n\n'
        f"CANDIDATE:\n{profile.prompt_block()}\n\n"
        f"SCORE GUIDE.\n{score_guide(profile)}\n\n"
        f"{listings}")


def parse_scores(raw, batch_size: int) -> dict:
    """{index: (score, reason)} from whatever the model actually returned.

    Models return a bare array, an object wrapping one, or a lone object.
    Anything unparseable is dropped rather than guessed at: a listing with no
    score stays unscored and gets another go, which is cheaper than acting on
    a number nobody produced.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}

    if isinstance(raw, dict):
        raw = raw.get("results") or raw.get("listings") or [raw]
    if not isinstance(raw, list):
        return {}

    out = {}
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        index = entry.get("listing", entry.get("index", i))
        try:
            index = int(index)
            score = max(0, min(100, int(float(entry.get("score", 0)))))
        except (TypeError, ValueError):
            continue
        if 0 <= index < batch_size:
            out[index] = (score, str(entry.get("reason", ""))[:300])
    return out


def score(listings, profile, ai, *, threshold: int = DEFAULT_THRESHOLD,
          batch_size: int = BATCH_SIZE) -> dict:
    """Score every listing. Returns {"passed": [...], "rejected": [...]}.

    `ai` is any callable taking a prompt and returning the model's text. Kept
    injectable so tests never touch the network and the caller owns retries
    and rate limiting.

    Each returned listing carries `.score` and `.score_reason`, so a user can
    always be told why something did or did not reach them.
    """
    passed, rejected = [], []
    to_score = []

    for listing in listings:
        # The deterministic floor runs first: it costs nothing, and there is
        # no sense paying a model to read an advert that states a wage below
        # what this person already earns.
        if not pays_enough(listing, profile):
            amount, unit = stated_pay(listing)
            listing.skipped = f"states {amount:g} per {unit}, below your floor"
            listing.score = 0
            listing.score_reason = listing.skipped
            rejected.append(listing)
        else:
            to_score.append(listing)

    for start in range(0, len(to_score), batch_size):
        batch = to_score[start:start + batch_size]
        try:
            scores = parse_scores(ai(build_prompt(batch, profile)), len(batch))
        except Exception as exc:
            # A failed batch is not a rejection. These stay unscored so the
            # next run picks them up; silently binning them would lose good
            # jobs to a network blip.
            print(f"[scoring] batch failed, leaving unscored: {exc}")
            continue

        for i, listing in enumerate(batch):
            if i not in scores:
                continue
            value, reason = scores[i]
            listing.score = value
            listing.score_reason = reason
            if value >= threshold:
                passed.append(listing)
            else:
                listing.skipped = f"scored {value}: {reason}"
                rejected.append(listing)

    passed.sort(key=lambda l: l.score, reverse=True)
    return {"passed": passed, "rejected": rejected}
