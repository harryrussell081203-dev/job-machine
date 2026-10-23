"""What a job is called in an advert, when people call it something else.

People describe the job they want in everyday words: "shop keeper", "carer",
"warehouse worker". UK adverts use their own titles for the same work:
"retail assistant", "care assistant", "warehouse operative". Searching only
for the person's words finds few adverts, and the ones it finds are often
the wrong job. The first person to sign up from ChatGPT (22 October) asked
for "shop keeper" and was shown a herd manager, a welding supervisor and a
solution architect.

So each everyday title also searches for the one or two titles employers
actually advertise it under, and the scorer is told they are the same job.
Kept short and conservative on purpose: an equivalent here is a job a person
asking for the first title would say yes to, not a related field.
"""

from __future__ import annotations

import re

# Everyday title -> titles the same job is advertised under, best first.
ALSO_ADVERTISED_AS = {
    "shop keeper": ("retail assistant", "sales assistant"),
    "shopkeeper": ("retail assistant", "sales assistant"),
    "shop assistant": ("retail assistant", "sales assistant"),
    "shop worker": ("retail assistant", "sales assistant"),
    "retail worker": ("retail assistant", "sales assistant"),
    "cashier": ("checkout assistant", "retail assistant"),
    "till operator": ("checkout assistant", "retail assistant"),
    "carer": ("care assistant", "support worker"),
    "care worker": ("care assistant", "support worker"),
    "warehouse worker": ("warehouse operative",),
    "packer": ("packing operative", "warehouse operative"),
    "cleaner": ("cleaning operative",),
    "cook": ("chef",),
    "barman": ("bar staff",),
    "barmaid": ("bar staff",),
    "bartender": ("bar staff",),
    "waiter": ("waiting staff",),
    "waitress": ("waiting staff",),
    "labourer": ("general operative",),
    "lorry driver": ("hgv driver",),
    "office worker": ("administrator", "admin assistant"),
    "secretary": ("administrator", "admin assistant"),
}

MAX_EXTRA = 2       # extra searches per title, so a profile stays cheap


def _key(role: str) -> str:
    return " ".join(re.sub(r"[^a-z ]+", " ", (role or "").lower()).split())


def equivalents(role: str) -> tuple[str, ...]:
    """The advertised titles for this everyday one; empty when it has none
    or is already the advertised title."""
    return ALSO_ADVERTISED_AS.get(_key(role), ())[:MAX_EXTRA]


def search_terms(roles) -> list[str]:
    """Every title to search for: each of the person's own first, then the
    advertised equivalents, each once whatever its spelling."""
    out, seen = [], set()
    for role in list(roles or []):
        for term in (role, *equivalents(role)):
            key = _key(term)
            if key and key not in seen:
                seen.add(key)
                out.append(term)
    return out


def describe(roles) -> str:
    """The target roles for the scorer, with what each is advertised as, so
    a retail assistant advert counts as the shop keeper job it is."""
    parts = []
    for role in list(roles or []):
        same = equivalents(role)
        parts.append(f"{role} (also advertised as {' or '.join(same)})"
                     if same else role)
    return ", ".join(parts)
