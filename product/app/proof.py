"""The founder's results, counted from his own inbox, for the landing page.

These are not the personal machine's running tally (track_record.py, which
the machine writes after every run). They are a one-off count of every
application email in Harry's Gmail, made on 6 October 2026, and they are
here because the landing page needed outcomes - interviews and offers -
which the machine never recorded.

How each figure was counted, so anybody can check it the same way:

  - **Applications**: every thread in his sent mail since 1 May 2026 that he
    started and that signs off "CV attached". Follow-ups in the same thread
    are not counted again.
  - **Bounced**: a thread with a delivery failure from mailer-daemon or a
    postmaster.
  - **A person wrote back**: a thread with any message from someone else
    that is not a bounce or an automatic reply. That includes polite noes:
    a no from a person still means the email reached a person. Replies that
    started a new thread (some interview invitations do) are not counted, so
    this is a floor.
  - **Interviews**: employers that invited him to an interview in writing.
  - **Offers**: written offers of employment.

May was the old way: addresses were guessed from a pattern. From August on,
the machine only used addresses an employer published. The split is kept
because it is the clearest evidence there is for the product's one rule.

No employer is named here, and none is named on the page. One of them is his
current employer, which public copy never mentions.
"""

from __future__ import annotations

COUNTED_ON = "6 October 2026"
PERIOD = "May to October 2026"

GUESSED = {"label": "Guessed address (May)",
           "sent": 304, "bounced": 132, "replied": 26}
PUBLISHED = {"label": "Published address only (Aug to Oct)",
             "sent": 264, "bounced": 5, "replied": 61}

SENT = GUESSED["sent"] + PUBLISHED["sent"]            # 568
REPLIED = GUESSED["replied"] + PUBLISHED["replied"]   # 87
INTERVIEW_EMPLOYERS = 8
OFFERS = 3

# One application, start to finish, with the employer left out.
TIMELINE = (
    ("15 September", "One short email to an engineering firm's info@ address."),
    ("24 September", "First interview."),
    ("25 September", "Second interview."),
    ("1 October", "Written job offer. Accepted."),
)


def pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def summary() -> dict:
    return {
        "sent": SENT, "replied": REPLIED,
        "interviews": INTERVIEW_EMPLOYERS, "offers": OFFERS,
        "period": PERIOD, "counted_on": COUNTED_ON,
        "guessed": dict(GUESSED, bounce_pct=pct(GUESSED["bounced"],
                                                GUESSED["sent"]),
                        reply_pct=pct(GUESSED["replied"], GUESSED["sent"])),
        "published": dict(PUBLISHED, bounce_pct=pct(PUBLISHED["bounced"],
                                                    PUBLISHED["sent"]),
                          reply_pct=pct(PUBLISHED["replied"],
                                        PUBLISHED["sent"])),
        "timeline": TIMELINE,
    }
