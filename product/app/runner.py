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

from jobseeker import companies_house, geo, mx
from jobseeker.pipeline import (compose, discover, harvest, pay, proofread,
                                scoring)
from jobseeker.profile import Profile, ProfileError

from . import config, db, learning

# The sweep's reads of company sites feed the same crawl memory as /find.
discover.CRAWL_MEMORY = learning

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
    undeliverable: int = 0
    too_far: int = 0
    dissolved: int = 0
    other_kind: int = 0        # agency/employer or terms the user didn't pick
    drafted: int = 0
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        """Every listing accounted for. It used to name only three reasons,
        so "2 drafted from 5 (1 had no real address)" left two jobs
        unexplained to the person reading it, and the honest reason - a
        domain that takes no mail, a firm that has closed - was the one
        they most needed to see."""
        reasons = [f"{self.no_address} had no real address",
                   f"{self.scored_out} scored too low",
                   f"{self.already_contacted} already contacted"]
        for count, words in ((self.prefiltered, "below your pay floor or not a real vacancy"),
                             (self.undeliverable, "the email domain takes no mail"),
                             (self.too_far, "too far away"),
                             (self.dissolved, "the company has closed"),
                             (self.other_kind, "not the kind of job you picked"),
                             (self.blocked, "on your never-write-to list")):
            if count:
                reasons.append(f"{count}: {words}")
        return (f"{self.drafted} drafted from {self.harvested} listings "
                f"({', '.join(reasons)})")


def wanted(listing, settings: dict) -> bool:
    """Whether this advert is the kind the person asked to write to: from an
    employer, a recruitment agency or either, and on the terms they picked.
    An advert that doesn't say its terms is kept; unknown is never hidden."""
    audience = settings.get("audience") or "both"
    kind = getattr(listing, "advertiser", "") or "employer"
    if audience == "employers" and kind == "agency":
        return False
    if audience == "recruiters" and kind != "agency":
        return False
    terms = [t for t in (settings.get("work_types") or "").split(",") if t]
    contract = getattr(listing, "contract", "") or ""
    if terms and contract and contract not in terms:
        return False
    return True


def credentials() -> harvest.Credentials:
    return harvest.Credentials(adzuna_app_id=config.ADZUNA_APP_ID,
                               adzuna_app_key=config.ADZUNA_APP_KEY,
                               reed_api_key=config.REED_API_KEY)


def run_for_user(user_id: int, *, ai=None, session=None,
                 cap: int = DEFAULT_DRAFT_CAP, delay: float | None = None,
                 interactive: bool = False, on_step=None) -> RunReport:
    """Produce drafts for one user. Never raises for one bad listing.

    `interactive` says a person is waiting on this. It picks the model caller
    that refuses to sit out a rate limit, because the server has one worker
    and a sleeping request blocks the whole app.

    `on_step(text, done=, total=, drafted=)` is called as the run moves
    between stages, so a screen can say what is happening. A callback rather
    than anything this module knows about: the scheduled sweep passes nothing
    and behaves exactly as it always has, and nothing in here has to learn
    what a web page is.
    """
    report = RunReport()

    def step(text, **counts):
        if on_step:
            try:
                on_step(text, **counts)
            except Exception:
                # Reporting is not the work. If the commentary breaks, the
                # letters still get written.
                pass

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
        from .ai import gemini, gemini_now, scorer, scorer_now
        ai = gemini_now if interactive else gemini
        score_ai = scorer_now if interactive else scorer
    else:
        # A caller that brings its own model means it for everything.
        score_ai = ai
    kwargs = {} if delay is None else {"delay": delay}

    seen = db.seen_ids(user_id)
    # How far back to look is the user's call. Two days by default, because a
    # listing posted this morning has ten applicants and one from three weeks
    # ago has three hundred - but a new account with an empty queue has every
    # reason to widen it once and catch up on what it missed.
    settings = db.get_send_settings(user_id)
    step(f"Searching the job boards for the last "
         f"{settings['search_days']} days")
    found = harvest.harvest(profile, credentials(), session=session,
                            known_ids=seen,
                            exclude_titles=profile.exclude_titles,
                            max_age_hours=settings["search_days"] * 24)
    report.harvested = len(found["keep"]) + len(found["dropped"])
    report.prefiltered = len(found["dropped"])
    for listing in found["dropped"]:
        db.mark_seen(user_id, listing.external_id, listing.skipped)

    # Drop anything belonging to an employer already written to, or blocked,
    # BEFORE scoring. There is no sense paying a model to judge a listing that
    # could never be sent.
    step(f"Found {report.harvested} listings. Setting aside employers you "
         f"have already written to")
    candidates = []
    for listing in found["keep"]:
        # Not marked seen: the person can change who they write to, and a job
        # set aside for that reason should come back if they do.
        if not wanted(listing, settings):
            report.other_kind += 1
            continue
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

    candidates = _measure(user_id, candidates, profile, session, report)
    candidates = _still_trading(user_id, candidates, report, session)

    judged = scoring.score(
        candidates, profile, score_ai,
        on_batch=lambda done, total: step(
            f"Scoring {total} job{'' if total == 1 else 's'} against your "
            f"profile", done=done, total=total))
    for listing in judged["rejected"]:
        report.scored_out += 1
        db.mark_seen(user_id, listing.external_id, listing.skipped)

    # Asked once, before the loop, because it cannot change mid-run and the
    # sign-off has to state it truthfully on every letter. A CV is NOT
    # required to get this far - run_for_user needs a profile and nothing
    # else - and the setup screen used to demand one first anyway, which is
    # where nine accounts out of nine stopped.
    cv_attached = bool(db.get_cv(user_id))

    # No more letters written than can go out today: each one is a model
    # call, and a letter written tomorrow is written about a fresher advert.
    cap = min(cap, max(1, settings["daily_cap"]))
    shortlist = judged["passed"][:cap]
    for i, listing in enumerate(shortlist):
        if report.drafted >= cap:
            break
        # Named, because "Writing to Kestrel Foods" is the moment somebody
        # watching stops wondering whether it works. A bare percentage never
        # does that.
        step(f"Finding a real address at {listing.company or 'the employer'}",
             done=i, total=len(shortlist), drafted=report.drafted)
        try:
            _draft_one(user_id, listing, profile, ai, session, report,
                       cv_attached=cv_attached, **kwargs)
        except Exception as exc:
            # One bad listing must never lose the rest of the run.
            report.errors.append(f"{listing.external_id}: {exc}")
            db.mark_seen(user_id, listing.external_id, f"error: {exc}"[:200])

    step("Finishing up", done=len(shortlist), total=len(shortlist),
         drafted=report.drafted)
    return report


def _measure(user_id, listings, profile, session, report) -> list:
    """Put a distance on every listing, and set aside the ones the boards
    leaked from well beyond the person's travel radius.

    The radius is measured from each place they search, not only home: a
    person in Aberdeen who also searches Edinburgh wants Edinburgh jobs, and
    those are 120 miles from home. The number on the card is from home,
    because that is the journey that matters to them.

    Nothing is set aside unless every place they search could be found - a
    search area of "United Kingdom" has no centre to measure from - or unless
    the job itself could be found. Unknown is shown, never hidden.
    """
    if not geo.enabled() or not listings:
        return listings
    get = session.get if session is not None else None
    home = geo.locate(profile.location, get=get)
    areas = [geo.locate(place, get=get) for place in profile.locations]
    can_filter = bool(areas) and all(areas)
    limit = profile.radius_miles + geo.slack(profile.radius_miles)

    kept = []
    for listing in listings:
        where = geo.where_listing_is(listing, get=get)
        if where and home:
            listing.distance_miles = round(geo.miles(home, where))
        if where and can_filter:
            nearest = min(geo.miles(a, where) for a in areas)
            if nearest > limit:
                report.too_far += 1
                db.mark_seen(user_id, listing.external_id,
                             f"about {round(nearest)} miles from where you "
                             f"search, beyond your {profile.radius_miles}-mile "
                             "limit")
                continue
        kept.append(listing)
    return kept


def _still_trading(user_id, listings, report, session) -> list:
    """Set aside adverts from employers Companies House says are gone -
    dissolved, in liquidation - before a model is paid to read them. Only a
    single exact name match counts; anything less keeps the job. Dormant
    without a Companies House key."""
    if not companies_house.enabled() or not listings:
        return listings
    get = session.get if session is not None else None
    kept = []
    for listing in listings:
        status = companies_house.gone(listing.company or "", get=get)
        if status:
            report.dissolved += 1
            db.mark_seen(user_id, listing.external_id,
                         f"Companies House lists the employer as {status}")
            continue
        kept.append(listing)
    return kept


def _draft_one(user_id, listing, profile, ai, session, report,
               cv_attached=True, **kwargs):
    contact = discover.discover(listing, profile, session=session, **kwargs)
    if not contact:
        report.no_address += 1
        db.mark_seen(user_id, listing.external_id,
                     "no real email address could be found - nothing is guessed")
        return
    # The address is only known now, and a second name for an employer
    # already written to (an agency's trading name, a parent company) is
    # caught here, before a model is paid to write to them again.
    if db.mail_contacted(user_id, contact.get("email") or ""):
        report.already_contacted += 1
        db.mark_seen(user_id, listing.external_id,
                     "you have already written to this address or domain")
        return

    # A domain that takes no mail would bounce, and a bounce is counted
    # against the user's own mailbox. Checked before the model is paid to
    # write, and the job is kept - marked, with no model call spent on it -
    # so the user can see why it will not go and find another address.
    if mx.undeliverable(contact.get("email") or ""):
        letter = compose.plain_letter(listing, contact, profile, cv_attached)
        draft_id = _save_draft(user_id, listing, letter)
        db.mark_draft(user_id, draft_id, "undeliverable")
        db.mark_seen(user_id, listing.external_id,
                     "the employer's email domain does not accept mail")
        report.undeliverable += 1
        return

    letter = compose.compose(listing, contact, profile, ai,
                             cv_attached=cv_attached)
    if letter is None:
        # A quota is a daily ceiling and hitting it is a normal Tuesday. A
        # plainer letter to a verified address beats no letter at all.
        letter = compose.plain_letter(listing, contact, profile, cv_attached)
        report.fallback_used += 1

    # British spelling, fixed only where the fix is not a judgement call.
    # Grammar and style are logged, never applied. See proofread.py.
    letter = proofread.letter(letter)

    # At a small firm a director is often the person hiring. Names only, as
    # a pointer on the card; never used to make an address.
    get = session.get if session is not None else None
    directors = ", ".join(companies_house.directors(listing.company or "",
                                                    get=get))
    _save_draft(user_id, listing, letter, directors)
    db.mark_seen(user_id, listing.external_id, "drafted")
    report.drafted += 1


def _save_draft(user_id, listing, letter, directors: str = "") -> int:
    return db.add_draft(
        user_id,
        job_title=listing.title, company=listing.company,
        location=listing.location, listing_url=listing.url,
        salary_text=_salary_text(listing), score=listing.score,
        score_reason=getattr(listing, "score_reason", "") or "",
        to_email=letter["to_email"], to_name=letter.get("to_name"),
        contact_tier=letter.get("contact_tier"),
        distance_miles=listing.distance_miles,
        directors=directors,
        advertiser=getattr(listing, "advertiser", "") or "",
        contract=getattr(listing, "contract", "") or "",
        subject=letter["subject"], body=letter["body"])


def _salary_text(listing) -> str:
    amount, unit = scoring.stated_pay(listing)
    if amount is None:
        # The boards' fields are empty on most adverts; the figure is often
        # in the text instead. Display only - see pay.py.
        found = pay.from_text(f"{listing.title}\n{listing.description or ''}")
        return pay.describe(*found) if found else ""
    if unit == "year":
        return f"£{amount:,.0f}"
    return f"£{amount:g} per {unit}"
