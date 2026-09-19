"""Draw the pictures that get posted where a link cannot go.

WHY IMAGES AND NOT LINKS.

Every channel left to this product punishes links and rewards pictures:

  - **Reddit** removes link posts from accounts under a karma or age
    threshold, silently and automatically. That is almost certainly what has
    been happening to the posts there. An image post from the same account
    goes up untouched.
  - **Instagram and TikTok** have no clickable link in a post at all. A
    picture is the only thing that can carry an idea.
  - **Facebook groups** show a native image in the feed and quietly bury a
    post whose point is an external URL.
  - **WhatsApp**, which is where a UK jobseeker actually hears about things,
    forwards a picture far more readily than a link. A link asks somebody to
    trust a stranger's domain; a picture is just readable.

So the thing to spread is not the address of the site. It is the finding,
drawn so it can be read without going anywhere, with the address small at the
bottom for the people the finding convinces.

Three rules, two inherited from make_og_card and one that is specific:

  - **Numbers come from the file the site reads**, never typed in here.
    Everything on these cards has to survive somebody checking it against
    /numbers, because that checkability is the whole argument.
  - **Design comes from the site's own stylesheet.** Real HTML, screenshotted.
    A card cannot drift away from the thing it advertises.
  - **A rate never travels without its sample.** These are made to be passed
    around with nobody to ask, so "67%" alone would be a worse claim than no
    claim. Every rate on every card carries the count it came from.

    python -m tools.make_share_cards               # draw the lot
    python -m tools.make_share_cards --list        # names and sizes only
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(HERE)
sys.path.insert(0, PRODUCT)

from app import track_record  # noqa: E402

OUT = os.path.join(PRODUCT, "share")
STYLE = os.path.join(PRODUCT, "app", "static", "style.css")

# Three shapes, because one image posted everywhere fits nowhere. Portrait is
# the feed on a phone, story is full screen, wide is a link preview and the
# only one Reddit's old layout shows properly.
SHAPES = {
    "portrait": (1080, 1350),
    "story": (1080, 1920),
    "wide": (1200, 630),
}
SCALE = 2

# TikTok refuses a photo post whose image does not fit inside 1920x1080 or
# 1080x1920, and refuses PNG outright. A card drawn at the default 2x is
# 2160x2700, so posting one needs `--scale 1 --format jpeg`; at 1x every
# shape here is within the box. Written down because the rejection arrives
# after the upload, sometimes asynchronously, and reads as a server error
# rather than as a size rule.
TIKTOK_MAX = (1080, 1920)

# The figures behind the tier table are fixed history: 86 cold emails over
# four weeks, which is the sample PLAYBOOK.md reports and the only one these
# percentages are true of. They do NOT move with track_record.json, which
# counts a different and larger thing, so they are written here with their
# counts beside them rather than read from a file that would silently change
# what the percentages mean.
TIERS = [
    ("Printed in the advert", 9, 6, 67),
    ("A hiring inbox", 10, 5, 50),
    ("A named person", 34, 13, 38),
    ("A generic info@", 42, 4, 10),
]

FRAME = """
<style>{css}</style>
<style>
  html, body {{ margin: 0; padding: 0; background: var(--bg); }}
  /* Content flows from the top with an even gap and the footer is pinned to
     the bottom, rather than space-between spreading everything apart. On a
     1080x1920 story space-between opens a third of the screen as a hole in
     the middle, and that hole is the part a thumb stops on. */
  .card {{
    width: {width}px; height: {height}px;
    box-sizing: border-box;
    padding: {pad}px;
    display: flex; flex-direction: column;
    gap: {gap}px;
    background: var(--bg); color: var(--ink);
  }}
  .card .foot {{ margin-top: auto; }}
  .card .brand {{ font-size: {brand}px; }}
  .card h1 {{
    font-size: {h1}px; line-height: 1.04; margin: 0;
    letter-spacing: -0.03em;
  }}
  .card p {{ font-size: {body}px; line-height: 1.4; margin: 0; }}
  .card .muted {{ color: var(--muted); }}
  .foot {{
    border-top: 1px solid var(--line);
    padding-top: {pad_half}px; font-size: {foot}px; color: var(--ink-2);
  }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{
    padding: {row}px 0; font-size: {body}px;
    border-bottom: 1px solid var(--line);
  }}
  td.n {{ text-align: right; font-size: {big}px; letter-spacing: -0.03em;
          font-weight: 600; white-space: nowrap; }}
  td.s {{ text-align: right; font-size: {foot}px; color: var(--muted);
          white-space: nowrap; padding-left: {pad_half}px; }}
  tr:last-child td {{ border-bottom: 0; }}
  .lede {{ font-size: {lede}px; line-height: 1.3; margin: 0; }}
  .words {{ font-size: {body}px; line-height: 1.65; color: var(--ink); }}
  .words b {{ font-weight: 600; }}
</style>
<div class="card">
  <div class="brand">recruit<span style="color:var(--muted);font-weight:400">ed</span></div>
  {middle}
  <div class="foot">{foot_text}</div>
</div>
"""


def tier_rows() -> str:
    out = []
    for label, sent, replied, rate in TIERS:
        out.append(
            f"<tr><td>{label}</td>"
            f"<td class='s'>{replied} of {sent}</td>"
            f"<td class='n'>{rate}%</td></tr>")
    return "".join(out)


def cards(record: dict | None) -> dict:
    """Every card, as a block of HTML to drop into the frame.

    `record` is the live published figures, or None when there are none. A
    card that would need them and cannot have them is simply not drawn, the
    same rule the site follows: no claim beats a blank where a number goes.
    """
    made = {}

    # 1. The finding. This is the one worth passing on, because it is
    #    surprising, it is checkable, and somebody can act on it today
    #    without visiting anything.
    made["who-you-email"] = dict(
        middle=(
            "<div>"
            "<h1>Who you email decides everything</h1>"
            "<p class='muted' style='margin-top:18px'>Same CV. Same person. "
            "Same four weeks. 86 cold emails to UK employers, and the only "
            "thing that changed was who received it.</p>"
            "</div>"
            f"<table>{tier_rows()}</table>"),
        foot=("Almost nobody writes to the address printed in the advert, "
              "because almost everybody clicks Apply. &nbsp;recruited.org.uk"))

    # 2. One thing to do, no product in it at all. The most forwardable.
    made["scroll-to-the-bottom"] = dict(
        middle=(
            "<div>"
            "<h1>Scroll to the bottom of the job advert</h1>"
            "<p class='lede' style='margin-top:26px'>Past the salary. Past "
            "the benefits. Past the equal opportunities paragraph.</p>"
            "<p class='lede' style='margin-top:22px'>A lot of adverts print a "
            "real email address down there.</p>"
            "</div>"
            "<div>"
            "<p class='lede'><b>6 of the 9 I sent to one got a reply.</b></p>"
            "<p class='muted' style='margin-top:16px'>Nine is a small sample "
            "and it will not hold at scale. It was still the best of any "
            "source, and almost nobody uses it.</p>"
            "</div>"),
        foot="Free write-up, no account: recruited.org.uk/playbook")

    # 3. The banned list. Useful to a jobseeker, useful to somebody hiring,
    #    and an argument people enjoy having - which is what gets shared.
    made["reads-as-generated"] = dict(
        middle=(
            "<div>"
            "<h1>What makes a job application read as AI</h1>"
            "<p class='muted' style='margin-top:18px'>Employers get dozens a "
            "week now. These are the tells.</p>"
            "</div>"
            "<p class='words'>I hope this email finds you well &middot; "
            "passionate &middot; leverage &middot; delve &middot; seamless "
            "&middot; synergy &middot; dynamic &middot; thrilled &middot; "
            "excited to apply &middot; perfect fit &middot; hit the ground "
            "running &middot; fast-paced environment &middot; proven track "
            "record &middot; results-driven &middot; detail-oriented &middot; "
            "team player &middot; I am writing to &middot; utilize &middot; "
            "spearheaded &middot; esteemed &middot; keen to &middot; "
            "furthermore &middot; moreover</p>"
            "<p><b>Also: exclamation marks, em dashes, and any markdown.</b></p>"),
        foot=("Asking a model to avoid them does not work. It complies for two "
              "paragraphs and drifts back. &nbsp;recruited.org.uk/answers"))

    # 4. The live record. Needs the published figures, so it is conditional.
    if record:
        made["the-numbers"] = dict(
            middle=(
                "<div>"
                "<h1>Applying online mostly does not work</h1>"
                "<p class='muted' style='margin-top:18px'>So I stopped, and "
                "emailed the person hiring instead. Every figure below is "
                "public and updates itself.</p>"
                "</div>"
                "<table>"
                f"<tr><td>Applications</td><td class='n'>{record['applications']}</td></tr>"
                f"<tr><td>Employers</td><td class='n'>{record['employers']}</td></tr>"
                f"<tr><td>Replies</td><td class='n'>{record['replies']}</td></tr>"
                f"<tr><td>Reply rate</td><td class='n'>{record['reply_rate']}%</td></tr>"
                "</table>"),
            foot=("Cold email normally runs 1-5%. A reply is not an interview. "
                  "&nbsp;recruited.org.uk/numbers"))
    return made


def sizes(width: int, height: int) -> dict:
    """Type scale for one shape.

    Derived from the width rather than written per shape, so a new size is one
    line in SHAPES instead of a fresh set of numbers to get subtly wrong.
    """
    k = width / 1080
    tall = height / width > 1.3
    return dict(
        pad=int(80 * k), pad_half=int(40 * k),
        # The gap carries the height difference. A story is 570px taller than
        # a portrait card with the same words in it, and that has to go
        # somewhere deliberate rather than into one hole in the middle.
        gap=int((64 if tall else 40) * k),
        brand=int(34 * k), h1=int((86 if tall else 68) * k),
        body=int(32 * k), lede=int(40 * k), big=int(60 * k),
        foot=int(25 * k), row=int(22 * k))


def draw(name: str, block: dict, shape: str, out_dir: str = OUT,
         scale: int = SCALE, fmt: str = "png") -> str:
    from playwright.sync_api import sync_playwright

    width, height = SHAPES[shape]
    with open(STYLE) as f:
        css = f.read()
    html = FRAME.format(css=css, width=width, height=height,
                        middle=block["middle"], foot_text=block["foot"],
                        **sizes(width, height))

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}-{shape}.{fmt}")
    shot = {"path": path, "type": "jpeg" if fmt in ("jpg", "jpeg") else "png",
            "clip": {"x": 0, "y": 0, "width": width, "height": height}}
    if shot["type"] == "jpeg":
        # High but not lossless: these are flat backgrounds and text, where
        # JPEG artefacts show up around letterforms before they show up
        # anywhere else.
        shot["quality"] = 92

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM",
                                           "/opt/pw-browsers/chromium"),
            args=["--no-sandbox"])
        page = browser.new_context(viewport={"width": width, "height": height},
                                   device_scale_factor=scale).new_page()
        page.set_content(html, wait_until="load")
        page.screenshot(**shot)
        browser.close()
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true",
                    help="print what would be drawn, draw nothing")
    ap.add_argument("--shape", choices=sorted(SHAPES),
                    help="only this shape (default: all three)")
    ap.add_argument("--scale", type=int, default=SCALE,
                    help="pixel density (default 2; use 1 to post to TikTok)")
    ap.add_argument("--format", choices=("png", "jpeg"), default="png",
                    help="png keeps text sharpest; TikTok refuses png")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    track_record._cache = None
    made = cards(track_record.read())
    shapes = [args.shape] if args.shape else sorted(SHAPES)

    if args.list:
        for name in made:
            for shape in shapes:
                w, h = SHAPES[shape]
                print(f"{name}-{shape}.{args.format}  "
                      f"{w * args.scale}x{h * args.scale}")
        return 0

    for name, block in made.items():
        for shape in shapes:
            path = draw(name, block, shape, args.out, args.scale, args.format)
            w, h = (d * args.scale for d in SHAPES[shape])
            fits = w <= TIKTOK_MAX[0] and h <= TIKTOK_MAX[1]
            note = "" if fits else "  (too big for TikTok)"
            print(f"drew {path}  {w}x{h}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
