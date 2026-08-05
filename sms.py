"""
Text messages, sent from Harry's own phone.

HOW IT WORKS
------------
httpSMS is an app on his Android handset. The machine POSTs a message to
api.httpsms.com, the app picks it up and sends it as an ordinary SMS from his
own number, out of his own allowance. So it costs nothing, it arrives from the
number on his CV, and a reply lands in his messages where he will see it.

The key lives in the repo secret HTTPSMS_API_KEY and nowhere else. This
repository is public.

WHO GETS TEXTED, AND WHO DOES NOT
---------------------------------
Two different things share this file, and only one of them is outbound.

  INBOUND    An interview invitation is worth knowing about in the minute it
             arrives, not at 22:00 in the digest. Those come to Harry's own
             phone. No etiquette question at all: he is texting himself.

  OUTBOUND   A cold text to a hiring manager's mobile reads as intrusive and
             costs the application. A text to a consultant who has ALREADY
             WRITTEN BACK reads as normal - they work off their phones, and
             the number came from their own signature. So that is the only
             set this will write to, and even then:

               - the number has to be a mobile. Most consultant direct lines
                 are landlines, which cannot receive an SMS at all; those go
                 on the call list instead of being texted into a void
               - one text per person, ever
               - weekdays, 09:00-18:00 UK, because a text is a phone buzzing
                 on someone's bedside table if you get it wrong
               - always after the email, never instead of it
               - three a day, total

Set SMS_OUTBOUND=0 to keep the inbound alerts and turn the outbound half off.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request

import job_machine as jm

ENDPOINT = jm.env_str("HTTPSMS_ENDPOINT",
                      "https://api.httpsms.com/v1/messages/send")
API_KEY = jm.env_str("HTTPSMS_API_KEY")
# His own handset, in E.164. The app sends from this number and httpSMS
# rejects a 'from' that is not the phone it is installed on.
SMS_FROM = jm.env_str("SMS_FROM", "+447398530978")
OUTBOUND = jm.env_flag("SMS_OUTBOUND", True)
DAILY_SMS_CAP = jm.env_int("DAILY_SMS_CAP", 3)

SENT = "sms_sent"
NUMBERS = "contact_numbers"

# UK numbers as they are actually written in a signature: 01224 327 030,
# 0333 202 6500, +44 7891 169509, 07398530978. Anything shorter is a year, an
# order number or a house number, and anything longer is not a phone number.
UK_NUMBER = re.compile(r"(?:\+44\s?|\b0)(?:\d[\s.-]?){9,10}\d\b")
# Numbers that cost the caller money, and the ones that are somebody's
# switchboard menu rather than a person. Neither is worth putting in front of
# him as 'the number to ring'.
PREMIUM = re.compile(r"^\+44(9|87|84)")


def configured():
    return bool(API_KEY and SMS_FROM)


def normalise(raw):
    """A UK number in E.164, or None.

    Deliberately strict. A wrong number here is a text to a stranger."""
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("+44"):
        rest = digits[3:]
    elif digits.startswith("44") and len(digits) >= 12:
        rest = digits[2:]
    elif digits.startswith("0"):
        rest = digits[1:]
    else:
        return None
    if not rest.isdigit() or not 9 <= len(rest) <= 10:
        return None
    number = f"+44{rest}"
    if PREMIUM.match(number):
        return None
    return number


def is_mobile(number):
    """Only a mobile can receive a text. A direct line is for ringing."""
    return bool(number) and number.startswith("+447")


def numbers_in(text):
    """Every real UK number in a block of text, best guess first.

    Signatures put the direct line above the switchboard, so document order is
    already the right order."""
    out = []
    for match in UK_NUMBER.findall(text or ""):
        number = normalise(match)
        if number and number != SMS_FROM and number not in out:
            out.append(number)
    return out


QUOTE_START = re.compile(
    r"^(On\b.{0,160}\bwrote:|-{2,}\s*Original Message|From:\s)", re.I)


def own_words(text):
    """Just what this person wrote, without the quoted trail beneath it.

    The trail carries Harry's own number, and on a forwarded thread it carries
    everybody else's too. Harvesting from it would file the wrong number
    against the wrong person, and the first thing that would do is text a
    stranger."""
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or QUOTE_START.match(stripped):
            break
        lines.append(line)
    return "\n".join(lines)


def numbers_from_signature(text):
    """Numbers from the sender's own signature, not from the thread below."""
    return numbers_in(own_words(text))


def remember(state, key, number, name="", source=""):
    """File a number against whoever it belongs to."""
    if not key or not number:
        return None
    book = state.setdefault(NUMBERS, {})
    entry = book.setdefault(key, {"numbers": [], "name": name, "at": jm.now()})
    if name and not entry.get("name"):
        entry["name"] = name
    if number not in entry["numbers"]:
        entry["numbers"].append(number)
        entry["source"] = source or entry.get("source", "")
    return entry


def texted_already(state, number):
    return number in state.setdefault(SENT, {})


def sent_today(state):
    today = jm.today()
    return sum(1 for e in state.setdefault(SENT, {}).values()
               if str(e.get("at", ""))[:10] == today)


def in_texting_hours(when=None):
    """Weekdays, 09:00-18:00 UK. A text is a phone buzzing in someone's
    pocket, and there is no version of this worth waking anyone for."""
    now = when or jm.uk_now()
    return now.weekday() < 5 and 9 <= now.hour < 18


def send(to_number, body):
    """POST to httpSMS. Returns the message id, raises on anything else."""
    if not configured():
        raise RuntimeError("HTTPSMS_API_KEY is not set")
    payload = json.dumps({"content": body, "from": SMS_FROM,
                          "to": to_number}).encode()
    request = urllib.request.Request(
        ENDPOINT, data=payload, method="POST",
        headers={"Content-Type": "application/json", "x-api-key": API_KEY})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.loads(response.read() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"httpSMS {e.code}: {e.read()[:200]!r}") from None
    return (answer.get("data") or {}).get("id", "sent")


def alert_harry(body):
    """A text to his own phone. No rules beyond the key being set, because
    the only person being interrupted is him."""
    if not configured():
        return False
    try:
        send(SMS_FROM, body[:300])
        print(f"[sms] alerted Harry: {body[:60]}")
        return True
    except Exception as e:
        print(f"[sms] could not alert Harry: {e}")
        return False


def interview_alert(job):
    """The one message worth interrupting him for."""
    company = (job.get("company") or "a company").strip()
    role = (job.get("title") or "a role").strip()
    return (f"INTERVIEW: {company} replied about the {role}. "
            f"An availability reply has gone back already. Check your email.")


def may_text(state, contact):
    """(allowed, reason_if_not) for one outbound text to a consultant."""
    if not OUTBOUND:
        return False, "outbound texting is off"
    if not configured():
        return False, "no httpSMS key"
    number = contact.get("number")
    if not number:
        return False, "no number"
    if not is_mobile(number):
        return False, f"{number} is a landline, that is a call not a text"
    if not contact.get("replied"):
        return False, "they have not written back, so a text is cold"
    if texted_already(state, number):
        return False, "already texted, and once is the limit"
    if sent_today(state) >= DAILY_SMS_CAP:
        return False, f"daily cap ({DAILY_SMS_CAP})"
    if not in_texting_hours():
        return False, "outside 09:00-18:00 on a weekday"
    return True, ""


def follow_up_text(contact):
    """Short, signed, and it says why they are hearing from him."""
    first = (contact.get("name") or "").split()[0] if contact.get("name") else ""
    greeting = f"Hi {first}, " if first else "Hi, "
    return (f"{greeting}Harry Russell here - you replied to my email about "
            f"technician work in Aberdeen. Free to talk any time today if "
            f"that is easier than email. 07398 530978")


def text_contact(state, contact, body=None):
    """Send one follow-up text, obeying may_text. Returns True if it went."""
    allowed, reason = may_text(state, contact)
    if not allowed:
        print(f"[sms] {contact.get('name') or contact.get('number')}: {reason}")
        return False
    number = contact["number"]
    to_number = SMS_FROM if jm.TEST_MODE else number
    message = body or follow_up_text(contact)
    if jm.TEST_MODE:
        message = f"[TEST -> {number}] {message}"
    try:
        send(to_number, message)
    except Exception as e:
        print(f"[sms] failed {number}: {e}")
        return False
    state.setdefault(SENT, {})[number] = {
        "at": jm.now(), "name": contact.get("name"),
        "company": contact.get("company"), "test": jm.TEST_MODE}
    print(f"[sms] {'TEST' if jm.TEST_MODE else 'LIVE'} texted {to_number}")
    return True


def call_list(state):
    """Everyone worth ringing, with the number, mobiles last.

    The landlines are the point of this. A consultant's direct line cannot be
    texted and is the single most useful thing in the file, so it belongs in
    front of him rather than buried in a state file he never opens."""
    rows = []
    for key, entry in state.get(NUMBERS, {}).items():
        for number in entry.get("numbers", []):
            rows.append({"key": key, "name": entry.get("name") or key,
                         "number": number, "mobile": is_mobile(number),
                         "source": entry.get("source", "")})
    rows.sort(key=lambda r: (r["mobile"], r["name"].lower()))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test", action="store_true",
                    help="send one text to Harry's own phone, to prove the "
                         "key and the handset are wired up")
    ap.add_argument("--call-list", action="store_true",
                    help="print everyone worth ringing")
    args = ap.parse_args(argv)

    if args.call_list:
        state = jm.load()
        rows = call_list(state)
        if not rows:
            print("[sms] no numbers known yet - they come out of replies")
            return 0
        for row in rows:
            kind = "mobile" if row["mobile"] else "direct line"
            print(f"  {row['name'][:34]:36} {row['number']}  ({kind})"
                  f"  {row['source']}")
        return 0

    if args.test:
        if not configured():
            print("[sms] HTTPSMS_API_KEY is not set - nothing to test")
            return 1
        print(f"[sms] sending a test message to {SMS_FROM}")
        try:
            message_id = send(SMS_FROM,
                              "job-machine is wired up to your phone. "
                              "Interview alerts will come here from now on.")
        except Exception as e:
            print(f"[sms] FAILED: {e}")
            return 1
        print(f"[sms] accepted by httpSMS ({message_id}) - it should arrive "
              f"once the handset picks it up")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
