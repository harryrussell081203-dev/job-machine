"""The model call, with the awkward realities of a shared quota built in.

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

import time

import httpx

from . import config

MODEL = "gemini-2.5-flash"
ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            f"{MODEL}:generateContent")

# Roughly ten calls a minute on the free tier. Spacing them here is cheaper
# than being told off and waiting longer.
MIN_INTERVAL = 6.0
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


def gemini(prompt: str, *, max_tokens: int = 900, temperature: float = 0.4,
           as_json: bool = True, attempts: int = 3, sleep=time.sleep) -> str:
    """One call. Returns the model's text, or raises."""
    global _last_call
    if not config.GEMINI_API_KEY:
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
    for attempt in range(attempts):
        _last_call = time.monotonic()
        try:
            r = httpx.post(ENDPOINT,
                           headers={"x-goog-api-key": config.GEMINI_API_KEY},
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
            print(f"[ai] rate limited, waiting {delay:.0f}s")
            sleep(delay)
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
