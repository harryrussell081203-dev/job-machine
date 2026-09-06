"""Run the machine from a profile file, with no server and no database.

    python -m jobseeker.cli --profile profile.yaml

This is the zero-cost way to use all of this. GitHub Actions runs it free on a
schedule, it needs no host, no domain and no database, and the drafts arrive
as an email you can send from with one tap.

It is the same pipeline the web app uses - harvest, score, find a real address,
write the letter. What it does not have is an account, a paywall, or anywhere
to click. State lives in one JSON file the workflow commits back, which is
exactly how the original machine has run for months.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import quote

from .names import company_key
from .pipeline import compose, discover, harvest, scoring
from .profile import Profile, ProfileError

STATE_VERSION = 1


# ----------------------------------------------------------------------
# state: one JSON file, committed back by the workflow
# ----------------------------------------------------------------------
def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"version": STATE_VERSION, "seen": {}, "contacted": [],
                "do_not_contact": []}
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    state.setdefault("seen", {})
    state.setdefault("contacted", [])
    state.setdefault("do_not_contact", [])
    return state


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


def may_contact(state: dict, company: str) -> bool:
    key = company_key(company)
    return key not in set(state["contacted"]) | set(state["do_not_contact"])


# ----------------------------------------------------------------------
# the run
# ----------------------------------------------------------------------
def run(profile: Profile, creds: harvest.Credentials, state: dict, ai,
        *, cap: int = 20, session=None, delay=None) -> tuple[list, dict]:
    """Returns (drafts, counts). Never raises for one bad listing."""
    counts = {"harvested": 0, "prefiltered": 0, "already_contacted": 0,
              "scored_out": 0, "no_address": 0, "drafted": 0, "fallback": 0}
    kwargs = {} if delay is None else {"delay": delay}

    found = harvest.harvest(profile, creds, session=session,
                            known_ids=set(state["seen"]),
                            exclude_titles=profile.exclude_titles)
    counts["harvested"] = len(found["keep"]) + len(found["dropped"])
    counts["prefiltered"] = len(found["dropped"])
    for listing in found["dropped"]:
        state["seen"][listing.external_id] = listing.skipped

    candidates = []
    for listing in found["keep"]:
        if may_contact(state, listing.company):
            candidates.append(listing)
        else:
            counts["already_contacted"] += 1
            state["seen"][listing.external_id] = "employer already contacted"

    judged = scoring.score(candidates, profile, ai)
    for listing in judged["rejected"]:
        counts["scored_out"] += 1
        state["seen"][listing.external_id] = listing.skipped

    drafts = []
    for listing in judged["passed"]:
        if len(drafts) >= cap:
            break
        try:
            contact = discover.discover(listing, profile, session=session, **kwargs)
            if not contact:
                counts["no_address"] += 1
                state["seen"][listing.external_id] = (
                    "no real email address found - nothing is guessed")
                continue

            letter = compose.compose(listing, contact, profile, ai)
            if letter is None:
                letter = compose.plain_letter(listing, contact, profile)
                counts["fallback"] += 1

            letter["listing"] = listing
            drafts.append(letter)
            state["seen"][listing.external_id] = "drafted"
            counts["drafted"] += 1
        except Exception as exc:            # one listing must not lose the run
            state["seen"][listing.external_id] = f"error: {exc}"[:200]
            print(f"[cli] {listing.external_id}: {exc}", file=sys.stderr)

    return drafts, counts


# ----------------------------------------------------------------------
# the digest
# ----------------------------------------------------------------------
def mailto(to_email: str, subject: str, body: str) -> str:
    return (f"mailto:{quote(to_email or '')}"
            f"?subject={quote(subject or '', safe='')}"
            f"&body={quote(body or '', safe='')}")


TIER_LABEL = {3: "a named person", 2: "a hiring inbox", 1: "a generic inbox"}


def digest_html(drafts: list, counts: dict, profile: Profile) -> str:
    """The email the user actually receives. One tap per letter."""
    when = datetime.now(timezone.utc).strftime("%A %d %B")
    parts = [
        "<div style=\"font:15px/1.6 system-ui,sans-serif;max-width:640px\">",
        f"<h2 style=\"margin:0 0 4px\">{len(drafts)} ready to send</h2>",
        f"<p style=\"color:#666;margin:0 0 18px\">{when}. "
        f"{counts['harvested']} listing"
        f"{'' if counts['harvested'] == 1 else 's'} looked at.</p>",
    ]

    for d in drafts:
        l = d["listing"]
        tier = TIER_LABEL.get(d.get("contact_tier"), "an inbox")
        parts += [
            "<div style=\"border:1px solid #ddd;border-radius:8px;"
            "padding:14px;margin-bottom:14px\">",
            f"<b>{l.title}</b><br>",
            f"<span style=\"color:#666;font-size:13px\">{l.company}"
            f"{' &middot; ' + l.location if l.location else ''} &middot; "
            f"to {d.get('to_name') or d['to_email']} ({tier})</span>",
            f"<p style=\"margin:10px 0 4px\"><b>{d['subject']}</b></p>",
            "<pre style=\"white-space:pre-wrap;font:inherit;background:#f7f7f5;"
            f"padding:10px;border-radius:6px\">{d['body']}</pre>",
            f"<p><a href=\"{mailto(d['to_email'], d['subject'], d['body'])}\" "
            "style=\"background:#1c5d3f;color:#fff;padding:9px 14px;"
            "border-radius:6px;text-decoration:none;display:inline-block\">"
            "Open in your mail app</a>",
        ]
        if l.url:
            parts.append(f" &nbsp; <a href=\"{l.url}\" style=\"color:#666;"
                         "font-size:13px\">read the advert</a>")
        parts.append("</p></div>")

    if not drafts:
        parts.append(
            "<p>Nothing today. Most listings end without a real address, and "
            "nothing is ever guessed &mdash; a guessed address bounces and "
            "costs you the next thirty.</p>")

    parts += [
        "<p style=\"color:#666;font-size:13px;border-top:1px solid #ddd;"
        f"padding-top:12px\">{counts['scored_out']} scored below your bar, "
        f"{counts['no_address']} had no real address, "
        f"{counts['already_contacted']} were employers you have already "
        "written to.</p>",
        "<p style=\"color:#666;font-size:13px\">Press send yourself. It goes "
        "from your address, with your signature.</p>",
        "</div>",
    ]
    return "".join(parts)


def send_digest(html: str, to_address: str, *, host: str, port: int,
                username: str, password: str, count: int) -> None:
    msg = EmailMessage()
    msg["Subject"] = (f"{count} job letters ready to send" if count
                      else "No job letters today")
    msg["From"] = username
    msg["To"] = to_address
    msg.set_content("This digest is easier to read in HTML.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(),
                          timeout=30) as s:
        s.login(username, password)
        s.send_message(msg)


# ----------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Find jobs and write the letters.")
    ap.add_argument("--profile", default="profile.yaml")
    ap.add_argument("--state", default="data/state.json")
    ap.add_argument("--cap", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the letters instead of emailing them")
    args = ap.parse_args(argv)

    try:
        profile = Profile.load(args.profile)
    except ProfileError as exc:
        print(f"profile problem:\n{exc}", file=sys.stderr)
        return 1

    creds = harvest.Credentials(
        adzuna_app_id=os.environ.get("ADZUNA_APP_ID", ""),
        adzuna_app_key=os.environ.get("ADZUNA_APP_KEY", ""),
        reed_api_key=os.environ.get("REED_API_KEY", ""))

    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 1

    from .gemini import call as ai

    state = load_state(args.state)
    drafts, counts = run(profile, creds, state, ai, cap=args.cap)

    print(f"[cli] {counts['drafted']} drafted from {counts['harvested']} "
          f"listings ({counts['no_address']} had no address, "
          f"{counts['scored_out']} scored too low)")

    if args.dry_run:
        for d in drafts:
            print("\n" + "=" * 64)
            print(f"TO:      {d.get('to_name') or ''} <{d['to_email']}>")
            print(f"SUBJECT: {d['subject']}\n")
            print(d["body"])
        return 0

    to_address = os.environ.get("DIGEST_TO") or profile.email
    user = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if to_address and user and password:
        send_digest(digest_html(drafts, counts, profile), to_address,
                    host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                    port=int(os.environ.get("SMTP_PORT", "465")),
                    username=user, password=password, count=len(drafts))
        print(f"[cli] digest sent to {to_address}")
    else:
        print("[cli] no mail settings, so nothing was emailed. "
              "Set GMAIL_ADDRESS, GMAIL_APP_PASSWORD and DIGEST_TO.")

    save_state(args.state, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
