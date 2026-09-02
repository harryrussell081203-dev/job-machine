"""How a finished letter actually reaches an employer.

This is the file with the product decision in it, so it is worth stating the
reasoning rather than leaving it to be rediscovered.

Sending mail *as* a user needs one of three things:

  1. **Gmail OAuth.** `gmail.send` is a Google restricted scope. A public app
     using it needs verification plus an annual third-party security
     assessment (CASA), which runs into five figures. Closed to a solo
     operator.
  2. **Their SMTP credentials.** Works today - it is what the original
     machine does for its one user - but a hosted app doing it holds
     credentials granting full send access to strangers' mailboxes. One
     breach is the whole business plus somebody else's inbox.
  3. **Sending from our own domain on their behalf.** No credentials, but the
     letter arrives as "via jobmachine", and the entire reason these get
     answered is that they read as one person writing to another. It would
     degrade the product's only real advantage.

So the default is a fourth option: **hand-off**. The app does every hard part
- finding the listing, scoring it, digging out a real named address, writing
the letter to the rules - and the user presses send from their own mail
client. One click each, from their own address, with their own signature and
reputation.

That keeps the product legal, cheap and honest, and it removes the credential
vault entirely. `smtp` remains implemented for operators who decide the
trade-off differently, and is per-user rather than global.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import quote

from . import config


class DeliveryError(RuntimeError):
    pass


def mailto_link(to_email: str, subject: str, body: str) -> str:
    """A link that opens the user's own mail client with the letter ready.

    Long bodies are fine in practice: browsers and clients handle a few
    thousand characters, and these letters are 60-90 words by design.
    """
    return (f"mailto:{quote(to_email or '')}"
            f"?subject={quote(subject or '', safe='')}"
            f"&body={quote(body or '', safe='')}")


def gmail_compose_link(to_email: str, subject: str, body: str) -> str:
    """The same thing for people who live in Gmail on the web."""
    return ("https://mail.google.com/mail/?view=cm&fs=1"
            f"&to={quote(to_email or '')}"
            f"&su={quote(subject or '', safe='')}"
            f"&body={quote(body or '', safe='')}")


def send_via_smtp(*, host: str, port: int, username: str, password: str,
                  to_email: str, subject: str, body: str,
                  attachment: tuple[str, bytes] | None = None,
                  reply_to: str = "", display_name: str = "") -> None:
    """Send one letter, using credentials the user supplied.

    Deliberately takes the credentials as arguments rather than reading them
    from anywhere: nothing in this module decides to store them, so nothing in
    this module can leak them.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    # A real name on the From line, because a letter from "Harry Russell"
    # reads as a person and one from a bare address reads as a mailshot.
    msg["From"] = (formataddr((display_name, username)) if display_name
                   else username)
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    if attachment:
        filename, blob = attachment
        maintype, _, subtype = guess_attachment_type(filename).partition("/")
        msg.add_attachment(blob, maintype=maintype, subtype=subtype,
                           filename=filename)

    _connect_and(host, port, username, password, lambda s: s.send_message(msg))


def guess_attachment_type(filename: str) -> str:
    """The right MIME type for a CV.

    Worth getting right: a .docx sent as application/pdf is rejected outright
    by some filters, and a CV that silently never arrives is worse than no CV.
    """
    lower = (filename or "").lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return ("application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document")
    if lower.endswith(".doc"):
        return "application/msword"
    if lower.endswith((".txt", ".md")):
        return "text/plain"
    return "application/octet-stream"


# Hosts people actually use, so the setup screen can ask for an address and
# work the rest out. Getting this wrong is the most common way a correct
# password looks broken.
KNOWN_HOSTS = {
    "gmail.com": ("smtp.gmail.com", 465),
    "googlemail.com": ("smtp.gmail.com", 465),
    "outlook.com": ("smtp-mail.outlook.com", 587),
    "hotmail.com": ("smtp-mail.outlook.com", 587),
    "live.co.uk": ("smtp-mail.outlook.com", 587),
    "live.com": ("smtp-mail.outlook.com", 587),
    "yahoo.co.uk": ("smtp.mail.yahoo.com", 465),
    "yahoo.com": ("smtp.mail.yahoo.com", 465),
    "icloud.com": ("smtp.mail.me.com", 587),
    "me.com": ("smtp.mail.me.com", 587),
}


def guess_host(address: str):
    """(host, port) for a well-known provider, or None to make the user say."""
    domain = (address or "").split("@")[-1].strip().lower()
    return KNOWN_HOSTS.get(domain)


def verify(*, host: str, port: int, username: str, password: str) -> None:
    """Prove these credentials work, before anything is stored.

    Raises DeliveryError with something a person can act on. The alternative -
    accepting them and finding out at 9am on a scheduled run - means the user
    believes their letters are going out when they are not, which is the worst
    failure this product has.
    """
    _connect_and(host, port, username, password, lambda s: None)


def _connect_and(host, port, username, password, action):
    """One connection routine for both verify() and send, so a working
    verification cannot pass while sending fails on a different code path."""
    try:
        context = ssl.create_default_context()
        if int(port) == 587:
            # STARTTLS rather than implicit TLS. Outlook and iCloud only offer
            # this one, and SMTP_SSL against 587 hangs rather than refusing.
            with smtplib.SMTP(host, int(port), timeout=30) as s:
                s.starttls(context=context)
                s.login(username, password)
                return action(s)
        with smtplib.SMTP_SSL(host, int(port), context=context,
                              timeout=30) as s:
            s.login(username, password)
            return action(s)
    except smtplib.SMTPAuthenticationError as exc:
        raise DeliveryError(
            "that mail account rejected the password. If this is Gmail or "
            "Outlook you need an app password rather than the one you type "
            "into the website - your normal password will always be refused "
            "here, even when it is correct."
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(
            f"could not reach {host} on port {port}: {exc}") from exc


def mode() -> str:
    m = (config.DELIVERY_MODE or "handoff").lower()
    return m if m in ("handoff", "smtp") else "handoff"
