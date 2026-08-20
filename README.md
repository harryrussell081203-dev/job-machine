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
| 0. Schedule | `run.yml` five times a weekday, two of them landing inside the Tue-Thu 9-11am UK window that replies best; `summary.yml` sends the digest at 22:00 UK time every day. |
| 1. Harvest | Adzuna + Reed + **job-alert email in Harry's own inbox** (`alert_harvest.py`, which is where most boards come from), listings **<=48h old**, every location in `SEARCH_LOCATIONS`, engineering/technician/electronics/instrumentation/comms keywords. Duplicates across the two boards are collapsed; obviously wrong titles (chartered, HGV, chef...) are dropped before they cost an AI call. |
| 2. Score | Gemini 2.5 Flash scores 0-100 against the candidate profile. **>=70 proceeds**, below is skipped with the reason recorded. |
| 3. Discover | Real addresses only. (a) addresses printed in the advert itself, (b) scraped from the company's own site - domain via Clearbit autocomplete, then `/`, `/contact`, `/careers`, `/jobs`, `/about`, `/team`. Ranked **named person > hiring inbox (careers@, hr@) > generic (info@)**. Domain must have an MX record. Nothing real found means `no_email` and nothing is sent. **Addresses are never guessed or pattern-generated.** |
| 4. Compose | Role-family template (communications / electronics_technician / instrumentation_maintenance / events_production / general) picked from the title. Gemini fills a fixed skeleton; code enforces the rules and rejects-and-retries up to 3 times. |
| 5. Send | Gmail SMTP over SSL, CV PDF attached. Covenant employers first, then best contact tier, then score, freshest first within that. One email per **employer** ever; agencies get up to four, one per vacancy. Off-peak runs hold the queue back for the window but always send anything posted in the last 14 hours. Caps per run and per day, 30s between sends. |
| 6. Follow-up | Day 4 and day 9 nudges to the same person, threaded, no attachment. Day 16, if a second real address at that company is known, one fresh approach to them with the CV. Any reply stops everything. |
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

The workflow runs five times a weekday, weighted towards Tuesday to
Thursday mid-morning. You can also run it
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

Tests (no network, nothing is sent, no employer is contacted):

```bash
python -m unittest discover -s tests -v          # 322 tests
```

---

## Why there is no application-portal agent

There used to be one: a real Chromium that opened the employer's application
form, worked down it, uploaded the CV and submitted. It has been removed, along
with `ats_finder.py`, `tools/handoff.py` and `portal.yml`.

It did not work as intended, and the reason is structural rather than a bug
worth chasing. Of fourteen Aberdeen employers probed, exactly one used a hosted
ATS and it needed an account. The rest take applications on a form of their own,
and every form is different. What the agent actually produced was a queue of
listings parked at `portal_manual` and `portal_review` waiting for a human -
listings the email route would have written to that morning. It was not adding
applications, it was delaying them.

The listings it had parked have been released back into the email queue and
carry `portal_fallback_at`, which exempts them from the off-peak hold: they have
waited long enough. Three genuine applications it did submit are still at
`portal_submitted` and are still watched for replies.

`data/answers.json` stays in the repo. Nothing reads it now, but it is the set
of answers to the questions application forms ask, in one place, for when you
fill one in yourself.

> **This repository is public**, so `data/answers.json` and the CV in `cv/` are
> readable by anyone - including your home address. Making the repository
> private fixes that and stays free: private repos get 2000 Actions minutes a
> month and this uses roughly a third of that.


## When somebody asks you to stop (`data/do_not_contact.json`)

Allstaff Recruitment phoned on 20 August 2026 to say Harry was asking the same
questions again. Three messages had gone to their `jobs@` inbox - the
application, a nudge four days later, and the last of the sequence at 08:35
that morning.

The machine had no way of being told to stop. Every register it kept answered
"have we written to this company yet", and the answer being *yes* is what
schedules the next message - so the harder somebody tried to make it stop, the
more certain the follow-up became.

Add them to `data/do_not_contact.json` and nothing in this repo writes to them
again:

```json
{"name": "Allstaff Recruitment",
 "domain": "allstaffrecruitment.co.uk",
 "emails": ["jobs@allstaffrecruitment.co.uk"],
 "reason": "phoned to ask us to stop",
 "added": "2026-08-20"}
```

`domain` is the strongest entry - it covers addresses nobody has discovered
yet, because the reason a company asked us to stop does not expire when the
discovery stage finds a different inbox there next week. The name is matched on
whole words, loosely enough that "All Staff Recruitment Ltd" is the same
company and strictly enough that an employer called "A" is not.

The check lives in `send_email()`, which every outgoing message goes through -
applications, follow-ups, speculative notes, replies - so a stage added later
cannot forget it. It is kept on disk rather than in `state.json` because it has
to survive a prune, a state reset and a merge that goes wrong.

### The cap nobody had written

Allstaff phoned after three messages. One consultant at Connect Appointments
had received **twelve**, and the inbox at Canmore eleven, without any rule
being broken - because both rules that decide sending reason about a single
vacancy. An agency may be approached about four roles, and each approach
carries an application plus two nudges.

`MAX_MESSAGES_PER_INBOX` (default 5) counts everything ever sent to one
address, across every vacancy, and stops there. It is per address, not per
company: it is about one person's patience, so a different consultant at the
same agency starts fresh.

## The inbox is the widest source of jobs (`alert_harvest.py`)

Adzuna and Reed have APIs and between them yield a median of **four** in-trade
Aberdeen listings a day. CV-Library, Totaljobs, s1jobs, Oil and Gas Job Search,
Rigzone and Energy Jobline carry most of the rest of this market and have **no
free API at all**.

Every one of them will email you a daily alert. So the machine reads your own
inbox.

**No account is ever automated.** Indeed and LinkedIn ban accounts for
automated access, their detection is good, and a banned account is a real loss
to a real job search - while every auto-apply tool that logs in reports the
2-5% response rate that is the worst channel there is. Reading your own email
is a different thing entirely: the alert was addressed to you because you
asked for it.

### Setting it up - one evening, then never again

On each site below: sign in as yourself, search for the roles and area you
want, and **save the search as a daily email alert** to the Gmail address the
machine uses. That is the whole integration.

| Site | Worth it because |
| --- | --- |
| CV-Library | biggest UK trade board with no API |
| Totaljobs / Jobsite | large technician volume |
| s1jobs | Scotland only, agencies post here first |
| Oil and Gas Job Search | Aberdeen energy market |
| Energy Jobline | offshore and renewables |
| Rigzone | offshore operators |
| Indeed | set the alert, never the login |
| LinkedIn | set the alert, never the login |
| Google Alerts | free, for phrases like `"instrumentation technician" Aberdeen` |
| Employers' own career pages | many have "register for alerts" - the best source of all, since these never reach a board |

While you are on those sites, **upload your CV to each one's database**.
Recruiters search those databases, and most rank by how recently a CV was
touched - so refreshing it weekly puts you at the top of their search results.
That is inbound: they come to you.

Once the alerts are arriving:

```bash
python alert_harvest.py --dry-run     # see what it would pull out, change nothing
python alert_harvest.py --days 7      # read the last week
```

The main pipeline runs it automatically at every harvest.

## What the research says works, and where it lives in the code

The methods here are not guesses. Cold outreach to a named human replies at
15-25% against 2-5% for a blind online application; applying within 24-48
hours produces two to three times the interviews; three touches capture about
93% of the replies a sequence will ever earn.

| Finding | Where it is implemented |
| --- | --- |
| A named human beats an application form, 15-25% vs 2-5% | the whole email path - real addresses only, ranked named person > hiring inbox > generic |
| 50-125 word emails reply ~50% better than long ones | the 60-90 word rule enforced in `check_copy` |
| Apply within 24-48 hours: 2-3x the interviews | harvest every run; freshest first in the queue; `brand_new()` sends today's listings immediately |
| Tue-Thu 9-11am gets the best open and reply rates | `in_peak_window()`, and two crons that land inside it in BST and GMT |
| Three touches capture ~93% of replies; a fourth to the same person reads as pressure | day 4 and day 9 nudges, then stop |
| A *different* person at the same company is the exception | `next_stakeholder()` - day 16, new conversation, CV attached |
| Referrals are ~7x more likely to be hired | no honest way to automate this one; see below |
| **A guaranteed interview scheme beats all of it** | `data/veteran_employers.json` - Armed Forces Covenant signatories, written to first |

**The Covenant route is the only one that produces an interview by right**
rather than by persuasion. Many signatories guarantee an interview to a
veteran who meets the minimum criteria for the role, and Harry served two
years in the Royal Navy. Expanding `data/veteran_employers.json` is the
highest-value thing anyone can do to this repository - each name added is an
employer where being ex-forces moves him from the pile to the shortlist.

The email never claims an employer holds an award or runs a scheme. It asks.
His service is a fact about him; whether they run a scheme is theirs to state.

**Agencies are not employers.** An employer has the one job they advertised
and a second unsolicited email reads as pestering, so they get one, ever. A
recruitment agency is paid to place people and holds dozens of live roles, so
it gets up to four approaches, one per vacancy, six days apart - with its own
template, because a consultant is matching a person against a list and needs
the facts that let them do it.

**Referrals** are the one well-evidenced channel left unautomated. Doing it
properly needs to know who Harry actually knows, and inventing a connection
would be worse than not claiming one.

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
3. **A second real address at the company.** If the first person has not
   replied by day 16 and another genuine address there is known, one fresh
   approach goes to them with the CV, so the application does not rest on one
   inbox nobody reads.
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
| `skipped` | below threshold, excluded title, company already contacted, or the inbox has had `MAX_MESSAGES_PER_INBOX` already |
| `do_not_contact` | they asked us to stop; nothing will ever be sent to them again |

Dead listings (`skipped`, `no_email`, `compose_failed`, `send_failed`) are pruned
after 45 days. Sent history and `companies_contacted` are kept forever, and
`data/do_not_contact.json` is not in `state.json` at all so that no prune,
reset or bad merge can lose it.

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
