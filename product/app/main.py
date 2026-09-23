"""The app: sign in, pay, describe your search, work your drafts.

Run it:

    cd product
    DEV_MODE=1 BILLING_ENABLED=0 uvicorn app.main:app --reload

In that mode sign-in links print to the console and the paywall is open, so
the whole thing can be clicked through with no Stripe account and no mail
configuration.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # so `jobseeker` imports

from jobseeker.pipeline import contacts, discover  # noqa: E402
from jobseeker.profile import Profile, ProfileError, Role  # noqa: E402
# Underscored, and imported anyway on purpose: it is the pipeline's own answer
# to "is this a searchable place", and seeding a search area out of a CV has to
# use that answer rather than a second copy of it that can drift from it.
from jobseeker.profile import _not_a_place  # noqa: E402

from . import funnel  # noqa: E402
from . import referrals  # noqa: E402
from . import attribution  # noqa: E402
from . import admin as adminlib  # noqa: E402
from . import answers as answerlib  # noqa: E402
from . import auth, autosend, billing, config, cv as cvlib, db, delivery, ratelimit, vault  # noqa: E402
from . import indexnow  # noqa: E402
from . import runner  # noqa: E402
from . import study  # noqa: E402
from . import track_record  # noqa: E402
from . import views  # noqa: E402

log = logging.getLogger("recruited")

# The website is the half that takes money, so it is the half that must not
# start if it cannot honour a payment. Deliberately at import rather than in a
# startup hook: a process that cannot serve checkout should never bind a port.
config.check_billing_config()

app = FastAPI(title="Recruited", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def _asset_version() -> str:
    """A token that changes whenever the stylesheet does.

    The typeface and the rewritten landing page went live correctly and Harry
    still saw the old page, because /static/style.css goes out with no
    Cache-Control header at all. A browser given no instruction invents one -
    usually a tenth of the file's age - so a returning visitor keeps the old
    stylesheet for hours after a deploy, and the newer the file the longer
    they keep it.

    Appending this to the href means a changed stylesheet is a changed URL,
    which no cache can match. Nothing else is needed: the content is what
    versions it, so there is no number for anybody to remember to bump.

    From the file's own modification time rather than a hash, because the
    whole point is to cost nothing on a cold start, and a deploy rewrites
    every mtime. Falls back to a constant if the file cannot be stat-ed, in
    which case caching behaves exactly as it does today rather than breaking.
    """
    try:
        return str(int((HERE / "static" / "style.css").stat().st_mtime))
    except OSError:
        return "0"


ASSET_VERSION = _asset_version()


def _ago(stamp) -> str:
    """"twenty minutes ago", not "2026-09-15 08:53".

    A timestamp makes the reader do arithmetic to answer the only question
    they actually have, which is whether this thing is still alive. Rounded
    deliberately: "about an hour ago" is as much precision as the answer to
    that question needs, and pretending to more of it from a schedule that
    runs late anyway would be false confidence.
    """
    try:
        seconds = db.now() - int(stamp or 0)
    except (TypeError, ValueError):
        return ""
    if not stamp or seconds < 0:
        return ""
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return "about an hour ago" if hours == 1 else f"{hours} hours ago"
    days = hours // 24
    return "yesterday" if days == 1 else f"{days} days ago"


templates.env.filters["ago"] = _ago

SESSION_COOKIE = "jm_session"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    ratelimit.init()
    # Told once per deploy that adds pages, or per day the figures move -
    # never on an ordinary wake from sleep, because the fingerprint has not
    # changed. See indexnow.submit_if_changed. It cannot raise and it cannot
    # delay a request: the only caller that pays for it is the first boot
    # after something actually changed.
    print(f"[indexnow] {indexnow.submit_if_changed(public_urls(), get_meta=db.get_meta, set_meta=db.set_meta)}")
    yield


app.router.lifespan_context = lifespan


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def current_user(request: Request):
    """The signed-in user, and the one place that knows somebody came back.

    The touch lives here rather than in middleware for the same reason the
    view counter lives in render(): this is the single door every
    authenticated request already goes through, so static files, redirects
    and the health check cannot be mistaken for a person using the app.

    It is throttled in db.touch_user, so a screen polled every few seconds
    costs one write a quarter of an hour rather than one a poll.
    """
    uid = auth.read_session(request.cookies.get(SESSION_COOKIE))
    if not uid:
        return None
    user = db.get_user(uid)
    if user:
        db.touch_user(user["id"], user["last_seen_at"])
    return user


def render(request: Request, template: str, **ctx):
    user = ctx.pop("user", None)
    if user is None:
        user = current_user(request)
    # Counted here rather than in middleware because this is already the one
    # door every HTML page goes through, which means static files, redirects
    # and the health check cannot accidentally be counted as readers.
    #
    # GET only, deliberately. render() also answers a POST - /find returns its
    # results through it - and counting those would make one person who pasted
    # an advert look like two arrivals, inflating the exact number this exists
    # to measure honestly.
    #
    # Guarded at both ends of the chain, like the progress bar. record() keeps
    # its own failures to itself, but the path and header reads happen out
    # here, and a counter is never worth a blank page.
    if request.method == "GET":
        try:
            views.record(request.url.path,
                         user_agent=request.headers.get("user-agent", ""),
                         referer=request.headers.get("referer", ""),
                         host=request.headers.get("host", ""))
        except Exception:
            pass
    response = templates.TemplateResponse(
        request, template,
        {"user": user, "paid": db.is_paid(user), "config": config,
         "is_admin": bool(user and config.is_admin(user["email"])),
         # Every page, because the meta description in base.html quotes it and
         # base.html is every page. Cached on the file's mtime, so this is a
         # dictionary lookup rather than a read. None if nothing is published,
         # and each template is written to make no claim in that case.
         "record": track_record.read(),
         # Appended to the stylesheet's URL so a changed file is a changed
         # URL. See _asset_version for the deploy this was invisible on.
         "asset_version": ASSET_VERSION, **ctx})
    # First touch, here, because this is the same single door: a campaign tag
    # has to be read on the page somebody LANDS on, not where they sign up.
    # By the time they tap the link in their inbox the request carries no
    # referrer and no campaign, so reading it there reports every user as
    # direct - including the ones a channel worked for. See attribution.py.
    #
    # Wrapped, and after the response exists, for the same reason the counter
    # above is: knowing where a visitor came from is never worth a blank page.
    if request.method == "GET":
        try:
            attribution.stamp(request, response)
        except Exception:
            pass
    return response


def needs_login(request: Request | None = None):
    """Where an anonymous visitor goes when they ask for a private screen.

    The landing page, NOT the sign-in form, and the reason is 27 people.

    The Snapchat story linked to /dashboard?utm_source=snapchat - somebody
    copied the address out of their own browser while signed in and looking at
    it, which is the most natural mistake there is. Every stranger who tapped
    that link was bounced straight to a form asking for their email address,
    for a product they had never heard of, having never seen the front page,
    the method or a single number. Twenty-seven of them. Three signed up.

    A person who already has an account loses almost nothing: the landing page
    leads with "Start free", which is the same form one tap later. A person
    who has never been here gains the entire argument. So the default is the
    page that explains what this is.

    Kept as a redirect rather than rendering the landing page in place,
    because the address in the bar has to stop saying /dashboard - otherwise
    a refresh, a share or a back button lands them right back on the wall.
    """
    return RedirectResponse("/", status_code=303)


# ----------------------------------------------------------------------
# public
# ----------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "landing.html", spots_left=db.free_spots_left())


# ----------------------------------------------------------------------
# the free tool
# ----------------------------------------------------------------------
# Paste a job advert, find out whether there is a real person to write to.
#
# It exists to be given away. The hardest and most valuable thing this
# product does is find an address nobody guessed, and no amount of landing
# copy demonstrates that as well as doing it once, for free, on an advert
# the visitor chose themselves.
#
# Deliberately it reads only the pasted text. It does not look the company
# up and it does not fetch anything, which means: no API cost, no scraping
# somebody else's site on an anonymous stranger's say-so, nothing that can
# be pointed at a third party as a denial of service, and no request that
# can sit for ten seconds holding the one worker this app has. The site
# lookup is the part you sign up for.
FIND_PER_IP = (30, 3600)
MAX_ADVERT = 20_000        # a long advert is 5k; past this it is an attack


@app.get("/find", response_class=HTMLResponse)
def find_form(request: Request):
    return render(request, "find.html")


@app.post("/find", response_class=HTMLResponse)
async def find_submit(request: Request):
    form = await request.form()
    advert = (form.get("advert") or "")[:MAX_ADVERT]
    if not advert.strip():
        return render(request, "find.html",
                      error="Paste the advert text first.")

    limit, window = FIND_PER_IP
    if not ratelimit.hit(f"find:ip:{ratelimit.client_ip(request)}",
                         limit=limit, window=window):
        return render(request, "find.html", advert=advert,
                      error="That is a lot of adverts in an hour. Try again "
                            "later, or sign up and let it run on its own.")

    found = contacts.rank(contacts.clean_emails(discover.emails_in(advert)))
    return render(request, "find.html", advert=advert, found=found,
                  searched=True)


@app.get("/answers", response_class=HTMLResponse)
def answers_index(request: Request):
    return render(request, "answers_index.html", answers=answerlib.ANSWERS,
                  og_title="Straight answers for jobseekers",
                  og_description="Why applications go unanswered, how to find "
                                 "a real address, and the numbers behind both.")


@app.get("/answers/{slug}", response_class=HTMLResponse)
def answer_page(request: Request, slug: str):
    """One question, answered, with the figures behind it.

    An assistant that recommends something is not remembering it - it runs a
    search as it answers and quotes what comes back. So the page that gets
    recommended is the one that answers the literal question, which the
    playbook could not be: it is one address holding five different answers
    inside a <pre> block. See answers.py.
    """
    answer = answerlib.get(slug)
    if not answer:
        return PlainTextResponse("Not found", status_code=404)
    return render(request, "answer.html", answer=answer,
                  og_title=answer.question,
                  og_description=answer.blurb)


@app.get("/playbook", response_class=HTMLResponse)
def playbook(request: Request):
    """The method, free, for everyone. It is the best advertisement the paid
    product has, and holding it back would not sell a single subscription."""
    text = (HERE.parent / "PLAYBOOK.md").read_text(encoding="utf-8")
    return render(request, "playbook.html", playbook=text)


# Reachable without an account, deliberately. Somebody deciding whether to
# hand over a CV and a mailbox password has to be able to read what happens
# to them first, and a privacy notice behind a sign-in wall is not a notice.
@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return render(request, "privacy.html")


@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return render(request, "terms.html")


@app.get("/numbers", response_class=HTMLResponse)
def numbers(request: Request):
    """Everything this product has actually done, live, for anybody.

    Public on purpose and uncomfortable on purpose. The whole pitch rests on
    a number, and a number nobody can check is a claim. This is the page that
    makes it checkable - including when the answer is zero, which is what it
    says today.
    """
    return render(request, "numbers.html", stats=db.public_stats(),
                  og_title="What Recruited has actually done",
                  og_description="Every letter sent through Recruited, live. "
                                 "Including when the answer is none.")


@app.get("/numbers.json")
def numbers_json():
    """The same figures, machine-readable, for anybody who wants to check or
    cite them without parsing a page.

    The whole argument this site makes is that its numbers can be checked, and
    a number that can only be checked by a human reading HTML is one a machine
    will quote without checking. This is the cheap fix: one address, three
    clearly separated sets of figures, and the definition of a reply printed
    next to each rate rather than assumed.

    The separation is the point. The 86-email study and the live counter
    measure different things and report different rates - 26% against 19% -
    and anybody lifting one of them needs to be told which. See study.py.
    """
    return {
        "source": config.BASE_URL,
        "licence": "CC BY 4.0 - use it, say where it came from.",
        # A fixed, finished sample. It will never change again.
        "study": study.as_dict(),
        # The founder's own job hunt, still running, published by the machine
        # that does it. Absent rather than zeroed when there is nothing to
        # say: a missing key is honest, a zero is a claim.
        "founder_live": _founder_live(),
        # What the product itself has sent for its users, which is a different
        # question again and currently a smaller number.
        "product_live": db.public_stats(),
    }


def _founder_live():
    record = track_record.read()
    if not record:
        return None
    return {
        **record,
        "reply_definition": "A message from a human that was not a rejection. "
                            "Automated acknowledgements and rejections are "
                            "counted separately and excluded here, so this "
                            "rate is lower than the study's and stricter.",
    }


# ----------------------------------------------------------------------
# sign in
# ----------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html", spots_left=db.free_spots_left())


# Deliberately generous for a real person and useless for a script. An honest
# user asks once, twice if the first went to spam.
LOGIN_PER_EMAIL = (5, 3600)      # 5 an hour to any one address
LOGIN_PER_IP = (20, 3600)        # 20 an hour from any one machine


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form("")):
    if not auth.valid_email(email):
        return render(request, "login.html", spots_left=db.free_spots_left(),
                      error="That does not look like an email address. It "
                            "needs an @ in it, like you@example.com.")

    address = email.strip().lower()
    limit, window = LOGIN_PER_EMAIL
    ip_limit, ip_window = LOGIN_PER_IP
    allowed = ratelimit.hit(f"login:email:{address}", limit=limit, window=window)
    allowed &= ratelimit.hit(f"login:ip:{ratelimit.client_ip(request)}",
                             limit=ip_limit, window=ip_window)
    if not allowed:
        # Same wording as success. Saying "rate limited" would confirm the
        # address exists, and would tell an abuser exactly what they hit.
        return render(request, "login.html", sent=email.strip())

    try:
        auth.send_login_email(address, auth.make_login_link(address))
    except Exception:
        # The user is told nothing useful on purpose, but the operator has to
        # be able to tell a rejected password from a blocked port. Without
        # this the only symptom is a red box and an empty log.
        log.exception("sign-in email could not be sent")
        return render(request, "login.html", spots_left=db.free_spots_left(),
                      error="Something went wrong sending that email. It is "
                            "our end, not yours. Try again in a minute.")
    # Always the same reply, whether or not the address has an account: the
    # response must not reveal who is a customer.
    #
    # What changed is only the WORDING. It used to hedge - "if that address
    # has an account, a link is on its way" - which is the standard phrasing
    # for a product where signing up and signing in are separate acts. Here
    # they are the same act: a link goes to any valid address, and tapping it
    # makes the account. So the hedge protected nothing and cost a great deal,
    # because the person most likely to read it is the one who has just
    # arrived, knows perfectly well they have no account, and reasonably
    # concludes that nothing was sent to them.
    #
    # The property that actually matters - an identical response either way -
    # is untouched, and asserted in the tests.
    return render(request, "login.html", sent=email.strip())


def _landing_for(user) -> str:
    """Where a signed-in person should be put.

    Somebody with no profile has nothing to look at on the dashboard except a
    note telling them so. Send them to the thing that needs doing; the CV
    upload there fills in most of the next screen on its own.
    """
    return "/dashboard" if db.load_profile(user["id"]) else "/setup"


@app.get("/auth/verify")
def verify(request: Request, token: str = ""):
    email = auth.consume_login_token(token)
    if not email:
        # A used link plus a live session is by far the commonest way to get
        # here, and it is not a failure: somebody goes back to their inbox and
        # taps the same link again. Sign-in links are single use - which is
        # right, and stays right - so the second tap is refused, and until now
        # that put a signed-in person on a login screen being told to ask for
        # a link they did not need. It is the reason the whole flow reads as
        # "an email every time" when the session actually lasts a month.
        #
        # Checked in this order deliberately. The token is consumed first, so
        # a genuinely expired link is still spent rather than left usable, and
        # the session below is only a nicer landing for somebody who is
        # already authenticated. It grants nothing on its own.
        user = current_user(request)
        if user:
            return RedirectResponse(_landing_for(user), status_code=303)
        return render(request, "login.html", spots_left=db.free_spots_left(),
                      error="That link has already been used, or it is more "
                            "than fifteen minutes old. Pop your email in "
                            "again and we will send a fresh one.")
    existed = db.get_user_by_email(email) is not None
    user = db.get_or_create_user(email)
    # A free launch place is taken by a new account, not by anybody who signs
    # in again. Claiming on every sign-in would hand a place to somebody who
    # already decided not to pay, which is the opposite of what it is for.
    if not existed:
        db.claim_free_spot(user["id"])
        # Where they came from, read off the cookie set on the page they first
        # landed on. This request cannot answer it: it arrived from a mail
        # client, so it carries no referrer and no campaign.
        #
        # New accounts only, and set_user_source will not overwrite either.
        # A returning user signing in from a different link is the same user,
        # and re-attributing them would move a sign-up between channels weeks
        # later and make a report that has already been read change.
        try:
            source = attribution.read(request)
            db.set_user_source(user["id"], source)
            db.record_event(user["id"], "signed_up",
                            detail=attribution.describe(source))
            referrals.credit_signup(user["id"], source.get("ref", ""))
        except Exception:
            # Never between a person and their account.
            log.exception("could not record the sign-up")
    response = RedirectResponse(_landing_for(user), status_code=303)
    response.set_cookie(
        SESSION_COOKIE, auth.make_session(user["id"]),
        max_age=config.SESSION_MAX_AGE, httponly=True, samesite="lax",
        secure=not config.DEV)
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ----------------------------------------------------------------------
# the app proper
# ----------------------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return needs_login()
    if not db.is_paid(user):
        return render(request, "paywall.html", user=user)
    return render(request, "dashboard.html", user=user,
                  profile=db.load_profile(user["id"]),
                  counts=db.counts(user["id"]),
                  drafts=db.list_drafts(user["id"], limit=5),
                  # Whether the machine is actually on, and the last thing it
                  # did. Every figure something that happened, never a
                  # prediction - see db.machine_status.
                  machine=db.machine_status(user["id"]),
                  # Rendered server-side as well as polled, so the panel is
                  # right on first paint and a browser with no JavaScript
                  # still sees where a run has got to on a refresh.
                  progress=db.run_progress(user["id"]))


@app.post("/run")
def run_now(request: Request):
    """Look for work now, rather than waiting for the next scheduled sweep.

    Starts the work and returns straight away, rather than holding the request
    open until it finishes. A run is minutes long - the job boards, then a
    free-tier model reading every listing a batch at a time - and the old
    version simply did not answer for all of it. On a phone that is a white
    screen with a spinner, then very often a proxy timeout and an error page
    for a run that actually worked.

    Returning immediately also means the person can leave. Lock the phone,
    come back, and the dashboard picks the progress back up, because it lives
    in the database rather than in this request.
    """
    user = current_user(request)
    if not user:
        return needs_login()
    if not db.is_paid(user):
        return render(request, "paywall.html", user=user)

    _start_run(user["id"])
    return RedirectResponse("/dashboard", status_code=303)


def _start_run(user_id: int) -> bool:
    """Start a search in the background. False if one is already going.

    Shared by the "look for work now" button and the quick start, because
    the quick start's whole point is that nobody should finish setting up and
    then land on an empty dashboard waiting for a sweep that runs hours
    later. The first thing after answering six questions should be letters
    being written.
    """
    if not db.start_run(user_id):
        # Already going. Not an error and not a second run - two taps half a
        # second apart on a phone is the normal case, not the odd one.
        return False

    def work():
        try:
            # A person is watching, so rate limits must not be waited out.
            report = runner.run_for_user(
                user_id, interactive=True,
                on_step=lambda text, **counts: db.set_run_step(
                    user_id, text, **counts))
            print(f"[run] user {user_id}: {report.summary()}")
            db.finish_run(user_id, result=report.summary(),
                          drafted=report.drafted)
        except Exception as exc:
            # This thread is nobody's caller, so an exception here would
            # otherwise vanish into the log and leave the page waiting for a
            # run that has already died.
            print(f"[run] user {user_id} failed: {exc}")
            db.finish_run(user_id, result=f"It stopped: {exc}", ok=False)

    threading.Thread(target=work, daemon=True,
                     name=f"run-{user_id}").start()
    return True


@app.get("/run/progress")
def run_progress(request: Request):
    """What the run is doing, for the bar on the dashboard to read.

    Deliberately small and deliberately boring: it is polled every couple of
    seconds by anybody watching a run, so it does one indexed lookup by
    primary key and returns a handful of fields.
    """
    user = current_user(request)
    if not user:
        return JSONResponse({"state": "none"}, status_code=401)
    return JSONResponse(db.run_progress(user["id"]) or {"state": "none"})


@app.get("/profile", response_class=HTMLResponse)
def profile_form(request: Request):
    user = current_user(request)
    if not user:
        return needs_login()
    if not db.is_paid(user):
        return render(request, "paywall.html", user=user)
    # Readable text, not merely a file: offering to fill this in from a CV we
    # cannot read is an offer that ends back on this page with an apology.
    row = db.get_cv(user["id"])
    return render(request, "profile.html", user=user,
                  data=db.load_profile(user["id"]) or {},
                  cv=bool(row and row["extracted"]))


@app.post("/profile", response_class=HTMLResponse)
async def profile_save(request: Request):
    user = current_user(request)
    if not user:
        return needs_login()
    if not db.is_paid(user):
        return render(request, "paywall.html", user=user)

    form = await request.form()
    data = _profile_from_form(form)

    # Validated through exactly the same Profile the engine uses, so the web
    # form cannot save something the pipeline would later choke on.
    try:
        Profile.from_dict(data)
    except ProfileError as exc:
        return render(request, "profile.html", user=user, data=data,
                      error=str(exc))

    db.save_profile(user["id"], data)
    # A saved profile is what starts the machine, so it is what "onboarded"
    # means. Idempotent - editing the profile later records nothing new.
    funnel.reached(user["id"], "onboarded", detail="full form")
    return RedirectResponse("/dashboard", status_code=303)


def _lines(raw: str) -> list[str]:
    """One entry per line. For fields whose entries are sentences.

    never_claim and qualifications are written as prose - "a completed degree,
    which is paused rather than finished" - so a comma is punctuation here and
    splitting on it would chop one rule into two half-rules.
    """
    return [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]


def _items(raw: str) -> list[str]:
    """One entry per line OR per comma. For lists of short things.

    Nobody types four towns on four lines when a comma is right there, and
    "Aberdeen, Edinburgh" arriving as a single location searched for a place
    of that name and found nothing. Accepting the separator people actually
    use is cheaper than teaching them not to.
    """
    out = []
    for line in (raw or "").splitlines():
        for part in line.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _profile_from_form(form) -> dict:
    def s(name, default=""):
        return (form.get(name) or default).strip()

    def i(name):
        try:
            return int(str(form.get(name) or "0").replace(",", "").replace("£", ""))
        except ValueError:
            return 0

    history = []
    for title, org, detail in zip(form.getlist("h_title"),
                                  form.getlist("h_org"),
                                  form.getlist("h_detail")):
        if title.strip() and org.strip():
            history.append({"title": title.strip(), "org": org.strip(),
                            "detail": detail.strip()})

    priorities = [p for p in form.getlist("priorities") if p]
    return {
        "name": s("name"), "location": s("location"), "phone": s("phone"),
        "email": s("email"),
        "situation": s("situation", "unemployed"),
        "current_salary": i("current_salary"),
        "may_name_employer": bool(form.get("may_name_employer")),
        "min_salary_annual": i("min_salary_annual"),
        "min_rate_hourly": i("min_rate_hourly"),
        "priorities": priorities,
        "wants_travel": "travel" in priorities,
        "wants_contract": "contract" in priorities,
        "history": history,
        "qualifications": _lines(s("qualifications")),
        "never_claim": _lines(s("never_claim")),
        "locations": _items(s("locations")),
        "radius_miles": i("radius_miles") or 25,
        "target_roles": _items(s("target_roles")),
    }


# ----------------------------------------------------------------------
# drafts
# ----------------------------------------------------------------------
@app.get("/drafts", response_class=HTMLResponse)
def drafts(request: Request, status: str = "draft"):
    user = current_user(request)
    if not user:
        return needs_login()
    if not db.is_paid(user):
        return render(request, "paywall.html", user=user)
    if status not in ("draft", "sent", "discarded"):
        status = "draft"

    rows = db.list_drafts(user["id"], status=status)
    items = [{
        "row": r,
        "mailto": delivery.mailto_link(r["to_email"], r["subject"], r["body"]),
        "gmail": delivery.gmail_compose_link(r["to_email"], r["subject"], r["body"]),
    } for r in rows]
    return render(request, "drafts.html", user=user, items=items, status=status)


@app.get("/applications", response_class=HTMLResponse)
def applications(request: Request):
    """Every letter that has gone, and what came of it."""
    user, blocked = _gate(request)
    if blocked:
        return blocked
    rows = db.applications(user["id"])
    stamp = db.now()
    items = [{
        "row": r,
        # Days waiting, which is the actionable number on this screen: it is
        # what tells somebody it is time to chase rather than keep waiting.
        "days": int((stamp - (r["sent_at"] or stamp)) // 86400),
    } for r in rows]
    return render(request, "applications.html", user=user, items=items,
                  stats=db.application_stats(user["id"]),
                  # What their own sending says, rather than what we believe.
                  working=db.what_is_working(user["id"]),
                  outcomes=db.OUTCOMES)


@app.post("/applications/{draft_id}/outcome")
async def set_outcome(request: Request, draft_id: int):
    user, blocked = _gate(request)
    if blocked:
        return blocked
    form = await request.form()
    db.set_outcome(user["id"], draft_id, form.get("outcome") or "")
    return RedirectResponse("/applications", status_code=303)


@app.post("/drafts/{draft_id}/block")
def block_employer(request: Request, draft_id: int):
    """'Never write to this company again.' Deliberately one click, and
    deliberately not reversible from the interface."""
    user = current_user(request)
    if not user:
        return needs_login()
    row = db.get_draft(user["id"], draft_id)
    if row:
        db.block_company(user["id"], row["company"], reason="asked by user")
        db.mark_draft(user["id"], draft_id, "discarded")
    return RedirectResponse("/drafts", status_code=303)


@app.post("/drafts/{draft_id}/{action}")
def draft_action(request: Request, draft_id: int, action: str):
    user = current_user(request)
    if not user:
        return needs_login()
    if action not in ("sent", "discarded"):
        return RedirectResponse("/drafts", status_code=303)

    row = db.get_draft(user["id"], draft_id)
    if row:
        db.mark_draft(user["id"], draft_id, action)
        if action == "sent":
            # One email per employer, ever - recorded the moment the user
            # says they sent it, not when it was drafted.
            db.record_contacted(user["id"], row["company"])
            db.record_mail_contacted(user["id"], row["to_email"] or "")
            # Sending it yourself IS sending. Until this, only automatic
            # sends reached the funnel, so the path that needs no mailbox -
            # the one most people will take - counted as nobody activating.
            funnel.reached(user["id"], "first_email_sent",
                           detail=f"by hand: {row['company'] or ''}")
    return RedirectResponse("/drafts", status_code=303)


# ----------------------------------------------------------------------
# billing
# ----------------------------------------------------------------------
@app.get("/billing/checkout")
def checkout(request: Request):
    user = current_user(request)
    if not user:
        return needs_login()

    # A Payment Link needs no API call at all - Stripe already hosts the page.
    # The user id rides along as client_reference_id so the webhook can tell
    # whose payment it was.
    if config.STRIPE_PAYMENT_LINK:
        return RedirectResponse(config.payment_link_for(user["id"]),
                                status_code=303)

    try:
        url = billing.create_checkout_session(user["email"], user["id"])
    except billing.BillingError as exc:
        return render(request, "paywall.html", user=user, error=str(exc))
    return RedirectResponse(url, status_code=303)


@app.get("/billing/done", response_class=HTMLResponse)
def billing_done(request: Request, ok: str = "0"):
    user = current_user(request)
    if not user:
        return needs_login()
    # Note what this does NOT do: it does not mark the user paid. Only a
    # verified webhook does that. This page just says what happened.
    return render(request, "billing_done.html", user=user, ok=(ok == "1"))


@app.get("/account", response_class=HTMLResponse)
def account(request: Request):
    user = current_user(request)
    if not user:
        return needs_login()
    portal = None
    if user["stripe_customer_id"]:
        try:
            portal = billing.create_portal_session(user["stripe_customer_id"])
        except billing.BillingError:
            portal = None
    return render(request, "account.html", user=user, portal=portal)


@app.get("/account/delete", response_class=HTMLResponse)
def delete_form(request: Request):
    user = current_user(request)
    if not user:
        return needs_login()
    return render(request, "delete.html", user=user,
                  counts=db.counts(user["id"]))


@app.post("/account/delete")
def delete_account(request: Request, confirm: str = Form("")):
    """Erase the account. Billing stops first, then the data goes.

    Order matters and is not arbitrary: cancelling at Stripe after the delete
    would leave a live subscription with nobody attached to it, quietly
    charging someone whose account no longer exists.
    """
    user = current_user(request)
    if not user:
        return needs_login()

    if confirm.strip().lower() != "delete":
        return render(request, "delete.html", user=user,
                      counts=db.counts(user["id"]),
                      error='Type "delete" to confirm.')

    if user["stripe_subscription_id"] and config.BILLING_ENABLED:
        try:
            billing.cancel_subscription(user["stripe_subscription_id"])
        except billing.BillingError as exc:
            # Refuse rather than delete: an orphaned live subscription is
            # worse than an account that outlived its owner's patience.
            return render(request, "delete.html", user=user,
                          counts=db.counts(user["id"]),
                          error="Your subscription could not be cancelled, so "
                                "nothing was deleted. Nobody will be charged "
                                f"for an account that no longer exists. ({exc})")

    db.delete_user(user["id"])
    response = RedirectResponse("/?deleted=1", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    try:
        event = billing.verify_webhook(
            payload, request.headers.get("stripe-signature", ""))
    except billing.BillingError as exc:
        # 400 tells Stripe to retry. Never act on an unverified payload.
        return PlainTextResponse(f"rejected: {exc}", status_code=400)
    return PlainTextResponse(billing.apply_event(event), status_code=200)


@app.get("/sw.js")
def service_worker():
    """Served from the root, not from /static/, and versioned by the code.

    A service worker only controls URLs at or below its own path, so one
    living at /static/sw.js could never control /dashboard - it would register
    without error and then do nothing, which is the most annoying kind of
    broken.

    THE VERSION IS SUBSTITUTED HERE RATHER THAN TYPED IN THE FILE.

    sw.js caches /static/ cache-first, and only drops caches whose key is not
    the current VERSION. So a stylesheet that changes without a matching bump
    is never seen again by anybody who has already loaded the app. The file
    said exactly that, in a comment, warning that the last redraw was
    invisible to every returning visitor until the constant went to v2.

    It was then forgotten the very next time the stylesheet changed - a new
    typeface and a rewritten landing page shipped, went live correctly, and
    Harry's phone kept serving the old one. A rule that is documented and
    still missed is not a rule, it is a trap.

    So the constant is now derived from the stylesheet's own modification
    time, which a deploy always changes. There is nothing left to remember.
    """
    from fastapi.responses import Response
    source = (HERE / "static" / "sw.js").read_text(encoding="utf-8")
    return Response(
        source.replace("__ASSET_VERSION__", ASSET_VERSION),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/",
                 # Never cache the worker itself, or the mechanism that
                 # invalidates everything else is the one thing that cannot.
                 "Cache-Control": "no-cache"})


@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(HERE / "static" / "icons" / "favicon.ico",
                        media_type="image/x-icon")


@app.get("/manifest.webmanifest")
def manifest():
    from fastapi.responses import FileResponse
    return FileResponse(HERE / "static" / "manifest.webmanifest",
                        media_type="application/manifest+json")


@app.get("/app", response_class=HTMLResponse)
def install_page(request: Request):
    """How to get this onto a phone.

    A page rather than only a banner, because the banner was shown from
    inside the beforeinstallprompt handler and Safari has never fired that
    event - so every iPhone user was told nothing at all, on a product whose
    audience checks for replies on a phone. It is also dismissible with a
    "Not now" that never clears, so anybody who tapped it once could not find
    the thing again.

    Public on purpose. It costs nothing to let somebody read what installing
    involves before they have an account, and putting it behind the login
    would mean the first time anybody sees it is a moment they are already
    busy.
    """
    return render(request, "install.html")


@app.post("/app/installed")
def record_install(request: Request):
    """The browser saying the app is now on somebody's home screen.

    Worth recording because "do the people who install it stick around" is a
    question the retention figure cannot answer on its own, and it is the
    cheapest possible way to ask it.

    Anonymous callers are accepted and counted as nothing. The page is public,
    so this can be reached without a session, and refusing it would be an
    error in a browser console over a statistic.
    """
    user = current_user(request)
    if user:
        funnel.reached(user["id"], "installed")
    return Response(status_code=204)


@app.get("/status")
def status(request: Request):
    """Is this deployment actually configured correctly?

    Exists because the most expensive mistakes here are silent. An app on a
    free host with no DATABASE_URL runs perfectly and loses every customer on
    the next deploy. A wrong BASE_URL sends sign-in links that go nowhere. A
    missing webhook secret means everyone who pays is locked out. None of
    those look like anything from the outside.

    Deliberately readable without signing in, because the failures it
    diagnoses are the ones that stop you signing in. So it reports **no
    values** - no hostnames, no keys, no addresses. Only whether each thing is
    set, and what is wrong.
    """
    from . import store, vault

    problems, warnings = [], []

    on_postgres = store.IS_POSTGRES
    reachable = store.ping()
    if not on_postgres:
        problems.append(
            "No DATABASE_URL, so this is running on a local SQLite file. On a "
            "host without a persistent disk that file - and every customer in "
            "it - is deleted by the next deploy. Set DATABASE_URL to a "
            "Supabase connection string.")
    if not reachable:
        problems.append(
            "The database cannot be reached. If this is Supabase, check the "
            "connection string and that the project is not paused.")

    if config.BILLING_ENABLED:
        if config.STRIPE_PAYMENT_LINK:
            billing = "payment link"
        elif config.STRIPE_SECRET_KEY and config.STRIPE_PRICE_ID:
            billing = "stripe api"
        else:
            billing = "misconfigured"
            problems.append("Billing is on but no payment route is set.")
        if not config.STRIPE_WEBHOOK_SECRET:
            problems.append(
                "No STRIPE_WEBHOOK_SECRET. Access is granted only by a "
                "verified webhook, so without this everyone who pays you is "
                "locked out.")
    else:
        billing = "disabled"
        problems.append(
            "BILLING_ENABLED is off, so every signed-in account is treated as "
            "paid. Nobody has to pay you.")

    mail_route = config.mail_route()
    if not mail_route:
        problems.append(
            "No way to send sign-in email is configured, so nobody can get "
            "in. Set BREVO_API_KEY with APP_SMTP_ADDRESS to send over HTTPS, "
            "or APP_SMTP_ADDRESS with APP_SMTP_PASSWORD for SMTP.")
    elif mail_route == "smtp":
        warnings.append(
            "Sign-in email goes over SMTP. A free Render web service cannot "
            "reach ports 25, 465 or 587, and the failure is a silent timeout. "
            "Set BREVO_API_KEY to send over HTTPS instead.")

    # A wrong BASE_URL is invisible until a customer clicks a dead link.
    base_ok = None
    if config.BASE_URL:
        here = str(request.base_url).rstrip("/")
        base_ok = config.BASE_URL.rstrip("/") == here
        if not base_ok:
            problems.append(
                "BASE_URL does not match the address this page was served "
                "from, so sign-in links will point somewhere else. Set it to "
                "this app's own URL and redeploy.")
    else:
        problems.append("BASE_URL is not set, so sign-in links will be wrong.")

    if not vault.available():
        warnings.append(
            "No CREDENTIAL_KEY, so automatic sending is unavailable. Letters "
            "are still written and users send them by hand.")

    missing_apis = [name for name, value in (
        ("ADZUNA_APP_ID", config.ADZUNA_APP_ID),
        ("ADZUNA_APP_KEY", config.ADZUNA_APP_KEY),
        ("REED_API_KEY", config.REED_API_KEY),
        ("GEMINI_API_KEY", config.GEMINI_API_KEY)) if not value]
    if missing_apis:
        warnings.append(
            f"Not set: {', '.join(missing_apis)}. Without Adzuna and Reed "
            "there are no listings; without Gemini nothing is scored or "
            "written.")

    if config.DEV:
        problems.append(
            "DEV_MODE is on in a deployed app. Sign-in links print to the log "
            "instead of being emailed, and a missing SECRET_KEY is generated "
            "on each boot, which logs everyone out on every restart.")

    return {
        "ok": not problems,
        "storage": "postgres" if on_postgres else "sqlite (not persistent)",
        "database_reachable": reachable,
        "billing": billing,
        "webhook_secret_set": bool(config.STRIPE_WEBHOOK_SECRET),
        "free_accounts": len(config.FREE_ACCESS_EMAILS),
        "free_spots_left": db.free_spots_left(),
        "admins": len(config.ADMIN_EMAILS),
        "sign_in_email_configured": bool(mail_route),
        "sign_in_email_route": mail_route or "none",
        "automatic_sending": ("available" if vault.available()
                              else "unavailable"),
        "base_url_set": bool(config.BASE_URL),
        "base_url_matches_this_page": base_ok,
        "job_search_apis_missing": missing_apis,
        "dev_mode": config.DEV,
        "problems": problems,
        "warnings": warnings,
    }


@app.get("/healthz")
def healthz():
    return {"ok": True}


# The pages a search engine may have, and the ones it may not. The allow-list
# is explicit rather than "everything except": a new signed-in screen added
# later is private by default that way round, and public by default the other,
# and the wrong default here puts somebody's drafts in Google.
PUBLIC_PAGES = ("/", "/find", "/playbook", "/answers", "/numbers", "/terms",
                "/privacy", "/login", "/app") + tuple(answerlib.paths())


def public_urls() -> list[str]:
    """Every public page as an absolute address, for the sitemap's readers
    and for IndexNow. One list, so a page cannot be in one and not the
    other."""
    return [config.BASE_URL + path for path in PUBLIC_PAGES]


# Serving the key is what proves the domain is ours, so the route only exists
# when there is a key to serve. A 404 here and a submission carrying the same
# key is how a key gets ignored.
if config.INDEXNOW_KEY:
    @app.get(f"/{indexnow.key_filename()}", response_class=PlainTextResponse)
    def indexnow_key():
        return config.INDEXNOW_KEY


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    lines = ["User-agent: *"]
    # Disallow by prefix, so /setup/mail and /applications/3/outcome are
    # covered without listing every route that will ever exist.
    for path in ("/dashboard", "/drafts", "/applications", "/setup", "/profile",
                 "/account", "/admin", "/cv", "/auth", "/billing", "/status"):
        lines.append(f"Disallow: {path}")
    # NAMED, THOUGH `*` ALREADY ALLOWS THEM.
    #
    # Being quoted by an assistant is the point of the answer pages, so the
    # agents that do the quoting are listed by name and allowed explicitly.
    # Two reasons that is worth the lines: a future Disallow added for one
    # crawler cannot silently widen to all of them, and an operator reading
    # this file can see the intent rather than inferring it from a wildcard.
    #
    # The split matters. The first group fetch a page to answer somebody's
    # question now; the second collect for training. Both are welcome here -
    # a product with nothing to hide and everything to be cited for - but
    # they are different decisions and this is where they would be made.
    for agent in ("OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot",
                  "Claude-User", "PerplexityBot", "Perplexity-User",
                  "DuckAssistBot",
                  "GPTBot", "ClaudeBot", "Google-Extended",
                  "Applebot-Extended", "CCBot"):
        lines += ["", f"User-agent: {agent}", "Allow: /"]
    lines.append("")
    lines.append(f"Sitemap: {config.BASE_URL}/sitemap.xml")
    return "\n".join(lines) + "\n"


@app.get("/sitemap.xml")
def sitemap():
    from xml.sax.saxutils import escape
    # The figures move most days and the pages quoting them change with the
    # file that publishes them, so the modification date of that file is the
    # honest answer for the pages built on it. A lastmod of "today" on every
    # page every day is the kind of thing a crawler learns to disregard, and
    # then stops re-reading the pages that genuinely did change.
    stamp = track_record.updated_on()
    when = f"<lastmod>{stamp}</lastmod>" if stamp else ""
    urls = "".join(
        f"<url><loc>{escape(config.BASE_URL + p)}</loc>{when}</url>"
        for p in PUBLIC_PAGES)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'{urls}</urlset>')
    return Response(content=xml, media_type="application/xml")


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt():
    """What this site is, for a model reading it rather than a person.

    The convention is young and no crawler is obliged to honour it. It costs
    one route, and the whole retrieval strategy here - the answer pages, the
    JSON endpoint, every rate carrying its count - is aimed at being quoted
    accurately by exactly the readers this file is addressed to. If it is
    ignored, nothing is lost; if it is read, the thing most likely to be got
    wrong is stated before it can be.

    That thing is the two reply rates. 26% and 19% are both published, they
    measure different events, and a model that takes one for the other will
    quote a number this site did not claim. So it is spelled out here, in the
    plainest form there is, alongside the address of the machine-readable
    version.
    """
    record = track_record.read()
    live = ""
    if record:
        live = (f"\n  Harry's live job hunt: {record['applications']} "
                f"applications to {record['employers']} employers, "
                f"{record['replies']} replies, {record['reply_rate']}%. "
                f"Updated {record.get('updated_at', '')[:10]}.\n")

    return f"""# Recruited

> A free tool that finds the real email address of a person who can act on a
> job application, taken from the advert and the employer's own website. It
> never guesses an address pattern, and when it finds nothing real it says so
> and sends nothing.

Not to be confused with other businesses called Recruited. This one is at
{config.BASE_URL}, it is made by one person job hunting in Aberdeen, and its
figures are published rather than asserted.

## The numbers, and the one thing most likely to be misquoted

Two reply rates appear on this site. They are both true and they are NOT the
same measurement:

  - 22 of 86, 26%. A fixed four-week study, logged before replies were
    classified, so it counts every message that came back including
    automated acknowledgements and rejections.
  - The live counter excludes autoresponders and rejections, so it is
    stricter and lower.
{live}
Quote either one WITH the definition attached. Machine-readable, with the
count under every rate and the caveat on every thin row:
{config.BASE_URL}/numbers.json

## Pages

- {config.BASE_URL}/find: the free tool. No account, nothing to install.
- {config.BASE_URL}/playbook: the whole method, free.
- {config.BASE_URL}/numbers: every figure, live, including the bad months.
- {config.BASE_URL}/answers: one page per question jobseekers actually ask.

## What it will not do

It does not guess `firstname.lastname@` patterns and verify them, which is
what every competing tool in this space sells. Three reasons: catch-all
domains make verifiers return "valid" for addresses belonging to nobody; a
bounce costs the sender reputation that delivers the next email; and a guess
that lands on the wrong person is a cold email about a job to somebody who
cannot act on it. Of the listings processed for the published figures, 516
ended with no address found and nothing was sent.
"""


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    """Every customer, and how far each of them actually got.

    Not behind the paywall - it is behind ADMIN_EMAILS, which is a stricter
    gate. A signed-in stranger gets the same 404 as a signed-out one, because
    "403 Forbidden" on a URL is an answer: it confirms the page exists and is
    worth attacking. There is nothing to gain from telling them.
    """
    user = current_user(request)
    if not user or not config.is_admin(user["email"]):
        return PlainTextResponse("Not found", status_code=404)
    rows = db.overview()
    now = time.time()
    return render(request, "admin.html", user=user, rows=rows,
                  ago=adminlib.ago, spots_left=db.free_spots_left(),
                  # The half of the funnel that happens before anybody signs
                  # up, which every stage below is otherwise blind to.
                  traffic_today=views.totals(since=now - 86400, now=now),
                  traffic_week=views.totals(since=now - 7 * 86400, now=now),
                  traffic_hours=views.by_hour(hours=24, now=now),
                  **adminlib.summarise(rows, now=now))


# ----------------------------------------------------------------------
# getting started: CV, then the questions, then how it sends
# ----------------------------------------------------------------------
def _gate(request: Request):
    """Signed in and paid, or the response that says otherwise.

    Returns (user, None) when they may proceed, (None, response) when not.
    Every screen below is behind the paywall, so this is written once.
    """
    user = current_user(request)
    if not user:
        return None, needs_login()
    if not db.is_paid(user):
        return None, render(request, "paywall.html", user=user)
    return user, None


@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request):
    """Where somebody lands after paying. Shows what is done and what is not,
    rather than dropping them on an empty dashboard."""
    user, blocked = _gate(request)
    if blocked:
        return blocked
    return render(request, "setup.html", user=user,
                  cv=db.cv_summary(user["id"]),
                  profile=db.load_profile(user["id"]),
                  mail=db.get_mail_account(user["id"]),
                  settings=db.get_send_settings(user["id"]),
                  vault_ready=vault.available())


# What "the least you will work for" means when somebody types one number.
# Below this it is an hourly rate; at or above it, a salary. Nobody earns
# £200 an hour in this market and nobody's salary is £199 a year, so one box
# can take either and the machine can tell which.
HOURLY_BELOW = 200


def _pay(raw: str) -> tuple[int, int]:
    """One free-text pay box -> (min_salary_annual, min_rate_hourly).

    Accepts what people actually type on a phone: "25000", "£25,000", "25k",
    "12.50". Returns (0, 0) for anything unreadable, which the Profile then
    refuses with its own message - so a bad value is caught by the same rule
    as every other route, rather than by a second copy of it here.
    """
    text = (raw or "").lower().replace("£", "").replace(",", "").strip()
    thousands = text.endswith("k")
    text = text.rstrip("k").strip()
    for suffix in ("per hour", "an hour", "/hr", "/h", "ph", "p/h", "per year",
                   "a year", "pa", "p.a."):
        text = text.replace(suffix, "").strip()
    try:
        value = float(text)
    except ValueError:
        return 0, 0
    if thousands:
        value *= 1000
    if value <= 0:
        return 0, 0
    # Rounded UP. This is a floor, and rounding a floor down quietly accepts
    # work below what the person said - "12.50" stored as 12 lets through a
    # £12.00 job they told us they would not take.
    if value < HOURLY_BELOW:
        return 0, math.ceil(value)
    return math.ceil(value), 0


def _quick_profile(form) -> dict:
    """The six answers the machine cannot start without, as a full profile.

    WHY THIS EXISTS. Nine people signed up and not one of them saved a
    profile. The setup screen told them it was "two minutes" and "the only
    thing it needs", then sent them to an eighteen-field form - salary floor,
    hourly floor, current salary, a three-part career highlight, a list of
    things never to claim. On a phone, having just tapped a link in a
    Snapchat story, that is where every one of them stopped. Not the mailbox;
    nobody ever got as far as the mailbox.

    The Profile itself hard-requires exactly six things: what job, where,
    name, phone, one previous job, and a pay floor. Everything else has a
    working default. So this asks for those six and nothing else. The full
    form is still there, described as what it is - a way to make the letters
    stronger - rather than as the price of entry.
    """
    def s(name):
        return (form.get(name) or "").strip()

    annual, hourly = _pay(s("min_pay"))
    location = s("location")
    title, org = s("last_title"), s("last_org")
    return {
        "name": s("name"), "location": location, "phone": s("phone"),
        "email": "",
        # The audience is people out of work, and "employed" would demand a
        # current salary this form deliberately does not ask for. Anybody in
        # work can say so on the full form.
        "situation": "unemployed",
        "current_salary": 0, "may_name_employer": False,
        "min_salary_annual": annual, "min_rate_hourly": hourly,
        "priorities": [], "wants_travel": False, "wants_contract": False,
        "history": ([{"title": title, "org": org, "detail": ""}]
                    if title and org else []),
        "qualifications": [], "never_claim": [],
        "locations": [location] if location else [],
        "radius_miles": 25,
        "target_roles": _items(s("target_roles")),
    }


@app.post("/setup/quick", response_class=HTMLResponse)
async def quick_start(request: Request):
    """Six answers in, a working search out, and the first run started."""
    user, blocked = _gate(request)
    if blocked:
        return blocked

    # Never over a fuller profile. The form is only shown to somebody with no
    # profile, but a back button or a second tab can still post it, and six
    # answers written over eighteen would silently throw away the other twelve.
    if db.load_profile(user["id"]):
        return RedirectResponse("/profile", status_code=303)

    form = await request.form()
    data = _quick_profile(form)
    try:
        # The same validation every other route uses, so a quick profile is
        # never a weaker one - just a shorter way to fill in the same thing.
        Profile.from_dict(data)
    except ProfileError as exc:
        return render(request, "setup.html", user=user,
                      cv=db.cv_summary(user["id"]), profile=None,
                      mail=db.get_mail_account(user["id"]),
                      settings=db.get_send_settings(user["id"]),
                      vault_ready=vault.available(),
                      quick=dict(form), quick_error=_friendly(str(exc)))

    db.save_profile(user["id"], data)
    funnel.reached(user["id"], "onboarded", detail="quick start")
    _start_run(user["id"])
    return RedirectResponse("/dashboard", status_code=303)


# The Profile's messages are written for a JSON file ("target_roles is empty -
# nothing to search for"). This form never shows those field names, so each
# is translated into the question that was actually on the screen.
_FRIENDLY = {
    "name is empty": "Put your name in - it signs every letter.",
    "location is empty": "Say where you live.",
    "does not look like a number": "That phone number does not look right.",
    "set min_salary_annual or min_rate_hourly":
        "Put the least you would work for - a yearly salary like 25000, or an "
        "hourly rate like 12.",
    "target_roles is empty": "Say what job you are after.",
    "history is empty":
        "Put your last job and who it was with - the letters need one real "
        "thing you have done.",
    "locations is empty": "Say where you live.",
}


def _friendly(message: str) -> str:
    lines = [ln.strip(" -") for ln in message.splitlines()[1:] if ln.strip()]
    if not lines:
        lines = [message]
    out = []
    for line in lines:
        for needle, plain in _FRIENDLY.items():
            if needle in line:
                if plain not in out:
                    out.append(plain)
                break
        else:
            # The place and job-title checks already speak plain English and
            # carry the example the person needs, so they pass through.
            out.append(line.replace("target role", "The job").replace(
                "location", "The place"))
    return " ".join(out)


@app.post("/setup/cv")
async def upload_cv(request: Request):
    """Take the CV, and use it to answer as many questions as it can.

    Answers JSON when asked to, because the page uploads with fetch rather
    than by submitting the form. That is not decoration - see the script in
    setup.html for why reading the bytes in the browser is the only place
    Chrome's ERR_UPLOAD_FILE_CHANGED can be fixed. The plain form post still
    works with no JavaScript at all, and returns redirects exactly as before.
    """
    wants_json = "application/json" in (request.headers.get("accept") or "")

    def answer(next_url: str = "", error: str = "", status: int = 200):
        if wants_json:
            body = {"error": error} if error else {"next": next_url}
            return JSONResponse(body, status_code=400 if error else 200)
        if error:
            return render(request, "setup.html", user=user, error=error,
                          cv=db.cv_summary(user["id"]),
                          profile=db.load_profile(user["id"]),
                          mail=db.get_mail_account(user["id"]),
                          settings=db.get_send_settings(user["id"]),
                          vault_ready=vault.available())
        return RedirectResponse(next_url, status_code=303)

    user, blocked = _gate(request)
    if blocked:
        return blocked

    form = await request.form()
    upload = form.get("cv")
    if upload is None or not getattr(upload, "filename", ""):
        if wants_json:
            return JSONResponse({"error": "no file was chosen"},
                                status_code=400)
        return RedirectResponse("/setup?e=nofile", status_code=303)

    # Read with a ceiling rather than trusting the declared length: the only
    # size that means anything is the number of bytes that actually arrived.
    # Reading in chunks and stopping matters - `await upload.read()` with no
    # argument will happily pull a 500MB upload into memory before anything
    # gets a chance to reject it, which is a way to take the server down from
    # a signed-in account.
    blob = b""
    while len(blob) <= cvlib.MAX_BYTES:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        blob += chunk
    try:
        cvlib.check(upload.filename, blob)
    except cvlib.CVError as exc:
        return answer(error=str(exc))

    text = cvlib.extract_text(upload.filename, blob)
    db.save_cv(user["id"], filename=upload.filename,
               content_type=upload.content_type or "application/octet-stream",
               blob=blob, extracted=text)
    # Its own event, separate from "onboarded". A CV is optional - the machine
    # runs without one - so it is not the line between set up and not. It is
    # recorded because it is the referral programme's first tier: a real
    # document is work, where a six-field form is not.
    funnel.reached(user["id"], "cv_uploaded")

    # Only offer to prefill an empty profile. Overwriting answers somebody
    # already gave with a model's reading of their CV would be rude and wrong.
    if text and not db.load_profile(user["id"]):
        return answer("/setup/from-cv")
    return answer("/setup")


def _seed_from_cv(data: dict, user) -> dict:
    """A CV reading, plus the few things a CV never states.

    The model is deliberately not asked for any of these. A CV gives an
    address, not a search area, so `locations` is derived from where it says
    they live - filtered through the pipeline's own rule for what is a
    searchable place, because "Currently based in the north east of Scotland"
    is a real thing to find on a CV and it searches for nothing.

    The pay floor is NOT defaulted and never will be. A guessed floor is how
    the machine ends up writing to jobs paying less than the user earns now,
    and it is the one number they have to say out loud.
    """
    out = dict(data)
    out.setdefault("email", user["email"])
    if not out.get("locations"):
        out["locations"] = [p for p in _items(out.get("location", ""))
                            if not _not_a_place(p)][:3]
    out.setdefault("radius_miles", 25)
    out.setdefault("situation", "unemployed")
    return out


@app.get("/setup/from-cv", response_class=HTMLResponse)
def profile_from_cv(request: Request):
    """What the CV says, to be checked, plus the two questions it cannot answer.

    This used to render the full profile form prefilled. That form is seven
    panels and saves nothing until the bottom of it, and the one user who ever
    reached it read a correct filling-in of their own CV and left without
    saving - so the machine never ran for them. Filling a long form in for
    somebody does not make it a short form. See from_cv.html.
    """
    user, blocked = _gate(request)
    if blocked:
        return blocked

    row = db.get_cv(user["id"])
    if not row or not row["extracted"]:
        return RedirectResponse("/profile?e=nocv", status_code=303)

    from .ai import AIError, gemini_now
    try:
        data = cvlib.suggest_profile(row["extracted"], gemini_now)
    except AIError:
        return RedirectResponse("/profile?e=ai", status_code=303)
    if not data:
        return RedirectResponse("/profile?e=cv", status_code=303)
    return render(request, "from_cv.html", user=user,
                  data=_seed_from_cv(data, user))


@app.post("/setup/from-cv", response_class=HTMLResponse)
async def profile_from_cv_save(request: Request):
    """Save the CV's reading, or carry it into the full form.

    Parsed by the same _profile_from_form and validated by the same Profile as
    the full form, so this shorter screen cannot save something the longer one
    would have refused.
    """
    user, blocked = _gate(request)
    if blocked:
        return blocked

    form = await request.form()
    data = _profile_from_form(form)

    # "Change something". The whole reading goes into the full form rather
    # than a link that would land them on a blank one and waste the read.
    if (form.get("action") or "") == "edit":
        return render(request, "profile.html", user=user, data=data,
                      from_cv=True)

    # The one thing this screen asks for, checked here so a person who missed
    # it gets the short page back rather than being dropped into twenty boxes
    # over one number.
    if not (data["min_salary_annual"] or data["min_rate_hourly"]):
        return render(request, "from_cv.html", user=user, data=data,
                      error="Put in the least you will work for. A year or an "
                            "hour, either one is enough.")

    try:
        Profile.from_dict(data)
    except ProfileError as exc:
        # Anything else wrong is in a field this screen does not show, so the
        # full form is the only place it can be fixed.
        return render(request, "profile.html", user=user, data=data,
                      error=str(exc))

    db.save_profile(user["id"], data)
    funnel.reached(user["id"], "onboarded", detail="from CV")
    # Same reason as the quick start: finishing setup should be followed by
    # letters being written, not by an empty dashboard until the next sweep.
    _start_run(user["id"])
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/setup/sending")
async def save_sending(request: Request):
    """Automatic or by hand, and the guard rails either way."""
    user, blocked = _gate(request)
    if blocked:
        return blocked

    form = await request.form()
    auto = 1 if form.get("auto_send") else 0

    if auto and not db.get_mail_account(user["id"]):
        return RedirectResponse("/setup/mail?e=needed", status_code=303)

    def clamp(name, default, low, high):
        try:
            return max(low, min(high, int(form.get(name) or default)))
        except (TypeError, ValueError):
            return default

    # Going past the recommended number is allowed, but only for somebody who
    # ticked the box that carries the warning. Without that tick the ceiling
    # is the recommended one, so a mistyped 60 becomes 25 rather than a month
    # of a personal address being scored as a spammer.
    ceiling = (config.ABSOLUTE_DAILY_CAP if form.get("accept_volume_risk")
               else config.MAX_DAILY_CAP)

    db.save_send_settings(
        user["id"], auto_send=auto,
        # Ceilings, not suggestions. A user who types 500 into the daily cap
        # is not making a considered decision about their own reputation, and
        # the form's own max attribute is a hint to a browser rather than a
        # rule - anything can POST here.
        hold_minutes=clamp("hold_minutes", 60, 0, 1440),
        daily_cap=clamp("daily_cap", 12, 1, ceiling),
        search_days=clamp("search_days", 2, 1, config.MAX_SEARCH_DAYS),
        follow_up=1 if form.get("follow_up") else 0,
        digest=1 if form.get("digest") else 0)
    return RedirectResponse("/setup", status_code=303)


@app.get("/setup/mail", response_class=HTMLResponse)
def mail_form(request: Request):
    user, blocked = _gate(request)
    if blocked:
        return blocked
    profile = db.load_profile(user["id"]) or {}
    return render(request, "mail.html", user=user,
                  mail=db.get_mail_account(user["id"]),
                  vault_ready=vault.available(),
                  managed_ready=config.managed_mail_available(),
                  managed_preview=(
                      db.issue_managed_address(
                          user["id"], profile.get("name") or "",
                          user["email"])
                      if config.managed_mail_available() else ""),
                  profile=profile)


@app.post("/setup/mail/managed")
def mail_managed(request: Request):
    """Issue a Recruited address instead of taking the user's own.

    No password to hand over, because there is nothing of theirs to
    authenticate as: we send through our own provider and put their real
    address on Reply-To, so an employer's answer goes straight to them.
    """
    user, blocked = _gate(request)
    if blocked:
        return blocked

    profile = db.load_profile(user["id"]) or {}
    if not vault.available() or not config.managed_mail_available():
        return render(request, "mail.html", user=user, mail=None,
                      vault_ready=vault.available(),
                      managed_ready=config.managed_mail_available(),
                      managed_preview="", profile=profile,
                      error="Recruited addresses are not switched on here.")

    # Where replies land. Their account address unless they gave a different
    # one on their profile - and never blank, because a letter no employer
    # can answer is worse than no letter.
    reply_to = (profile.get("email") or user["email"] or "").strip()
    if "@" not in reply_to:
        return render(request, "mail.html", user=user, mail=None,
                      vault_ready=True, managed_ready=True,
                      managed_preview="", profile=profile,
                      error="We need a real address to send replies to "
                            "before we can issue you a sending one.")

    address = db.issue_managed_address(user["id"], profile.get("name") or "",
                                       user["email"])
    first_time = db.get_mail_account(user["id"]) is None
    db.save_mail_account(
        user["id"], address=address,
        host=config.MANAGED_MAIL_HOST, port=config.MANAGED_MAIL_PORT,
        password=config.MANAGED_MAIL_KEY, kind="managed", reply_to=reply_to)
    if first_time:
        db.save_send_settings(user["id"], auto_send=1)
    return RedirectResponse("/setup", status_code=303)


@app.post("/setup/mail", response_class=HTMLResponse)
async def mail_save(request: Request):
    """Connect a mail account, but only after proving it works.

    The verification is not a nicety. Storing credentials that turn out to be
    wrong means the user believes their letters are going out while nothing
    is happening, which is the worst failure this product has.
    """
    user, blocked = _gate(request)
    if blocked:
        return blocked

    if not vault.available():
        return render(request, "mail.html", user=user, mail=None,
                      vault_ready=False, profile=db.load_profile(user["id"]) or {},
                      error="Automatic sending is switched off at the moment, "
                            "so there is nothing to connect yet. Letters are "
                            "still written for you to send.")

    form = await request.form()
    address = (form.get("address") or "").strip()
    password = form.get("password") or ""
    guessed = delivery.guess_host(address)
    host = (form.get("host") or (guessed[0] if guessed else "")).strip()
    try:
        port = int(form.get("port") or (guessed[1] if guessed else 465))
    except (TypeError, ValueError):
        port = 465

    def again(message):
        # The Recruited option has to survive this screen. Somebody who has
        # just been told their app password was rejected is exactly the person
        # who wants the route that needs no password, and dropping it from the
        # error render would hide it at the only moment it is obviously
        # useful.
        profile = db.load_profile(user["id"]) or {}
        return render(request, "mail.html", user=user, mail=None,
                      vault_ready=True, error=message, address=address,
                      host=host, port=port,
                      managed_ready=config.managed_mail_available(),
                      managed_preview=(
                          db.issue_managed_address(
                              user["id"], profile.get("name") or "",
                              user["email"])
                          if config.managed_mail_available() else ""),
                      profile=profile)

    if not address or "@" not in address:
        return again("That does not look like an email address.")
    if not password:
        return again("The app password is missing.")
    if not host:
        return again("We do not know the mail server for that address - "
                     "please fill in the server and port yourself.")

    # Three outcomes, not two, and conflating the last two locked every user
    # out of the product's main feature.
    #
    #   proved to work      -> store it, verified
    #   proved to be wrong  -> refuse, and say so. Unchanged.
    #   could not be asked  -> store it unchecked, and say THAT
    #
    # The third is the common case on free hosting, which blocks outbound
    # SMTP almost everywhere to stop spam. On this plan a correct Gmail app
    # password is unreachable-not-wrong every single time, and the old code
    # told the user their password had been rejected - an accusation it had
    # no evidence for, about the one step it needs them to get right.
    #
    # The sweep runs on GitHub Actions, which is not blocked, so the proof
    # still happens before a single letter goes out. Only its location moves.
    # Unreachable is caught FIRST because it is a subclass, and everything
    # else still refuses exactly as it did before. Only the one failure that
    # has been positively identified is relaxed; an unrecognised error is not
    # quietly assumed to be this host's fault.
    verified = True
    try:
        delivery.verify(host=host, port=port, username=address,
                        password=password)
    except delivery.DeliveryUnreachableError:
        verified = False
    except delivery.DeliveryError as exc:
        return again(str(exc))

    first_time = not db.get_mail_account(user["id"])
    db.save_mail_account(user["id"], address=address, host=host, port=port,
                         password=password, verified=verified)
    # Connecting a mailbox to a thing whose stated job is to send letters from
    # it IS the decision to let it send. Leaving automatic sending off after
    # that is a second, hidden step that people finish setup without ever
    # finding, and then wonder why nothing goes out. The holding window and
    # the daily cap are what make this safe to default on, and it is one
    # checkbox to turn off.
    if first_time:
        db.save_send_settings(user["id"], auto_send=1)
    return RedirectResponse("/setup", status_code=303)


@app.post("/setup/mail/forget")
def mail_forget(request: Request):
    """Disconnect. Turns automatic sending off in the same breath, because
    leaving it on with no way to send would silently do nothing."""
    user, blocked = _gate(request)
    if blocked:
        return blocked
    db.forget_mail_account(user["id"])
    db.save_send_settings(user["id"], auto_send=0)
    return RedirectResponse("/setup", status_code=303)


@app.get("/cv")
def download_cv(request: Request):
    """Give the user back exactly what they uploaded."""
    user, blocked = _gate(request)
    if blocked:
        return blocked
    row = db.get_cv(user["id"])
    if not row:
        return RedirectResponse("/setup", status_code=303)
    from fastapi.responses import Response
    return Response(
        bytes(row["blob"]),
        media_type=delivery.guess_attachment_type(row["filename"]),
        headers={"Content-Disposition":
                 f'attachment; filename="{row["filename"]}"'})


@app.post("/cv/delete")
def remove_cv(request: Request):
    user, blocked = _gate(request)
    if blocked:
        return blocked
    db.delete_cv(user["id"])
    return RedirectResponse("/setup", status_code=303)


@app.post("/send-now")
def send_now(request: Request):
    """Send everything due, without waiting for the next sweep."""
    user, blocked = _gate(request)
    if blocked:
        return blocked
    report = autosend.send_due_for_user(user["id"])
    print(f"[send] user {user['id']}: {report.summary()}")
    return RedirectResponse("/drafts", status_code=303)
