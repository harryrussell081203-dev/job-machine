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
from jobseeker.gemini import score_call as scorer  # noqa: F401
from jobseeker import gemini as _gemini


class _Ledger:
    """Calls per model per quota day, kept in site_meta so an optional
    GEMINI_DAILY_CAPS holds across the web server and every sweep. Both
    reads and writes swallow failure: a counter that cannot be reached must
    never be the reason a letter was not written."""

    @staticmethod
    def _key(model: str) -> str:
        return f"ai_calls:{_gemini.quota_day()}:{model}"

    def used(self, model: str) -> int:
        from . import db
        try:
            return int(db.get_meta(self._key(model)) or 0)
        except ValueError:
            return 0

    def add(self, model: str) -> None:
        from . import db
        db.set_meta(self._key(model), str(self.used(model) + 1))


_gemini.usage = _Ledger()


def gemini_now(prompt: str, **kwargs) -> str:
    """Gemini, for callers with a person waiting on a page.

    Raises AIError immediately on a rate limit instead of blocking. Callers
    already handle an unscored listing or a missing suggestion; none of them
    handle a page that never loads.
    """
    kwargs.setdefault("budget", 0.0)
    return gemini(prompt, **kwargs)


def scorer_now(prompt: str, **kwargs) -> str:
    """The scoring models, for callers with a person waiting. Same rule."""
    kwargs.setdefault("budget", 0.0)
    return scorer(prompt, **kwargs)


__all__ = ["gemini", "gemini_now", "scorer", "scorer_now", "AIError",
           "QuotaExhausted"]
