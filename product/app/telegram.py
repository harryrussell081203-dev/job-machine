"""The Recruited bot on Telegram: send it a company or an advert, get the
real address back.

It is the free /find tool somewhere people already are. Anybody who messages
it gets an answer, and the Share button puts it in front of the next person
who is job hunting.

What it does not do, because Telegram does not allow it and because it
would be wrong anyway: message anybody who has not messaged it first. It
never starts a conversation. In a group it answers only its own /find
command, so it is never noise in somebody else's chat.

Nothing anybody sends is stored. The chat id is kept only as a rate-limit
key, which the rate limiter deletes within hours.

How it is wired: Telegram POSTs each message to a secret path on this site
(a webhook), and the reply goes back in the same HTTP response, so no
outbound call is needed to answer. The webhook is registered once, at
startup, whenever the token or the site address changes.

Dormant until TELEGRAM_BOT_TOKEN is set. Switch: TELEGRAM_ENABLED.
"""

from __future__ import annotations

import hashlib
from urllib.parse import quote

from jobseeker import settings
from jobseeker.pipeline import contacts, discover

from . import config

PER_CHAT = (20, 3600)
MAX_TEXT = 20_000
TIER = {3: "named person", 2: "hiring inbox", 1: "generic inbox"}


def token() -> str:
    return settings.text("TELEGRAM_BOT_TOKEN")


def enabled() -> bool:
    return settings.flag("TELEGRAM_ENABLED") and bool(token())


def username() -> str:
    return settings.text("TELEGRAM_BOT_USERNAME").lstrip("@")


def _derive(label: str) -> str:
    return hashlib.sha256(f"{label}:{token()}".encode()).hexdigest()[:40]


def path_secret() -> str:
    """The webhook path, derived from the token so there is nothing else to
    set. Somebody without the token cannot find the endpoint."""
    return _derive("path")


def header_secret() -> str:
    """Sent by Telegram on every call (secret_token), checked on arrival."""
    return _derive("header")


def webhook_url() -> str:
    return f"{config.BASE_URL}/telegram/{path_secret()}"


def register(*, post=None, get_meta=None, set_meta=None) -> str:
    """Point Telegram at this site, once per token and address. Never raises."""
    if not enabled():
        return "off"
    marker = hashlib.sha256(webhook_url().encode()).hexdigest()[:16]
    if get_meta and get_meta("telegram_webhook") == marker:
        return "already registered"
    if post is None:
        import httpx
        post = httpx.post
    try:
        r = post(f"https://api.telegram.org/bot{token()}/setWebhook",
                 json={"url": webhook_url(), "secret_token": header_secret(),
                       "allowed_updates": ["message"],
                       "drop_pending_updates": True},
                 timeout=15)
        ok = r.status_code == 200 and (r.json() or {}).get("ok")
    except Exception as exc:
        return f"could not register: {exc}"
    if not ok:
        return f"could not register: HTTP {r.status_code}"
    if set_meta:
        set_meta("telegram_webhook", marker)
    return "registered"


HELLO = (
    "Send me the name of a company you want to work for, or paste a whole job "
    "advert, and I'll find a real email address to write to: one the "
    "employer published themselves. I never guess an address.\n\n"
    "It's free. The same tool is at {site}/find")


def _share_markup() -> dict | None:
    if not username():
        return None
    link = f"https://t.me/{username()}"
    text = ("Free bot: send it a company name and it finds a real email "
            "address to apply to")
    return {"inline_keyboard": [[{
        "text": "Share with someone job hunting",
        "url": (f"https://t.me/share/url?url={quote(link, safe='')}"
                f"&text={quote(text, safe='')}")}]]}


def _looks_like_advert(text: str) -> bool:
    return "@" in text or len(text) > 160 or text.count("\n") >= 3


def answer(text: str, *, lookup=None) -> str:
    """The reply to one message: a company name or an advert."""
    text = (text or "").strip()[:MAX_TEXT]
    if not text or text.lower() in ("/start", "/help"):
        return HELLO.format(site=config.BASE_URL)

    if _looks_like_advert(text):
        found = contacts.rank(contacts.clean_emails(discover.emails_in(text)))
        where = "in the advert"
        domain = None
    else:
        from . import company_lookup
        from . import db
        result = (lookup or company_lookup.lookup)(
            text, store=db._PlaceCache())
        domain = result.get("domain") or ""
        if not domain:
            return (f"I couldn't be sure which website is {text}'s, so I "
                    "didn't check anything rather than risk the wrong firm. "
                    "Try the full name as the company writes it, or paste "
                    "the job advert.")
        found = contacts.rank(result.get("emails") or [])
        where = f"on {domain}"

    if not found:
        if domain:
            return (f"{domain} doesn't publish an email address on its main "
                    "pages, and I don't guess. If you have the job advert, "
                    "paste it here: the contact is often in the small print.")
        return ("There's no email address in that advert. That's normal, a "
                "lot of adverts route you to a portal. Send me the company's "
                "name and I'll check their own website.")

    lines = [f"Found {len(found)} {where}:"]
    for c in found[:5]:
        label = TIER.get(c.get("tier"), "address")
        name = f", {c['name'].title()}" if c.get("name") else ""
        lines.append(f"• {c['email']} ({label}{name})")
    lines.append("")
    lines.append("Write to the top one. Keep it to 60 to 90 words: the job, "
                 "one detail from the advert, two things you've done, one "
                 "question.")
    lines.append(f"Is your letter sounding like AI? {config.BASE_URL}"
                 "/tools/cover-letter-ai-check")
    return "\n".join(lines)


def reply_to(update: dict, *, lookup=None, allow=None) -> dict | None:
    """The sendMessage call to return to Telegram, or None to stay quiet."""
    message = (update or {}).get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text") or ""
    chat_id = chat.get("id")
    if chat_id is None or not text:
        return None
    if chat.get("type") != "private":
        # In a group, only its own command. Never noise in other people's chat.
        if not text.startswith("/find"):
            return None
        text = text.split(None, 1)[1] if " " in text else ""
    if allow is not None and not allow(chat_id):
        body = "That's a lot of lookups in an hour. Try again a bit later."
    else:
        body = answer(text, lookup=lookup)
    out = {"method": "sendMessage", "chat_id": chat_id, "text": body,
           "disable_web_page_preview": True}
    markup = _share_markup()
    if markup:
        out["reply_markup"] = markup
    return out
