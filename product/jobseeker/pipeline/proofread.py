"""A spelling pass over every letter before it is queued, in British English.

LanguageTool is open source. The sweep runs its own copy in a container
next to the job, so letters are checked without leaving the runner and
without the public service's rate limit. The web server has no room for
Java, so a letter drafted there while somebody watches is not checked
unless LANGUAGETOOL_URL points somewhere that can.

What it may change, and it is deliberately little:

  - **Spelling only.** A misspelt word with exactly ONE suggestion, which
    is the case where the fix is not a judgement call. "recieve" becomes
    "receive"; "color" becomes "colour".
  - **Never a word with a capital or a digit in it.** Those are names -
    the employer, the person, a place, a ticket like "17th Edition" - and a
    dictionary that does not know them is not a reason to change them.
  - **Never grammar or style.** Those suggestions are logged for a person
    to read, never applied. A rewrite by rule is how a letter that was
    checked for honesty (compose.py) stops being the letter that was
    checked.

Anything that goes wrong - no server, a timeout, an answer it cannot read -
returns the letter unchanged. A proofreader is never a reason a letter is
not written.

Switch: LANGUAGETOOL_URL (empty means off).
"""

from __future__ import annotations

import re

import httpx

from .. import settings

LANGUAGE = "en-GB"
TIMEOUT = 10.0
_PLAIN_WORD = re.compile(r"^[a-z]+(?:'[a-z]+)?$")


def server() -> str:
    return settings.text("LANGUAGETOOL_URL").rstrip("/")


def matches(text: str, *, post=None) -> list[dict]:
    """LanguageTool's findings for `text`, or [] if it cannot be asked."""
    url = server()
    if not url or not (text or "").strip():
        return []
    post = post or httpx.post
    try:
        r = post(f"{url}/v2/check",
                 data={"language": LANGUAGE, "text": text}, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[proofread] LanguageTool answered {r.status_code}; "
                  "letter left as written")
            return []
        found = r.json().get("matches") or []
        return [m for m in found if isinstance(m, dict)]
    except Exception as exc:
        print(f"[proofread] LanguageTool unavailable ({exc}); "
              "letter left as written")
        return []


def _safe_fix(match: dict, text: str) -> str | None:
    """The replacement, if this match is one that may be applied."""
    rule = match.get("rule") or {}
    if rule.get("issueType") != "misspelling":
        return None
    suggestions = [s.get("value") for s in match.get("replacements") or []
                   if isinstance(s, dict) and s.get("value")]
    if len(suggestions) != 1:
        return None
    try:
        start, length = int(match["offset"]), int(match["length"])
    except (KeyError, TypeError, ValueError):
        return None
    original = text[start:start + length]
    replacement = suggestions[0]
    if not _PLAIN_WORD.match(original) or not _PLAIN_WORD.match(replacement):
        return None
    return replacement


def proofread(text: str, *, post=None) -> tuple[str, list[str]]:
    """(corrected text, notes). Notes list what changed and, separately,
    what was only suggested."""
    found = matches(text, post=post)
    if not found:
        return text, []
    notes, fixes = [], []
    for m in found:
        fix = _safe_fix(m, text)
        start, length = m.get("offset"), m.get("length")
        if fix is not None:
            fixes.append((int(start), int(length), fix))
            continue
        try:
            shown = text[int(start):int(start) + int(length)]
        except (TypeError, ValueError):
            shown = ""
        notes.append(f"suggestion ({(m.get('rule') or {}).get('id', '?')}): "
                     f"'{shown}' - {m.get('message', '')}")
    # Right to left, so earlier offsets stay true as the text changes.
    out = text
    for start, length, fix in sorted(fixes, reverse=True):
        notes.append(f"fixed: '{out[start:start + length]}' -> '{fix}'")
        out = out[:start] + fix + out[start + length:]
    return out, notes


def letter(letter: dict, *, post=None) -> dict:
    """The same letter with safe spelling fixes applied to subject and body.
    Everything found is printed for the run's log."""
    if not server():
        return letter
    out = dict(letter)
    for field in ("subject", "body"):
        fixed, notes = proofread(out.get(field) or "", post=post)
        out[field] = fixed
        for note in notes:
            print(f"[proofread] {field}: {note}")
    return out
