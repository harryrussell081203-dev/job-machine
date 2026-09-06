# Context brain

Everything Harry has told this project, in one place, so no session has to be
told twice and no session quietly forgets.

**Read this first, before changing anything.** The rules in section 2 are
absolute and several of them exist because breaking them already caused real
harm. If something here is wrong, edit it — but edit it deliberately, and say
in the commit why.

`data/goals.json` (what to do next), `data/situation.json` (where he stands
today), `data/answers.json` (form answers) stay the machine-readable sources
of truth and are read by code. **This file is read by people and by Claude.**
It never contradicts those three; where they overlap, they win.

---

## 1. Who this is for

**Harry Dean Russell**, 22, Aberdeen (AB25 3AJ), `07398 530978`.

| | |
| --- | --- |
| **Now** | Technician at Hydro Group since **24 Aug 2026**. £30k / £15ph, Mon–Fri, half-day Friday, no offshore. "The job's alright but I want better." |
| **Does** | Tests and repairs subsea cables and connectors; moulds cables with epoxy resins and polyurethane |
| **Before** | 3 years Sonardyne (subsea positioning) |
| **Before that** | Royal Navy 2021–2023, Communications & Information Specialist, Type 23 frigate, 2 years at sea |
| **Education** | BEng at RGU, paused |
| **Also runs** | **Leads2Profit** — marketing automation for nightlife and events venues, founded ~2024 |
| **Licence** | **No driving licence.** The single biggest limit on where he can take work. Provisional is £34 on gov.uk and is goal #2 in `goals.json` |
| **Clearance** | **None.** Held during service, lapsed at discharge. Eligible to be vetted |
| **Tickets** | No BOSIET/MIST. ~£1,000, effectively mandatory offshore, goal #1 |

### What he wants from a job

Set in his own words on 2026-08-29 and refined by direct answers since:

- **Pay floor £35,000 / £18 per hour.** Below that never reaches him.
- **Travel is non-negotiable.** "This job must incorporate paying me to go on
  regular work trips abroad." Every letter asks the employer about it.
- **Contract work is actively wanted** — "really intriguing", not a fallback.
- **Anywhere**, if travel and lodging are paid. Would relocate to Edinburgh
  or the Central Belt. UK-wide contract with digs is fine.
- **Only upward.** A sideways move from Hydro Group scores nothing.

---

## 2. Standing rules — absolute

Kept in his words where he gave them in his words. None of these is a
preference to be weighed against convenience.

### On truthfulness

> **"we have to remove dv from everything as I am no longer dv cleared just
> do this from now on"**

Not a tidy-up. `answers.json` once read `"DV (Developed Vetting)"`, an
automated commit reverted the file on 31 Aug 2026, and the false claim sat in
a **public** repo until 3 Sep. `CLEARANCE_CLAIM` in `job_machine.py` now
guards both outgoing email and that file, and a repeat fails the build.

### On contact details

**Real addresses only. Never guessed, never pattern-generated.** Nothing real
found means `no_email` and no send. The same rule extends to phone numbers.
This is the rule the whole product is sold on, and it is why the free `/find`
page exists.

### On cost

**The personal machine costs £0 to run and stays that way.** The product may
cost money; the job hunt may not.

### On accounts and identity

**Do not create accounts in his name.** CTP RightJob verifies service records
— it has to be him.

### On paid services

**Clay: free credits only. Stop when exhausted. Buy nothing.**

### Employers who are off limits

| Who | Rule | In his words |
| --- | --- | --- |
| **Hydro Group** | Never contacted. His employer. | "dont hit hydro again" |
| **Allstaff Recruitment** | No follow-ups. | "allstaff just called me saying im asking the same questions again dont follow up with them" |

Both are enforced in `data/do_not_contact.json`. Hydro Group uses
`"match": "exact"` on purpose — `company_key()` reduces it to `hydro`, which
would otherwise block Hydro Cleansing, Hydro Systems and Hydro International.

### Things he decided against

- **The application portal.** "get rid of the application portal thing it
  doesnt work as intended" — removed, do not reintroduce.
- **Voicemail drops in his voice.** Declined on UK PECR reg 19: automated
  calling systems playing a recorded or synthetic message need prior consent.

### The limiter he asked to be reminded about

> **"i can give you more gmails if needed too just ask when this is
> neccecary and i can do it remind me about this one as its a limiter"**

Inbox capacity caps outreach volume (`MAX_MESSAGES_PER_INBOX = 5`). **Ask him
for another Gmail when it binds.** Asked on 2026-09-06; he chose to use his
main mailbox for the product's first live run instead, with history imported
first.

### Events

> **"register me when you can if you cant let me know and make a list"**

Register where possible. Where registration needs him, list it and say so.

---

## 3. Goals for the machine

Two machines, one repo. **They must not interfere with each other** — his
words: "I wanna make sure this is still running as intended despite making a
public version I wanna reiterate these are separate."

### Machine A — the personal job hunt (root of the repo)

**Purpose:** get Harry a better job than Hydro Group, on the terms in §1.

**Runs:** free GitHub Actions, three times a weekday. `run.yml`, `reply.yml`,
`summary.yml`, `morning.yml`, `nudge.yml`.

**Standing goals**, in priority order, from `data/goals.json`:
1. Get OPITO BOSIET and MIST paid for (~£1,000, the gate to the offshore market)
2. Start the driving licence — provisional, then theory
3. Ring one recruiter already emailed

**Widened scope**, on his instruction of 2026-09-03 — "EMAIL ANYONE THAT CAN
GIVE ME ANY OPPOURTUNITIES … CONNECT ME WITH PEOPLE … CONTINUE THE JOB HUNT
BUT UP THE OUTPUT ON OTHER ASPECTS":

| Strand | Where | Rule |
| --- | --- | --- |
| Employers and agencies | `job_machine.py` | The core hunt. Never slows down for the others. |
| Charities and support bodies | `support_outreach.py`, `data/support_orgs.json` | Asks for the tickets and for business support |
| Trade bodies | `networking_outreach.py`, `data/networking_targets.json` | Asks for a conversation. One approach each, ever. Never a job pitch, never money. |
| Funding for Leads2Profit | `data/funding_opportunities.json` | **A monthly reminder to Harry only.** Never an outbound email — see below. |
| Free local events | Aberdeen, plus Dundee and Inverness by train | Register where possible, list where not |

**Why funding is a reminder and never an email:** a cold email inviting
investment is a financial promotion under **FSMA s21**. Sending one without
FCA authorisation is a criminal offence. So `funding_opportunities.json`
deliberately holds **no contact details at all** — it surfaces open schemes
to Harry on the 1st of the month and he approaches them himself.

### Machine B — the product (`product/`)

**Purpose:** make money and get users. His words: "i want to sell this system
as a cheap subscription", and "i want to make money from this app and get
users too".

**The product in one line, his:** "you upload your cv and answer set
questions about what jobs you want where you want them and find the jobs the
person will be best applicant for and the interviews just land in their
inbox".

**Live at** `job-machine.onrender.com` (Render free plan, Supabase eu-west-1)
**since 2 Sep 2026.** To move to a bought domain — **the domain is for
hosting the app**, that is its primary job.

**Design commitments already made and not to be undone:**

- **Clean, stylish, streamlined, user-ready.** Stated 2026-09-06.
- **A UI and automatic sending are both necessary.** Both are built; the
  sweep cron has never been able to fire (wrong directory, wrong branch).
- **The playbook is given away free** at `/playbook`, in full, no account.
- **The hard part is given away free** at `/find` — paste an advert, see real
  addresses. It reads only the pasted text and fetches nothing.
- **Landing-page numbers must survive somebody asking to see the working.**
  26 sent, 7 human replies, 27%, counted against the real sent folder.
  Autoresponders are named and excluded rather than quietly counted.
- **Never guess an address. Never claim a qualification. Never write to the
  same employer twice. Never name a user's current employer.** The same four
  promises as Machine A, made in public.
- **Automatic sending stores a mail app password encrypted**, key outside the
  database, and the by-hand route exists permanently for people who won't.

**Not done, and blocking:** a privacy policy and terms (UK GDPR — he holds
CVs, home addresses and mailbox passwords); commercial API terms from Adzuna,
Reed and Google, whose free tiers are for personal use.

---

## 4. Decisions log

Choices already made, so they are not relitigated every session.

| Date | Decision |
| --- | --- |
| 2026-08-29 | Pay floor set to £35k / £18ph; contract work wanted; travel required |
| 2026-09-03 | Voicemail generation declined — PECR reg 19 |
| 2026-09-03 | Repo is public and holds his address, phone, CV and rejection history. **Making it private is free and still outstanding.** |
| 2026-09-06 | Domain: **buy it, to host the app.** Free Cloudflare forwarding plus Gmail "send mail as" is a free bonus that also removes the date of birth from `harryrussell081203@gmail.com` in every application's From line. Do **not** move the mailbox — it would break the IMAP alert harvest and reset deliverability. |
| 2026-09-06 | Product merges to **main**, with isolation enforced by path filters, separate secrets and separate concurrency groups — not by living on a branch |
| 2026-09-06 | First live product run uses his **main Gmail**, with all 169 contacted companies imported **before** the mailbox is connected |
| 2026-09-06 | Spend: domain ~£8–10/yr. No paid email-finder API (£27+/mo, and a logic fix does most of it free). No Render paid tier until `/status` says cold starts are costing signups. |

## 5. How to report numbers

`status=sent` **drains** — a record flips to `replied` and leaves the bucket,
so the figure goes down. It means "sent and still waiting", not a total.
`send_counts` is a per-day **cap counter** merged with `max()` across
concurrent runs and is never a lifetime figure.

**Quote `sent + replied`.** It only ever goes up. `job_machine.py --stats`
prints one labelled block; use it rather than counting by hand.
