"""A notification on the phone the moment an employer writes back.

The reply email the app already sends works for anybody who reads their
email. A job search is lived on a phone, though, and the moment somebody
answers is the one moment worth interrupting them for - so it is also the
only thing this ever pushes. No "you have not logged in", no tips, no
marketing. A channel used for anything else gets switched off, and then it
is not there for the reply.

Web Push, with the open-source pywebpush library and a VAPID key pair this
site holds itself: no third-party service, no account, nothing to pay. The
message is encrypted to the browser's own keys, so the push service
(Google's, Apple's or Mozilla's, depending on the browser) delivers it but
cannot read it. It still says only the employer's name.

On an iPhone it works once the site has been added to the home screen
(iOS 16.4 onwards), which the install banner already asks for.

Dormant until VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are set. Switch:
PUSH_ENABLED (default on).
"""

from __future__ import annotations

import json
import logging

from jobseeker import settings

from . import config, db

log = logging.getLogger(__name__)


# The browsers' own push services. The server POSTs to whatever address a
# browser hands it, so anything else is refused rather than trusted.
PUSH_SERVICES = ("fcm.googleapis.com", "updates.push.services.mozilla.com",
                 "push.apple.com", "notify.windows.com")


def is_push_service(endpoint: str) -> bool:
    from urllib.parse import urlsplit
    if not endpoint or len(endpoint) > 1000:
        return False
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(
        host == s or host.endswith("." + s) for s in PUSH_SERVICES)


def public_key() -> str:
    return settings.text("VAPID_PUBLIC_KEY")


def available() -> bool:
    return (settings.flag("PUSH_ENABLED") and bool(public_key())
            and bool(settings.text("VAPID_PRIVATE_KEY")))


def subject() -> str:
    """Who the push services should contact about misuse of this key."""
    chosen = settings.text("VAPID_SUBJECT")
    if chosen:
        return chosen
    if config.CONTACT_EMAIL:
        return f"mailto:{config.CONTACT_EMAIL}"
    return config.BASE_URL


def reply_message(companies: list[str]) -> dict:
    if len(companies) == 1:
        title = f"{companies[0]} wrote back"
    else:
        title = f"{len(companies)} employers wrote back"
    return {"title": title,
            # Only that something arrived - the app never reads the mail.
            "body": "Something arrived from them. It is in your inbox now.",
            "url": "/applications?show=heard"}


def send(user_id: int, message: dict, *, webpush=None) -> int:
    """Push `message` to every browser this person switched it on in.
    Returns how many were delivered. Never raises."""
    if not available():
        return 0
    if webpush is None:
        try:
            from pywebpush import webpush
        except ImportError:
            log.warning("push: pywebpush is not installed")
            return 0
    delivered = 0
    for row in db.push_subscriptions(user_id):
        info = {"endpoint": row["endpoint"],
                "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}}
        try:
            webpush(subscription_info=info, data=json.dumps(message),
                    vapid_private_key=settings.text("VAPID_PRIVATE_KEY"),
                    vapid_claims={"sub": subject()}, ttl=86400)
            delivered += 1
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            if status in (404, 410):
                # The browser has unsubscribed or gone. Stop asking.
                db.delete_push_subscription(row["endpoint"])
            else:
                log.warning("push to user %s failed: %s", user_id, exc)
    return delivered


def tell_about_replies(user_id: int, companies: list[str], *,
                       webpush=None) -> int:
    if not companies:
        return 0
    return send(user_id, reply_message(companies), webpush=webpush)
