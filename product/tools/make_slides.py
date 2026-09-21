"""Slideshow decks for TikTok: one short line a slide, big enough to read at a glance.

A photo carousel is the format that works on a cold account. Somebody swipes
because the next slide is one tap away, and every swipe is engagement the feed
counts. The rules that make it work are unforgiving:

  - **Four to six words a slide.** Not a sentence with the fat trimmed, a line.
    If it takes a second read it has already been scrolled past.
  - **The pain first, the product last.** Slide one has to be the thing the
    reader already believes about their own week. Nobody swipes into a pitch.
  - **One idea a deck.** A deck that makes two points makes neither.

ONE STYLE PER ACCOUNT, AND WHY THE LOOK HAS TO CHANGE TOO.

The plan these are built for is several accounts, each running one style, to
find out which framing actually lands. That is four different channels for
four different readers, which is a legitimate thing to run - and it is one
sentence away from the thing that gets every one of them suppressed at once,
which is several accounts pushing the same content.

So the separation is enforced here rather than left to whoever is posting:

  - **A deck belongs to exactly one style** and is only ever rendered in that
    style's look. `--check` fails if two styles contain the same line, because
    the moment two accounts post the same words the network stops being four
    channels and starts being one spammer.
  - **Each style has its own typography, palette, case and alignment.** Image
    similarity does not care that the words changed. Four accounts posting
    white-serif-on-black at the same size is one detectable template, however
    different the sentences are.

WHAT THESE ARE ALLOWED TO SAY.

The angle is that employers screen applications automatically and this routes
around that by reaching a person. That is true and it is the actual mechanism.

What they must never say is that the product blasts employers. It sends one
letter per employer, never to a guessed address, and refuses to send at all
when it cannot find a real one. A deck promising volume would be selling a
thing the machine is built to refuse.

Every figure carries its count. "67%" is six replies out of nine, and on a
slide with no room for a caveat the honest move is to print the count instead
of the percentage. `6 of 9 wrote back` is four words and cannot mislead.

    python -m tools.make_slides --list
    python -m tools.make_slides --check
    python -m tools.make_slides --style rage
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

SERIF = 'Newsreader, Georgia, "Times New Roman", serif'
SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'

# One entry per account. `voice` is the note for whoever writes the next deck;
# it is here rather than in a commit message because a style drifting into
# another style's voice is the failure that makes the whole plan collapse.
STYLES = {
    "rage": dict(
        who="Somebody four hundred applications deep, mid-spiral.",
        voice="First person, furious, flat short sentences. No swearing - "
              "TikTok throttles reach on it, which costs the exact thing "
              "these exist to get.",
        font=SERIF, weight=600, case="none", align="left",
        ink="#f5f5f7", paper="#0b0b0c", accent="#ffffff",
        # Inverts on the hook and the turn. The flip is what stops a thumb.
        invert=True, tracking="-0.035em", leading=1.02,
    ),
    "receipts": dict(
        who="Older, methodical, mid-career. Wants evidence before method.",
        voice="Third person and measured. States what was counted and what "
              "the sample was. Never raises its voice.",
        font=SANS, weight=500, case="none", align="left",
        ink="#14161a", paper="#e8e6e1", accent="#8a1c1c",
        invert=False, tracking="-0.02em", leading=1.12,
    ),
    "tips": dict(
        who="Actively applying this week. Wants technique, not sympathy.",
        voice="Second person, practical, warm. No grievance and no product - "
              "somebody can act on every slide tonight without clicking "
              "anything.",
        font=SANS, weight=700, case="none", align="center",
        ink="#1a1205", paper="#f7f0e0", accent="#b45309",
        invert=False, tracking="-0.025em", leading=1.1,
    ),
    "pov": dict(
        who="Youngest. Scrolling at 1am. Wants to feel seen, not taught.",
        voice="Second person scene-setting, lower case throughout, present "
              "tense. It is a moment, not an argument.",
        font=SANS, weight=600, case="lower", align="center",
        ink="#eaeaea", paper="#000000", accent="#6ee7b7",
        invert=False, tracking="-0.03em", leading=1.15,
    ),
}

# A slide is (text, kind). kind picks the emphasis within a style:
#   "hook"  the opening line, biggest
#   "beat"  a step in the argument
#   "turn"  the pivot, where it stops being a complaint
#   "num"   a figure, set in the accent colour
#   "end"   the address or the sign-off
DECKS = {
    # ---- rage -------------------------------------------------------
    "retype": dict(style="rage", slides=[
        ("Upload your CV", "hook"),
        ("Now type your CV again", "beat"),
        ("Now make an account", "beat"),
        ("Now answer 30 questions", "beat"),
        ("Then nothing. Every single time.", "turn"),
        ("So I emailed a person instead", "turn"),
        ("13 of 34 wrote back", "num"),
    ]),
    "four-hundred": dict(style="rage", slides=[
        ("400 applications this year", "hook"),
        ("No human read a single one", "beat"),
        ("“We’ll keep you on file”", "beat"),
        ("I’m not sad. I’m furious.", "turn"),
        ("So I stopped clicking Apply", "turn"),
        ("13 of 34 wrote back", "num"),
    ]),
    "we-regret": dict(style="rage", slides=[
        ("“We regret to inform you”", "hook"),
        ("Sent at 2am by a bot", "beat"),
        ("I applied eleven minutes earlier", "beat"),
        ("Nobody read it. Nobody could.", "turn"),
        ("That advert had a real email", "turn"),
        ("6 of 9 wrote back", "num"),
    ]),

    # ---- receipts ---------------------------------------------------
    "who-gets-read": dict(style="receipts", slides=[
        ("86 cold emails. Four weeks.", "hook"),
        ("One variable changed", "beat"),
        ("Who the email went to", "turn"),
        ("Generic inbox: 4 of 42", "num"),
        ("Named person: 13 of 34", "num"),
        ("In the advert: 6 of 9", "num"),
        ("The sample is published", "end"),
    ]),
    "timing-did-not-matter": dict(style="receipts", slides=[
        ("Everyone says send Tuesday morning", "hook"),
        ("It was measured", "beat"),
        ("In hours: 23.5 percent", "num"),
        ("Outside hours: 21.2 percent", "num"),
        ("That gap is noise", "turn"),
        ("Recipient beat timing outright", "end"),
    ]),

    # ---- tips -------------------------------------------------------
    "sixty-to-ninety": dict(style="tips", slides=[
        ("Keep it 60 to 90 words", "hook"),
        ("Name the exact role first", "beat"),
        ("Add one detail from the advert", "beat"),
        ("Two or three proof points", "beat"),
        ("Then exactly one question", "turn"),
        ("Two questions halves your replies", "end"),
    ]),
    "hidden-email": dict(style="tips", slides=[
        ("Job adverts hide a real email", "hook"),
        ("Scroll past the salary", "beat"),
        ("Past the benefits", "beat"),
        ("Past the equal opportunities bit", "beat"),
        ("It is usually a person", "turn"),
        # Deliberately not the rage deck's wording of the same fact. Two
        # accounts printing an identical line is the whole problem.
        ("Six of those nine replied", "num"),
        ("Go and look tonight", "end"),
    ]),

    # ---- pov --------------------------------------------------------
    "pov-1am": dict(style="pov", slides=[
        ("pov: it is 1am", "hook"),
        ("you are still applying", "beat"),
        ("you have stopped reading them", "beat"),
        ("upload, retype, submit, nothing", "beat"),
        ("nobody is on the other end", "turn"),
        ("there is another way in", "end"),
    ]),
    "pov-eight-months": dict(style="pov", slides=[
        ("pov: eight months unemployed", "hook"),
        ("your CV is not the problem", "beat"),
        ("your degree is not either", "beat"),
        ("the button is the problem", "turn"),
        ("it files you. it never sends.", "turn"),
        ("email a human instead", "end"),
    ]),
}

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
        "to.\n\n"
        "What changed it was emailing a real person instead. 86 cold emails "
        "to UK employers, 22 came back.\n\n"
        "#jobsearch #ukjobs #unemployed #jobhunting #careertok"),
    "we-regret": (
        "Applied at 1:49am because I could not sleep. Rejected at 2:00am. "
        "Eleven minutes.\n\n"
        "Nobody read it. Nobody could have.\n\n"
        "Here is the bit that got me. That same advert had a real email "
        "printed at the bottom, under the salary and the benefits, and I had "
        "never once scrolled that far.\n\n"
        "I sent 9 emails to addresses like that and 6 came back. Small "
        "sample, and I am not pretending otherwise. It still beat everything "
        "else.\n\n"
        "#jobsearch #ukjobs #jobhunting #unemployed #careeradvice"),
    "who-gets-read": (
        "86 cold emails to UK employers over four weeks. One person, the same "
        "CV every time.\n\n"
        "Generic inbox: 4 replies from 42.\n"
        "A named human: 13 from 34.\n"
        "The address printed in the advert: 6 from 9.\n\n"
        "Counts rather than percentages, because the last row is nine emails "
        "and a percentage would flatter it.\n\n"
        "#jobsearch #ukjobs #careeradvice #jobseekers #recruitment"),
    "timing-did-not-matter": (
        "The advice is always to send on a Tuesday morning. It was worth "
        "checking.\n\n"
        "Sent inside office hours: 23.5% replied. Outside: 21.2%. That "
        "difference is noise, not an effect.\n\n"
        "What did matter was who received it. A named person replied roughly "
        "four times as often as a generic inbox, on 86 emails.\n\n"
        "#jobsearch #ukjobs #careeradvice #jobseekers #recruitment"),
    "sixty-to-ninety": (
        "The shape that worked, from 86 of them:\n\n"
        "60 to 90 words in the body. Line one names the exact role plus one "
        "concrete detail from that specific advert, which is what proves you "
        "read it. Then two or three numbered proof points that matter to this "
        "job. Then exactly one question.\n\n"
        "One. Two questions means the reader has to compose a reply instead "
        "of answering one.\n\n"
        "Sign off the same way every time: name, phone, CV attached.\n\n"
        "#jobsearch #ukjobs #careeradvice #coverletter #jobseekers"),
    "hidden-email": (
        "Scroll to the very bottom of the next job advert you see. Past the "
        "salary, past the benefits, past the equal opportunities "
        "paragraph.\n\n"
        "A lot of them print a real email address down there, and it is "
        "usually a person rather than an inbox.\n\n"
        "I sent 9 and 6 came back. Small sample, I know. It still beat "
        "everything else, and almost nobody uses it because almost everybody "
        "clicks Apply.\n\n"
        "#jobsearch #ukjobs #jobhunting #careeradvice #jobseekers"),
    "pov-1am": (
        "the 1am application spiral is a real thing and nobody warns you "
        "about it\n\n"
        "the reason it feels pointless is that it mostly is. an application "
        "through a job site is filed, not delivered. nobody is told it "
        "arrived.\n\n"
        "86 emails to actual people at the companies instead, 22 came back\n\n"
        "#jobsearch #ukjobs #unemployed #jobhunting #careertok"),
    "pov-eight-months": (
        "eight months is long enough to start believing it is you. it is "
        "probably not.\n\n"
        "the Apply button files you. it does not send you anywhere. no human "
        "is told your application exists and there is no thread for anyone to "
        "reply to.\n\n"
        "86 cold emails to real people, 22 replies\n\n"
        "#jobsearch #ukjobs #unemployed #jobhunting #careertok"),
}

TITLES = {
    "retype": "Upload your CV. Now type your CV.",
    "four-hundred": "400 applications. Not one read by a human.",
    "we-regret": "Rejected eleven minutes after applying, at 2am",
    "who-gets-read": "86 emails, one variable: who received it",
    "timing-did-not-matter": "Send it Tuesday morning? It made no difference.",
    "sixty-to-ninety": "The email shape that got 22 replies from 86",
    "hidden-email": "Job adverts hide a real email at the bottom",
    "pov-1am": "pov: it is 1am and you are still applying",
    "pov-eight-months": "pov: eight months unemployed",
}

SLIDE = """
<style>{css}</style>
<style>
  html, body {{ margin: 0; padding: 0; background: {paper}; }}
  .slide {{
    width: {w}px; height: {h}px;
    box-sizing: border-box;
    padding: 110px 90px;
    display: flex; flex-direction: column; justify-content: center;
    background: {paper}; color: {ink};
    text-align: {align};
  }}
  .line {{
    font-family: {font};
    font-weight: {weight};
    font-size: {size}px;
    line-height: {leading};
    letter-spacing: {tracking};
    text-transform: {case};
    margin: 0;
  }}
  .mark {{
    position: absolute; left: 90px; bottom: 80px;
    font-family: {sans}; font-size: 28px; opacity: 0.45;
    color: {ink}; letter-spacing: -0.01em;
  }}
</style>
<div class="slide"><p class="line">{text}</p></div>
<div class="mark">{markup}</div>
"""


def look(style: str, kind: str) -> dict:
    """Ink, paper and size for one slide, from its style and its kind."""
    s = STYLES[style]
    ink, paper = s["ink"], s["paper"]

    # Inverting styles flip on the two slides that carry the weight. A deck
    # that is one colour throughout reads as a document.
    if s["invert"] and kind in ("hook", "turn"):
        ink, paper = paper, ink
    if kind == "num":
        ink = s["accent"]

    size = {"hook": 132, "turn": 124, "num": 118, "end": 112}.get(kind, 120)
    # A centred style wraps sooner, so it needs a little less.
    if s["align"] == "center":
        size = int(size * 0.92)
    return dict(ink=ink, paper=paper, size=size, font=s["font"],
                weight=s["weight"], align=s["align"], leading=s["leading"],
                tracking=s["tracking"],
                case="lowercase" if s["case"] == "lower" else "none")


def too_long(slides) -> list[str]:
    """Slides over six words. The format dies the moment one needs reading
    twice. The sign-off is exempt, being an address."""
    return [f"{len(t.split())} words: {t!r}"
            for t, kind in slides if kind != "end" and len(t.split()) > 6]


def shared_lines() -> list[str]:
    """Any line appearing under two different styles.

    This is the check that keeps the plan legitimate. Several accounts each
    running one style is four channels; several accounts running the same
    lines is one spammer with four logins, and the difference is enforced
    here rather than trusted to whoever is posting at the time.
    """
    seen: dict[str, str] = {}
    clashes = []
    for name, deck in DECKS.items():
        for text, _ in deck["slides"]:
            key = text.strip().lower()
            other = seen.get(key)
            if other and DECKS[other]["style"] != deck["style"]:
                clashes.append(
                    f"{text!r} in {other} ({DECKS[other]['style']}) "
                    f"and {name} ({deck['style']})")
            seen.setdefault(key, name)
    return clashes


def draw(name: str, out_dir: str = OUT) -> list[str]:
    from playwright.sync_api import sync_playwright

    deck = DECKS[name]
    with open(STYLE) as f:
        css = f.read()

    folder = os.path.join(out_dir, deck["style"], name)
    os.makedirs(folder, exist_ok=True)
    paths = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM",
                                           "/opt/pw-browsers/chromium"),
            args=["--no-sandbox"])
        page = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT},
                                   device_scale_factor=SCALE).new_page()
        for i, (text, kind) in enumerate(deck["slides"], 1):
            last = i == len(deck["slides"])
            html = SLIDE.format(css=css, w=WIDTH, h=HEIGHT, text=text,
                                sans=SANS,
                                markup="" if last else "recruited.org.uk",
                                **look(deck["style"], kind))
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
    ap.add_argument("--check", action="store_true",
                    help="fail if two styles share a line, or a slide is long")
    ap.add_argument("--deck", choices=sorted(DECKS))
    ap.add_argument("--style", choices=sorted(STYLES),
                    help="every deck for one account")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    if args.list:
        for style, s in STYLES.items():
            decks = [n for n, d in DECKS.items() if d["style"] == style]
            print(f"\n{style}  -  {s['who']}")
            print(f"  voice: {s['voice']}")
            for n in decks:
                print(f"  {n}: {len(DECKS[n]['slides'])} slides  {TITLES[n]}")
        return 0

    if args.check:
        bad = shared_lines()
        for name, deck in DECKS.items():
            bad += [f"{name}: {line}" for line in too_long(deck["slides"])]
        for line in bad:
            print("FAIL", line)
        print("ok" if not bad else f"{len(bad)} problems")
        return 1 if bad else 0

    if args.style:
        names = [n for n, d in DECKS.items() if d["style"] == args.style]
    elif args.deck:
        names = [args.deck]
    else:
        names = sorted(DECKS)

    for name in names:
        for line in too_long(DECKS[name]["slides"]):
            print(f"warning {name}: {line}")
        for path in draw(name, args.out):
            print("drew", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
