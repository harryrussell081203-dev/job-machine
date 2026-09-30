"""The weekly job-hunting tips: eight short emails, only to people who asked.

This is how Recruited reaches jobseekers by email without breaking the rule
the whole product is built on. Nobody is written to who did not ask:

  - **Double opt-in.** Typing an address sends ONE email, asking them to
    confirm. Until they click it, nothing else is ever sent, and the
    unconfirmed row is deleted after a week. The click is the consent PECR
    requires, and the record of it.
  - **One-click unsubscribe** in every email, as a link and as the
    List-Unsubscribe headers mail providers show as a button. Unsubscribing
    deletes the row; there is nothing to keep.
  - **Eight emails, one a week, then it stops.** A list that never ends is
    one people report as spam.

The emails are Harry's own story and figures, first person, and every
number in them is one the site already publishes on /numbers and in the
playbook. Run weekly by .github/workflows/tips.yml.

Switch: TIPS_ENABLED (default on).
"""

from __future__ import annotations

import secrets

from jobseeker import settings

from . import auth, config, db

WEEK = 7 * 86400
# A little under a week, so a job that fires a few minutes early still sends.
SPACING = WEEK - 6 * 3600
UNCONFIRMED_KEEP = WEEK


def enabled() -> bool:
    return settings.flag("TIPS_ENABLED")


def _site() -> str:
    return config.BASE_URL


TIPS = [
    {"subject": "The address is usually in the advert already",
     "body": """This year I sent 171 job applications. Clicking Apply got me almost nothing back, so I changed one thing: I wrote to a real person at the employer instead.

The easiest place to find one is the advert itself. Read it to the very last line. A lot of adverts, especially from smaller firms and agencies, print a contact address at the bottom, under the salary and the reference number. Almost nobody uses it, because almost everybody clicks Apply.

Of the 9 emails I sent to an address printed in the advert, 6 got a reply.

If you don't have the advert to hand, type the company's name here and it will look on their own website for an address they publish:
{site}/find"""},
    {"subject": "Who you write to matters more than what you write",
     "body": """Over four weeks I logged 86 cold emails to UK employers. The letters were near identical. What changed the reply rate was who received them:

- a named person: 38% replied
- a hiring inbox like careers@ or hr@: 50%
- a generic info@: 10%

So before you polish the wording, spend two minutes finding the right inbox. The company's own website is the place: the contact, careers, about and team pages.

{site}/find does that search for you."""},
    {"subject": "60 to 90 words is enough",
     "body": """Every letter I sent that got a reply was short. Here's the shape:

1. The exact job title, plus one detail from that advert, so they know you read it.
2. Two things you've actually done that matter for this job, with a number if you have one.
3. One question. Just one, so replying is easy.

Then your name, your phone number, and "CV attached".

That's it. 60 to 90 words. A busy manager reads it in twenty seconds, and twenty seconds is all you get."""},
    {"subject": "The phrases that make a letter sound like AI",
     "body": """Hiring managers now read a lot of letters written by ChatGPT, and they spot them fast. The giveaways are nearly always the same: "I hope this email finds you well", "passionate", "leverage", "thrilled", "perfect fit", "proven track record", "team player", plus long dashes and exclamation marks.

I built a free checker that highlights them in your own letter and says what to write instead. Nothing you paste is stored:
{site}/tools/cover-letter-ai-check"""},
    {"subject": "Follow up once, then move on",
     "body": """If you hear nothing, send one short follow-up, five working days later, as a reply to your first email so they can see what it's about. Two or three lines: still interested, happy to talk, is it worth sending anything else.

If nothing's come back after three weeks, stop and put the time into the next application. A nudge to something they've forgotten is just another cold email.

This works out the dates for you:
{site}/tools/follow-up"""},
    {"subject": "Never guess an email address",
     "body": """A lot of advice says to work out a company's pattern (firstname.lastname@) and guess the hiring manager's address. Don't.

- A bounce isn't free. Too many and your email starts landing in spam, including your reply to the employer who did want to talk.
- A guess that reaches the wrong person is a stranger reading about a job they can't do anything about.

If you can't find a real address, apply the normal way and move on. I never guessed one across all 171 applications, and I still got 32 replies."""},
    {"subject": "A reply isn't always a yes, and that's fine",
     "body": """My figures so far: 171 applications to 137 employers, 32 replies. That's 19%. Cold email normally gets 1 to 5%.

Plenty of those replies were polite noes. That still counts. A no from a person means your email reached someone and was read, which is more than most portal applications ever get.

Silence from a portal teaches you nothing. A reply, even a no, tells you your approach is reaching people. Keep going."""},
    {"subject": "If you want it done for you",
     "body": """This is the last one in the series.

Everything I've sent you works by hand, and the free tools will stay free. If you'd rather it ran without you, that's what Recruited does. It searches the job boards for your trade every weekday, finds a real address at each employer (never a guessed one), writes a short letter in your voice that never claims anything you can't back up, and sends it from your own Gmail. One email per employer, ever, with a daily limit you set.

{site}

Good luck with the hunt.

Harry"""},
]


def _footer(token: str) -> str:
    return (f"\n\nHarry\nRecruited, {_site()}\n\n"
            "You're getting this because you asked for the weekly job-hunting "
            "tips at recruited.org.uk. 8 emails, then it stops.\n"
            f"Unsubscribe in one click: {_site()}/tips/unsubscribe/{token}\n")


def _headers(token: str) -> dict:
    url = f"{_site()}/tips/unsubscribe/{token}"
    return {"List-Unsubscribe": f"<{url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def subscribe(email: str, source: str = "", *, send=None) -> None:
    """Record the address and send the one confirmation email. Says the same
    thing whether or not the address was already there, so the form cannot
    be used to find out who is subscribed."""
    email = (email or "").strip().lower()
    if not auth.valid_email(email):
        raise ValueError("not an email address")
    now = db.now()
    with db.connect() as c:
        row = c.execute("SELECT * FROM tip_subscribers WHERE email = ?",
                        (email,)).fetchone()
        if row and row["confirmed_at"]:
            return
        if row:
            token = row["token"]
        else:
            token = secrets.token_urlsafe(24)
            c.execute("INSERT INTO tip_subscribers (email, token, source, "
                      "created_at) VALUES (?, ?, ?, ?)",
                      (email, token, (source or "")[:40], now))
    body = ("Tap the link below to get one short job-hunting tip a week, "
            "8 in all. They're what worked in my own job hunt: 171 "
            "applications, 32 replies.\n\n"
            f"{_site()}/tips/confirm/{token}\n\n"
            "If you didn't ask for this, ignore it and you'll never hear from "
            "us.\n\nHarry\nRecruited")
    (send or auth.send_app_email)(email, "Confirm your job-hunting tips",
                                  body)


def confirm(token: str, *, send=None) -> bool:
    """Mark the address confirmed and send the first tip straight away."""
    with db.connect() as c:
        row = c.execute("SELECT * FROM tip_subscribers WHERE token = ?",
                        (token or "",)).fetchone()
    if not row:
        return False
    if not row["confirmed_at"]:
        with db.connect() as c:
            c.execute("UPDATE tip_subscribers SET confirmed_at = ? "
                      "WHERE id = ?", (db.now(), row["id"]))
        _send_next(dict(row, confirmed_at=db.now()), send=send)
    return True


def unsubscribe(token: str) -> bool:
    with db.connect() as c:
        row = c.execute("SELECT id FROM tip_subscribers WHERE token = ?",
                        (token or "",)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM tip_subscribers WHERE id = ?", (row["id"],))
    return True


def _send_next(row: dict, *, send=None) -> bool:
    index = int(row["sent_count"] or 0)
    if index >= len(TIPS):
        return False
    tip = TIPS[index]
    body = tip["body"].format(site=_site()) + _footer(row["token"])
    (send or auth.send_app_email)(row["email"], tip["subject"], body,
                                  headers=_headers(row["token"]))
    with db.connect() as c:
        c.execute("UPDATE tip_subscribers SET sent_count = ?, "
                  "last_sent_at = ? WHERE id = ?",
                  (index + 1, db.now(), row["id"]))
    return True


def send_due(*, now: int | None = None, send=None) -> dict:
    """Send the next tip to everybody whose last one was a week ago, and
    forget addresses that were never confirmed. Safe to run as often as you
    like: the spacing is per person."""
    if not enabled():
        return {"sent": 0, "failed": 0, "forgotten": 0, "reason": "off"}
    now = db.now() if now is None else now
    with db.connect() as c:
        forgotten = c.execute(
            "DELETE FROM tip_subscribers WHERE confirmed_at IS NULL "
            "AND created_at < ?", (now - UNCONFIRMED_KEEP,)).rowcount
        due = c.execute(
            "SELECT * FROM tip_subscribers WHERE confirmed_at IS NOT NULL "
            "AND sent_count < ? AND (last_sent_at IS NULL "
            "OR last_sent_at <= ?)", (len(TIPS), now - SPACING)).fetchall()
    sent = failed = 0
    for row in due:
        try:
            if _send_next(dict(row), send=send):
                sent += 1
        except Exception as exc:
            failed += 1
            print(f"[tips] could not send to subscriber {row['id']}: {exc}")
    return {"sent": sent, "failed": failed, "forgotten": forgotten or 0}


def main() -> int:
    db.init()
    print(f"[tips] {send_due()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
