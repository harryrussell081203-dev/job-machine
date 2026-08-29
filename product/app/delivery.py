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
                  attachment: tuple[str, bytes] | None = None) -> None:
    """Opt-in automatic sending, using credentials the user supplied.

    Deliberately takes the credentials as arguments rather than reading them
    from anywhere: nothing in this module decides to store them, so nothing
    in this module can leak them.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = to_email
    msg.set_content(body)

    if attachment:
        filename, blob = attachment
        msg.add_attachment(blob, maintype="application", subtype="pdf",
                           filename=filename)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as s:
            s.login(username, password)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise DeliveryError(
            "the mail account rejected those credentials - if this is Gmail, "
            "it needs an app password rather than the account password"
        ) from exc
    except Exception as exc:
        raise DeliveryError(str(exc)) from exc


def mode() -> str:
    m = (config.DELIVERY_MODE or "handoff").lower()
    return m if m in ("handoff", "smtp") else "handoff"
