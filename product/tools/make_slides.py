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
DECKS = {
    # The angle he asked for, and the one that is actually true: employers
    # screen with software before a person reads anything.
    "cheat-code": [
        ("They use AI to bin you", "hook"),
        ("Before a human reads it", "beat"),
        ("400 applications. Silence.", "beat"),
        ("You were never the problem", "turn"),
        ("So skip the filter", "turn"),
        ("Email the hiring manager", "beat"),
        ("13 of 34 wrote back", "num"),
        ("recruited.org.uk", "end"),
    ],
    # The secret hiding in plain sight. Teaches something usable with no
    # product in it until the last slide, which is what gets saved and sent on.
    "hidden-email": [
        ("Job adverts hide an email", "hook"),
        ("Right at the bottom", "beat"),
        ("Past the salary", "beat"),
        ("Past the benefits", "beat"),
        ("Nobody ever scrolls there", "turn"),
        ("6 of 9 wrote back", "num"),
        ("Go and look. Tonight.", "end"),
    ],
    # Pure pain, no method. Built to be commented on rather than clicked.
    "not-your-fault": [
        ("400 applications", "hook"),
        ("3 replies", "hook"),
        ("You rewrite the CV again", "beat"),
        ("Still nothing", "beat"),
        ("It was never read", "turn"),
        ("The Apply button eats it", "turn"),
        ("There is a way round", "end"),
    ],
    # The comparison, stripped to numbers. Most screenshot-able.
    "who-gets-read": [
        ("Same CV. Four weeks.", "hook"),
        ("Only one thing changed", "beat"),
        ("Who actually got it", "turn"),
        ("Generic info@: 4 of 42", "num"),
        ("A named person: 13 of 34", "num"),
        ("In the advert: 6 of 9", "num"),
        ("Stop clicking Apply", "end"),
    ],
}

# Caption per deck. TikTok shows the first line or two, so it front-loads and
# the hashtags sit at the end.
CAPTIONS = {
    "cheat-code": (
        "Most applications are screened by software before a person sees "
        "them. That is why you hear nothing.\n\n"
        "I sent 86 cold emails to UK employers over four weeks and kept a "
        "record. A named person wrote back 13 times out of 34. A generic "
        "info@ address, 4 out of 42.\n\n"
        "Same CV both times. The only thing that changed was who received "
        "it.\n\n"
        "#jobsearch #ukjobs #jobhunting #unemployed #careertok #jobseekers"),
    "hidden-email": (
        "Scroll to the very bottom of the next job advert you see. Past the "
        "salary, past the benefits, past the equal opportunities paragraph.\n\n"
        "A lot of them print a real email address down there. I sent 9 and 6 "
        "came back. Nine is a small sample and I am not pretending otherwise, "
        "but it beat everything else I tried.\n\n"
        "Almost nobody uses it, because almost everybody clicks Apply.\n\n"
        "#jobsearch #ukjobs #jobhunting #careeradvice #jobseekers"),
    "not-your-fault": (
        "If you are hundreds of applications in with nothing back, it is "
        "probably not your CV.\n\n"
        "An application through a job site is filed, not delivered. Nobody is "
        "told it arrived and there is no thread for anyone to reply to.\n\n"
        "What changed it for me was emailing a real person at the company "
        "instead. 86 cold emails, 22 replies.\n\n"
        "#jobsearch #ukjobs #unemployed #jobhunting #careertok"),
    "who-gets-read": (
        "86 cold emails to UK employers, four weeks, one person, same CV "
        "every time.\n\n"
        "Generic info@ address: 4 replies out of 42.\n"
        "A named human: 13 out of 34.\n"
        "The address printed in the advert: 6 out of 9.\n\n"
        "Counts not percentages, because the last row is nine emails and a "
        "percentage would flatter it.\n\n"
        "#jobsearch #ukjobs #jobhunting #jobseekers #careeradvice"),
}

TITLES = {
    "cheat-code": "They screen you with software. Go round it.",
    "hidden-email": "Job adverts hide a real email at the bottom",
    "not-your-fault": "400 applications and 3 replies is not your CV",
    "who-gets-read": "Same CV, four weeks: who actually replied",
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
