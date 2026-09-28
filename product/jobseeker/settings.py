"""Switches read at the moment they are used, not at import.

Every feature added on top of the core machine can be turned off from the
environment. Read here each time rather than once at startup, so the
scheduled sweep picks up a change to a GitHub repository variable on its
next run with no code change, and nothing is cached across a long process
that the operator believes they have switched off.

Lives in jobseeker/ rather than app/config.py because the pipeline runs
without the web app, and app/config.py refuses to import without the web
app's secrets.
"""

from __future__ import annotations

import os


def text(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def flag(name: str, default: bool = True) -> bool:
    raw = text(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def number(name: str, default: int) -> int:
    try:
        return int(text(name))
    except ValueError:
        return default
