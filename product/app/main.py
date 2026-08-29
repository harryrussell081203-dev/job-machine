"""The app: sign in, pay, describe your search, work your drafts.

Run it:

    cd product
    DEV_MODE=1 BILLING_ENABLED=0 uvicorn app.main:app --reload

In that mode sign-in links print to the console and the paywall is open, so
the whole thing can be clicked through with no Stripe account and no mail
configuration.
"""

from __future__ import annotations

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

from . import auth, billing, config, db, delivery  # noqa: E402

app = FastAPI(title="job machine", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

SESSION_COOKIE = "jm_session"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
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


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form("")):
    if not auth.valid_email(email):
        return render(request, "login.html", error="That is not an email address.")
    try:
        auth.send_login_email(email.strip(), auth.make_login_link(email))
    except Exception:
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
    response = RedirectResponse("/dashboard", status_code=303)
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
                  drafts=db.list_drafts(user["id"], limit=5))


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


@app.get("/healthz")
def healthz():
    return {"ok": True}
