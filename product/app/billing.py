"""The paywall. Stripe Checkout for taking money, webhooks for believing it.

The rule this file exists to enforce: **a user becomes paid because Stripe
said so on a signed webhook, never because a browser arrived at /success.**
A success URL is just a redirect, and anyone can type one.

Talks to Stripe over its REST API with httpx rather than the SDK - the app
needs four calls, and the signature verification below is the only subtle
part either way.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import httpx

from . import config, db

API = "https://api.stripe.com/v1"
WEBHOOK_TOLERANCE = 300      # five minutes, as Stripe recommends


class BillingError(RuntimeError):
    pass


def _auth() -> dict:
    if not config.STRIPE_SECRET_KEY:
        raise BillingError("STRIPE_SECRET_KEY is not set")
    return {"Authorization": f"Bearer {config.STRIPE_SECRET_KEY}"}


# ----------------------------------------------------------------------
# taking the money
# ----------------------------------------------------------------------
def create_checkout_session(user_email: str, user_id: int) -> str:
    """Return the URL to send the customer to."""
    data = {
        "mode": "subscription",
        "line_items[0][price]": config.STRIPE_PRICE_ID,
        "line_items[0][quantity]": "1",
        "customer_email": user_email,
        "success_url": f"{config.BASE_URL}/billing/done?ok=1",
        "cancel_url": f"{config.BASE_URL}/billing/done?ok=0",
        # Comes back on the webhook. It is how the payment is tied to the
        # account, and it is why the redirect does not need to be trusted.
        "client_reference_id": str(user_id),
        "metadata[user_id]": str(user_id),
        "subscription_data[metadata][user_id]": str(user_id),
        "allow_promotion_codes": "true",
    }
    try:
        r = httpx.post(f"{API}/checkout/sessions", headers=_auth(), data=data,
                       timeout=30)
    except httpx.HTTPError as exc:
        raise BillingError(f"could not reach Stripe: {exc}") from exc
    if r.status_code != 200:
        raise BillingError(f"Stripe refused the checkout ({r.status_code}): "
                           f"{r.text[:300]}")
    return r.json()["url"]


def create_portal_session(customer_id: str) -> str:
    """Where a customer goes to cancel or change a card. Legally they must
    be able to, and doing it themselves is cheaper than emailing you."""
    r = httpx.post(f"{API}/billing_portal/sessions", headers=_auth(),
                   data={"customer": customer_id,
                         "return_url": f"{config.BASE_URL}/account"},
                   timeout=30)
    if r.status_code != 200:
        raise BillingError(f"Stripe refused the portal ({r.status_code})")
    return r.json()["url"]


# ----------------------------------------------------------------------
# believing the money arrived
# ----------------------------------------------------------------------
def verify_webhook(payload: bytes, signature_header: str) -> dict:
    """Check a webhook really came from Stripe, and recently.

    Raises BillingError on anything suspicious. The caller must not act on a
    payload this did not return.
    """
    if not config.STRIPE_WEBHOOK_SECRET:
        raise BillingError("STRIPE_WEBHOOK_SECRET is not set")
    if not signature_header:
        raise BillingError("no Stripe-Signature header")

    timestamp, signatures = None, []
    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not signatures:
        raise BillingError("malformed Stripe-Signature header")

    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        raise BillingError("bad timestamp in Stripe-Signature") from None
    if age > WEBHOOK_TOLERANCE:
        # Stops a captured webhook being replayed later.
        raise BillingError("webhook timestamp outside tolerance")

    expected = hmac.new(
        config.STRIPE_WEBHOOK_SECRET.encode(),
        b"%s.%s" % (timestamp.encode(), payload),
        hashlib.sha256).hexdigest()

    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise BillingError("webhook signature did not match")

    import json
    try:
        return json.loads(payload)
    except ValueError as exc:
        raise BillingError("webhook body was not JSON") from exc


# Events that actually change whether someone may use the app. Anything else
# Stripe sends is acknowledged and ignored.
HANDLED = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
}


def apply_event(event: dict) -> str:
    """Update one user's billing state from a verified event."""
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if kind not in HANDLED:
        return f"ignored {kind}"

    user_id = None
    meta = obj.get("metadata") or {}
    if meta.get("user_id"):
        user_id = int(meta["user_id"])
    elif obj.get("client_reference_id"):
        user_id = int(obj["client_reference_id"])

    customer = obj.get("customer")
    if user_id is None and customer:
        row = db.user_by_stripe_customer(customer)
        if row:
            user_id = row["id"]
    if user_id is None:
        return f"{kind}: no user could be identified"

    if kind == "checkout.session.completed":
        db.set_billing(user_id, customer_id=customer,
                       subscription_id=obj.get("subscription"),
                       status="active")
        return f"{kind}: user {user_id} active"

    if kind == "invoice.payment_failed":
        db.set_billing(user_id, status="past_due")
        return f"{kind}: user {user_id} past_due"

    if kind == "customer.subscription.deleted":
        db.set_billing(user_id, status="canceled", paid_until=obj.get(
            "current_period_end"))
        return f"{kind}: user {user_id} canceled"

    # created / updated
    db.set_billing(user_id, customer_id=customer,
                   subscription_id=obj.get("id"),
                   status=obj.get("status") or "active",
                   paid_until=obj.get("current_period_end"))
    return f"{kind}: user {user_id} -> {obj.get('status')}"
