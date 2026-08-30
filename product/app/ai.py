"""The app's model caller. The implementation lives in jobseeker/gemini.py
so the command-line runner can use it without importing the web app."""

from jobseeker.gemini import AIError, QuotaExhausted, call as gemini  # noqa: F401

__all__ = ["gemini", "AIError", "QuotaExhausted"]
