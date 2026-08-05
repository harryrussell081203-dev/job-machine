"""
Getting past the door: accounts, and the emails that activate them.

WHY THIS EXISTS
---------------
Roughly a third of the applications in Harry's queue are not applications at
all until he has an account. Workday, Taleo, iCIMS and most employers' own
portals put a sign-in wall in front of the form, and the agent has been
recording every one of them as 'do this one by hand'. That is 35 or so jobs
sitting untouched because of a five-field signup form.

THE PASSWORD, AND WHY IT IS NEVER WRITTEN DOWN
----------------------------------------------
data/state.json is committed to a PUBLIC repository on every run. Anything
written there is published. So no password is ever stored - not encrypted,
not encoded, not at all. There are two ways to have one anyway:

  PORTAL_PASSWORD       one password everywhere, Harry's own choice, held in
                        a repo secret. This is what he asked for, and the
                        reason is good: a derived password is unguessable and
                        also unknowable, so he could never sign in himself to
                        check an application or withdraw one. Whatever he
                        picks is padded to something every portal accepts -
                        'password123' becomes 'Password123!', which is the
                        same password to the person typing it and the
                        difference between an account existing and a signup
                        rejected on a rules message.

  PORTAL_PASSWORD_SALT  the fallback when no shared password is set. The
                        password is derived per site,
                        HMAC-SHA256(salt, domain), so a breach at one
                        employer says nothing about any other.

The trade is real and it is his to make: one password across every portal
means one breach exposes the lot. These are job portals holding a CV he
sends to strangers anyway, and he would rather be able to log in. Either
way, the login is EMAILED to him the moment an account is made, so his own
inbox becomes the record of where accounts exist.

WHAT IT WILL NOT DO
-------------------
  - It does not defeat bot protection. A signup page carrying a CAPTCHA is
    handed over exactly like an application carrying one.
  - It does not tick a box that says something is true. Terms and conditions
    are a contract, and agreeing to one on somebody's behalf is not a
    checkbox, whatever it looks like on the page.
  - It creates an account only where Harry is actually applying for a job.
"""
import hashlib
import hmac
import re
import time
from datetime import datetime, timedelta, timezone

import job_machine as jm

ACCOUNTS = "portal_accounts"
SALT = jm.env_str("PORTAL_PASSWORD_SALT")
# One password everywhere, if Harry sets one. He asked for this: a derived
# password is unguessable and also unknowable, and he could never sign in to
# one of these portals himself without asking the machine.
#
# It lives in the repo secret PORTAL_PASSWORD and nowhere else - putting it in
# the code would publish it, since this repository is public. Whatever he
# chooses is padded to something every portal will accept, because the most
# common reason a signup fails is a password rule, and an account that will
# not be created is worth nothing however memorable the password was.
SHARED = jm.env_str("PORTAL_PASSWORD")

# The wall. Any of these, and there is no form to fill until there is a login.
LOGIN_WALL = re.compile(
    r"sign in to (apply|continue)|you must (be )?(sign|log)ged in|"
    r"create an account to apply|please (sign|log) in|"
    r"existing user|returning (applicant|candidate)|"
    r"start your application by creating", re.I)
# The way through it.
SIGNUP_TEXTS = ("create account", "create an account", "register", "sign up",
                "new user", "create profile", "join now", "get started")
# A field on a signup form, as opposed to an application form.
PASSWORD_FIELD = re.compile(r"password|passcode", re.I)
CONFIRM_FIELD = re.compile(r"confirm|repeat|re-?enter|again|verify", re.I)


def configured():
    return bool(SALT or SHARED)


def acceptable(password):
    """The same password, in a shape every portal will take.

    Portals reject far more signups over password rules than anything else:
    twelve characters, an upper, a lower, a digit and a symbol is the union
    of what the big ones demand. 'password123' fails three of those, so it is
    padded rather than refused - the point is an account that exists, and
    'Password123!' is the same password to the person typing it."""
    padded = password
    if not re.search(r"[a-z]", padded):
        padded += "a"
    if not re.search(r"[A-Z]", padded):
        padded = padded[:1].upper() + padded[1:]
        if not re.search(r"[A-Z]", padded):
            padded += "A"
    if not re.search(r"\d", padded):
        padded += "1"
    if not re.search(r"[^\w]", padded):
        padded += "!"
    while len(padded) < 12:
        padded += "!"
    return padded


def password_for(domain):
    """The password for this employer's portal.

    Shared if Harry has set one, so he can sign in himself without asking the
    machine. Otherwise derived per site, which is stronger but unknowable to
    him. Either way it is never written down: this repository is public and
    data/state.json is committed on every run."""
    if SHARED:
        return acceptable(SHARED)
    if not SALT:
        raise RuntimeError(
            "neither PORTAL_PASSWORD nor PORTAL_PASSWORD_SALT is set")
    digest = hmac.new(SALT.encode(), (domain or "").lower().encode(),
                      hashlib.sha256).digest()
    # Portals demand upper, lower, digit and symbol, and reject anything long.
    body = _b58(digest)[:14]
    return f"Hr{body}9!"


_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _b58(raw):
    """Readable characters only - some portals mangle the rest."""
    number = int.from_bytes(raw, "big")
    out = []
    while number and len(out) < 24:
        number, index = divmod(number, len(_ALPHABET))
        out.append(_ALPHABET[index])
    return "".join(out)


def domain_of(url):
    return re.sub(r"^https?://", "", url or "").split("/")[0].lower()


def account(state, domain):
    return state.setdefault(ACCOUNTS, {}).get(domain)


def remember_account(state, domain, verified=False):
    """What is recorded: that an account exists. Never how to open it."""
    entry = state.setdefault(ACCOUNTS, {}).setdefault(
        domain, {"email": jm.GMAIL_ADDRESS, "created_at": jm.now()})
    if verified:
        entry["verified_at"] = jm.now()
    entry["seen_at"] = jm.now()
    return entry


def needs_an_account(page, fields):
    """Is this a wall rather than a form?

    Asked only when the ordinary form-finding has already failed, so a page
    with a real application on it never reaches here."""
    try:
        text = page.inner_text("body")[:4000]
    except Exception:
        return False
    if LOGIN_WALL.search(text):
        return True
    # A page whose only inputs are an email and a password is a login page
    # whatever it says in words.
    kinds = [(f.get("type") or "") for f in fields]
    labels = " ".join((f.get("label") or f.get("name") or "") for f in fields)
    return (len(fields) <= 3 and "password" in kinds
            and not re.search(r"cv|resume|cover", labels, re.I))


def tell_harry(domain, job=None):
    """Email him the login, because an account he cannot get into is no use.

    This is the gap either way. A derived password is unguessable and also
    unknowable - he could never sign in to check an application, reply to a
    message in the portal, or withdraw one. A shared password he already
    knows still leaves him with no record of WHERE accounts exist. So the
    machine sends the details to his own inbox the moment one is made, and
    that inbox becomes the list."""
    try:
        password = password_for(domain)
    except RuntimeError:
        return False
    where = f" while applying to {job.get('company')}" if job else ""
    body = (
        f"An account was created for you at {domain}{where}.\n\n"
        f"  site:     https://{domain}\n"
        f"  username: {jm.GMAIL_ADDRESS}\n"
        f"  password: {password}\n\n"
        f"You can sign in yourself any time - to check an application, reply "
        f"to a message in their portal, or withdraw one.\n\n"
        f"This email is the record of which portals you have accounts on. "
        f"Nothing is written into the repository, which is public.\n")
    try:
        jm.send_email(jm.GMAIL_ADDRESS,
                      f"[job-machine] account created at {domain}",
                      body, attach_cv=False)
        print(f"[account] login for {domain} emailed to Harry")
        return True
    except Exception as e:
        print(f"[account] could not email the login: {e}")
        return False


# ======================================================================
# THE VERIFICATION EMAIL
# ======================================================================
VERIFY_LINK = re.compile(
    r"https?://[^\s\"'<>\)\]]*"
    r"(?:verify|confirm|activate|validation|token|register)"
    r"[^\s\"'<>\)\]]*", re.I)


def verification_link(domain, since, tries=6, wait=15):
    """The activation link the portal just emailed, or None.

    Waits, because the email is in flight while this is asked. Six looks
    fifteen seconds apart is a minute and a half, which is longer than any of
    these take and short enough not to spend the run on one signup."""
    if not (jm.GMAIL_ADDRESS and jm.GMAIL_APP_PASSWORD):
        return None
    root = ".".join(domain.split(".")[-2:]) if domain else ""
    for attempt in range(tries):
        link = _search_for_link(root, since)
        if link:
            print(f"[account] verification link found for {domain}")
            return link
        if attempt < tries - 1:
            time.sleep(wait)
    print(f"[account] no verification email from {domain} after "
          f"{tries * wait}s")
    return None


def _search_for_link(root, since):
    import email as email_mod
    import imaplib
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", timeout=jm.IMAP_TIMEOUT)
        conn.login(jm.GMAIL_ADDRESS, jm.GMAIL_APP_PASSWORD)
        conn.select("INBOX")
    except Exception as e:
        print(f"[account] inbox unreachable: {e}")
        return None
    try:
        window = (since - timedelta(minutes=5)).strftime("%d-%b-%Y")
        status, data = conn.search(None, "SINCE", window)
        if status != "OK" or not data or not data[0].split():
            return None
        for num in reversed(data[0].split()[-40:]):
            try:
                status, msgdata = conn.fetch(num, "(RFC822)")
                msg = email_mod.message_from_bytes(msgdata[0][1])
            except Exception:
                continue
            sender = (msg.get("From") or "").lower()
            if root and root not in sender:
                continue
            when = jm.parse_ts(msg.get("Date")) or _rfc_date(msg.get("Date"))
            if when and when < since - timedelta(minutes=5):
                continue
            body = jm._message_text(msg)
            for match in VERIFY_LINK.findall(body):
                if "unsubscribe" in match.lower():
                    continue
                return match.rstrip(".,)>\"'")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return None


def _rfc_date(raw):
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(raw)
        if parsed and not parsed.tzinfo:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


# ======================================================================
# CREATING THE ACCOUNT
# ======================================================================
# A checkbox that says "I agree to the terms" is assent, and Harry has asked
# for the account to be created - assent he has already given. A checkbox that
# says "I certify this information is true and complete" is a statement of
# fact about him, and that is his to make, not the machine's. The two look
# identical on a page and are not remotely the same thing.
AGREE_TO_TERMS = re.compile(
    r"terms|privacy|conditions|policy|data protection|gdpr|"
    r"processing of my (data|information)", re.I)
MARKETING = re.compile(
    r"marketing|newsletter|updates about|keep me (posted|informed)|"
    r"send me|third part|partners", re.I)
A_CLAIM_OF_FACT = re.compile(
    r"i (certify|declare|confirm that)|true and (complete|accurate)|"
    r"to the best of my knowledge", re.I)


def plan_signup(fields, domain, answers):
    """(plan, blockers). Only the boxes a signup form really has."""
    plan, blockers = [], []
    seen_password = False
    for field in fields:
        label = " ".join(str(field.get(k) or "") for k in
                         ("label", "aria_label", "name", "id", "placeholder"))
        kind = field.get("type") or "text"

        if kind == "checkbox":
            if A_CLAIM_OF_FACT.search(label):
                blockers.append(f"a declaration to agree to: {label[:60]}")
            elif MARKETING.search(label):
                continue          # never opt him into marketing
            elif AGREE_TO_TERMS.search(label) or field.get("required"):
                plan.append({"field": field, "kind": "check", "value": True})
            continue

        if kind == "password" or PASSWORD_FIELD.search(label):
            plan.append({"field": field, "kind": "text",
                         "value": password_for(domain)})
            seen_password = True
            continue
        if re.search(r"e-?mail|username|user id", label, re.I):
            plan.append({"field": field, "kind": "text",
                         "value": jm.GMAIL_ADDRESS})
            continue
        for key, pattern in (("first_name", r"first|forename|given"),
                             ("last_name", r"last|surname|family"),
                             ("phone", r"phone|mobile|telephone"),
                             ("postcode", r"post ?code|zip")):
            if re.search(pattern, label, re.I) and answers.get(key):
                plan.append({"field": field, "kind": "text",
                             "value": answers[key]})
                break
        else:
            if field.get("required") and kind not in ("hidden", "submit"):
                blockers.append(f"required and not understood: {label[:60]}")

    if not seen_password:
        blockers.append("no password field - this is not a signup form")
    return plan, blockers


def sign_up(page, state, job, answers):
    """Create an account on this portal. True if the door is now open.

    Everything here is one step of a job Harry asked for: he cannot apply
    without an account, so creating one is part of applying. It stops at
    anything that is a statement about him rather than a step in a process."""
    import portal_agent as pa

    if not configured():
        print("[account] PORTAL_PASSWORD_SALT is not set, cannot create one")
        return False
    domain = domain_of(page.url)

    # Get to the signup form: many portals show sign-in first.
    for text in SIGNUP_TEXTS:
        for role in ("link", "button"):
            try:
                control = page.get_by_role(role, name=text, exact=False).first
                if control.is_visible():
                    control.click(timeout=pa.PAGE_TIMEOUT_MS)
                    page.wait_for_timeout(2500)
                    raise StopIteration
            except StopIteration:
                break
            except Exception:
                continue
        else:
            continue
        break

    if pa.captcha_kind(page) == "challenge":
        job["portal_reason"] = ("account needed and the signup page runs a "
                                "bot check - create it by hand once and the "
                                "agent can use it from then on")
        return False

    surface, fields = pa.form_surface(page)
    plan, blockers = plan_signup(pa.visible_fields(fields), domain, answers)
    if blockers:
        job["portal_reason"] = f"signup needs you: {blockers[0]}"
        print(f"[account] not creating one at {domain}: {blockers[0]}")
        return False

    started = datetime.now(timezone.utc)
    filled, failed = pa.apply_plan(surface, plan)
    if failed:
        job["portal_reason"] = f"signup form would not fill: {failed[0]}"
        return False
    if not pa.click_submit(surface):
        job["portal_reason"] = "could not find the button to create the account"
        return False
    page.wait_for_timeout(4000)
    remember_account(state, domain)
    print(f"[account] created at {domain} with {len(filled)} field(s)")
    tell_harry(domain, job)

    # Most portals will not let the account in until the address is confirmed.
    link = verification_link(domain, started)
    if link:
        try:
            page.goto(link, wait_until="domcontentloaded",
                      timeout=pa.PAGE_TIMEOUT_MS)
            page.wait_for_timeout(2500)
            remember_account(state, domain, verified=True)
            print(f"[account] verified {domain}")
        except Exception as e:
            print(f"[account] verification link would not open: {e}")
    return True
