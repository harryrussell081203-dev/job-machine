"""Ask the questions, write the profile, prove the keys work.

Setup is where a self-hosted tool loses people. Five API keys, an app password
and a YAML file is a lot to ask of someone who just wants a better job, and
every buyer who gets halfway and stalls becomes a refund and a support email.

So this does three things in order, and the third is the one that matters:

  1. asks for the profile in plain questions
  2. writes `profile.yaml` and a `.env` beside it
  3. CALLS every API with the key just given, and says which ones answered

Finding out a key is wrong here takes ten seconds. Finding out at 9am on the
first live run, after a silent empty harvest, takes a week and a bad review.

    python -m jobseeker.wizard
"""

from __future__ import annotations

import os
import sys

from .profile import KNOWN_PRIORITIES, SITUATIONS, Profile, ProfileError, Role

BANNER = """
  job machine - setup
  ------------------------------------------------------------------
  About ten minutes. Nothing is sent to anybody during this, and
  nothing leaves your machine except the key checks at the end.

  Press Ctrl-C at any point to stop; nothing is written until the end.
"""


# ----------------------------------------------------------------------
# asking
# ----------------------------------------------------------------------
def ask(prompt: str, default: str = "", required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = input(f"{prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nstopped. nothing was written.")
            sys.exit(1)
        value = raw or default
        if value or not required:
            return value
        print("  ^ needed.")


def ask_int(prompt: str, default: int = 0, required: bool = True) -> int:
    while True:
        raw = ask(prompt, str(default) if default else "", required=required)
        if not raw:
            return 0
        try:
            return int(raw.replace(",", "").replace("£", "").strip())
        except ValueError:
            print("  ^ numbers only, e.g. 38000")


def ask_bool(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        raw = ask(f"{prompt} ({d})", required=False).lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  ^ y or n.")


def ask_list(prompt: str, hint: str = "") -> list[str]:
    if hint:
        print(f"  {hint}")
    print("  One per line. Blank line when done.")
    out: list[str] = []
    while True:
        try:
            line = input("    - ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nstopped. nothing was written.")
            sys.exit(1)
        if not line:
            if out:
                return out
            print("  ^ at least one.")
            continue
        out.append(line)


def ask_choice(prompt: str, options: tuple[str, ...], default: str) -> str:
    print(f"  options: {', '.join(options)}")
    while True:
        raw = ask(prompt, default).lower()
        if raw in options:
            return raw
        print(f"  ^ one of {', '.join(options)}")


# ----------------------------------------------------------------------
# the interview
# ----------------------------------------------------------------------
def build_profile() -> Profile:
    print("\n-- you " + "-" * 56)
    name = ask("Your full name, as it should sign a letter")
    location = ask("Where you are (town, country)")
    phone = ask("Phone number employers should ring")
    email = ask("Your email address", required=False)

    print("\n-- where you stand " + "-" * 47)
    situation = ask_choice("Your situation", SITUATIONS, "employed")
    current_salary = 0
    may_name = False
    if situation == "employed":
        print("  This is not cosmetic: while you are in work, a job that pays")
        print("  the same as yours is scored as worthless however good the fit.")
        current_salary = ask_int("What you earn now, per year")
        may_name = ask_bool(
            "May your current employer be named in letters? (usually no)", False)

    print("\n-- your floor " + "-" * 52)
    print("  With no floor set, the machine writes to jobs that pay less than")
    print("  you already earn. Set at least one of these.")
    min_salary = ask_int("Lowest yearly salary worth your time", required=False)
    min_rate = ask_int("Lowest hourly rate on a contract (0 to skip)", required=False)

    print("\n-- what you want " + "-" * 49)
    print(f"  Pick from: {', '.join(KNOWN_PRIORITIES)}")
    print("  Order matters - the first one wins when two jobs are equal.")
    priorities = ask_list("Priorities, most important first")

    print("\n-- your history " + "-" * 50)
    print("  This is what earns replies. Be specific: equipment, standards,")
    print("  systems, numbers. 'Responsible for maintenance' is worth nothing.")
    history: list[Role] = []
    while True:
        title = ask("Job title", required=True)
        org = ask("  Employer")
        start = ask("  From (year)", required=False)
        end = ask("  To (year, or blank for present)", required=False)
        detail = ask("  What you actually did, one sentence")
        history.append(Role(title=title, org=org, start=start, end=end,
                            detail=detail))
        if not ask_bool("Add another job?", False):
            break

    print("\n-- qualifications " + "-" * 48)
    quals = ask_list("Tickets, cards, certificates, degrees")

    print("\n-- what must never be said about you " + "-" * 29)
    print("  The most important question here. Asked to sell you, a model will")
    print("  reach for credentials you never mentioned. Anything you do NOT")
    print("  currently hold goes here: a lapsed ticket, a paused degree, a")
    print("  clearance that expired, a licence you are still working towards.")
    never_claim: list[str] = []
    if ask_bool("Anything to rule out?", True):
        never_claim = ask_list("Never claim...")

    print("\n-- where to look " + "-" * 49)
    locations = ask_list("Towns and cities to search")
    radius = ask_int("Miles around each", 25)

    print("\n-- what to look for " + "-" * 46)
    target_roles = ask_list("Job titles to search for",
                            "e.g. maintenance technician, field service engineer")

    return Profile(
        name=name, location=location, phone=phone, email=email,
        situation=situation, current_salary=current_salary,
        may_name_employer=may_name,
        min_salary_annual=min_salary, min_rate_hourly=min_rate,
        priorities=[p.lower() for p in priorities],
        target_roles=target_roles,
        wants_travel="travel" in [p.lower() for p in priorities],
        wants_contract="contract" in [p.lower() for p in priorities],
        history=history, qualifications=quals, never_claim=never_claim,
        locations=locations, radius_miles=radius,
    )


# ----------------------------------------------------------------------
# writing
# ----------------------------------------------------------------------
def to_yaml(p: Profile) -> str:
    def block(items: list[str]) -> str:
        return "\n".join(f"  - {i}" for i in items) if items else "  []"

    lines = [
        "# Written by `python -m jobseeker.wizard`. Safe to edit by hand.",
        "",
        f"name: {p.name}",
        f"location: {p.location}",
        f'phone: "{p.phone}"',
    ]
    if p.email:
        lines.append(f"email: {p.email}")
    lines += [
        "",
        f"situation: {p.situation}",
    ]
    if p.current_salary:
        lines.append(f"current_salary: {p.current_salary}")
    lines += [
        f"may_name_employer: {str(p.may_name_employer).lower()}",
        "",
        f"min_salary_annual: {p.min_salary_annual}",
        f"min_rate_hourly: {p.min_rate_hourly}",
        "",
        "priorities:", block(p.priorities),
        f"wants_travel: {str(p.wants_travel).lower()}",
        f"wants_contract: {str(p.wants_contract).lower()}",
        "",
        "history:",
    ]
    for r in p.history:
        lines.append(f"  - title: {r.title}")
        lines.append(f"    org: {r.org}")
        if r.start:
            lines.append(f'    start: "{r.start}"')
        if r.end:
            lines.append(f'    end: "{r.end}"')
        if r.detail:
            lines.append(f"    detail: >-\n      {r.detail}")
    lines += [
        "",
        "qualifications:", block(p.qualifications),
        "",
        "never_claim:", block(p.never_claim),
        "",
        "locations:", block(p.locations),
        f"radius_miles: {p.radius_miles}",
        "",
        "target_roles:", block(p.target_roles),
        "",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# keys, and proving they work
# ----------------------------------------------------------------------
KEYS = [
    ("ADZUNA_APP_ID", "Adzuna app id", "developer.adzuna.com - free"),
    ("ADZUNA_APP_KEY", "Adzuna app key", ""),
    ("REED_API_KEY", "Reed API key", "reed.co.uk/developers - free"),
    ("GEMINI_API_KEY", "Gemini API key", "aistudio.google.com - free tier"),
    ("GMAIL_ADDRESS", "The Gmail address that sends", ""),
    ("GMAIL_APP_PASSWORD", "Gmail app password",
     "Google account > Security > App passwords (needs 2FA on)"),
]


def collect_keys() -> dict:
    print("\n-- keys " + "-" * 58)
    print("  These stay on your machine, in a .env file git ignores.")
    out = {}
    for name, label, hint in KEYS:
        if hint:
            print(f"  ({hint})")
        out[name] = ask(label, os.environ.get(name, ""), required=False)
    return out


def check_keys(keys: dict) -> list[tuple[str, bool, str]]:
    """Call each service once. Ten seconds now saves a silent dead run later."""
    results: list[tuple[str, bool, str]] = []
    try:
        import requests
    except ImportError:
        return [("(skipped)", False, "pip install requests to check keys")]

    if keys.get("ADZUNA_APP_ID") and keys.get("ADZUNA_APP_KEY"):
        try:
            r = requests.get(
                "https://api.adzuna.com/v1/api/jobs/gb/search/1",
                params={"app_id": keys["ADZUNA_APP_ID"],
                        "app_key": keys["ADZUNA_APP_KEY"], "results_per_page": 1},
                timeout=20)
            results.append(("Adzuna", r.status_code == 200,
                            f"HTTP {r.status_code}"))
        except Exception as exc:
            results.append(("Adzuna", False, str(exc)[:60]))

    if keys.get("REED_API_KEY"):
        try:
            r = requests.get("https://www.reed.co.uk/api/1.0/search",
                             params={"keywords": "engineer", "resultsToTake": 1},
                             auth=(keys["REED_API_KEY"], ""), timeout=20)
            results.append(("Reed", r.status_code == 200, f"HTTP {r.status_code}"))
        except Exception as exc:
            results.append(("Reed", False, str(exc)[:60]))

    if keys.get("GEMINI_API_KEY"):
        try:
            r = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.5-flash:generateContent",
                headers={"x-goog-api-key": keys["GEMINI_API_KEY"]},
                json={"contents": [{"parts": [{"text": "say ok"}]}]},
                timeout=30)
            results.append(("Gemini", r.status_code == 200, f"HTTP {r.status_code}"))
        except Exception as exc:
            results.append(("Gemini", False, str(exc)[:60]))

    if keys.get("GMAIL_ADDRESS") and keys.get("GMAIL_APP_PASSWORD"):
        import smtplib
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as s:
                s.login(keys["GMAIL_ADDRESS"], keys["GMAIL_APP_PASSWORD"])
            results.append(("Gmail", True, "logged in"))
        except Exception as exc:
            results.append(("Gmail", False, str(exc)[:60]))

    return results


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out_dir = argv[0] if argv else "."
    profile_path = os.path.join(out_dir, "profile.yaml")
    env_path = os.path.join(out_dir, ".env")

    print(BANNER)
    if os.path.exists(profile_path):
        if not ask_bool(f"{profile_path} exists. Overwrite?", False):
            print("left alone.")
            return 1

    try:
        profile = build_profile()
    except ProfileError as exc:
        # Reached only if the answers contradict each other, e.g. a floor set
        # below the salary they just gave. Say so plainly and let them re-run.
        print(f"\nthat profile would not work:\n{exc}\n")
        return 1

    keys = collect_keys()

    with open(profile_path, "w", encoding="utf-8") as fh:
        fh.write(to_yaml(profile))
    with open(env_path, "w", encoding="utf-8") as fh:
        fh.write("# keys for the job machine. never commit this file.\n")
        for name, _, _ in KEYS:
            fh.write(f"{name}={keys.get(name, '')}\n")
    os.chmod(env_path, 0o600)

    print(f"\nwrote {profile_path}")
    print(f"wrote {env_path} (chmod 600)")

    print("\n-- checking your keys " + "-" * 44)
    results = check_keys(keys)
    if not results:
        print("  nothing to check - no keys given.")
    for service, ok, detail in results:
        print(f"  {'OK  ' if ok else 'FAIL'}  {service:<8} {detail}")

    failed = [s for s, ok, _ in results if not ok]
    print()
    if failed:
        print(f"fix these before your first run: {', '.join(failed)}")
        print("re-run this wizard, or edit .env directly.")
        return 1

    print("all good. next:")
    print("  python job_machine.py --dry-run     # writes nothing, sends nothing")
    print("  TEST_MODE=1 python job_machine.py   # sends, but only to you")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
