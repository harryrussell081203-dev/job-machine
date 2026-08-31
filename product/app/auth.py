"""Sign-in by emailed link. There are no passwords in this system.

A one-person product cannot afford a password breach, and the cheapest way to
never have one is to never hold a password. A link is signed, mailed, valid
for fifteen minutes, and usable once.

Two properties matter and both are enforced rather than assumed:

  - **expiry**, from itsdangerous' timestamp signer
  - **single use**, from a token id burned in the database on first use,
    because a signed link stays valid until it expires and mail gets
    forwarded, scanned and left in browser histories
"""

from __future__ import annotations

import re
import secrets
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import config, db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

_login_signer = URLSafeTimedSerializer(config.SECRET_KEY, salt="login")
_session_signer = URLSafeTimedSerializer(config.SECRET_KEY, salt="session")


def valid_email(address: str) -> bool:
    return bool(EMAIL_RE.match((address or "").strip()))


# ----------------------------------------------------------------------
# magic links
# ----------------------------------------------------------------------
def make_login_link(email: str) -> str:
    token = _login_signer.dumps({"email": email.strip().lower(),
                                 "jti": secrets.token_urlsafe(16)})
    return f"{config.BASE_URL}/auth/verify?token={token}"


def consume_login_token(token: str) -> str | None:
    """Return the email a valid, unused, unexpired token belongs to."""
    try:
        payload = _login_signer.loads(token, max_age=config.MAGIC_LINK_MAX_AGE)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    jti, email = payload.get("jti"), payload.get("email")
    if not jti or not email:
        return None
    if not db.claim_token(jti):      # already used
        return None
    return email


# ----------------------------------------------------------------------
# sessions
# ----------------------------------------------------------------------
def make_session(user_id: int) -> str:
    return _session_signer.dumps({"uid": user_id})


def read_session(cookie: str | None) -> int | None:
    if not cookie:
        return None
    try:
        payload = _session_signer.loads(cookie, max_age=config.SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid = payload.get("uid") if isinstance(payload, dict) else None
    return uid if isinstance(uid, int) else None


# ----------------------------------------------------------------------
# sending the link
# ----------------------------------------------------------------------
SUBJECT = "Your sign-in link"


def _body(link: str) -> str:
    return ("Here is your sign-in link. It works once and expires in fifteen "
            f"minutes.\n\n{link}\n\n"
            "If you did not ask for this, ignore it - nobody can sign in without "
            "the link, and it will expire on its own.\n")


def _send_over_https(address: str, body: str) -> None:
    """Brevo's transactional endpoint, on 443.

    Exists because a free host may block every SMTP port outright, and a
    blocked port is indistinguishable from a broken mail server until you
    know to look for it.
    """
    try:
        r = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": config.BREVO_API_KEY,
                     "accept": "application/json",
                     "content-type": "application/json"},
            json={"sender": {"email": config.SMTP_ADDRESS,
                             "name": "Job Machine"},
                  "to": [{"email": address}],
                  "subject": SUBJECT,
                  "textContent": body},
            timeout=30)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"could not reach Brevo: {exc}") from exc
    if r.status_code not in (200, 201, 202):
        # The body carries the actual reason - an unverified sender is the
        # usual one, and it is worth seeing rather than guessing.
        raise RuntimeError(
            f"Brevo refused the message ({r.status_code}): {r.text[:300]}")


def _send_over_smtp(address: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = config.SMTP_ADDRESS
    msg["To"] = address
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                          context=context, timeout=30) as s:
        s.login(config.SMTP_ADDRESS, config.SMTP_PASSWORD)
        s.send_message(msg)


def send_login_email(address: str, link: str) -> None:
    """In development the link goes to the console, so no mail setup is
    needed to work on the app. In production a missing mail configuration is
    an error rather than a silently unsent link."""
    route = config.mail_route()

    if config.DEV and not route:
        print(f"\n  [dev] sign-in link for {address}:\n  {link}\n", flush=True)
        return
    if not route:
        raise RuntimeError(
            "No way to send mail is configured. Set BREVO_API_KEY with "
            "APP_SMTP_ADDRESS to send over HTTPS, or APP_SMTP_ADDRESS with "
            "APP_SMTP_PASSWORD to send over SMTP. Note that a free Render "
            "web service cannot reach an SMTP port at all.")

    body = _body(link)
    if route == "brevo":
        _send_over_https(address, body)
    else:
        _send_over_smtp(address, body)
