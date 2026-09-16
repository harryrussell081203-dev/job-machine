"""Draw the picture that every shared link shows.

Post the site anywhere - Facebook, WhatsApp, iMessage, Reddit, a TikTok bio -
and this image is what appears. It is seen by everybody who is shown the link
and only clicked by some of them, which makes it the highest-traffic surface
the product has and the one nobody ever looks at twice.

Which is how it came to be wrong. The card was made once, by hand, and the
script was never kept - so when the published figures moved it could not be
redrawn, and for six days the card said 105 applications and 25% while the
page it linked to said 125 and 17%. Somebody comparing the two would conclude
the numbers were decorative. They are the entire argument.

Two rules, both learned the hard way elsewhere in this project:

  - **The numbers come from the same file the page reads.** Not typed in
    here. One fact, one source, or it is three chances to be wrong.
  - **The design comes from the site's own stylesheet.** The card is real
    HTML screenshotted at the exact share size, so it cannot drift away from
    the thing it advertises the first time either changes.

    python -m tools.make_og_card            # redraw from data/track_record.json
    python -m tools.make_og_card --check    # say whether it is stale, write nothing

`--check` exits non-zero when the card disagrees with the published figures,
so a workflow can notice without a human remembering to.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(HERE)
sys.path.insert(0, PRODUCT)

from app import track_record  # noqa: E402

CARD = os.path.join(PRODUCT, "app", "static", "og.png")
STYLE = os.path.join(PRODUCT, "app", "static", "style.css")

# Facebook, X and LinkedIn all want 1200x630. Rendered at 2x so it stays sharp
# on a phone, which is where nearly every share is opened.
WIDTH, HEIGHT, SCALE = 1200, 630, 2

TEMPLATE = """
<style>{css}</style>
<style>
  /* Only what the page cannot give it: the card is a fixed canvas, not a
     document that scrolls. Every colour and typeface below is inherited. */
  html, body {{ margin: 0; padding: 0; background: var(--bg); }}
  .card {{
    width: {width}px; height: {height}px;
    box-sizing: border-box;
    padding: 78px 80px;
    display: flex; flex-direction: column; justify-content: space-between;
    background: var(--bg); color: var(--ink);
  }}
  .card .brand {{ font-size: 34px; }}
  .card h1 {{
    font-size: 86px; line-height: 1.02; margin: 0; max-width: 15ch;
    letter-spacing: -0.03em;
  }}
  .figures {{ display: flex; gap: 74px; }}
  .figures b {{ display: block; font-size: 76px; letter-spacing: -0.03em; }}
  .figures span {{ font-size: 24px; color: var(--muted); }}
  .foot {{
    border-top: 1px solid var(--line);
    padding-top: 26px; font-size: 26px; color: var(--ink-2);
  }}
</style>
<div class="card">
  <div class="brand">recruit<span style="color:var(--muted);font-weight:400">ed</span></div>
  <h1>Your CV, in front of a human.</h1>
  <div class="figures">
    <div><b>{applications}</b><span>applications</span></div>
    <div><b>{replies}</b><span>replies</span></div>
    <div><b>{rate}%</b><span>reply rate</span></div>
  </div>
  <div class="foot">A real address at every company &mdash;
    <b>never guessed</b>. recruited.org.uk</div>
</div>
"""


def wanted() -> dict | None:
    """The figures the card should be showing, or None if there are none."""
    track_record._cache = None          # always the file, never a stale read
    return track_record.read()


def sidecar(path: str = CARD) -> str:
    """Where the figures the card was drawn with are recorded.

    A PNG cannot be asked what it says without OCR, and comparing timestamps
    instead is wrong in the direction that matters: the personal machine
    rewrites track_record.json at the end of EVERY run, numbers changed or
    not, so an mtime check calls the card stale several times a day while it
    is perfectly accurate. Then the warning gets ignored, and the one time it
    means something it is ignored too.

    So the card writes down what it drew, and staleness is a comparison of
    figures rather than of clocks.
    """
    return os.path.splitext(path)[0] + ".json"


def drawn_figures(path: str = CARD) -> dict | None:
    import json
    try:
        with open(sidecar(path)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def draw(record: dict, path: str = CARD) -> str:
    import json
    from playwright.sync_api import sync_playwright

    with open(STYLE) as f:
        css = f.read()
    html = TEMPLATE.format(css=css, width=WIDTH, height=HEIGHT,
                           applications=record["applications"],
                           replies=record["replies"],
                           rate=record["reply_rate"])

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM",
                                           "/opt/pw-browsers/chromium"),
            args=["--no-sandbox"])
        page = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT},
                                   device_scale_factor=SCALE).new_page()
        page.set_content(html, wait_until="load")
        page.screenshot(path=path, clip={"x": 0, "y": 0,
                                         "width": WIDTH, "height": HEIGHT})
        browser.close()

    with open(sidecar(path), "w") as f:
        json.dump({k: record[k] for k in
                   ("applications", "replies", "reply_rate")}, f,
                  indent=1, sort_keys=True)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report whether the card is out of date, write nothing")
    ap.add_argument("--out", default=CARD)
    args = ap.parse_args(argv)

    record = wanted()
    if not record:
        print("nothing published yet, so there are no figures to draw")
        return 1

    figures = (f"{record['applications']} applications, "
               f"{record['replies']} replies, {record['reply_rate']}%")

    if args.check:
        if not os.path.exists(args.out):
            print(f"no card at {args.out} - it should say: {figures}")
            return 1
        was = drawn_figures(args.out)
        if was is None:
            print(f"the card does not say what it was drawn with, so it "
                  f"cannot be checked. Redraw it: {figures}")
            return 1
        now = {k: record[k] for k in ("applications", "replies", "reply_rate")}
        if was != now:
            print(f"the card is out of date.\n"
                  f"  it says:      {was['applications']} applications, "
                  f"{was['replies']} replies, {was['reply_rate']}%\n"
                  f"  it should say: {figures}")
            return 1
        print(f"the card is current: {figures}")
        return 0

    draw(record, args.out)
    print(f"drew {args.out}: {figures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
