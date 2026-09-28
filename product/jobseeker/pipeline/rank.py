"""Putting the likeliest listings in front of the model first.

Scoring reads at most MAX_SCORED_PER_RUN listings a run, because the free
model allowance is small. Which sixty it reads used to be whichever the job
boards returned first, so on a busy day a run could spend its whole
allowance on warehouse jobs while the one maintenance role sat at number 200
and aged out unread.

So before the model sees anything, every listing is ranked by how close it
is to what this person does. Nothing is thrown away here - a listing ranked
last is only read later, never rejected - because this ranker is a guess
about relevance, and only the model's score (and the pay floor) may say no.

Two rankers, and the better one is optional:

  - **An open-source embedding model** (BAAI's bge-small, via the fastembed
    library, downloaded free from Hugging Face). It knows that a
    "multi-skilled engineer" and a "maintenance technician" are the same
    job, which word matching never will. It runs on the sweep's own CPU in
    a few seconds, costs nothing and sends nothing anywhere. It is installed
    only where the sweep runs: the web server's 512MB would not hold it.
  - **Word overlap** with their target roles and past job titles. Always
    available, and what is used if the model cannot be loaded for any
    reason. A worse ranking is never a reason to stop a run.
"""

from __future__ import annotations

import math
import os
import re

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
# Where the sweep keeps the downloaded model between runs, so Hugging Face is
# asked once rather than six times a day.
CACHE_DIR = os.environ.get("EMBEDDING_CACHE", "")

_WORD = re.compile(r"[a-z]+")
# Words that appear in every advert and say nothing about the job.
_NOISE = {"and", "the", "for", "with", "of", "to", "in", "a", "an", "or",
          "at", "on", "job", "jobs", "role", "roles", "uk", "ltd", "limited",
          "senior", "junior", "lead", "principal", "trainee", "apprentice",
          "i", "ii", "iii", "level", "grade", "new", "immediate", "start",
          "permanent", "temporary", "contract", "full", "part", "time"}

_embedder = None
_embedder_failed = False


def profile_text(profile) -> str:
    """What this person does, in their own words, for comparing against."""
    parts = list(profile.target_roles or [])
    parts += [r.title for r in profile.history or []]
    parts += [r.detail for r in (profile.history or [])[:3] if r.detail]
    parts += list(profile.qualifications or [])[:5]
    return ". ".join(p for p in parts if p)


def listing_text(listing) -> str:
    # The title twice: it is the one line written by somebody who knows what
    # the job is. Descriptions are half boilerplate about the company.
    title = listing.title or ""
    return f"{title}. {title}. {(listing.description or '')[:500]}"


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower())
            if len(w) > 2 and w not in _NOISE}


def word_overlap(profile, listings) -> list[float]:
    """Share of the listing's title words that match what they do, plus a
    little for matching words in the description. 0 to about 1.5."""
    wanted = _words(profile_text(profile))
    out = []
    for l in listings:
        title = _words(l.title)
        body = _words((l.description or "")[:500])
        t = len(title & wanted) / len(title) if title else 0.0
        b = len(body & wanted) / (len(wanted) or 1)
        out.append(t + 0.5 * b)
    return out


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _load_embedder():
    """The fastembed model, or None. Tried once per process."""
    global _embedder, _embedder_failed
    if _embedder is not None or _embedder_failed:
        return _embedder
    try:
        from fastembed import TextEmbedding
        kwargs = {"cache_dir": CACHE_DIR} if CACHE_DIR else {}
        model = TextEmbedding(model_name=EMBEDDING_MODEL, **kwargs)

        def embed(texts):
            return [list(v) for v in model.embed(list(texts))]
        _embedder = embed
    except Exception as exc:
        # Not installed (the web server), no network, a renamed model: all
        # the same answer. Rank by words and carry on.
        print(f"[rank] embedding model unavailable, ranking by words: {exc}")
        _embedder_failed = True
    return _embedder


def similarity(profile, listings, *, embed=None) -> list[float]:
    """How close each listing is to this person, by embedding if possible."""
    if not listings:
        return []
    embed = embed if embed is not None else _load_embedder()
    if embed is not None:
        try:
            vectors = embed([profile_text(profile)]
                            + [listing_text(l) for l in listings])
            me, rest = vectors[0], vectors[1:]
            if len(rest) == len(listings):
                return [_cosine(me, v) for v in rest]
        except Exception as exc:
            print(f"[rank] embedding failed, ranking by words: {exc}")
    return word_overlap(profile, listings)


def rank(listings, profile, *, embed=None) -> list:
    """The same listings, likeliest first. Stable, so equal scores keep the
    boards' own order (newest first)."""
    listings = list(listings)
    if len(listings) < 2 or not profile_text(profile):
        return listings
    scores = similarity(profile, listings, embed=embed)
    order = sorted(range(len(listings)), key=lambda i: -scores[i])
    return [listings[i] for i in order]
