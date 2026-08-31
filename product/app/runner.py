"""One run of the machine, for one user.

harvest -> score -> find a real address -> write the letter -> a draft on
their screen. This is the file that turns four tested stages into a product.

Three things it is careful about, all for the same reason - the person on the
other end of the letter is real:

  - an employer already written to is never written to again
  - an employer who asked to be left alone is never written to at all
  - no letter is produced without an address somebody published

And one thing it is careful about for the user's sake: **every listing that
does not become a draft is recorded with the reason.** "Nothing today" is a
perfectly normal outcome, and a user who cannot see why stops trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobseeker.pipeline import compose, discover, harvest, scoring
from jobseeker.profile import Profile, ProfileError

from . import config, db

DEFAULT_DRAFT_CAP = 20


@dataclass
class RunReport:
    harvested: int = 0
    already_seen: int = 0
    prefiltered: int = 0
    scored_out: int = 0
    no_address: int = 0
    already_contacted: int = 0
    blocked: int = 0
    compose_failed: int = 0
    fallback_used: int = 0
    drafted: int = 0
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.drafted} drafted from {self.harvested} listings "
                f"({self.no_address} had no real address, "
                f"{self.scored_out} scored too low, "
                f"{self.already_contacted} already contacted)")


def credentials() -> harvest.Credentials:
    return harvest.Credentials(adzuna_app_id=config.ADZUNA_APP_ID,
                               adzuna_app_key=config.ADZUNA_APP_KEY,
                               reed_api_key=config.REED_API_KEY)


def run_for_user(user_id: int, *, ai=None, session=None,
                 cap: int = DEFAULT_DRAFT_CAP, delay: float | None = None,
                 interactive: bool = False) -> RunReport:
    """Produce drafts for one user. Never raises for one bad listing.

    `interactive` says a person is waiting on this. It picks the model caller
    that refuses to sit out a rate limit, because the server has one worker
    and a sleeping request blocks the whole app.
    """
    report = RunReport()

    raw = db.load_profile(user_id)
    if not raw:
        report.errors.append("no profile yet")
        return report
    try:
        profile = Profile.from_dict(raw)
    except ProfileError as exc:
        report.errors.append(f"profile unusable: {exc}")
        return report

    if ai is None:
        from .ai import gemini, gemini_now
        ai = gemini_now if interactive else gemini
    kwargs = {} if delay is None else {"delay": delay}

    seen = db.seen_ids(user_id)
    found = harvest.harvest(profile, credentials(), session=session,
                            known_ids=seen,
                            exclude_titles=profile.exclude_titles)
    report.harvested = len(found["keep"]) + len(found["dropped"])
    report.prefiltered = len(found["dropped"])
    for listing in found["dropped"]:
        db.mark_seen(user_id, listing.external_id, listing.skipped)

    # Drop anything belonging to an employer already written to, or blocked,
    # BEFORE scoring. There is no sense paying a model to judge a listing that
    # could never be sent.
    candidates = []
    for listing in found["keep"]:
        if db.is_blocked(user_id, listing.company):
            report.blocked += 1
            db.mark_seen(user_id, listing.external_id,
                         "you asked never to contact this employer")
        elif db.already_contacted(user_id, listing.company):
            report.already_contacted += 1
            db.mark_seen(user_id, listing.external_id,
                         "you have already written to this employer")
        else:
            candidates.append(listing)

    judged = scoring.score(candidates, profile, ai)
    for listing in judged["rejected"]:
        report.scored_out += 1
        db.mark_seen(user_id, listing.external_id, listing.skipped)

    for listing in judged["passed"]:
        if report.drafted >= cap:
            break
        try:
            _draft_one(user_id, listing, profile, ai, session, report, **kwargs)
        except Exception as exc:
            # One bad listing must never lose the rest of the run.
            report.errors.append(f"{listing.external_id}: {exc}")
            db.mark_seen(user_id, listing.external_id, f"error: {exc}"[:200])

    return report


def _draft_one(user_id, listing, profile, ai, session, report, **kwargs):
    contact = discover.discover(listing, profile, session=session, **kwargs)
    if not contact:
        report.no_address += 1
        db.mark_seen(user_id, listing.external_id,
                     "no real email address could be found - nothing is guessed")
        return

    letter = compose.compose(listing, contact, profile, ai)
    if letter is None:
        # A quota is a daily ceiling and hitting it is a normal Tuesday. A
        # plainer letter to a verified address beats no letter at all.
        letter = compose.plain_letter(listing, contact, profile)
        report.fallback_used += 1

    db.add_draft(
        user_id,
        job_title=listing.title, company=listing.company,
        location=listing.location, listing_url=listing.url,
        salary_text=_salary_text(listing), score=listing.score,
        to_email=letter["to_email"], to_name=letter.get("to_name"),
        contact_tier=letter.get("contact_tier"),
        subject=letter["subject"], body=letter["body"])
    db.mark_seen(user_id, listing.external_id, "drafted")
    report.drafted += 1


def _salary_text(listing) -> str:
    amount, unit = scoring.stated_pay(listing)
    if amount is None:
        return ""
    if unit == "year":
        return f"£{amount:,.0f}"
    return f"£{amount:g} per {unit}"
