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
     letter arrives as "via recruited", and the entire reason these get
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

import imaplib
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import quote

from . import config


class DeliveryError(RuntimeError):
    pass


class DeliveryAuthError(DeliveryError):
    """The mail server looked at these credentials and refused them.

    A definite answer, and the only one that justifies turning a user away.
    """


class DeliveryUnreachableError(DeliveryError):
    """We never got to ask. The server was unreachable from here.

    Not the same thing as a bad password, and the difference decides whether
    a user can use this product at all. Free hosting blocks outbound SMTP
    almost universally - Render, Oracle and most others shut ports 25, 465
    and 587 because that is how spam gets sent - so on the free plan this is
    raised for every correct password in the world.

    Treating it as "wrong password" locked every user out of the one feature
    the product exists for, while telling them their own credentials were at
    fault. Kept separate so the caller can say "not checked yet" instead of
    an accusation it cannot support.
    """


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
                  reply_to: str = "", display_name: str = "",
                  from_address: str = "") -> None:
    """Send one letter, using credentials the user supplied.

    Deliberately takes the credentials as arguments rather than reading them
    from anywhere: nothing in this module decides to store them, so nothing in
    this module can leak them.

    `from_address` exists because the two are not always the same thing. On a
    user's own mailbox they are: you authenticate as harry@gmail.com and the
    letter comes from harry@gmail.com. On a Recruited address you
    authenticate to Resend as the literal username "resend" and the letter
    comes from harry.russell@mail.recruited.org.uk - and putting "resend" on
    the From line would be both wrong and faintly comic. Defaults to the
    username so every existing caller is unchanged.
    """
    sender = from_address or username
    msg = EmailMessage()
    msg["Subject"] = subject
    # A real name on the From line, because a letter from "Harry Russell"
    # reads as a person and one from a bare address reads as a mailshot.
    msg["From"] = (formataddr((display_name, sender)) if display_name
                   else sender)
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


# A Recruited address has to read as a person's, because the entire reason
# these letters get answered is that they look like one person writing to
# another. harry.russell@ passes; user4821@ or noreply@ announces a tool
# before the subject line is read.
_LOCAL_PART_OK = "abcdefghijklmnopqrstuvwxyz0123456789."


def local_part_for(name: str, fallback: str = "") -> str:
    """The local part of a Recruited address, from a person's name.

    "Harry Russell" -> "harry.russell". Accents are folded rather than
    dropped, so Björn becomes bjorn instead of bj rn, and O'Brien keeps its
    letters instead of splitting into two.
    """
    import unicodedata
    raw = unicodedata.normalize("NFKD", (name or "").strip())
    raw = "".join(c for c in raw if not unicodedata.combining(c)).lower()
    raw = raw.replace("'", "").replace("’", "")
    cleaned = "".join(c if c in _LOCAL_PART_OK else " " for c in raw)
    parts = [p for p in cleaned.split() if p]
    local = ".".join(parts).strip(".")
    while ".." in local:
        local = local.replace("..", ".")
    if not local:
        # Somebody whose name is entirely outside the Latin alphabet still
        # needs an address. Falling back to the account's email local part
        # keeps it recognisable to them, and only then to a generic one.
        local = local_part_for(fallback.split("@")[0]) if fallback else ""
    return local[:40] or "applicant"


# A send gets the patient timeout: it is a background job, nobody is watching,
# and giving up on a slow-but-working server would cost a real letter.
SEND_TIMEOUT = 30

# A verification gets a short one, because somebody IS watching - they have
# just pressed a button and are looking at a spinner.
#
# Measured, not guessed. Driving the real form in a real browser on a host
# that blocks SMTP, the user waits 30.7 seconds before anything happens, and
# Harry's own three attempts are 31 seconds apart in the production logs. Half
# a minute of nothing, three times over, and then a message telling him his
# password was wrong.
#
# Cutting it short is safe here in a way it would not have been before: an
# unreachable server no longer rejects the user, it stores the account
# unverified for the sweep to prove. So the worst case of a too-short timeout
# is a working mailbox taking the "not checked yet" path and being confirmed a
# few minutes later - while the best case is the difference between a form
# that answers and a form that looks broken.
VERIFY_TIMEOUT = 8


def verify(*, host: str, port: int, username: str, password: str) -> None:
    """Prove these credentials work, before anything is stored.

    Raises DeliveryError with something a person can act on. The alternative -
    accepting them and finding out at 9am on a scheduled run - means the user
    believes their letters are going out when they are not, which is the worst
    failure this product has.
    """
    _connect_and(host, port, username, password, lambda s: None,
                 timeout=VERIFY_TIMEOUT)


def _connect_and(host, port, username, password, action,
                 timeout: int = SEND_TIMEOUT):
    """One connection routine for both verify() and send, so a working
    verification cannot pass while sending fails on a different code path."""
    try:
        context = ssl.create_default_context()
        if int(port) == 587:
            # STARTTLS rather than implicit TLS. Outlook and iCloud only offer
            # this one, and SMTP_SSL against 587 hangs rather than refusing.
            with smtplib.SMTP(host, int(port), timeout=timeout) as s:
                s.starttls(context=context)
                s.login(username, password)
                return action(s)
        with smtplib.SMTP_SSL(host, int(port), context=context,
                              timeout=timeout) as s:
            s.login(username, password)
            return action(s)
    except smtplib.SMTPAuthenticationError as exc:
        raise DeliveryAuthError(
            "that mail account rejected the password. If this is Gmail or "
            "Outlook you need an app password rather than the one you type "
            "into the website - your normal password will always be refused "
            "here, even when it is correct."
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryUnreachableError(
            f"could not reach {host} on port {port}: {exc}") from exc


# Reading the mailbox, so the tracker can notice an employer has answered
# instead of waiting to be told.
#
# A Gmail or Outlook app password already grants IMAP as well as SMTP, so this
# asks the user for nothing they have not already given. That is precisely why
# it has to be handled carefully: the credential they handed over to SEND can
# also READ everything they have, and nothing here should come close to
# exercising that.
KNOWN_IMAP_HOSTS = {
    "gmail.com": ("imap.gmail.com", 993),
    "googlemail.com": ("imap.gmail.com", 993),
    "outlook.com": ("outlook.office365.com", 993),
    "hotmail.com": ("outlook.office365.com", 993),
    "live.co.uk": ("outlook.office365.com", 993),
    "live.com": ("outlook.office365.com", 993),
    "yahoo.co.uk": ("imap.mail.yahoo.com", 993),
    "yahoo.com": ("imap.mail.yahoo.com", 993),
    "icloud.com": ("imap.mail.me.com", 993),
    "me.com": ("imap.mail.me.com", 993),
}


def guess_imap_host(address: str):
    """(host, port) for a well-known provider, or None.

    Derived from the address rather than stored, because the SMTP host was
    stored before this existed and guessing keeps every account that already
    connected working without a migration or a re-entered password.
    """
    domain = (address or "").split("@")[-1].strip().lower()
    return KNOWN_IMAP_HOSTS.get(domain)


def find_replies(*, host: str, port: int, username: str, password: str,
                 addresses) -> set:
    """Which of `addresses` have sent this mailbox anything.

    The narrowest question that answers "did they get back to us", and
    deliberately the only one asked. For each employer already written to it
    runs a single SEARCH FROM and looks at whether the result is empty.

    What this never does, and must never start doing: FETCH a message, read a
    subject, a body or an attachment, or touch any address the user did not
    already send an application to. The server does the matching and returns
    message ids; nothing is downloaded. So the blast radius of this function
    is "the app learns that Acme Ltd emailed you", which is the fact the
    tracker exists to show, and nothing else in the mailbox is legible to it.

    Unreachable is not "no replies" - it raises, and the caller leaves every
    draft exactly as it was. Silently reporting an empty set would mean a
    broken connection looked identical to a quiet week.
    """
    found = set()
    wanted = [a for a in {(a or "").strip().lower() for a in addresses} if a]
    if not wanted:
        return found
    try:
        box = imaplib.IMAP4_SSL(host, int(port), timeout=30)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise DeliveryUnreachableError(
            f"could not reach {host} on port {port}: {exc}") from exc
    try:
        try:
            box.login(username, password)
        except imaplib.IMAP4.error as exc:
            raise DeliveryAuthError(
                "that mail account rejected the password when reading the "
                "inbox. If this is Gmail you may need to allow IMAP in "
                "Settings - See all settings - Forwarding and POP/IMAP."
            ) from exc
        box.select("INBOX", readonly=True)
        for address in wanted:
            try:
                status, data = box.search(None, "FROM", f'"{address}"')
            except imaplib.IMAP4.error:
                continue          # one bad address must not lose the others
            if status == "OK" and data and data[0].split():
                found.add(address)
    finally:
        try:
            box.logout()
        except Exception:
            pass
    return found


def mode() -> str:
    m = (config.DELIVERY_MODE or "handoff").lower()
    return m if m in ("handoff", "smtp") else "handoff"
