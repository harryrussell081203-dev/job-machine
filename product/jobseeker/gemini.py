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

import os
import time

import httpx

# The model that writes letters. The best of the free ones, kept for the job
# a person reads.
MODEL = "gemini-2.5-flash"

# The models that score listings, tried in order. Scoring is a yes/no with a
# reason, not prose, and it is what used to run the day's allowance out
# before a single letter was written. Each model has its OWN free daily
# allowance, so moving scoring off the letter model gives letters theirs
# back. Gemma is Google's open-weights model, served free on the same key;
# the letter model comes last so a renamed model never stops scoring
# altogether. Overridable because Google renames these every few months.
SCORING_MODELS = tuple(
    m.strip() for m in os.environ.get(
        "GEMINI_SCORING_MODELS",
        f"gemini-2.5-flash-lite,gemma-3-27b-it,{MODEL}").split(",")
    if m.strip())

BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
ENDPOINT = f"{BASE}{MODEL}:generateContent"

# The free tier allows ten calls a minute. Six seconds apart sits exactly on
# that ceiling, so any jitter tips over it; seven leaves a little room. Being
# spaced out here is cheaper than being told off and waiting a minute.
# Counted per model, because each model's limit is its own.
MIN_INTERVAL = 7.0
_last_call: dict[str, float] = {}

# Models Google has said are done for today. Every later call to one in this
# process fails at once instead of spending the rest of the run - and more of
# tomorrow's goodwill - asking again. On 25 September one sweep spent 47
# minutes waiting out 429s on a quota that was simply used up.
_spent_for_today: set[str] = set()


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
           budget: float | None = None, model: str = MODEL) -> str:
    """One call. Returns the model's text, or raises.

    `budget` caps the total seconds this may spend waiting out rate limits.
    None means wait as long as the model asks - right for the scheduled sweep,
    which has nobody watching. Zero means give up the moment a 429 arrives -
    right for anything inside a web request, because the server runs one
    worker and a sleeping request holds up every other user's page.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AIError("GEMINI_API_KEY is not set")
    if model in _spent_for_today:
        raise QuotaExhausted(f"daily quota for {model} exhausted")

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
                _spent_for_today.add(model)
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
            return _unfence(text) if as_json else text
        except (KeyError, IndexError, ValueError) as exc:
            last = AIError(f"unreadable reply: {exc}")
            continue

    raise last or AIError("the model could not be reached")


def call_first(prompt: str, *, models=SCORING_MODELS, **kwargs) -> str:
    """The first of `models` that answers. A model that is spent for the day
    or no longer served is skipped; anything else is a real failure and is
    raised as it is, so the caller's own give-up rules still apply."""
    for model in models:
        try:
            return call(prompt, model=model, **kwargs)
        except (QuotaExhausted, ModelUnavailable) as exc:
            print(f"[ai] {exc}; trying the next model")
    raise QuotaExhausted("every scoring model is spent for today")


def score_call(prompt: str, **kwargs) -> str:
    """The caller for scoring listings: the scoring models, in order."""
    return call_first(prompt, **kwargs)
