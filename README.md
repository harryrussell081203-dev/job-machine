# job-machine

Automated job-application outreach for Harry Russell. One Python file, run by
GitHub Actions three times a weekday, costing nothing.

It finds fresh engineering/technician jobs in Scotland, scores them against
Harry's profile, digs out a **real** email address for the employer, writes a
short personalised email, sends it from Gmail with the CV attached, and chases
once after four days if nobody replies.

**This is live.** Emails go to real employers. Every night at 22:00 UK time it
emails Harry a digest of everything that went out that day.

To put it back into rehearsal, set the repo variable `TEST_MODE` to `1`: the full
pipeline still runs and still sends through Gmail, but every message lands in
Harry's own inbox with the intended recipient in the subject line, no company is
marked as contacted, and follow-ups are off.

---

## Pipeline

| Stage | What happens |
| --- | --- |
| 0. Schedule | `run.yml` at 08:00, 11:30 and 15:00 UTC on weekdays; `summary.yml` sends the digest at 22:00 UK time every day. |
| 1. Harvest | Adzuna + Reed, listings **<=48h old**, every location in `SEARCH_LOCATIONS`, engineering/technician/electronics/instrumentation/comms keywords. Duplicates across the two boards are collapsed; obviously wrong titles (chartered, HGV, chef...) are dropped before they cost an AI call. |
| 2. Score | Gemini 2.5 Flash scores 0-100 against the candidate profile. **>=70 proceeds**, below is skipped with the reason recorded. |
| 3. Discover | Real addresses only. (a) addresses printed in the advert itself, (b) scraped from the company's own site - domain via Clearbit autocomplete, then `/`, `/contact`, `/careers`, `/jobs`, `/about`, `/team`. Ranked **named person > hiring inbox (careers@, hr@) > generic (info@)**. Domain must have an MX record. Nothing real found means `no_email` and nothing is sent. **Addresses are never guessed or pattern-generated.** |
| 4. Compose | Role-family template (communications / electronics_technician / instrumentation_maintenance / events_production / general) picked from the title. Gemini fills a fixed skeleton; code enforces the rules and rejects-and-retries up to 3 times. |
| 5. Send | Gmail SMTP over SSL, CV PDF attached, best contact tier first then highest score. One email per company **ever**. Caps per run and per day, 30s between sends. |
| 6. Follow-up | 4 days after sending, IMAP checks the inbox for a reply from that address. No reply means one short follow-up, no attachment, threaded onto the original. Replied means marked `replied` and never touched again. |
| 7. State | Everything in `data/state.json`, committed back by the workflow. |

### Copy rules enforced in code, not just asked of the model

- 60-90 words in the body, excluding greeting and sign-off
- greeting by first name when one is known, otherwise `Hi,`
- first line names the exact role plus one concrete detail from that listing
- 2-3 numbered proof points, relevant to that job only
- exactly one question as the call to action
- sign-off is always `Harry / Harry Russell / 07398 530978 / CV attached`
- subject max 8 words, must name the role, never "Application for"
- no markdown, no em dashes, no exclamation marks
- a banned-phrase list (`I hope this email finds you well`, `passionate`,
  `leverage`, `delve`, `seamless`, `synergy`, `dynamic`, `thrilled`, ...) - any
  hit and the draft is rejected and rewritten

If a draft still fails after three attempts the job is marked `compose_failed`
and nothing is sent.

---

## Setup

### Secrets
`Settings > Secrets and variables > Actions > Secrets`

| Secret | Where it comes from |
| --- | --- |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | developer.adzuna.com (free) |
| `REED_API_KEY` | reed.co.uk/developers (free) |
| `GEMINI_API_KEY` | aistudio.google.com (free tier) |
| `GMAIL_ADDRESS` | the Gmail account that sends |
| `GMAIL_APP_PASSWORD` | Google account > Security > App passwords (needs 2FA on) |

### Variables (all optional)
`Settings > Secrets and variables > Actions > Variables`

| Variable | Default | Notes |
| --- | --- | --- |
| `TEST_MODE` | `0` (live) | Set to `1` to route everything back to your own inbox. |
| `DAILY_SEND_CAP` | `20` | across all runs in a UTC day |
| `PER_RUN_SEND_CAP` | `7` | the workflow runs 3x per weekday |
| `SEARCH_LOCATIONS` | `Aberdeen,Dundee,Edinburgh,Glasgow,Inverness` | comma separated |
| `SEARCH_RADIUS_MILES` | `25` | per location |
| `SCORE_THRESHOLD` | `70` | |

### CV

The pipeline attaches the first `*.pdf` it finds in `cv/`, then the repo root.
**No PDF means nothing is sent** - that is deliberate.

`cv/Harry_Russell_CV.pdf` was generated from `cv/Harry_Russell_CV.docx` by
`tools/build_cv_pdf.py`. If you would rather use Word's own "Save as PDF"
output, drop that file into `cv/` and delete the generated one. After editing
the .docx:

```bash
pip install reportlab
python tools/build_cv_pdf.py
```

---

## Running it

The workflow runs at 08:00, 11:30 and 15:00 UTC on weekdays. You can also run it
by hand from the Actions tab (**Run workflow**), which takes two inputs:

- `test_mode` - override `TEST_MODE` for that one run
- `dry_run` - compose everything and send nothing; drafts land in `state.json`
  as `draft_subject` / `draft_body`

Locally:

```bash
pip install requests dnspython
export GEMINI_API_KEY=... ADZUNA_APP_ID=... ADZUNA_APP_KEY=... REED_API_KEY=...
export GMAIL_ADDRESS=... GMAIL_APP_PASSWORD=...
python job_machine.py --dry-run           # nothing is sent
TEST_MODE=1 python job_machine.py         # sends, but only to your own inbox
python job_machine.py                     # LIVE - real employers
python job_machine.py --summary --force   # send tonight's digest right now
```

Tests (no network, nothing is sent):

```bash
python -m unittest discover -s tests -v
```

---

## The nightly digest

At 22:00 UK time every day, `summary.yml` emails you what went out:

- every application sent in the last 24 hours - role, company, location, score,
  who it went to and which tier that address was, and the subject line used
- any replies that came in, and any follow-ups sent
- how many listings were found, how many had no real address, how many are
  queued, how many are waiting on a reply, and the all-time total

GitHub cron is UTC only and does not know about British Summer Time, so the
workflow fires at both 21:00 and 22:00 UTC and the script sends only on the one
that is actually 22:00 in the UK. To see a digest right now, run the **job-machine
summary** workflow by hand with `force` left at `true`.

## Pausing or rehearsing

- **Stop everything:** disable the `job-machine` workflow in the Actions tab.
- **Rehearse instead of sending:** set the repo variable `TEST_MODE` to `1`.
  Everything runs, but the emails come to you with `[TEST -> real@address]` in
  the subject and no company gets marked as contacted.
- **See the copy without sending at all:** run the workflow by hand with
  `dry_run` set to `true`; drafts land in `data/state.json` as `draft_subject`
  and `draft_body`.

Companies contacted while live are remembered forever in `companies_contacted`
and are never emailed twice.

---

## Job statuses in `data/state.json`

| Status | Meaning |
| --- | --- |
| `new` | harvested, not scored yet |
| `scored` | scored >=70, waiting on an email address |
| `ready` | has a real address, queued to send |
| `sent` | emailed for real |
| `test_sent` | emailed to your own inbox by TEST_MODE |
| `replied` | they replied, so it is left alone forever |
| `no_email` | no real address found; nothing was sent |
| `compose_failed` | three drafts failed the style rules |
| `send_failed` | SMTP error; the company is still free to try again |
| `skipped` | below threshold, excluded title, or company already contacted |

Dead listings (`skipped`, `no_email`, `compose_failed`, `send_failed`) are pruned
after 45 days. Sent history and `companies_contacted` are kept forever.

---

## Notes and limits

- Free tiers all round: Adzuna and Reed APIs, Gemini 2.5 Flash, Clearbit
  autocomplete (no key), GitHub Actions minutes on a public repo.
- Reed only publishes a posting date, not a time, so its 48h window is enforced
  as "today or yesterday". Adzuna gives a timestamp and is filtered exactly.
- TEST_MODE sends count towards `DAILY_SEND_CAP` - they are still real Gmail
  sends, and Gmail has its own daily limits.
- Scoring is capped at 40 listings and discovery at 15 per run to stay inside the
  Gemini free tier.
- Job boards and Gemini fail softly: a broken stage prints the error and the run
  carries on, so state is never lost.
