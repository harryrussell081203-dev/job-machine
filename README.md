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
| 0. Schedule | `run.yml` at 08:00, 11:30 and 15:00 UTC weekdays; `portal.yml` at 09:30 UTC weekdays; `summary.yml` sends the digest at 22:00 UK time every day. |
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

Tests (no network, nothing is sent, no real portal is touched):

```bash
python -m unittest discover -s tests -v          # 86 tests
PORTAL_BROWSER_TESTS=1 python -m unittest discover -s tests   # + drives Chromium
```

The browser tests run against `tests/fixtures/fake_ats.html`, a local replica of
a Greenhouse/Lever style form, so the form-filling is proven end to end without
sending junk to a real employer.

---

## The portal agent (`portal_agent.py`)

The email side finds a human and writes to them. This side does the opposite: it
opens the employer's actual application form in a real Chromium, works down it
like a person would, uploads the CV and submits.

```bash
python portal_agent.py                 # show the queue
python portal_agent.py --harvest       # pull and score a month of listings
python portal_agent.py --run           # fill forms in, stop before submitting
python portal_agent.py --run --submit  # actually submit the clean ones
python portal_agent.py --run --headed  # watch it work in a visible browser
```

It runs from `.github/workflows/portal.yml` at 09:30 UTC on weekdays, and every
form it touches is screenshotted and kept as a build artifact for 30 days.

### Where the answers come from

Everything is answered from **`data/answers.json`** and nothing else. Anything
still `null` in that file is treated as "I don't know" and gets the application
flagged rather than guessed at, so it is worth filling in.

Answers are shaped to fit the box they are going in: `"Immediately"` becomes
tomorrow's date in a date picker, and `"35000"` loses its pound sign and comma in
a number-only field. If a value cannot be made to fit, the field is flagged
instead of forced.

> **This repository is public**, so `data/answers.json` and the CV in `cv/` are
> readable by anyone. If you would rather your home address were not, set
> repo **secrets** `ANSWER_ADDRESS_LINE_1` and `ANSWER_POSTCODE` and blank those
> two entries in the file - any `ANSWER_<KEY>` environment variable overrides the
> file. Making the repository private also works and stays free (private repos
> get 2000 Actions minutes a month; this uses roughly a third of that).

Free-text questions ("why do you want this role?") go to Gemini, which must
answer from your profile *and name the fact it used*. If it cannot ground the
answer, the field is left blank and the application is flagged. It never makes
something up to fill a box.

### What it refuses to do

| It will not | Because |
| --- | --- |
| Answer convictions, DBS, health or certification declarations | Only you can answer those, and a wrong answer follows you |
| Enter your NI number, passport, bank details or date of birth | A placeholder like `0000 0000` is false information submitted under your name. A real application form does not ask for bank details either - that is an onboarding step, so a form asking at this stage is a scam signal worth looking at yourself |
| Tick "I certify the above is true and complete" | A machine cannot certify anything on your behalf |
| Solve or work around a CAPTCHA | That is defeating bot protection, and it gets accounts banned |
| Log into Workday, Taleo, LinkedIn, Indeed or Reed | They need an account and block automation; the digest links them for you |

Hit any of those and the application is filled to the last safe field,
screenshotted, and handed to you as `portal_review` rather than submitted.

**Equal-opportunities monitoring is different.** Ethnicity, gender, disability
and orientation questions nearly always offer "Prefer not to say" (in whatever
wording), and the agent picks it. That is a real answer the form itself offers,
so those questions stop blocking applications without anything being invented.
If a monitoring question offers no opt-out, it is flagged like the rest.

### Portals it works on

Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Recruitee, Teamtailor,
JOIN and Personio — the ones that accept an application with no account and no
bot check. Anything else is recorded as `portal_manual` with a direct link.

### Portal variables

| Variable | Default | Notes |
| --- | --- | --- |
| `PORTAL_SUBMIT` | `0` | `0` fills and screenshots but stops short of submitting. Set to `1` to actually apply. |
| `PORTAL_MAX_AGE_DAYS` | `30` | How far back to look |
| `PORTAL_PER_RUN_CAP` | `10` | Applications per run |
| `PORTAL_DAILY_CAP` | `25` | Applications per day |

## Converting applications into interviews

Five levers run automatically on top of the basic apply loop:

1. **Reply watcher** (`reply.yml`, every 2h on weekdays, plus every pipeline
   run). Scans the inbox for replies from anyone we contacted and classifies
   them. An **interview invitation gets an availability reply within minutes**
   ("free any day this week, tomorrow included") threaded onto their message,
   and you get an alert email flagged `[job-machine INTERVIEW]`. Questions and
   anything unclassifiable get an alert flagged `NEEDS YOUR ANSWER` - the
   machine never negotiates on your behalf. Automated receipts stay silent.
   Set repo variable `REPLY_AUTORESPOND=0` to turn the auto-reply off.
2. **Tailored CV per application.** The attached PDF is rebuilt per role family
   (`cv_tailor.py`): the summary leads with what that kind of job cares about
   and the key skills are reordered to match. Everything in it already exists in
   the master CV - tailoring reorders and emphasises, it never invents.
3. **The double-tap.** When the portal agent submits an application and a named
   person was discovered, they get a short "just applied through your portal"
   note with the CV attached, so the application does not sit unread in an ATS
   queue.
4. **Two follow-ups, then silence.** Day 4 and day 9. A reply at any point stops
   everything.
5. **Speculative outreach** (`data/targets.json`). Two notes a day to a curated
   list of Aberdeen subsea/O&G and Scottish defence employers who are not
   advertising - the hidden market. Honest copy ("nothing advertised that I can
   see"), real scraped addresses only, one per company ever, and the one
   concrete detail each email uses comes from the curated note, not from the
   model's imagination. `SPEC_PER_DAY=0` turns it off.

Freshly posted listings are also applied to first within a tier - being an early
applicant is itself a conversion lever.

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
