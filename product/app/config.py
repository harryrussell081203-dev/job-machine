"""Everything the app needs from its environment, in one place.

Nothing here has a default that would let the app run in a state its operator
did not choose. A missing Stripe key is not a reason to quietly hand out free
accounts, and a missing signing secret is not a reason to sign with a
guessable one, so both refuse at import in production instead.
"""

from __future__ import annotations

import os
import secrets


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# --- how the app is running -------------------------------------------
# DEV loosens exactly two things: the signing secret may be generated, and
# magic links are printed to the console instead of emailed. Neither is
# survivable in production, so both are gated on this one flag.
DEV = _flag("DEV_MODE", False)

BASE_URL = _env("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DB_PATH = _env("DB_PATH", "jobmachine.db")

# --- signing ----------------------------------------------------------
# Sessions and magic links are both signed with this. Rotating it logs
# everyone out, which is the correct behaviour after a suspected leak.
SECRET_KEY = _env("SECRET_KEY")
if not SECRET_KEY:
    if not DEV:
        raise RuntimeError(
            "SECRET_KEY is not set. Generate one with:\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
    SECRET_KEY = secrets.token_urlsafe(48)

MAGIC_LINK_MAX_AGE = 900        # 15 minutes
SESSION_MAX_AGE = 60 * 60 * 24 * 30

# --- email that the app itself sends (magic links, receipts) ----------
# Separate from anything a user sends about a job. This is the app talking
# to its own customers.
SMTP_ADDRESS = _env("APP_SMTP_ADDRESS")
SMTP_PASSWORD = _env("APP_SMTP_PASSWORD")
SMTP_HOST = _env("APP_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_env("APP_SMTP_PORT", "465"))

# --- billing ----------------------------------------------------------
STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = _env("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")

# A Stripe Payment Link (https://buy.stripe.com/...), configured in the Stripe
# dashboard rather than in code. Setting this is the shortest route to taking
# money: no secret key is needed, because the app never calls the Stripe API -
# it just sends the customer to a page Stripe already hosts.
#
# The webhook secret is still required. Access is granted only by a verified
# webhook, and that does not change because the checkout page came from a link.
STRIPE_PAYMENT_LINK = _env("STRIPE_PAYMENT_LINK")

# How long a ONE-OFF payment buys. Ignored for subscriptions, which carry
# their own period end from Stripe.
#
# There is no right answer Stripe can tell us here: a single payment says
# nothing about how long it was meant to last. Thirty days is the assumption,
# and it is deliberately not "forever" - granting lifetime access to one
# payment is the expensive way to discover the link was set up wrong.
ONE_OFF_ACCESS_DAYS = int(_env("ONE_OFF_ACCESS_DAYS", "30"))

# With billing switched off every signed-in account is treated as paid. It
# exists so the app can be developed and demoed without Stripe, and it is
# refused in production precisely because "the paywall was off" is not a
# mistake anyone notices from the outside.
BILLING_ENABLED = _flag("BILLING_ENABLED", True)
if BILLING_ENABLED and not DEV:
    if STRIPE_PAYMENT_LINK:
        # Link mode: no API calls, so no secret key. The webhook is still what
        # opens the app, so it is still mandatory.
        required = (("STRIPE_WEBHOOK_SECRET", STRIPE_WEBHOOK_SECRET),)
    else:
        required = (("STRIPE_SECRET_KEY", STRIPE_SECRET_KEY),
                    ("STRIPE_PRICE_ID", STRIPE_PRICE_ID),
                    ("STRIPE_WEBHOOK_SECRET", STRIPE_WEBHOOK_SECRET))
    missing = [n for n, v in required if not v]
    if missing:
        raise RuntimeError(
            f"billing is on but {', '.join(missing)} not set. Set them, or set "
            "STRIPE_PAYMENT_LINK to use a Stripe Payment Link instead, or "
            "BILLING_ENABLED=0 to run without a paywall.")


def payment_link_for(user_id: int) -> str:
    """The payment link with this user stamped on it.

    client_reference_id is how a Payment Link payment gets tied back to an
    account: Stripe echoes it on checkout.session.completed, which is the only
    thing that opens the app. Without it a payment arrives from nobody in
    particular and cannot be honoured.
    """
    join = "&" if "?" in STRIPE_PAYMENT_LINK else "?"
    return f"{STRIPE_PAYMENT_LINK}{join}client_reference_id={user_id}"

# --- the job search itself --------------------------------------------
ADZUNA_APP_ID = _env("ADZUNA_APP_ID")
ADZUNA_APP_KEY = _env("ADZUNA_APP_KEY")
REED_API_KEY = _env("REED_API_KEY")
GEMINI_API_KEY = _env("GEMINI_API_KEY")

# --- delivery ---------------------------------------------------------
# "handoff"  - the app drafts; the user sends from their own mail client.
# "smtp"     - the app sends using credentials the user supplied.
#
# handoff is the default on purpose. See delivery.py for why that is a
# product decision rather than a limitation.
DELIVERY_MODE = _env("DELIVERY_MODE", "handoff")

PRICE_LABEL = _env("PRICE_LABEL", "£9 a month")
