"""
Build the CAPTCHA handoff page.

A CAPTCHA is tied to a live browser session, and the session the bot filled the
form in is gone by the time anyone reads this. So the handoff is not a frozen
browser - it is the answers.

For every application the bot filled but could not submit, this writes a page
with the job, a link to the form, and a one-click PREFILL button. Clicking it
copies a snippet; paste it into the browser console on the open form (or use the
bookmarklet once) and every field the bot worked out is filled instantly. All
that is left is the CAPTCHA and Submit.

    python tools/handoff.py           # writes handoff/index.html
    python tools/handoff.py --open    # and prints the path
    python tools/handoff.py --email   # send it to Harry, once a day
"""
import argparse
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import job_machine as jm  # noqa: E402

OUT_DIR = os.path.join(jm.ROOT, "handoff")
OUT_FILE = os.path.join(OUT_DIR, "index.html")

# Fills by label text, then by name, then by placeholder - ATS markup varies.
PREFILL_JS = r"""
(function(){
  const answers = __ANSWERS__;
  const norm = s => (s||'').replace(/\s+/g,' ').trim().toLowerCase();
  const controls = Array.from(document.querySelectorAll('input,select,textarea'));
  const labelOf = el => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.id) { const l = document.querySelector('label[for="'+CSS.escape(el.id)+'"]');
                 if (l) return l.innerText; }
    const w = el.closest('label'); if (w) return w.innerText;
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend');
                                                 if (lg) return lg.innerText; }
    return '';
  };
  const setVal = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
                : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  let done = 0, missed = [];
  for (const a of answers) {
    const want = norm(a.label), wantName = norm(a.name);
    let el = controls.find(c => c.name && norm(c.name) === wantName && wantName);
    if (!el) el = controls.find(c => want && norm(labelOf(c)) === want);
    if (!el) el = controls.find(c => want && norm(labelOf(c)).includes(want));
    if (!el) el = controls.find(c => want && norm(c.placeholder) === want);
    if (!el) { missed.push(a.label || a.name); continue; }
    try {
      if (el.tagName === 'SELECT') {
        const opt = Array.from(el.options).find(o => norm(o.textContent) === norm(a.value))
                 || Array.from(el.options).find(o => norm(o.textContent).includes(norm(a.value)));
        if (opt) { el.value = opt.value; el.dispatchEvent(new Event('change',{bubbles:true})); done++; }
        else missed.push(a.label);
      } else if (el.type === 'checkbox') {
        if (!el.checked) el.click();
        done++;
      } else if (el.type === 'radio') {
        const group = controls.filter(c => c.type==='radio' && c.name===el.name);
        const pick = group.find(r => norm(labelOf(r)) === norm(a.value))
                  || group.find(r => norm(labelOf(r)).includes(norm(a.value)));
        if (pick) { pick.click(); done++; } else missed.push(a.label);
      } else { setVal(el, a.value); done++; }
    } catch (e) { missed.push(a.label); }
  }
  console.log('job-machine prefill: ' + done + ' filled', missed.length ? missed : '');
  alert('Filled ' + done + ' field(s).' +
        (missed.length ? '\n\nCould not place: ' + missed.join(', ') : '') +
        '\n\nUpload the CV, do the CAPTCHA, then Submit.');
})();
"""


def pending(state):
    jobs = [j for j in state.get("jobs", {}).values()
            if j.get("status") == "portal_awaiting_captcha"
            and not j.get("captcha_done_at")]
    return sorted(jobs, key=lambda j: -(j.get("score") or 0))


def card(job, index):
    answers = job.get("captcha_answers") or []
    snippet = PREFILL_JS.replace("__ANSWERS__", json.dumps(answers))
    rows = "".join(
        f"<tr><td>{html.escape(str(a.get('label') or a.get('name')))}</td>"
        f"<td>{html.escape(str(a.get('value'))[:90])}</td></tr>"
        for a in answers)
    flags = job.get("captcha_flags") or []
    flag_html = ("<p class=warn><b>Needs your answer:</b> "
                 + html.escape("; ".join(flags)) + "</p>") if flags else ""
    # Some of these are blocked by a bot check that runs BEFORE the form
    # loads, so there was nothing to read and nothing to fill. Offering a
    # 'prefill' button that places zero fields would waste his time twice -
    # once pressing it and once working out why nothing happened.
    if answers:
        action = (f'<button onclick="copyFill({index})">Copy prefill snippet'
                  f'</button>\n'
                  f'  <details><summary>{len(answers)} answer(s) the bot '
                  f'worked out</summary>\n    <table>{rows}</table></details>\n'
                  f'  <textarea id="fill{index}" class=hidden>'
                  f'{html.escape(snippet)}</textarea>')
    else:
        action = ('<p class=m>Nothing pre-filled: the bot check runs before '
                  'the form loads, so the agent never got to read it. Open '
                  'it and fill it in as normal.</p>')
    return f"""
<article>
  <h3>{html.escape(job.get('title') or '?')}
      <span class=co>{html.escape(job.get('company') or '?')}</span>
      <span class=score>{job.get('score') or '-'}</span></h3>
  <p><a href="{html.escape(job.get('apply_url') or '#')}" target="_blank"
        rel="noopener">Open the application form</a>
     <span class=m>{html.escape(job.get('ats') or '')}</span></p>
  {flag_html}
  {action}
</article>"""


def build(state):
    jobs = pending(state)
    cards = "".join(card(j, i) for i, j in enumerate(jobs))
    body = cards or "<p>Nothing waiting. Every filled application went through.</p>"
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
table{{border-collapse:collapse;font-size:13px;margin-top:8px;width:100%}}
td{{border-bottom:1px solid #eee;padding:4px 6px;vertical-align:top}}
.hidden{{position:absolute;left:-9999px}}
ol{{font-size:14px}}
</style></head><body>
<h1>Applications waiting on a CAPTCHA</h1>
<p><b>{len(jobs)}</b> waiting, <b>{sum(1 for j in jobs if j.get('captcha_answers'))}</b>
of them already filled in. For each one:</p>
<ol>
  <li>Open the form.</li>
  <li>Click <b>Copy prefill snippet</b>, then paste it into the browser console
      (F12 &rarr; Console) and press Enter. Every field fills instantly.
      Some have no snippet - the bot check on those runs before the form
      loads, so there was nothing to read and nothing to fill.</li>
  <li>Attach the CV, solve the CAPTCHA, press Submit.</li>
</ol>
<p class=m>The bot cannot hand you a live session - a CAPTCHA is tied to the
browser that loaded it, and that browser is gone. What it can hand you is every
answer, so this takes seconds rather than minutes.</p>
{body}
<script>
function copyFill(i) {{
  const t = document.getElementById('fill' + i);
  navigator.clipboard.writeText(t.value).then(
    () => alert('Copied. Paste into the console on the open form.'),
    () => {{ t.classList.remove('hidden'); t.select(); }});
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
    for job in jobs:
        lines.append(f"{job.get('title') or '?'} - {job.get('company') or '?'}"
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
        f"On a desktop, open the attached page: each one has a Copy prefill "
        f"snippet button. Open the form, paste the snippet into the browser "
        f"console (F12, Console, Enter), and every field fills at once. Then "
        f"attach the CV, do the puzzle and press submit.\n\n"
        f"On a phone the links above still work, you just fill them in by "
        f"hand.\n"
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
