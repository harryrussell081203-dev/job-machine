"""The app's model caller. The implementation lives in jobseeker/gemini.py
so the command-line runner can use it without importing the web app.

Two flavours, and which one you pick matters more than it looks. The server
runs a single worker, so a request that sleeps out a rate limit holds up every
other user's page for as long as it sleeps. Anything reached from a web
request must therefore use `gemini_now`, which treats a 429 as an answer
rather than something to wait out. The scheduled sweep has nobody watching and
uses the patient one.
"""

from jobseeker.gemini import AIError, QuotaExhausted, call as gemini  # noqa: F401


def gemini_now(prompt: str, **kwargs) -> str:
    """Gemini, for callers with a person waiting on a page.

    Raises AIError immediately on a rate limit instead of blocking. Callers
    already handle an unscored listing or a missing suggestion; none of them
    handle a page that never loads.
    """
    kwargs.setdefault("budget", 0.0)
    return gemini(prompt, **kwargs)


__all__ = ["gemini", "gemini_now", "AIError", "QuotaExhausted"]
