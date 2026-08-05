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
| 0. Schedule | `run.yml` five times a weekday, two of them landing inside the Tue-Thu 9-11am UK window that replies best; `portal.yml` at 09:30 UTC weekdays; `summary.yml` sends the digest at 22:00 UK time every day. |
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
| `HTTPSMS_API_KEY` | the httpSMS app on the phone, Settings > API key (optional - no key, no texting) |

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
| `AGENCY_REGISTER_GAP_DAYS` | `30` | Days before an agency may be written to again |
| `AGENCY_REGISTER_MAX` | `6` | Approaches per agency, ever |
| `AGENCY_REGISTER_PER_RUN` | `12` | Cap for a manual `fire-agencies` run |
| `AGENCY_PIPELINE_PER_RUN` | `2` | Cap for the stage inside an ordinary run |
| `SMS_FROM` | `+447398530978` | The handset httpSMS is installed on, E.164 |
| `SMS_OUTBOUND` | `1` | `0` keeps interview alerts, stops texts to anyone else |
| `DAILY_SMS_CAP` | `3` | Texts to other people per day |

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

Pushing a branch fires one job on its own, which is how the side routes are run
when the Actions UI is not to hand:

| Branch | What it does |
| --- | --- |
| `fire-run/...` | an ordinary run |
| `fire-rescore/...` | re-judge listings scored under an older profile |
| `fire-support/...` | write to the charities and training bodies - empties whatever is left on that list |
| `fire-signup/...` | just the organisations that run the veterans' employment services, asking them to register him (`--only registration`) |
| `fire-agencies/...` | register with, or refresh at, the recruitment agencies |
| `fire-sms/...` | one test text to Harry's own phone, to prove the httpSMS bridge |

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
python -m unittest discover -s tests -v          # 270 tests
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

### How it finds the form

The job boards are a dead end: Adzuna's outbound page renders blank to a
headless browser, proven over five runs. So the form is looked for in cost
order, and the first route that answers wins:

| # | Route | Cost |
| --- | --- | --- |
| 0 | The listing URL is already a portal | free |
| 1 | The advert text names the portal — employers paste their link into the description all the time | free, no network |
| 2 | The employer's **board API** — Greenhouse, Lever, Ashby, SmartRecruiters, Workable and Recruitee each publish a free JSON board that 404s honestly | 6 requests, asked in parallel |
| 3 | The employer's **own careers page**, walked through to the vacancy and its apply link | a few page loads |
| 4 | Last resort: follow the job board's own interstitial | a browser hop |

Route 2 replaced guessing at board *pages*. Every one of those platforms
answers `200` for a company that is not on it — SmartRecruiters serves a
generic search page, Workable serves a bot check — so a `200` proved nothing
and once matched two Aberdeen firms to **AECOM's** adverts. The APIs 404
properly, and hand back the real postings with their real application URLs.

**Route 3 matters more than it sounds.** Of fourteen employers probed in
Aberdeen, exactly one used a hosted ATS, and it needed an account. The rest
take applications on a form of their own. Off the known platforms a page has
to look like an application — a CV upload, or a name/email/phone set — before
anything is typed into it, so a contact form or a newsletter signup is never
mistaken for a vacancy.

### Roles the job boards never advertised

An employer's own board carries every role they have open, not just the ones
they paid to advertise. Once a company's board is known, listing it costs a
single request, so `--harvest` also sweeps the thirty curated employers in
`data/targets.json` plus any company whose advert already scored 60+, and adds
anything that fits and is somewhere you could actually work. Those arrive with
their application URL already known.

Which ATS a company uses is remembered in `data/state.json` for three weeks —
finding a board costs up to eighteen requests, re-listing a known one costs
one, and firms do not change ATS often. The open roles themselves are always
fetched live.

### Portals it works on

Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Recruitee, Teamtailor,
JOIN and Personio — plus any employer's own form that has a CV upload and no
bot check. Workday, Taleo, LinkedIn, Indeed and Reed need an account and are
recorded as `portal_manual` with a direct link for you.

### Checking it without waiting for a run

Push any branch named `fire-probe/...` and the workflow reports, in about
thirty seconds and with no browser, exactly what each platform answers for the
companies currently in the queue. `fire-diagnose/...` opens the pages and
reports what is on them without filling anything in. `fire-submit/...` really
applies, to one job only.

### Portal variables

| Variable | Default | Notes |
| --- | --- | --- |
| `PORTAL_SUBMIT` | `0` | `0` fills and screenshots but stops short of submitting. Set to `1` to actually apply. |
| `PORTAL_MAX_AGE_DAYS` | `30` | How far back to look |
| `PORTAL_PER_RUN_CAP` | `10` | Applications per run |
| `PORTAL_DAILY_CAP` | `25` | Applications per day |

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
| CTP RightJob / Forces Employment Charity | ex-military vacancies from employers who asked for service leavers - the warmest adverts in this market. See the note on eligibility above |
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

## The agency register (`agency_outreach.py`)

Twenty-one recruitment agencies in `data/agencies.json` - the Aberdeen energy
desks (Cammach, TMM, TEXO, Orion), the international rotational staffers
(NES Fircroft, Airswift, Petroplan, Brunel, Atlas Professionals), the Scottish
technical desks, and the ex-forces specialists - written to with the CV and
asked to put Harry on the database.

```bash
python agency_outreach.py            # who is due, and the letters they'd get
python agency_outreach.py --send     # send them
python agency_outreach.py --list     # the register: who, when, which approach
```

It also runs as a stage of every ordinary pipeline run, capped at
`AGENCY_PIPELINE_PER_RUN` (default 2), and can be fired on its own by pushing a
branch named `fire-agencies/...`.

**This is the one route allowed to write to the same firm twice**, and the rest
of the design is about earning that:

| Rail | Why |
| --- | --- |
| 30-day cooldown per agency (`AGENCY_REGISTER_GAP_DAYS`) | Long enough to be a refresh rather than a chase. It is also roughly how often a CV needs touching to stay near the top of a consultant's search results, which is the entire point. |
| 6 approaches, then stop (`AGENCY_REGISTER_MAX`) | Half a year of monthly contact. After that, silence from a desk is an answer. |
| The second letter is a *different* letter | It says "I last wrote in June, here is my current CV" and asks what is live. The same pitch sent twice is a mail merge and reads like one. |
| An agency the pipeline pitched about a vacancy in the last 6 days is skipped | Both routes land in the same inbox, and the live vacancy is the more useful of the two. |
| Addresses scraped and MX-checked, never guessed | Same rule as everywhere. The MX check is on the address found, not on the site scraped, because agencies routinely run the careers site on one domain and their mail on another. |

The letter gives a consultant what they actually match on - trade, location,
availability, money - **including the awkward parts**: he does not drive, and
he does not hold BOSIET or MIST yet. A consultant who finds that out at the
point of submission has wasted a placement and Harry's shot at it.

No account is created and no portal is filled in. Several of these firms want a
profile on their own site, and the run prints which ones found nothing postable
so they can be done by hand - Orion is the known case, since orionjobs.com
carries no MX record at all.

## Texting, from Harry's own number (`sms.py`)

[httpSMS](https://httpsms.com) is an app on his Android handset. The machine
POSTs a message, the app sends it as an ordinary SMS **from his own number, out
of his own allowance**. Nothing to pay, it arrives from the number on his CV,
and replies land in his messages rather than in an API nobody reads.

```bash
python sms.py --test        # one text to his own phone, to prove the bridge
python sms.py --call-list   # everyone worth ringing, with the number
```

Or push a branch named `fire-sms/...`, which does the `--test` and nothing else.

**The key is the repo secret `HTTPSMS_API_KEY` and lives nowhere else — this
repository is public.** With no key set, every texting path is skipped and the
rest of the pipeline is unchanged. A test asserts nothing key-shaped has been
committed to the code, the README or the workflows.

Two halves, and only one of them writes to anybody else:

**Inbound.** An interview invitation is worth knowing about in the minute it
lands, not at 22:00 in the digest. Those go to his own phone. No etiquette
question — he is texting himself.

**Outbound.** A cold text to a hiring manager's mobile reads as intrusive and
costs the application. A text to a consultant who has *already written back*
reads as normal: they work off their phones, and the number came out of their
own signature. So that is the only set this writes to, and even then:

| Rail | Why |
| --- | --- |
| Mobiles only | Most consultant direct lines are landlines and cannot receive an SMS at all. Those go on the call list instead of into a void. |
| They must have replied first | The whole difference between a follow-up and a cold text. |
| One per person, ever | There is no second text that helps. |
| Weekdays 09:00-18:00 UK | Get this wrong and it is a phone buzzing on somebody's bedside table. |
| Three a day | `DAILY_SMS_CAP`. |
| Always after the email | Never instead of it. |

`SMS_OUTBOUND=0` keeps the interview alerts and turns the outbound half off.

**Numbers are harvested from replies, not from adverts.** A consultant's direct
line only ever appears in one place: the signature of a reply they wrote
themselves. The reply watcher now keeps it, taking care to read only their own
words and not the quoted trail underneath — which carries Harry's number and, on
a forwarded thread, everyone else's. The nightly digest prints the result as a
call list, landlines first, because ringing a consultant beats everything else
in that email and is the one thing here nobody should ever automate.

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

**On CTP and what replaces it.** CTP resettlement - myPlan, the workshops and
the CTP RightJob board - runs from two years before discharge to two years
after. Harry left the Royal Navy in 2023, so that window has closed behind him,
and `data/support_orgs.json` used to record him as "already registered -
RightJob" and skip the charity entirely on the strength of it. Registered and
still entitled are not the same thing. **Op ASCEND** is the door that opens at
exactly that point: government funded, delivered by the same Forces Employment
Charity, for veterans who left *more* than two years ago, and it lasts for life.
Both it and CTP are now written to with an `ask` of `registration` - a letter
that asks to be signed up and asks what is still open to him, rather than
assuming either way. Their alert emails are in `ALERT_SENDERS`, so once he is
on a veterans' board its vacancies feed the machine like any other board's.

**Agencies are not employers.** An employer has the one job they advertised
and a second unsolicited email reads as pestering, so they get one, ever. A
recruitment agency is paid to place people and holds dozens of live roles, so
it gets up to four approaches, one per vacancy, six days apart - with its own
template, because a consultant is matching a person against a list and needs
the facts that let them do it.

**And the agency's database is a channel of its own** - see
`agency_outreach.py` below. Answering an agency's advert only ever reaches the
agencies that advertised something this week. Being *on the database* is
searched by the consultant, against roles that never got advertised at all.

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
