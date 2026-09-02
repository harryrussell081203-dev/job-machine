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

MODEL = "gemini-2.5-flash"
ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            f"{MODEL}:generateContent")

# The free tier allows ten calls a minute. Six seconds apart sits exactly on
# that ceiling, so any jitter tips over it; seven leaves a little room. Being
# spaced out here is cheaper than being told off and waiting a minute.
MIN_INTERVAL = 7.0
_last_call = 0.0


class AIError(RuntimeError):
    pass


class QuotaExhausted(AIError):
    """The day's allowance is gone. Callers should fall back, not fail."""


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
           budget: float | None = None) -> str:
    """One call. Returns the model's text, or raises.

    `budget` caps the total seconds this may spend waiting out rate limits.
    None means wait as long as the model asks - right for the scheduled sweep,
    which has nobody watching. Zero means give up the moment a 429 arrives -
    right for anything inside a web request, because the server runs one
    worker and a sleeping request holds up every other user's page.
    """
    global _last_call
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AIError("GEMINI_API_KEY is not set")

    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        sleep(wait)

    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": max_tokens,
                                 # Thinking tokens come out of the same budget
                                 # and buy nothing here.
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    if as_json:
        body["generationConfig"]["responseMimeType"] = "application/json"

    last: Exception | None = None
    waited = 0.0
    for attempt in range(attempts):
        _last_call = time.monotonic()
        try:
            r = httpx.post(ENDPOINT,
                           headers={"x-goog-api-key": api_key},
                           json=body, timeout=90)
        except httpx.HTTPError as exc:
            last = AIError(f"could not reach the model: {exc}")
            sleep(5)
            continue

        if r.status_code == 429:
            # Distinguish "slow down" from "you are done for today". Only the
            # second is worth giving up on.
            if "quota" in r.text.lower() and "per day" in r.text.lower():
                raise QuotaExhausted("daily model quota exhausted")
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

        if r.status_code != 200:
            raise AIError(f"HTTP {r.status_code}: {r.text[:200]}")

        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, ValueError) as exc:
            last = AIError(f"unreadable reply: {exc}")
            continue

    raise last or AIError("the model could not be reached")
