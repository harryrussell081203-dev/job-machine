"""Slideshow decks for TikTok: one short line a slide, big enough to read at a glance.

A photo carousel is the format that works on a cold account. Somebody swipes
because the next slide is one tap away, and every swipe is engagement the feed
counts. The rules that make it work are unforgiving:

  - **Four to six words a slide.** Not a sentence with the fat trimmed, a line.
    If it takes a second read it has already been scrolled past.
  - **The pain first, the product last.** Slide one has to be the thing the
    reader already believes about their own week. Nobody swipes into a pitch.
  - **One idea a deck.** A deck that makes two points makes neither.

WHAT THIS IS ALLOWED TO SAY.

The angle is that employers screen applications automatically and this routes
around that by reaching a person. That is true and it is the actual mechanism.

What these decks must never say is that the product blasts employers. It sends
one letter per employer, never to a guessed address, and refuses to send at all
when it cannot find a real one. A deck promising volume would be selling a
thing the machine is built to refuse, and the first user to notice would be
right to say so publicly.

Every figure carries its count. "67%" is six replies out of nine, and on a
slide with no room for a caveat the honest move is to print the count instead
of the percentage. `6 of 9 wrote back` is four words and it cannot mislead.

    python -m tools.make_slides --list
    python -m tools.make_slides --deck cheat-code
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(HERE)
sys.path.insert(0, PRODUCT)

OUT = os.path.join(PRODUCT, "share", "slides")
STYLE = os.path.join(PRODUCT, "app", "static", "style.css")

# TikTok photo posts: 1080x1920 fits its box exactly, and at 1x the file is
# well inside the 20MB ceiling. JPEG because PNG is refused outright.
WIDTH, HEIGHT, SCALE = 1080, 1920, 1

# A slide is (text, kind). kind picks the treatment:
#   "hook"  - the opening line, biggest, this is the whole post
#   "beat"  - a step in the argument
#   "turn"  - the pivot, where it stops being a complaint
#   "num"   - a figure, set large
#   "end"   - the address
#   VOICE
#
# First person, and angry. These are written as one unemployed person talking,
# because that is who is writing them and because the reader is in the middle
# of the same week.
#
# The earlier decks were written in the third person - "they use AI to bin
# you", "you were never the problem" - and read like a report about
# jobseekers rather than a message from one. Nobody shares a report. What gets
# sent to a mate is the thing that says what they have been thinking.
#
# So: "I" not "you". A specific grievance rather than a general one; everybody
# has retyped a CV into a portal after uploading the CV, and nobody has ever
# had a feeling about "automated screening". Short, flat, bitter sentences.
#
# No swearing. Not squeamishness - TikTok suppresses reach on profanity, so it
# costs the exact thing these exist to get.
#
# The anger has to stay true. Every figure is still the real one with its
# count attached, because a deck that rages and then makes a number up is just
# another person on the internet shouting.
DECKS = {
    # Everybody has done this. It is the most specific grievance available and
    # specificity is what makes somebody send it to a friend.
    "retype": [
        ("Upload your CV", "hook"),
        ("Now type your CV again", "beat"),
        ("Now make an account", "beat"),
        ("Now answer 30 questions", "beat"),
        ("Then nothing. Every single time.", "turn"),
        ("So I emailed a person instead", "turn"),
        ("13 of 34 wrote back", "num"),
    ],
    # The rage one. No method until the end, so it earns the right to mention
    # anything at all.
    "four-hundred": [
        ("400 applications this year", "hook"),
        ("No human read a single one", "beat"),
        ("“We’ll keep you on file”", "beat"),
        ("I’m not sad. I’m furious.", "turn"),
        ("So I stopped clicking Apply", "turn"),
        ("13 of 34 wrote back", "num"),
    ],
    # The rejection everybody has had, and the detail that gives it away as
    # automatic. The turn teaches something usable in the same breath.
    "we-regret": [
        ("“We regret to inform you”", "hook"),
        ("Sent at 2am by a bot", "beat"),
        ("I applied eleven minutes earlier", "beat"),
        ("Nobody read it. Nobody could.", "turn"),
        ("That advert had a real email", "turn"),
        ("Right at the bottom", "beat"),
        ("6 of 9 wrote back", "num"),
    ],
    # Pure method, still in voice. The one that gets saved rather than shared.
    "hidden-email": [
        ("Job adverts hide a real email", "hook"),
        ("Right at the bottom", "beat"),
        ("Past the salary", "beat"),
        ("Past the benefits", "beat"),
        ("I never knew. Nobody does.", "turn"),
        ("6 of 9 wrote back", "num"),
        ("Go and look. Tonight.", "end"),
    ],
}

# Caption per deck. TikTok shows the first line or two, so it front-loads and
# the hashtags sit at the end.
CAPTIONS = {
    "retype": (
        "Upload your CV. Then type it all in again. Then make an account you "
        "will never log into. Then thirty questions about whether you can "
        "work in the UK, which was on the CV.\n\n"
        "Then nothing. Not a rejection. Nothing.\n\n"
        "I stopped doing it and started emailing an actual person at the "
        "company. 86 of those over four weeks. A named person wrote back 13 "
        "times out of 34. A generic info@ address, 4 out of 42.\n\n"
        "Same CV. Same me. The only thing that changed was who got it.\n\n"
        "#jobsearch #ukjobs #jobhunting #unemployed #careertok #jobseekers"),
    "four-hundred": (
        "Four hundred. I counted.\n\n"
        "If you are that deep in with nothing back, I do not think it is your "
        "CV. An application through a job site is filed, not delivered. "
        "Nobody is told it arrived and there is no thread for anyone to reply "
        "to, so even someone who liked it has nothing to reply to.\n\n"
        "What changed it was emailing a real person instead. 86 cold emails "
        "to UK employers, 22 came back.\n\n"
        "#jobsearch #ukjobs #unemployed #jobhunting #careertok #jobseekers"),
    "we-regret": (
        "Applied at 1:49am because I could not sleep. Rejected at 2:00am. "
        "Eleven minutes.\n\n"
        "Nobody read it. Nobody could have.\n\n"
        "Here is the bit that got me though. That same advert had a real "
        "email address printed at the bottom of it, under the salary and the "
        "benefits, and I had never once scrolled that far.\n\n"
        "I sent 9 emails to addresses like that and 6 came back. Nine is a "
        "small sample and I am not going to pretend otherwise. It still beat "
        "everything else I tried.\n\n"
        "#jobsearch #ukjobs #jobhunting #unemployed #careeradvice"),
    "hidden-email": (
        "Scroll to the very bottom of the next job advert you see. Past the "
        "salary, past the benefits, past the equal opportunities "
        "paragraph.\n\n"
        "A lot of them print a real email address down there. I had no idea. "
        "I had been clicking Apply for months.\n\n"
        "I sent 9 and 6 came back. Small sample, I know. It still beat "
        "everything else, and almost nobody uses it because almost everybody "
        "clicks Apply.\n\n"
        "#jobsearch #ukjobs #jobhunting #careeradvice #jobseekers"),
}

TITLES = {
    "retype": "Upload your CV. Now type your CV.",
    "four-hundred": "400 applications. Not one read by a human.",
    "we-regret": "Rejected eleven minutes after applying, at 2am",
    "hidden-email": "Job adverts hide a real email at the bottom",
}

SLIDE = """
<style>{css}</style>
<style>
  html, body {{ margin: 0; padding: 0; background: var(--bg); }}
  .slide {{
    width: {w}px; height: {h}px;
    box-sizing: border-box;
    padding: 110px 90px;
    display: flex; flex-direction: column; justify-content: center;
    background: {bg}; color: {fg};
    text-align: left;
  }}
  /* One line a slide, so it is set as large as it can be and still break
     somewhere sensible. Tight leading and negative tracking is what makes a
     short line read as a statement rather than as a caption. */
  .line {{
    font-family: var(--display, Georgia, serif);
    font-weight: 600;
    font-size: {size}px;
    line-height: 1.02;
    letter-spacing: -0.035em;
    margin: 0;
  }}
  .mark {{
    position: absolute; left: 90px; bottom: 80px;
    font-size: 30px; color: {mark};
    letter-spacing: -0.01em;
  }}
</style>
<div class="slide"><p class="line">{text}</p></div>
<div class="mark">{markup}</div>
"""


def look(kind: str) -> dict:
    """Ink, paper and size for one kind of slide.

    The hook and the turn are inverted - white on black - because a deck that
    is one colour all the way through reads as a document. The flip is what
    makes somebody's thumb stop on the slide that carries the point.
    """
    black, white = "#0b0b0c", "#f5f5f7"
    if kind in ("hook", "turn"):
        return dict(bg=black, fg=white, mark="rgba(245,245,247,0.45)", size=132)
    if kind == "num":
        return dict(bg=white, fg=black, mark="rgba(11,11,12,0.40)", size=118)
    if kind == "end":
        return dict(bg=black, fg=white, mark="rgba(245,245,247,0.45)", size=110)
    return dict(bg=white, fg=black, mark="rgba(11,11,12,0.40)", size=124)


def check(deck: list) -> list[str]:
    """Slides that break the rule this format lives by.

    Returned rather than raised: a deck with one long line is still worth
    drawing, and the writer should be told which slide rather than left to
    guess. The address slide is exempt, being one word.
    """
    bad = []
    for text, kind in deck:
        if kind == "end":
            continue
        n = len(text.split())
        if n > 6:
            bad.append(f"{n} words: {text!r}")
    return bad


def draw(name: str, out_dir: str = OUT) -> list[str]:
    from playwright.sync_api import sync_playwright

    deck = DECKS[name]
    with open(STYLE) as f:
        css = f.read()

    folder = os.path.join(out_dir, name)
    os.makedirs(folder, exist_ok=True)
    paths = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM",
                                           "/opt/pw-browsers/chromium"),
            args=["--no-sandbox"])
        page = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT},
                                   device_scale_factor=SCALE).new_page()
        for i, (text, kind) in enumerate(deck, 1):
            last = i == len(deck)
            html = SLIDE.format(css=css, w=WIDTH, h=HEIGHT, text=text,
                                markup="" if last else "recruited.org.uk",
                                **look(kind))
            path = os.path.join(folder, f"{i:02d}.jpeg")
            page.set_content(html, wait_until="load")
            page.screenshot(path=path, type="jpeg", quality=92,
                            clip={"x": 0, "y": 0,
                                  "width": WIDTH, "height": HEIGHT})
            paths.append(path)
        browser.close()
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--deck", choices=sorted(DECKS))
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    if args.list:
        for name, deck in DECKS.items():
            bad = check(deck)
            print(f"{name}: {len(deck)} slides  {TITLES[name]}")
            for line in bad:
                print(f"   TOO LONG  {line}")
        return 0

    for name in ([args.deck] if args.deck else sorted(DECKS)):
        for line in check(DECKS[name]):
            print(f"warning {name}: {line}")
        for path in draw(name, args.out):
            print("drew", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
