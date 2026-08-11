"""
Build the CAPTCHA handoff page.

A CAPTCHA is tied to a live browser session, and the session the bot filled the
form in is gone by the time anyone reads this. So the handoff is not a frozen
browser - it is the answers.

    python tools/handoff.py           # writes handoff/index.html
    python tools/handoff.py --open    # and prints the path
    python tools/handoff.py --email   # send it to Harry, once a day

WHAT THIS USED TO ASK OF HIM, AND WHY IT HAD TO CHANGE
------------------------------------------------------
The first version handed him a snippet of JavaScript per application and told
him to paste it into the browser console. Written out, the job was: open the
form, press Copy, press F12, click Console, press Ctrl+V, press Enter, attach
the CV, solve the puzzle, press Submit - and then write a reply saying he had
done it.

Nine steps, and one of them stopped working: Chrome, Edge and Firefox all now
refuse a paste into the console until you type the words "allow pasting" by
hand. So the instruction on the page was not merely long, it was wrong, and
nothing on his end would have told him why nothing happened.

His words: "simplify the captcha thing... to the point where all I need to do
is click it and then hand it back to you."

WHAT IT ASKS NOW
----------------
Once, ever:   drag one bookmark to the bookmarks bar. It never changes, so it
              is never dragged again.
Per job:      press "Fill and open" - the form opens and the answers go on the
              clipboard in the same click. On the form, click the bookmark.
              Solve the puzzle. Press Submit.
Handing back: press "Done" on the page and press Send. No typing.

THE BOOKMARK IS THE WHOLE TRICK, AND IT IS WORTH SAYING WHY
-----------------------------------------------------------
A page cannot fill in a form on another website - that is the same-origin rule,
and it is the rule that stops any web page reading your bank. A bookmarklet is
the one thing that legitimately crosses it: it is code the user themselves runs
on the page they are looking at, by clicking it.

The bookmark carries the filling logic and no answers, which is what makes it
permanent. The answers ride on the clipboard. It tries to read the clipboard
itself, and if the browser refuses - some do, for a bookmarklet - it opens a
box and asks for one Ctrl+V. Either way there is no console and nothing to type.
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import job_machine as jm  # noqa: E402

OUT_DIR = os.path.join(jm.ROOT, "handoff")
OUT_FILE = os.path.join(OUT_DIR, "index.html")

# The filler. Fills by label text, then by name, then by placeholder - ATS
# markup varies. This lives in the BOOKMARK, which is why it carries no answers
# and never has to be dragged twice.
FILL_JS = r"""
(function(){
  var norm = function(s){ return (s||'').replace(/\s+/g,' ').trim().toLowerCase(); };
  var labelOf = function(el){
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.id) { var l = document.querySelector('label[for="'+CSS.escape(el.id)+'"]');
                 if (l) return l.innerText; }
    var w = el.closest('label'); if (w) return w.innerText;
    var fs = el.closest('fieldset');
    if (fs) { var lg = fs.querySelector('legend'); if (lg) return lg.innerText; }
    return '';
  };
  var setVal = function(el, value){
    var proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
              : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
              : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  var fill = function(text){
    var answers;
    try { answers = JSON.parse(text); } catch (e) { answers = null; }
    if (!answers || !answers.length) {
      alert('No job-machine answers on the clipboard.\n\nGo back to the handoff '
          + 'page, press "Fill and open" on the one you are doing, then click '
          + 'this bookmark again.');
      return;
    }
    var controls = Array.prototype.slice.call(
      document.querySelectorAll('input,select,textarea'));
    var done = 0, missed = [];
    answers.forEach(function(a){
      var want = norm(a.label), wantName = norm(a.name), el = null;
      if (wantName) el = controls.filter(function(c){
        return c.name && norm(c.name) === wantName; })[0];
      if (!el && want) el = controls.filter(function(c){
        return norm(labelOf(c)) === want; })[0];
      if (!el && want) el = controls.filter(function(c){
        return norm(labelOf(c)).indexOf(want) >= 0; })[0];
      if (!el && want) el = controls.filter(function(c){
        return norm(c.placeholder) === want; })[0];
      if (!el) { missed.push(a.label || a.name); return; }
      try {
        if (el.tagName === 'SELECT') {
          var opts = Array.prototype.slice.call(el.options);
          var opt = opts.filter(function(o){
            return norm(o.textContent) === norm(a.value); })[0]
            || opts.filter(function(o){
            return norm(o.textContent).indexOf(norm(a.value)) >= 0; })[0];
          if (opt) { el.value = opt.value;
                     el.dispatchEvent(new Event('change',{bubbles:true})); done++; }
          else missed.push(a.label);
        } else if (el.type === 'checkbox') {
          if (!el.checked) el.click();
          done++;
        } else if (el.type === 'radio') {
          var group = controls.filter(function(c){
            return c.type === 'radio' && c.name === el.name; });
          var pick = group.filter(function(r){
            return norm(labelOf(r)) === norm(a.value); })[0]
            || group.filter(function(r){
            return norm(labelOf(r)).indexOf(norm(a.value)) >= 0; })[0];
          if (pick) { pick.click(); done++; } else missed.push(a.label);
        } else { setVal(el, a.value); done++; }
      } catch (e) { missed.push(a.label); }
    });
    alert('Filled ' + done + ' field(s).'
        + (missed.length ? '\n\nCould not place: ' + missed.join(', ') : '')
        + '\n\nAttach the CV, do the puzzle, press Submit.');
  };
  var ask = function(){
    var t = prompt('Press Ctrl+V then Enter.');
    if (t) fill(t);
  };
  // Read the clipboard if the browser will allow it from a bookmarklet - some
  // will, some want a click on the page first. When it refuses, one Ctrl+V.
  // Either way: no console, and nothing to type.
  if (navigator.clipboard && navigator.clipboard.readText) {
    navigator.clipboard.readText().then(fill, ask);
  } else { ask(); }
})();
"""


def bookmarklet():
    """The one bookmark, as a javascript: URL.

    Whitespace and comments squeezed out - a bookmark href has no length
    problem worth worrying about, but a tidy one is easier to trust when you
    hover over it, and this is a link he is being asked to keep."""
    body = re.sub(r"\s*//[^\n]*", "", FILL_JS)
    body = re.sub(r"\s*\n\s*", " ", body).strip()
    return "javascript:" + urllib.parse.quote(body, safe="")


def pending(state):
    """What is waiting, best first - and the ones he can do in seconds first.

    The ones with answers banked are a click and a puzzle; the ones without
    are a form to fill from scratch, because the bot check on those fired
    before the form ever loaded. Putting a from-scratch one at the top of the
    list because it scored two points higher is how a list of eleven stops
    getting finished at number three."""
    jobs = [j for j in state.get("jobs", {}).values()
            if j.get("status") == "portal_awaiting_captcha"
            and not j.get("captcha_done_at")]
    return sorted(jobs, key=lambda j: (not j.get("captcha_answers"),
                                       -(j.get("score") or 0)))


# HANDING IT BACK, WITHOUT TYPING ANYTHING.
#
# It used to be: reply to the email and write the word done, or 'done DOF,
# T-Tech' for some of them. That is not much, but it is a sentence to compose
# at the end of a job that is otherwise finished, and it means remembering
# which ones he got through.
#
# A mailto: link is the whole message written for him - address, subject and
# body - so the hand-back is a click and Send. The subject carries a marker
# tools/applied.py looks for, so this works from a fresh compose window and
# does not depend on him replying to the right thread.
DONE_MARKER = "[job-machine done]"


def done_link(what="all", label=None):
    query = urllib.parse.urlencode({
        "subject": f"{DONE_MARKER} {what}",
        "body": f"done {what}\n"})
    return (f'<a class=done href="mailto:{jm.GMAIL_ADDRESS}?{query}">'
            f'{html.escape(label or "Done")}</a>')


def card(job, index):
    answers = job.get("captcha_answers") or []
    rows = "".join(
        f"<tr><td>{html.escape(str(a.get('label') or a.get('name')))}</td>"
        f"<td>{html.escape(str(a.get('value'))[:90])}</td></tr>"
        for a in answers)
    flags = job.get("captcha_flags") or []
    flag_html = ("<p class=warn><b>Needs your answer:</b> "
                 + html.escape("; ".join(flags)) + "</p>") if flags else ""
    company = (job.get("company") or "").strip()
    url = html.escape(job.get("apply_url") or "#")
    # Some of these are blocked by a bot check that runs BEFORE the form
    # loads, so there was nothing to read and nothing to fill. Offering a
    # 'fill' button that places zero fields would waste his time twice - once
    # pressing it and once working out why nothing happened.
    if answers:
        action = (
            f'<button class=go onclick="fillAndOpen({index})">Fill and open'
            f'</button>\n'
            f'  <span class=m>then click the bookmark on the form</span>\n'
            f'  <details><summary>{len(answers)} answer(s) the bot worked out'
            f'</summary>\n    <table>{rows}</table></details>\n'
            f'  <textarea id="fill{index}" class=hidden>'
            f'{html.escape(json.dumps(answers))}</textarea>')
    else:
        action = (f'<p><a class=go href="{url}" target="_blank" rel="noopener">'
                  f'Open the form</a></p>\n'
                  f'  <p class=m>Nothing pre-filled: the bot check on this one '
                  f'runs before the form loads, so the agent never got to read '
                  f'it. Fill it in as normal - or skip it, it is a whole form '
                  f'either way.</p>')
    return f"""
<article>
  <h3><span class=n>{index + 1}.</span>{html.escape(job.get('title') or '?')}
      <span class=co>{html.escape(company or '?')}</span>
      <span class=score>{job.get('score') or '-'}</span></h3>
  <p><a href="{url}" target="_blank" rel="noopener">{url[:70]}</a>
     <span class=m>{html.escape(job.get('ats') or '')}</span></p>
  {flag_html}
  {action}
  <p class=after>Submitted it? {done_link(company or job.get('title') or 'all',
                                          'Mark this one done')}</p>
  <span class="hidden url" id="url{index}">{url}</span>
</article>"""


def build(state):
    jobs = pending(state)
    cards = "".join(card(j, i) for i, j in enumerate(jobs))
    body = cards or "<p>Nothing waiting. Every filled application went through.</p>"
    filled = sum(1 for j in jobs if j.get("captcha_answers"))
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>job-machine: applications needing a CAPTCHA</title>
<style>
body{{font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:900px;margin:24px auto;
padding:0 16px;color:#111;line-height:1.45}}
article{{border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:12px 0}}
h3{{margin:0 0 6px;font-size:16px}}
.co{{color:#444;font-weight:normal}} .score{{float:right;color:#888;font-size:13px}}
.m{{color:#777;font-size:12px}} .warn{{color:#a30;font-size:13px}}
button{{padding:8px 14px;font-size:14px;cursor:pointer}}
.go{{display:inline-block;background:#1a5fb4;color:#fff;border:0;border-radius:6px;
padding:9px 16px;font-size:14px;text-decoration:none;cursor:pointer}}
table{{border-collapse:collapse;font-size:13px;margin-top:8px;width:100%}}
td{{border-bottom:1px solid #eee;padding:4px 6px;vertical-align:top}}
.hidden{{position:absolute;left:-9999px}}
.setup{{background:#fff8e6;border:1px solid #e6d3a3;border-radius:8px;
padding:12px 16px;margin:16px 0}}
.bm{{display:inline-block;background:#111;color:#fff;border-radius:6px;
padding:7px 14px;text-decoration:none;font-weight:bold;cursor:grab}}
.handback{{background:#f4f8f4;border:1px solid #cfe0cf;border-radius:8px;
padding:10px 14px;font-size:14px}}
.done{{color:#1a5fb4}}
.after{{font-size:13px;color:#555;margin:8px 0 0}}
.n{{color:#999;font-weight:normal;margin-right:6px}}
ol{{font-size:14px}}
</style></head><body>
<h1>Applications waiting on a CAPTCHA</h1>
<p><b>{len(jobs)}</b> waiting, <b>{filled}</b> of them already filled in.</p>

<div class=setup>
  <p><b>Once, and never again:</b> drag this onto your bookmarks bar.<br>
  <a class=bm href="{html.escape(bookmarklet())}">job-machine fill</a></p>
  <p class=m>Press Ctrl+Shift+B first if the bar is hidden. This bookmark
  carries no answers, only the filling, so it never goes stale and you never
  have to drag it again.</p>
</div>

<p>Then, for each one below:</p>
<ol>
  <li>Press <b>Fill and open</b>. The form opens in a new tab and the answers
      go on your clipboard in the same click.</li>
  <li>On the form, click the <b>job-machine fill</b> bookmark. Every field
      fills. If your browser asks, press Ctrl+V and Enter - that is the whole
      of it.</li>
  <li>Attach the CV, solve the puzzle, press Submit.</li>
  <li>Press <b>Mark this one done</b> and send the email it opens. No typing.</li>
</ol>

<p class=handback><b>Finished the lot?</b> {done_link("all",
"Press here and send - that clears the whole list")}. Most employers send a
confirmation anyway, and those come off on their own without you doing
anything.</p>

<p class=m>Why a bookmark and not just a button: a web page is not allowed to
fill in a form on another website, which is the rule that stops any page
reading your bank. A bookmarklet is the one thing that legitimately crosses it,
because it is you running it on the page you are looking at.</p>
{body}
<script>
function fillAndOpen(i) {{
  const t = document.getElementById('fill' + i);
  const url = document.getElementById('url' + i).textContent.trim();
  const go = () => window.open(url, '_blank', 'noopener');
  // The open has to happen inside the click either way, or the browser treats
  // it as a pop-up and blocks it.
  navigator.clipboard.writeText(t.value).then(go, () => {{
    t.classList.remove('hidden');
    t.select();
    document.execCommand && document.execCommand('copy');
    t.classList.add('hidden');
    go();
  }});
}}
</script>
</body></html>"""
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w") as f:
        f.write(page)
    return len(jobs)


EMAILED = "handoff_emailed_on"


def plain_list(jobs):
    """The same list in text, for reading on a phone without opening anything."""
    lines = []
    for number, job in enumerate(jobs, 1):
        # Numbered, because this list is meant to grow. Working through
        # fifteen of these is a different job from working through three, and
        # a number is how he keeps his place.
        lines.append(f"{number}. {job.get('title') or '?'} - "
                     f"{job.get('company') or '?'}"
                     f"  (score {job.get('score') or '-'})")
        lines.append(f"  {job.get('apply_url') or ''}")
        answers = job.get("captcha_answers") or []
        if answers:
            lines.append(f"  {len(answers)} answer(s) already worked out")
        for flag in (job.get("captcha_flags") or [])[:3]:
            lines.append(f"  NEEDS YOU: {flag}")
        lines.append("")
    return "\n".join(lines)


def email_page(state, force=False):
    """Put the working page in his own inbox.

    The page has always existed; it lived in a GitHub Actions artifact, which
    means a login, a zip and a desktop before anyone can press a button on it.
    Emailing it is the difference between 'the answers are saved somewhere'
    and 'the answers are one click away on the machine you apply from'.

    Sent as an attachment rather than as the body on purpose: Gmail strips
    the script that makes the prefill buttons work, and a page that looks
    interactive and is not is worse than a file you open."""
    jobs = pending(state)
    if not jobs:
        print("[handoff] nothing waiting, no email sent")
        return 0
    if state.get(EMAILED) == jm.today() and not force:
        print("[handoff] already emailed today")
        return 0
    build(state)
    with open(OUT_FILE, "rb") as f:
        page = f.read()
    count = len(jobs)
    subject = (f"[job-machine] {count} application needs only the CAPTCHA"
               if count == 1 else
               f"[job-machine] {count} applications need only the CAPTCHA")
    filled = sum(1 for j in jobs if j.get("captcha_answers"))
    body = (
        f"{count} application{'' if count == 1 else 's'} stopped by a bot "
        f"check - the one step the machine will not do for you.\n\n"
        f"{filled} of them {'is' if filled == 1 else 'are'} filled in "
        f"completely and waiting on the puzzle alone."
        + ("" if filled == count else
           f" The other {count - filled} had the bot check BEFORE the form "
           f"loaded, so there was nothing to read and nothing to fill - those "
           f"are from scratch.")
        + "\n\n"
        f"{plain_list(jobs)}"
        f"OPEN THE ATTACHED PAGE ON THE DESKTOP. The first thing on it is one "
        f"bookmark to drag onto your bookmarks bar - once, ever. After that "
        f"each application is: press 'Fill and open', click the bookmark on "
        f"the form, do the puzzle, press Submit.\n\n"
        f"No console, no pasting code. The console route is gone: Chrome and "
        f"Firefox both refuse a paste in there now unless you type 'allow "
        f"pasting' by hand, so the old instructions would not have worked "
        f"even if you had followed them exactly.\n\n"
        f"On a phone the links above still work, you just fill them in by "
        f"hand.\n\n"
        f"------------------------------------------------------------\n"
        f"HANDING THEM BACK IS A CLICK. Every one on the page has a 'Mark this "
        f"one done' link, and there is a single one at the top for the lot. "
        f"They open an email that is already written - just press Send.\n"
        f"------------------------------------------------------------\n"
        f"Replying to this email with the word 'done' still works if that is "
        f"quicker, and you do not have to do anything at all for the ones "
        f"where the employer emails a confirmation - those come off the list "
        f"on their own.\n"
    )
    jm.send_email(jm.GMAIL_ADDRESS, subject, body, attach_cv=False,
                  attachments=[("captcha-handoff.html", "text", "html", page)])
    state[EMAILED] = jm.today()
    print(f"[handoff] emailed {count} application(s) to {jm.GMAIL_ADDRESS}")
    return count


def main():
    parser = argparse.ArgumentParser(description="Build the CAPTCHA handoff page")
    parser.add_argument("--open", action="store_true", help="print the path")
    parser.add_argument("--email", action="store_true",
                        help="send the page to Harry, once a day")
    parser.add_argument("--force", action="store_true",
                        help="with --email, send it even if today's has gone")
    args = parser.parse_args()
    state = jm.load()
    if args.email:
        email_page(state, force=args.force)
        jm.save(state)
        return
    count = build(state)
    print(f"[handoff] {count} application(s) waiting -> {OUT_FILE}")
    if args.open:
        print(OUT_FILE)


if __name__ == "__main__":
    main()
