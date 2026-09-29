"""The model call, with the awkward realities of a shared quota built in.

Lives here rather than in the app so the CLI can use it without importing a
web framework. The key comes from the environment, which is the one place both
the app and a GitHub Actions run agree on.

Everything upstream takes `ai` as a plain callable - prompt in, text out - so
the pipeline is testable offline and the choice of model is not baked into it.
This is the one implementation that actually reaches the network.

Two things it handles that a naive caller does not:

  - **rate limits are normal, not exceptional.** A free tier answers 429 as a
    matter of course, and the right response is to wait the interval it asks
    for rather than to fail the run.
  - **the quota does run out.** When it does, `QuotaExhausted` is raised so the
    caller can fall back to a hand-assembled letter instead of sending nothing.
"""

from __future__ import annotations

import datetime as dt
import os
import time
from zoneinfo import ZoneInfo

import httpx

from . import settings

# Which model does which job. Read when used, not at import, so a renamed
# model is fixed by changing a repository variable rather than the code.
#
# GEMINI_LETTER_MODEL writes letters: the best of the free ones, kept for the
# job a person reads.
#
# GEMINI_SCORING_MODELS score listings, tried in order. Scoring is a number
# and a reason, not prose, and it is what used to run the day's allowance out
# before a single letter was written. Each model has its OWN free daily
# allowance, so moving scoring off the letter model gives letters theirs
# back. Gemma is Google's open-weights model, served free on the same key;
# the letter model comes last so a renamed model never stops scoring.
DEFAULT_LETTER_MODEL = "gemini-2.5-flash"
DEFAULT_SCORING_MODELS = "gemini-2.5-flash-lite,gemma-3-27b-it"


def letter_model() -> str:
    return settings.text("GEMINI_LETTER_MODEL", DEFAULT_LETTER_MODEL)


def scoring_models() -> tuple[str, ...]:
    listed = [m.strip() for m in settings.text(
        "GEMINI_SCORING_MODELS", DEFAULT_SCORING_MODELS).split(",")
        if m.strip()]
    if letter_model() not in listed:
        listed.append(letter_model())
    return tuple(listed)


def daily_caps() -> dict[str, int]:
    """GEMINI_DAILY_CAPS, e.g. "gemini-2.5-flash-lite=900,gemma-3-27b-it=5000".

    Optional, and empty by default. No free limit is written into this code,
    because Google publishes different numbers every few months and the
    authoritative answer is the 429 it sends when one is reached, which is
    handled below either way. A cap here is for stopping short of that on
    purpose - leaving room for the day's interactive users, say."""
    caps = {}
    for part in settings.text("GEMINI_DAILY_CAPS").split(","):
        name, _, value = part.partition("=")
        try:
            caps[name.strip()] = int(value)
        except ValueError:
            continue
    return caps


# Where the calls made today are counted, so a cap holds across the several
# processes a day runs. None counts nothing. The app plugs in its database
# (app/ai.py); a bare command-line run has no caps to keep.
#   usage.used(model) -> int     usage.add(model) -> None
usage = None


BASE = "https://generativelanguage.googleapis.com/v1beta/models/"

# The free tier allows ten calls a minute. Six seconds apart sits exactly on
# that ceiling, so any jitter tips over it; seven leaves a little room. Being
# spaced out here is cheaper than being told off and waiting a minute.
# Counted per model, because each model's limit is its own.
MIN_INTERVAL = 7.0
_last_call: dict[str, float] = {}

# Models Google has said are done for today, with the day they were spent on.
# Every later call to one fails at once instead of spending the rest of the
# run - and more of tomorrow's goodwill - asking again. On 25 September one
# sweep spent 47 minutes waiting out 429s on a quota that was simply used up.
#
# Keyed by day because the web server is a process that outlives a day: a
# plain "spent" flag set on Monday would still be refusing on Wednesday.
_spent_for_today: dict[str, str] = {}

# Google resets the free allowance at midnight Pacific time.
_QUOTA_ZONE = ZoneInfo("America/Los_Angeles")


def quota_day(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(_QUOTA_ZONE).date().isoformat()


def _spent(model: str) -> bool:
    return _spent_for_today.get(model) == quota_day()


def _mark_spent(model: str) -> None:
    _spent_for_today[model] = quota_day()


def _is_daily_limit(text: str) -> bool:
    """Google names the limit that was hit, e.g.
    GenerateRequestsPerDayPerProjectPerModel-FreeTier. The old check looked
    for "per day" with a space and never matched it."""
    flat = (text or "").lower().replace(" ", "").replace("_", "").replace("-", "")
    return "perday" in flat


class AIError(RuntimeError):
    pass


class QuotaExhausted(AIError):
    """The day's allowance is gone. Callers should fall back, not fail."""


class ModelUnavailable(AIError):
    """The model name is not served (renamed or retired). Try the next one."""


def _open_weights(model: str) -> bool:
    # Gemma takes neither a thinking budget nor a JSON response type, and
    # refuses the whole request if either is sent.
    return model.startswith("gemma")


def _unfence(text: str) -> str:
    """A model without a JSON mode wraps its JSON in a markdown fence."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _retry_after(response, fallback: float) -> float:
    try:
        detail = response.json().get("error", {})
        for item in detail.get("details", []):
            delay = item.get("retryDelay")
            if delay and delay.endswith("s"):
                return min(float(delay[:-1]), 90.0)
    except Exception:
        pass
    return fallback


def call(prompt: str, *, max_tokens: int = 900, temperature: float = 0.4,
           as_json: bool = True, attempts: int = 3, sleep=time.sleep,
           budget: float | None = None, model: str | None = None) -> str:
    """One call. Returns the model's text, or raises.

    `budget` caps the total seconds this may spend waiting out rate limits.
    None means wait as long as the model asks - right for the scheduled sweep,
    which has nobody watching. Zero means give up the moment a 429 arrives -
    right for anything inside a web request, because the server runs one
    worker and a sleeping request holds up every other user's page.
    """
    model = model or letter_model()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AIError("GEMINI_API_KEY is not set")
    if _spent(model):
        raise QuotaExhausted(f"daily quota for {model} exhausted")
    cap = daily_caps().get(model)
    if cap is not None and usage is not None:
        try:
            over = usage.used(model) >= cap
        except Exception:
            over = False          # a counter that cannot be read caps nothing
        if over:
            _mark_spent(model)
            raise QuotaExhausted(f"{model} reached its cap of {cap} today")

    wait = MIN_INTERVAL - (time.monotonic() - _last_call.get(model, 0.0))
    if wait > 0:
        sleep(wait)

    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": max_tokens}}
    if not _open_weights(model):
        # Thinking tokens come out of the same budget and buy nothing here.
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        if as_json:
            body["generationConfig"]["responseMimeType"] = "application/json"

    last: Exception | None = None
    waited = 0.0
    for attempt in range(attempts):
        _last_call[model] = time.monotonic()
        try:
            r = httpx.post(f"{BASE}{model}:generateContent",
                           headers={"x-goog-api-key": api_key},
                           json=body, timeout=90)
        except httpx.HTTPError as exc:
            last = AIError(f"could not reach the model: {exc}")
            sleep(5)
            continue

        if r.status_code == 429:
            # Distinguish "slow down" from "you are done for today". Only the
            # second is worth giving up on.
            if _is_daily_limit(r.text):
                _mark_spent(model)
                raise QuotaExhausted(f"daily quota for {model} exhausted")
            delay = _retry_after(r, 15 * (attempt + 1))
            if budget is not None and waited + delay > budget:
                # Somebody is waiting on a page. Unscored is a better answer
                # than a minute of blank screen, and the caller treats it so.
                print(f"[ai] rate limited, giving up (budget {budget:.0f}s)")
                raise AIError("rate limited")
            print(f"[ai] rate limited, waiting {delay:.0f}s")
            sleep(delay)
            waited += delay
            last = AIError("rate limited")
            continue

        if r.status_code in (500, 502, 503, 504):
            sleep(5 * (attempt + 1))
            last = AIError(f"HTTP {r.status_code}")
            continue

        if r.status_code == 404:
            raise ModelUnavailable(f"{model} is not served: {r.text[:120]}")
        if r.status_code != 200:
            raise AIError(f"HTTP {r.status_code}: {r.text[:200]}")

        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, ValueError) as exc:
            last = AIError(f"unreadable reply: {exc}")
            continue
        # Which provider answered, every time, so a log shows where the
        # day's allowance went.
        print(f"[ai] answered by {model}")
        if usage is not None:
            try:
                usage.add(model)
            except Exception:
                pass
        return _unfence(text) if as_json else text

    raise last or AIError("the model could not be reached")


def call_first(prompt: str, *, models=None, **kwargs) -> str:
    """The first of `models` that answers. A model that is spent for the day
    or no longer served is skipped; anything else is a real failure and is
    raised as it is, so the caller's own give-up rules still apply."""
    for model in models or scoring_models():
        try:
            return call(prompt, model=model, **kwargs)
        except (QuotaExhausted, ModelUnavailable) as exc:
            print(f"[ai] {exc}; trying the next model")
    raise QuotaExhausted("every scoring model is spent for today")


def score_call(prompt: str, **kwargs) -> str:
    """The caller for scoring listings: the scoring models, in order."""
    return call_first(prompt, **kwargs)
