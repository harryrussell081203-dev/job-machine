"""The 86-email study, as data rather than as prose.

WHY THIS FILE EXISTS.

The same tables were written out three times - in PLAYBOOK.md as markdown, in
answers.py as sentences, and in the slide decks as four-word lines. Three
copies of one set of counts is two copies to forget to update, and this
project has already been caught quoting three different figures for the same
thing. So the counts live here once, the JSON endpoint serves them, and a test
asserts the playbook still says what this says.

THE THING THAT MATTERS MOST HERE IS THE DEFINITION OF A REPLY.

The site publishes two reply rates and they are not the same measurement:

  - THIS STUDY: 22 of 86, 26%. A fixed four-week sample, logged before the
    machine could classify what came back. So its 22 is every message that
    arrived, which includes automated acknowledgements and rejections.
  - THE LIVE COUNTER on /numbers: 31 of 166, 19%. That one excludes
    autoresponders and rejections, because the machine now reads replies and
    classifying them is knowledge worth spending on honesty rather than on a
    headline. 41 messages came back; 31 were from a human and were not a no.

Both are true and they measure different things. Printing them side by side
without saying so would be the exact trick this site exists not to play, so
the endpoint carries the definition next to each number and the difference is
stated rather than smoothed over.

If a figure here is ever wrong, it is wrong everywhere at once, which is the
point.
"""

from __future__ import annotations

# A row is (label, sent, replied). The rate is computed, never stored: a
# stored rate is a number that can drift away from the counts under it, and
# that drift is invisible until somebody checks.
BY_RECIPIENT = (
    ("A named human (j.smith@, sarah.brown@)", 34, 13),
    ("A hiring inbox (careers@, hr@, recruitment@)", 10, 5),
    ("A generic inbox (info@, enquiries@, hello@)", 42, 4),
)

BY_SOURCE = (
    ("Printed in the job advert itself", 9, 6),
    ("Dug out of the company's own website", 77, 16),
)

SENT = 86
REPLIED = 22

# Listings that reached address discovery and produced nothing real, over the
# same period. Published because it is the number a product selling address
# lookup would rather not print, and because sending nothing was the correct
# outcome every one of those times.
NO_ADDRESS_FOUND = 516

CAVEATS = (
    "The hiring-inbox row is ten emails. Read 50% as 'about as good as a "
    "named person', not as better than one.",
    "The in-advert row is nine emails. 67% will not hold at scale.",
    "These are engineering and technician roles in Scotland, sent by one "
    "person over four weeks. A different trade may behave differently.",
    "A reply is not an interview. Plenty of the 22 were polite noes.",
    "Replies here were counted before the machine could classify them, so "
    "this 22 includes automated acknowledgements and rejections. The live "
    "counter on /numbers uses a stricter definition and reports a lower rate.",
)


def rate(replied: int, sent: int) -> float:
    return round(100 * replied / sent, 1) if sent else 0.0


def _rows(rows):
    return [{"label": label, "sent": sent, "replied": replied,
             "reply_rate": rate(replied, sent)}
            for label, sent, replied in rows]


def as_dict() -> dict:
    """The study, shaped for the JSON endpoint."""
    return {
        "what": "86 cold emails to real UK employers over four weeks, sent "
                "by one person looking for work, logged as they went.",
        "sent": SENT,
        "replied": REPLIED,
        "reply_rate": rate(REPLIED, SENT),
        "reply_definition": "Any message received in reply, including "
                            "automated acknowledgements and rejections.",
        "benchmark": "Cold outreach is normally quoted at 1% to 5%.",
        "by_recipient": _rows(BY_RECIPIENT),
        "by_source": _rows(BY_SOURCE),
        "listings_with_no_address_found": NO_ADDRESS_FOUND,
        "caveats": list(CAVEATS),
        "method": "/playbook",
    }


def totals_reconcile() -> bool:
    """Both breakdowns are cuts of the same 86, so both must add up to it."""
    for rows in (BY_RECIPIENT, BY_SOURCE):
        if sum(sent for _, sent, _ in rows) != SENT:
            return False
        if sum(replied for _, _, replied in rows) != REPLIED:
            return False
    return True
