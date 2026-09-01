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
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # so `jobseeker` imports

from jobseeker.profile import Profile, ProfileError, Role  # noqa: E402

from . import auth, autosend, billing, config, cv as cvlib, db, delivery, ratelimit, vault  # noqa: E402
from . import runner  # noqa: E402

log = logging.getLogger("jobmachine")

app = FastAPI(title="job machine", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

SESSION_COOKIE = "jm_session"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    ratelimit.init()
    yield


app.router.lifespan_context = lifespan


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def current_user(request: Request):
    uid = auth.read_session(request.cookies.get(SESSION_COOKIE))
    return db.get_user(uid) if uid else None


def render(request: Request, template: str, **ctx):
    user = ctx.pop("user", None)
    if user is None:
        user = current_user(request)
    return templates.TemplateResponse(
        request, template,
        {"user": user, "paid": db.is_paid(user), "config": config, **ctx})


def needs_login():
    return RedirectResponse("/login", status_code=303)


# ----------------------------------------------------------------------
# public
# ----------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "landing.html")


@app.get("/playbook", response_class=HTMLResponse)
def playbook(request: Request):
    """The method, free, for everyone. It is the best advertisement the paid
    product has, and holding it back would not sell a single subscription."""
    text = (HERE.parent / "PLAYBOOK.md").read_text(encoding="utf-8")
    return render(request, "playbook.html", playbook=text)


# ----------------------------------------------------------------------
# sign in
# ----------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html")


# Deliberately generous for a real person and useless for a script. An honest
# user asks once, twice if the first went to spam.
LOGIN_PER_EMAIL = (5, 3600)      # 5 an hour to any one address
LOGIN_PER_IP = (20, 3600)        # 20 an hour from any one machine


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form("")):
    if not auth.valid_email(email):
        return render(request, "login.html", error="That is not an email address.")

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
        return render(request, "login.html",
                      error="The sign-in email could not be sent. Try again shortly.")
    # Always the same reply, whether or not the address has an account: the
    # response must not reveal who is a customer.
    return render(request, "login.html", sent=email.strip())


@app.get("/auth/verify")
def verify(request: Request, token: str = ""):
    email = auth.consume_login_token(token)
    if not email:
        return render(request, "login.html",
                      error="That link has expired or was already used. "
                            "Here is a fresh one.")
    user = db.get_or_create_user(email)
    # Somebody with no profile has nothing to look at on the dashboard except
    # a note telling them so. Send them to the thing that needs doing; the CV
    # upload there fills in most of the next screen on its own.
    landing = "/dashboard" if db.load_profile(user["id"]) else "/setup"
    response = RedirectResponse(landing, status_code=303)
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
                  # Why nothing came through is as important as what did. A
                  # quiet day with no explanation reads as a broken product.
                  outcomes=db.recent_outcomes(user["id"], limit=8))


@app.post("/run")
def run_now(request: Request):
    """Look for work now, rather than waiting for the next scheduled sweep."""
    user = current_user(request)
    if not user:
        return needs_login()
    if not db.is_paid(user):
        return render(request, "paywall.html", user=user)

    # A person is watching this page, so rate limits must not be waited out.
    report = runner.run_for_user(user["id"], interactive=True)
    print(f"[run] user {user['id']}: {report.summary()}")
    return RedirectResponse("/drafts" if report.drafted else "/dashboard",
                            status_code=303)


@app.get("/profile", response_class=HTMLResponse)
def profile_form(request: Request):
    user = current_user(request)
    if not user:
        return needs_login()
    if not db.is_paid(user):
        return render(request, "paywall.html", user=user)
    return render(request, "profile.html", user=user,
                  data=db.load_profile(user["id"]) or {})


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
    return RedirectResponse("/dashboard", status_code=303)


def _lines(raw: str) -> list[str]:
    return [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]


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
        "locations": _lines(s("locations")),
        "radius_miles": i("radius_miles") or 25,
        "target_roles": _lines(s("target_roles")),
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
    """Served from the root, not from /static/.

    A service worker only controls URLs at or below its own path, so one
    living at /static/sw.js could never control /dashboard - it would register
    without error and then do nothing, which is the most annoying kind of
    broken.
    """
    from fastapi.responses import FileResponse
    return FileResponse(
        HERE / "static" / "sw.js", media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/",
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


@app.post("/setup/cv")
async def upload_cv(request: Request):
    """Take the CV, and use it to answer as many questions as it can."""
    user, blocked = _gate(request)
    if blocked:
        return blocked

    form = await request.form()
    upload = form.get("cv")
    if upload is None or not getattr(upload, "filename", ""):
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
        return render(request, "setup.html", user=user, error=str(exc),
                      cv=db.cv_summary(user["id"]),
                      profile=db.load_profile(user["id"]),
                      mail=db.get_mail_account(user["id"]),
                      settings=db.get_send_settings(user["id"]),
                      vault_ready=vault.available())

    text = cvlib.extract_text(upload.filename, blob)
    db.save_cv(user["id"], filename=upload.filename,
               content_type=upload.content_type or "application/octet-stream",
               blob=blob, extracted=text)

    # Only offer to prefill an empty profile. Overwriting answers somebody
    # already gave with a model's reading of their CV would be rude and wrong.
    if text and not db.load_profile(user["id"]):
        return RedirectResponse("/setup/from-cv", status_code=303)
    return RedirectResponse("/setup", status_code=303)


@app.get("/setup/from-cv", response_class=HTMLResponse)
def profile_from_cv(request: Request):
    """The profile form, prefilled from the CV, for the user to correct.

    Never saved without them pressing save. A model reading a CV gets things
    wrong, and the two fields it is never allowed to touch - the pay floor and
    the never-claim list - are exactly the two that must be deliberate.
    """
    user, blocked = _gate(request)
    if blocked:
        return blocked

    row = db.get_cv(user["id"])
    if not row or not row["extracted"]:
        return RedirectResponse("/profile", status_code=303)

    from .ai import AIError, gemini_now
    try:
        data = cvlib.suggest_profile(row["extracted"], gemini_now)
    except AIError:
        return RedirectResponse("/profile?e=ai", status_code=303)
    if not data:
        return RedirectResponse("/profile?e=cv", status_code=303)
    data.setdefault("email", user["email"])
    return render(request, "profile.html", user=user, data=data,
                  from_cv=True)


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

    db.save_send_settings(
        user["id"], auto_send=auto,
        # Ceilings, not suggestions. A user who types 500 into the daily cap
        # is not making a considered decision about their own reputation.
        hold_minutes=clamp("hold_minutes", 60, 0, 1440),
        daily_cap=clamp("daily_cap", 20, 1, 50))
    return RedirectResponse("/setup", status_code=303)


@app.get("/setup/mail", response_class=HTMLResponse)
def mail_form(request: Request):
    user, blocked = _gate(request)
    if blocked:
        return blocked
    return render(request, "mail.html", user=user,
                  mail=db.get_mail_account(user["id"]),
                  vault_ready=vault.available(),
                  profile=db.load_profile(user["id"]) or {})


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
        return render(request, "mail.html", user=user, mail=None,
                      vault_ready=True, error=message, address=address,
                      host=host, port=port,
                      profile=db.load_profile(user["id"]) or {})

    if not address or "@" not in address:
        return again("That does not look like an email address.")
    if not password:
        return again("The app password is missing.")
    if not host:
        return again("We do not know the mail server for that address - "
                     "please fill in the server and port yourself.")

    try:
        delivery.verify(host=host, port=port, username=address,
                        password=password)
    except delivery.DeliveryError as exc:
        return again(str(exc))

    db.save_mail_account(user["id"], address=address, host=host, port=port,
                         password=password)
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
