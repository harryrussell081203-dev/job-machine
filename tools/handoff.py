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
    return f"""
<article>
  <h3>{html.escape(job.get('title') or '?')}
      <span class=co>{html.escape(job.get('company') or '?')}</span>
      <span class=score>{job.get('score') or '-'}</span></h3>
  <p><a href="{html.escape(job.get('apply_url') or '#')}" target="_blank"
        rel="noopener">Open the application form</a>
     <span class=m>{html.escape(job.get('ats') or '')}</span></p>
  {flag_html}
  <button onclick="copyFill({index})">Copy prefill snippet</button>
  <details><summary>{len(answers)} answer(s) the bot worked out</summary>
    <table>{rows}</table></details>
  <textarea id="fill{index}" class=hidden>{html.escape(snippet)}</textarea>
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
<p><b>{len(jobs)}</b> filled and ready. For each one:</p>
<ol>
  <li>Open the form.</li>
  <li>Click <b>Copy prefill snippet</b>, then paste it into the browser console
      (F12 &rarr; Console) and press Enter. Every field fills instantly.</li>
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


def main():
    parser = argparse.ArgumentParser(description="Build the CAPTCHA handoff page")
    parser.add_argument("--open", action="store_true", help="print the path")
    args = parser.parse_args()
    count = build(jm.load())
    print(f"[handoff] {count} application(s) waiting -> {OUT_FILE}")
    if args.open:
        print(OUT_FILE)


if __name__ == "__main__":
    main()
