"""What to search the job boards for, worked out from the person.

People describe the job they want in their own words, and their words are
not what adverts say. The first person to sign up from ChatGPT (22 October)
typed one phrase, "shop keeper restraunt waiter": two jobs run together, one
misspelt. Searched for any of its words, it found a herd manager, a welding
supervisor and a solution architect; searched for all of them, it would find
nothing. Their history was landscaping.

So the titles to search are worked out from the whole person - what they
asked for, the work they have done, their CV - by a model told to split,
correct and translate into the titles UK employers actually advertise, at
the level the person can start at now. A few titles from their own work
history come after, kept apart so the scorer knows they are a second choice
rather than what was asked for. The answer is cached against the profile and
asked again only when the profile changes.

When the model cannot be reached, plain rules do the same job less well:
split the phrase on the titles it contains, correct near-miss spellings, and
add the titles each everyday one is advertised under.
"""

from __future__ import annotations

import difflib
import hashlib
import json
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
    "waiter": ("waiting staff", "front of house"),
    "waitress": ("waiting staff", "front of house"),
    "carer": ("care assistant", "support worker"),
    "care worker": ("care assistant", "support worker"),
    "warehouse worker": ("warehouse operative",),
    "packer": ("packing operative", "warehouse operative"),
    "cleaner": ("cleaning operative",),
    "cook": ("chef",),
    "barman": ("bar staff",),
    "barmaid": ("bar staff",),
    "bartender": ("bar staff",),
    "labourer": ("general operative",),
    "landscaper": ("grounds maintenance", "gardener"),
    "lorry driver": ("hgv driver",),
    "office worker": ("administrator", "admin assistant"),
    "secretary": ("administrator", "admin assistant"),
}

# Titles that are already what adverts say. With the keys above, this is
# the vocabulary a run-together phrase is split on.
ADVERTISED = (
    "retail assistant", "sales assistant", "customer assistant",
    "checkout assistant", "store assistant", "shop assistant",
    "waiting staff", "front of house", "bar staff", "barista",
    "kitchen porter", "kitchen assistant", "chef", "commis chef",
    "food and beverage assistant", "catering assistant", "cleaning operative",
    "care assistant", "support worker", "healthcare assistant",
    "warehouse operative", "picker", "packing operative", "forklift driver",
    "delivery driver", "van driver", "hgv driver", "general operative",
    "production operative", "machine operator", "receptionist",
    "administrator", "admin assistant", "customer service advisor",
    "call centre advisor", "security officer", "gardener",
    "grounds maintenance", "landscape gardener", "electrician", "plumber",
    "joiner", "carpenter", "painter and decorator", "maintenance technician",
    "teaching assistant", "nursery assistant", "hairdresser",
)

MAX_EXTRA = 2       # advertised equivalents added per everyday title
MAX_TITLES = 5      # titles searched for what they asked for
MAX_FROM_HISTORY = 2


def _key(role: str) -> str:
    return " ".join(re.sub(r"[^a-z ]+", " ", (role or "").lower()).split())


KNOWN = sorted({_key(t) for t in (*ALSO_ADVERTISED_AS, *ADVERTISED)},
               key=lambda t: (-len(t.split()), t))
_VOCAB = sorted({w for t in KNOWN for w in t.split()} | {"restaurant", "cafe"})


def equivalents(role: str) -> tuple[str, ...]:
    """The advertised titles for this everyday one; empty when it has none
    or is already the advertised title."""
    return ALSO_ADVERTISED_AS.get(_key(role), ())[:MAX_EXTRA]


def _correct(word: str) -> str:
    """A near-miss spelling of a word in a known title, corrected. Short
    words and words already known are left alone."""
    if word in _VOCAB or len(word) < 5:
        return word
    near = difflib.get_close_matches(word, _VOCAB, n=1, cutoff=0.8)
    return near[0] if near else word


def split_roles(roles) -> list[str]:
    """Separate jobs run together in one box, with near-miss spellings
    corrected: "shop keeper restraunt waiter" -> shop keeper, waiter.
    A phrase with no known title in it is kept as it was typed."""
    out: list[str] = []
    for role in list(roles or []):
        for part in re.split(r"\s*(?:[,/;&+]|\band\b|\bor\b)\s*", role or "",
                             flags=re.I):
            words = [_correct(w) for w in _key(part).split()]
            if not words:
                continue
            found, i = [], 0
            while i < len(words):
                for title in KNOWN:
                    n = len(title.split())
                    if words[i:i + n] == title.split():
                        found.append(title)
                        i += n
                        break
                else:
                    i += 1
            for title in (found or [part.strip()]):
                if _key(title) not in {_key(o) for o in out}:
                    out.append(title)
    return out


def search_terms(roles) -> list[str]:
    """Every title to search for by the rules alone: each of the person's
    own (split and corrected), then the advertised equivalents, each once."""
    out, seen = [], set()
    for role in split_roles(roles):
        for term in (role, *equivalents(role)):
            key = _key(term)
            if key and key not in seen:
                seen.add(key)
                out.append(term)
    return out[:MAX_TITLES * 2]


# ----------------------------------------------------------------------
# Working it out from the whole person.
# ----------------------------------------------------------------------
PROMPT = """You choose the job titles to search UK job boards for, for one jobseeker.

Read what they asked for and their background. Reply with the titles UK
employers actually put on adverts, for work this person wants and could get
now.

Rules:
- What they asked for comes first. Their words may be several jobs run
  together, misspelt or informal ("shop keeper restraunt waiter" means shop
  work and restaurant waiting): split them, correct them, and give the
  advertised titles for each (for shop work: retail assistant, sales
  assistant; for waiting: waiter, waitress, waiting staff).
- Pitch the level they can start at now. No manager, supervisor, senior,
  lead or graduate titles unless their history shows that level.
- Separately, up to {max_history} titles that use the work they have
  already done, if it is work they could go back to. Leave this empty if
  their history is blank.
- Each title 1 to 4 words, UK spelling, no places, no pay, no company names.
- At most {max_titles} titles for what they asked for.
- In "work", one plain sentence on the kind of work they want and can do
  now, so somebody reading an advert with a different title can tell
  whether it is that work (for example "Customer-facing shop, cafe and
  restaurant work at entry level").

Reply with JSON only:
{{"asked": ["..."], "from_history": ["..."], "work": "..."}}

WHAT THEY ASKED FOR: {asked}
WORK HISTORY: {history}
QUALIFICATIONS: {qualifications}
CV (start): {cv}
"""

def is_planning(prompt: str) -> bool:
    """Whether a model call is this one: about the person, once per profile,
    never about an advert."""
    return (prompt or "").startswith(PROMPT.split("\n", 1)[0])


_TITLE = re.compile(r"^[A-Za-z][A-Za-z '&\-]{1,38}[A-Za-z]$")
_TOO_SENIOR = re.compile(r"\b(manager|head|director|senior|lead|principal)\b",
                         re.I)


PLAN_VERSION = "2"     # bump when the question changes, so it is asked again


def fingerprint(profile, cv_text: str = "") -> str:
    """What the titles were worked out from. A change to any of it asks
    again; nothing else does."""
    parts = [PLAN_VERSION, *(profile.target_roles or []),
             *[f"{r.title}|{r.org}|{r.detail}" for r in profile.history or []],
             *(profile.qualifications or []), (cv_text or "")[:2000]]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def prompt_for(profile, cv_text: str = "") -> str:
    history = "; ".join(
        f"{r.title} at {r.org}" + (f" ({r.detail})" if r.detail else "")
        for r in profile.history or []) or "none given"
    return PROMPT.format(
        max_history=MAX_FROM_HISTORY, max_titles=MAX_TITLES,
        asked="; ".join(profile.target_roles or []) or "not stated",
        history=history,
        qualifications="; ".join(profile.qualifications or []) or "none given",
        cv=" ".join((cv_text or "").split())[:1500] or "none")


def _clean(titles, limit: int, seniority_ok: bool) -> list[str]:
    out = []
    for t in titles if isinstance(titles, list) else []:
        t = " ".join(str(t).split())
        if not _TITLE.match(t) or len(t.split()) > 4:
            continue
        if _TOO_SENIOR.search(t) and not seniority_ok:
            continue
        if _key(t) not in {_key(o) for o in out}:
            out.append(t.lower())
        if len(out) >= limit:
            break
    return out


def parse_plan(text: str, profile) -> dict:
    """The model's answer, held to the rules whatever it said. Raises
    ValueError when nothing usable came back."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("no JSON in the answer")
    data = json.loads(match.group(0))
    senior = any(_TOO_SENIOR.search(r.title or "") for r in profile.history or [])
    asked = _clean(data.get("asked"), MAX_TITLES, senior)
    if not asked:
        raise ValueError("no titles for what they asked for")
    history = [t for t in _clean(data.get("from_history"), MAX_FROM_HISTORY,
                                 senior)
               if _key(t) not in {_key(a) for a in asked}]
    if not profile.history:
        history = []
    work = " ".join(str(data.get("work") or "").split())[:200]
    if not re.fullmatch(r"[A-Za-z][A-Za-z ,.'&\-]*", work or "x1"):
        work = ""
    return {"asked": asked, "from_history": history, "work": work,
            "by": "model"}


def by_rules(profile) -> dict:
    return {"asked": search_terms(profile.target_roles), "from_history": [],
            "work": "", "by": "rules"}


def plan(profile, ai=None, cv_text: str = "") -> dict:
    """{"asked": [...], "from_history": [...], "by": "model"|"rules"}.
    The model's answer when it gives a usable one, the rules' otherwise."""
    if ai is not None:
        try:
            return parse_plan(ai(prompt_for(profile, cv_text)), profile)
        except Exception as exc:
            print(f"[roles] working out titles by rules instead: {exc}")
    return by_rules(profile)


def titles(profile) -> list[str]:
    """Everything to search for this person: the plan when one has been
    attached, the rules otherwise."""
    asked = list(getattr(profile, "search_titles", None) or [])
    if not asked:
        return search_terms(profile.target_roles)
    extra = [t for t in getattr(profile, "experience_titles", None) or []
             if _key(t) not in {_key(a) for a in asked}]
    return asked + extra


def describe(profile_or_roles) -> str:
    """The target roles for the scorer: their own words, what those are
    advertised as, and what their history also fits, so an advert for any of
    them is judged as the job they want rather than a near miss."""
    work = ""
    if isinstance(profile_or_roles, (list, tuple)):
        roles, asked, extra = list(profile_or_roles), [], []
    else:
        roles = list(profile_or_roles.target_roles or [])
        asked = list(getattr(profile_or_roles, "search_titles", None) or [])
        extra = list(getattr(profile_or_roles, "experience_titles", None) or [])
        work = getattr(profile_or_roles, "wanted_work", "") or ""
    if not asked:
        asked = [t for t in search_terms(roles)
                 if _key(t) not in {_key(r) for r in roles}]
    text = "; ".join(roles)
    if asked:
        text += (". These are the same jobs as advertised, and count as what "
                 "they asked for: " + ", ".join(asked))
    if extra:
        text += (". Also suitable, from work they have done (a second "
                 "choice): " + ", ".join(extra))
    if work:
        text += (". The work they want: " + work.rstrip(".") + ". Judge an "
                 "advert by the work it describes, not by its exact title")
    return text
