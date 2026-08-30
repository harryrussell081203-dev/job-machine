"""One person's job search, described in a file instead of hardcoded.

The original machine carried its candidate as a 40-line string literal near the
top of `job_machine.py`, which is exactly right when the machine has one user
and wrong the moment it has two. This module is that string literal turned into
something a stranger can fill in without reading any Python.

Two things come out of a Profile and they are the only two the engine needs:

  - `prompt_block()`  - the CANDIDATE section handed to the scorer and the
                        composer, in the same shape the engine already expects
  - `signoff()`       - the fixed sign-off line every letter ends with

Everything else here exists to stop a half-filled profile reaching an employer.
A bad profile does not fail loudly at send time - it quietly produces plausible,
wrong letters - so validation runs at load and refuses rather than warns.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

# Priorities the scorer understands. A user can order these however they like,
# but inventing a new one means nothing downstream reads it, so the set is
# closed and an unknown value is an error rather than a silent no-op.
KNOWN_PRIORITIES = ("money", "travel", "contract", "progression", "stability",
                    "hours", "location", "training")

# A UK mobile or landline, loosely. Deliberately permissive: the point is to
# catch an empty box or an obvious typo, not to police formatting of a number
# the user will see in every letter they send anyway.
PHONE_RE = re.compile(r"^[0-9+][0-9\s()+-]{8,19}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Situations that change how the whole search behaves. `employed` is not a
# cosmetic flag - it is what makes a sideways move worthless, and it is what
# licenses the "I am not desperate" note in a follow-up.
SITUATIONS = ("employed", "unemployed", "notice_period", "student")


class ProfileError(ValueError):
    """A profile that would produce wrong letters. Always fatal."""


@dataclass
class Role:
    """One line of history. `detail` is what actually earns replies."""
    title: str
    org: str
    start: str = ""
    end: str = ""
    detail: str = ""

    def render(self) -> str:
        span = ""
        if self.start or self.end:
            span = f" {self.start}-{self.end or 'present'}".rstrip("-")
        head = f"{self.title} at {self.org}{span}"
        return f"{head}: {self.detail}" if self.detail else head


@dataclass
class Profile:
    # --- identity -----------------------------------------------------
    name: str
    location: str
    phone: str
    email: str = ""

    # --- where they stand ---------------------------------------------
    situation: str = "unemployed"
    current_salary: int = 0
    # Naming your current employer to another employer in the same town is how
    # a quiet search stops being quiet. Off by default, on only deliberately.
    may_name_employer: bool = False

    # --- the floor ----------------------------------------------------
    min_salary_annual: int = 0
    min_rate_hourly: int = 0

    # --- what they want -----------------------------------------------
    priorities: list[str] = field(default_factory=list)
    target_roles: list[str] = field(default_factory=list)
    wants_travel: bool = False
    wants_contract: bool = False

    # --- what they have -----------------------------------------------
    history: list[Role] = field(default_factory=list)
    qualifications: list[str] = field(default_factory=list)

    # --- what they must never say -------------------------------------
    # The single most important field here. The original had one instance of
    # it - never claim a security clearance that lapsed - and generalising it
    # is what keeps this honest for everyone else: never claim a licence, a
    # ticket, a degree or a right to work you do not currently hold. A model
    # asked to sell someone will reach for these unprompted.
    never_claim: list[str] = field(default_factory=list)

    # --- search shape --------------------------------------------------
    locations: list[str] = field(default_factory=list)
    radius_miles: int = 25

    # Titles to bin unseen. Empty by default and deliberately so: the machine
    # this came from excluded "sales executive" and "care assistant", which is
    # right for one man's trade and actively wrong for anybody who works in
    # either. A filter nobody asked for hides the job they wanted.
    exclude_titles: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.validate()

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def validate(self) -> None:
        problems: list[str] = []

        if not self.name.strip():
            problems.append("name is empty - it signs every letter")
        if not self.location.strip():
            problems.append("location is empty - employers ask first")
        if not PHONE_RE.match(self.phone.strip() or ""):
            problems.append(
                f"phone {self.phone!r} does not look like a number - it is "
                "printed in every sign-off, so a typo costs you the reply")
        if self.email and not EMAIL_RE.match(self.email.strip()):
            problems.append(f"email {self.email!r} is not an address")

        if self.situation not in SITUATIONS:
            problems.append(
                f"situation {self.situation!r} unknown - one of {list(SITUATIONS)}")

        if self.situation == "employed" and self.current_salary <= 0:
            problems.append(
                "situation is 'employed' but current_salary is 0 - without it "
                "nothing can tell whether a listing is a rise or a pay cut")

        if not self.min_salary_annual and not self.min_rate_hourly:
            problems.append(
                "set min_salary_annual or min_rate_hourly - with no floor the "
                "machine will write to jobs that pay less than you earn now")

        # A floor below what you already earn is almost always a typo, and it
        # is an expensive one: it opens the gate to every sideways move going.
        if (self.situation == "employed" and self.min_salary_annual
                and self.current_salary
                and self.min_salary_annual < self.current_salary):
            problems.append(
                f"min_salary_annual ({self.min_salary_annual}) is below "
                f"current_salary ({self.current_salary}) - that accepts a pay cut")

        for p in self.priorities:
            if p not in KNOWN_PRIORITIES:
                problems.append(
                    f"priority {p!r} is not understood - one of {list(KNOWN_PRIORITIES)}")

        if not self.target_roles:
            problems.append("target_roles is empty - nothing to search for")
        if not self.history:
            problems.append(
                "history is empty - the letters have no proof points to make")
        if not self.locations:
            problems.append("locations is empty - nowhere to search")

        if problems:
            raise ProfileError(
                "profile is not usable yet:\n  - " + "\n  - ".join(problems))

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def signoff(self, cv_attached: bool = True) -> str:
        """The fixed last line. Same shape the engine already enforces."""
        bits = [self.name, self.phone.strip()]
        if cv_attached:
            bits.append("CV attached")
        return " / ".join(bits)

    def prompt_block(self) -> str:
        """The CANDIDATE section for the scorer and composer.

        Written as instructions to a model, because that is what it is. The
        ordering matters: money and the floor come first, since the most
        common failure is a technically-good match that pays less than the
        job the user already has.
        """
        out: list[str] = [f"{self.name}, {self.location}."]

        if self.situation == "employed":
            earning = f" on GBP {self.current_salary:,} a year" if self.current_salary else ""
            out.append(
                f"- IN WORK{earning}. They are not looking for a job, they are "
                "looking for a BETTER one. A role that matches the trade but "
                "pays the same or less is worth nothing however good the fit.")
            if not self.may_name_employer:
                out.append(
                    "- NEVER NAME THEIR CURRENT EMPLOYER - not in a letter, not "
                    "in a subject line, not anywhere. It gets back to them.")
        elif self.situation == "notice_period":
            out.append("- Currently working a notice period and available shortly.")
        elif self.situation == "student":
            out.append("- Studying, and looking for the first role in the trade.")
        else:
            out.append("- Available to start now.")

        for role in self.history:
            out.append(f"- {role.render()}")

        for qual in self.qualifications:
            out.append(f"- {qual}")

        for claim in self.never_claim:
            out.append(
                f"- NEVER STATE, IMPLY OR HINT: {claim}. It would be a false "
                "claim to an employer and it would be found out.")

        out.append("- WHAT THEY ARE AFTER, in order of priority:")
        for i, p in enumerate(self.priorities, 1):
            out.append(f"  {i}. {self._priority_line(p)}")

        if self.target_roles:
            out.append("- TARGET ROLES: " + ", ".join(self.target_roles) + ".")

        return "\n".join(out)

    def _priority_line(self, p: str) -> str:
        if p == "money":
            floor = []
            if self.min_salary_annual:
                floor.append(f"at least GBP {self.min_salary_annual:,} a year")
            if self.min_rate_hourly:
                floor.append(f"GBP {self.min_rate_hourly} an hour on a contract")
            joined = ", or ".join(floor) if floor else "a rise on what they earn now"
            return f"MONEY. {joined}. Below that is not of interest."
        if p == "travel":
            return ("TRAVEL. Paid work abroad - field service, installation and "
                    "commissioning overseas, rotational or fly-in-fly-out. A "
                    "stated requirement, not a nice-to-have.")
        if p == "contract":
            return ("CONTRACT WORK IS WANTED. Day rate, umbrella, fixed-term, "
                    "shift patterns - actively of interest, not a compromise.")
        if p == "progression":
            return "PROGRESSION. A step up in responsibility or title."
        if p == "stability":
            return "STABILITY. A permanent role with an established employer."
        if p == "hours":
            return "HOURS. Predictable hours, no unplanned weekends."
        if p == "location":
            return "LOCATION. Close to home, or genuinely remote."
        if p == "training":
            return "TRAINING. An employer who will pay for tickets and courses."
        return p

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, raw: dict) -> "Profile":
        if not isinstance(raw, dict):
            raise ProfileError("profile file must be a mapping at the top level")

        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known - {"history"}
        if unknown:
            raise ProfileError(
                f"profile has fields nothing reads: {sorted(unknown)} - "
                "a typo here fails silently, so it is refused instead")

        history = []
        for i, entry in enumerate(raw.get("history") or []):
            if not isinstance(entry, dict):
                raise ProfileError(f"history[{i}] must be a mapping")
            bad = set(entry) - {"title", "org", "start", "end", "detail"}
            if bad:
                raise ProfileError(f"history[{i}] has unknown fields: {sorted(bad)}")
            if not entry.get("title") or not entry.get("org"):
                raise ProfileError(f"history[{i}] needs both title and org")
            history.append(Role(**entry))

        kwargs = {k: v for k, v in raw.items() if k != "history"}
        kwargs["history"] = history
        try:
            return cls(**kwargs)
        except TypeError as exc:
            raise ProfileError(f"profile could not be read: {exc}") from exc

    @classmethod
    def load(cls, path: str) -> "Profile":
        if not os.path.exists(path):
            raise ProfileError(
                f"no profile at {path} - run `python -m jobseeker.wizard` to make one")
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()

        if path.endswith((".yaml", ".yml")):
            try:
                import yaml
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise ProfileError(
                    "reading a .yaml profile needs PyYAML (pip install pyyaml), "
                    "or save your profile as .json instead") from exc
            raw = yaml.safe_load(text)
        else:
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProfileError(f"{path} is not valid JSON: {exc}") from exc

        return cls.from_dict(raw or {})
