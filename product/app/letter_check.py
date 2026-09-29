"""Does this cover letter sound like it was written by AI?

A free public tool, and deliberately a set of rules rather than a model:
nothing pasted here is sent anywhere, stored, or used for anything, and a
check that runs in a few milliseconds costs nothing to give away.

It does not claim to detect AI. Nothing reliably can, and a tool that said
"87% AI" would be making up a number. What it can do honestly is point at
the things a hiring manager notices - the stock phrases, the sentences all
the same length, the same word four times, American spellings in a letter
to a British employer - and say which ones this letter has. The score is a
count of those, weighted, and says so.

The phrase list starts from the one every Recruited letter is checked
against before it is sent (jobseeker/pipeline/compose.py), so the tool
teaches the same rules the product follows.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from jobseeker.pipeline.compose import BANNED

MAX_CHARS = 6000

# Phrases a model reaches for in a cover letter and a person rarely does, on
# top of the product's own banned list.
STOCK_PHRASES = sorted(set(BANNED) | {
    "I am excited to", "I am confident that", "align with", "aligns with",
    "valuable asset", "unique blend", "in today's", "testament to",
    "commitment to excellence", "eager to contribute", "I believe my skills",
    "tapestry", "navigate the", "foster", "robust", "cutting-edge", "honed",
    "adept at", "invaluable", "resonates", "keen interest", "I would welcome the opportunity",
    "look forward to the opportunity", "make a meaningful", "drive success",
    "wealth of experience", "strong work ethic", "go above and beyond",
    "Dear Hiring Manager", "To whom it may concern", "I am writing to apply",
    "thank you for considering my application", "embark", "elevate",
}, key=str.lower)

# American spellings a British employer notices, with the British form.
# Only words where the US form is simply wrong in British English: "program"
# (software) and "license" (the verb) are correct here and are left alone.
US_TO_UK = {
    "color": "colour", "colors": "colours", "behavior": "behaviour",
    "favorite": "favourite", "labor": "labour", "honor": "honour",
    "neighbor": "neighbour", "endeavor": "endeavour", "center": "centre",
    "centers": "centres", "theater": "theatre", "fiber": "fibre",
    "catalog": "catalogue", "defense": "defence", "offense": "offence",
    "traveled": "travelled", "traveling": "travelling",
    "modeling": "modelling", "labeled": "labelled", "canceled": "cancelled",
    "fulfill": "fulfil", "enroll": "enrol", "jewelry": "jewellery",
    "gray": "grey", "aluminum": "aluminium", "analyze": "analyse",
    "analyzed": "analysed", "organize": "organise", "organized": "organised",
    "organization": "organisation", "realize": "realise",
    "realized": "realised", "recognize": "recognise",
    "recognized": "recognised", "prioritize": "prioritise",
    "specialize": "specialise", "specialized": "specialised",
    "optimize": "optimise", "optimized": "optimised", "utilize": "utilise",
    "apologize": "apologise", "summarize": "summarise",
    "emphasize": "emphasise", "minimize": "minimise",
    "maximize": "maximise", "customize": "customise",
    "standardize": "standardise", "finalize": "finalise",
    "mom": "mum", "resume": "CV",
}

# Words too common to count as "overused".
_COMMON = set("""
a an the and or but if of to in on at by for with from as is are was were be
been being have has had do does did i me my we our you your they them their it
its this that these those there here which who whom what when where why how
not no so than then too very can could will would should may might must shall
am also into about over after before up out all any each more most other some
such only own same just now well work working role job jobs company team
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


@dataclass
class Finding:
    kind: str        # phrase, spelling, rhythm, repeat, dash, length
    title: str
    detail: str
    weight: int


@dataclass
class Result:
    words: int = 0
    score: int = 100
    verdict: str = ""
    findings: list[Finding] = field(default_factory=list)
    # The text in pieces for the page to show, each (text, reason or "").
    marked: list[tuple[str, str]] = field(default_factory=list)


def _spans(text: str) -> list[tuple[int, int, str]]:
    spans = []
    low = text.lower()
    for phrase in STOCK_PHRASES:
        for m in re.finditer(r"\b" + re.escape(phrase.lower()) + r"\b", low):
            spans.append((m.start(), m.end(), "Stock phrase"))
    for m in _WORD.finditer(text):
        uk = US_TO_UK.get(m.group(0).lower())
        if uk:
            spans.append((m.start(), m.end(), f"American spelling - {uk}"))
    for m in re.finditer("[—–]", text):
        spans.append((m.start(), m.end(), "Long dash"))
    # Longest first, then drop anything overlapping a span already taken.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept, end = [], -1
    for start, stop, why in spans:
        if start >= end:
            kept.append((start, stop, why))
            end = stop
    return kept


def _mark(text: str, spans) -> list[tuple[str, str]]:
    out, at = [], 0
    for start, stop, why in spans:
        if start > at:
            out.append((text[at:start], ""))
        out.append((text[start:stop], why))
        at = stop
    if at < len(text):
        out.append((text[at:], ""))
    return out


def check(text: str) -> Result:
    text = (text or "")[:MAX_CHARS]
    result = Result()
    words = _WORD.findall(text)
    result.words = len(words)
    if not words:
        return result

    spans = _spans(text)
    result.marked = _mark(text, spans)

    phrases = sorted({text[a:b].strip() for a, b, why in spans
                      if why == "Stock phrase"}, key=str.lower)
    if phrases:
        result.findings.append(Finding(
            "phrase", f"{len(phrases)} stock phrase"
            f"{'' if len(phrases) == 1 else 's'}",
            "These turn up in letter after letter, most of them written by "
            "AI. A reader skims past them. Say the specific thing instead: "
            + ", ".join(f"“{p}”" for p in phrases[:8]) + ".",
            min(40, 8 * len(phrases))))

    spelt = sorted({text[a:b] for a, b, why in spans
                    if why.startswith("American")}, key=str.lower)
    if spelt:
        result.findings.append(Finding(
            "spelling", "American spelling",
            "A British employer reads these as a letter not written for "
            "them: " + ", ".join(
                f"{w} → {US_TO_UK[w.lower()]}" for w in spelt[:8]) + ".",
            min(15, 5 * len(spelt))))

    dashes = sum(1 for _, _, why in spans if why == "Long dash")
    if dashes >= 2:
        result.findings.append(Finding(
            "dash", f"{dashes} long dashes",
            "Long dashes all through a short letter are one of the most "
            "recognised signs of AI writing. A full stop or a comma does the "
            "same job.", min(10, 3 * dashes)))

    sentences = [s for s in (m.group(0).strip()
                             for m in _SENTENCE.finditer(text))
                 if len(_WORD.findall(s)) >= 3]
    lengths = [len(_WORD.findall(s)) for s in sentences]
    if len(lengths) >= 6:
        mean = statistics.mean(lengths)
        spread = statistics.pstdev(lengths) / mean if mean else 0
        if spread < 0.15:
            result.findings.append(Finding(
                "rhythm", "Every sentence is about the same length",
                f"Your sentences average {mean:.0f} words and barely vary. "
                "People write a short one now and then. Models mostly do not.",
                15))

    counts: dict[str, int] = {}
    for w in words:
        lw = w.lower().strip("'")
        if len(lw) >= 4 and lw not in _COMMON:
            counts[lw] = counts.get(lw, 0) + 1
    limit = 3 if len(words) < 250 else 4
    repeated = sorted((w for w, n in counts.items() if n >= limit),
                      key=lambda w: -counts[w])
    if repeated:
        result.findings.append(Finding(
            "repeat", "Words used over and over",
            ", ".join(f"“{w}” {counts[w]} times"
                      for w in repeated[:5]) + ".",
            min(15, 5 * len(repeated))))

    if len(words) > 250:
        result.findings.append(Finding(
            "length", f"{len(words)} words",
            "Long for a first email to a busy person. Recruited keeps every "
            "letter it sends to 60 to 90 words.",
            5))

    penalty = sum(f.weight for f in result.findings)
    result.score = max(0, 100 - penalty)
    if result.score >= 85:
        result.verdict = "Sounds like a person wrote it"
    elif result.score >= 60:
        result.verdict = "A few things give it away"
    else:
        result.verdict = "Reads like AI wrote it"
    return result
