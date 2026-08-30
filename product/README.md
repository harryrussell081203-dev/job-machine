# job machine — point it at your own job search

The machine in the root of this repository was built for one person, and it is
still running for him. This directory is the part anyone can use.

It sends short, specific emails **directly to the people who do the hiring**,
instead of dropping your CV into an applicant tracking system with four hundred
others. Over four weeks the original sent 86 of them and got **22 replies — a
26% reply rate.** Cold email normally runs 1–5%.

`PLAYBOOK.md` is the method, in full, free of any software. Read that first —
if you only ever do it by hand in Gmail, it still works.

---

## What this is

Two things, deliberately separate:

**The playbook** (`PLAYBOOK.md`) — who to send to, how to write it, when to
follow up, and the numbers behind each rule. No setup, no keys, no code.

**The machine** — the same method, run for you three times a weekday by GitHub
Actions, for free. It finds fresh listings, scores them against your profile,
digs out a real address, writes the letter, sends it with your CV attached, and
chases twice if nobody answers.

---

## What it does

**Working now:**

- **The app** (`app/`) — sign in without a password, a Stripe paywall, upload
  your CV, answer the questions, and a drafts screen you work through one
  click at a time. Installs to a phone home screen; see below.
- **The whole pipeline** — harvest, score, find a real named address, write
  the letter. `jobseeker/pipeline/`.
- **Automatic sending**, if you want it: connect your own email once and
  letters go out on their own, with your CV attached, from your address.
- **Scheduled runs** for every subscriber, on free GitHub Actions hardware.
- `PLAYBOOK.md` — the method, complete, free to everyone at `/playbook`.

**Not done:**

- A privacy policy and terms. Required before charging anyone in the UK.
- Commercial API terms from Adzuna, Reed and Google. Their free tiers are for
  personal use, and this is the thing most likely to change the shape of the
  product.

Nothing here has yet made a live API call to Adzuna, Reed, Gemini or Stripe —
they are unreachable from the build container, so every test uses an injected
fake. The logic is exercised; the credentials and response shapes are not.

## Two ways letters go out

**By hand (the default).** The app writes the letter and you press send from
your own mail client, one click each. Nothing of yours is stored.

**Automatically.** You connect your own email once and letters go out without
being asked. This is what most people want, and it is off until you turn it
on.

Automatic sending needs your mail password, because there is no way to send
as somebody without something that authorises it. Be clear about the
trade-off before you turn it on:

- Use an **app password**, not your account password. It is revocable on its
  own, without changing your password or touching another device.
- It is stored **encrypted**, with a key held in the environment and never in
  the database — so a leaked database dump, the most likely breach by a
  distance, yields ciphertext.
- It is still **access to your mailbox**. An app password can read that
  mailbox as well as send from it. That is the honest summary, and it is why
  the by-hand route exists and always will.

Three guard rails apply whichever you choose, and two more only to automatic:

| Rule | Why |
| --- | --- |
| One employer, one letter, ever | The method stops working the moment it is abused |
| Never a guessed address | A guessed address bounces and costs you the next thirty |
| Never a claim you do not hold | The one mistake that follows you to vetting |
| A holding window before sending | Automatic does not have to mean unrecallable |
| A daily ceiling | Broad search terms must not become four hundred letters |

The holding window is the important one. A draft waits — an hour by default —
before it goes, so anything that reads wrong can be stopped. No employer reads
their email in that hour, so the window costs nothing.

## Installing it on a phone

The app installs to a home screen from a plain link: no app store, no review,
no developer fee. On Android and desktop Chrome it offers an Install button;
on iPhone it is Share → Add to Home Screen.

If you want it in the actual stores later, the honest costs:

| | Fee | The catch |
| --- | --- | --- |
| Install from a link | £0 | Works today, on every platform |
| Google Play | $25 once | Play Billing takes 15% of subscriptions |
| App Store | $99/year | Guideline 4.2 rejects thin web wrappers, and in-app purchase takes 15–30% |

The install-from-a-link route keeps 100% of the subscription and needs
nobody's permission, which is why it is the one that is built.

## Two ways to run it

**Free, on GitHub Actions.** No server, no domain, no database, no card. Fork
the repo, add your keys as repository secrets, fill in `profile.yaml`, and the
included workflow runs it three times a weekday on GitHub's hardware. Drafts
arrive as an email with a send button beside each letter. This is how the
original has run for months and it costs nothing.

```bash
python -m jobseeker.cli --profile profile.yaml --dry-run   # see the letters
python -m jobseeker.cli --profile profile.yaml             # email them to yourself
```

**As a web app**, if you want accounts and a paywall to charge other people.
This can also be free: the app on a free host, the database on Supabase, and
a `.onrender.com` address. See [DEPLOY.md](DEPLOY.md).

The pipeline is identical either way.

---

## Setting up

You need Python 3.9 or newer.

```bash
cd product
pip install -r requirements.txt

# run the app
DEV_MODE=1 BILLING_ENABLED=0 SECRET_KEY=dev uvicorn app.main:app --reload
# -> http://127.0.0.1:8000, sign-in links printed to the terminal

# or set up a profile in the terminal instead
python -m jobseeker.wizard
```

The wizard asks about ten minutes of questions, writes `profile.yaml` and a
`.env` next to it, and then checks each key by actually calling the service. A
key that does not work fails here, in ten seconds, rather than silently at 9am
on your first live run.

You can skip the wizard and copy `profile.example.yaml` to `profile.yaml` by
hand instead. Same file either way.

### The keys you need

All free tiers. Budget half an hour.

| Key | Where | Notes |
| --- | --- | --- |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | developer.adzuna.com | instant |
| `REED_API_KEY` | reed.co.uk/developers | instant |
| `GEMINI_API_KEY` | aistudio.google.com | free tier is enough |
| `GMAIL_ADDRESS` | your Gmail | |
| `GMAIL_APP_PASSWORD` | Google account → Security → App passwords | needs 2FA on first |

Those free tiers are for personal use. If you plan to run this as a service for
other people, check each provider's terms first — most of them do not allow it.

---

## Filling in your profile

Most fields are obvious. Four are not, and they are the four that decide whether
this works for you.

**`situation` and `current_salary`.** If you are employed, say so and say what
you earn. It changes everything downstream: a job matching your trade that pays
the same is then scored as worthless, which is correct, and stops the machine
spending your reputation on sideways moves.

**Your floor** (`min_salary_annual` / `min_rate_hourly`). Set at least one. With
no floor it will write to jobs paying less than you already earn. Setting it
below your current salary is refused outright.

**`priorities`, in your order.** The first one wins when two jobs are otherwise
equal. Be honest here rather than aspirational.

**`never_claim`.** The most important list in the file, and the one people skip.
Anything you do *not* currently hold: a lapsed ticket, a paused degree, an
expired clearance, a licence you are still working towards.

Asked to sell you, a language model will reach for credentials you never
mentioned, because that is what selling looks like in its training data. Say
"Royal Navy" and it will write "security cleared" unprompted. It gets found out
at vetting, and it is the one mistake here that follows you. Fill this in.

---

## Running the tests

```bash
cd product
python -m unittest discover -s tests          # 189, the profile and pipeline
python -m unittest discover -s app/tests -t . # 108, the app
```

297 tests. The pipeline ones are largely about *refusing* bad profiles rather
than accepting good ones, because a profile that loads but is subtly wrong does
not crash — it sends confident, inaccurate mail to real employers.

The app ones concentrate on what is expensive to get wrong:

- a sign-in link must not work twice
- nobody becomes a paying customer without a verified Stripe webhook
- a mail password must not be readable from a database row
- one employer must get one letter, even when two drafts arrive together
- a rejected password must stop, not retry until the account locks
- an uploaded file must not be trusted because of what it is called
- the service worker must never cache a page, because a cached dashboard on a
  shared phone is one person's job search shown to the next one

---

## Before you send anything to a real employer

- **Read the drafts.** All of them, for the first week.
- **Check the address** each one is going to before you press send.
- **Open the original advert** — the link is on every draft — and check the
  letter's first line actually refers to *that* job.

You are responsible for what goes out under your name. Read the first ten before
you trust it with the eleventh.

## Being a decent correspondent

The reason this works is that almost nobody does it, and it stops working the
moment it is abused. So:

- One email per employer, ever.
- If someone asks you to stop, stop — permanently, and record it so it cannot
  happen twice.
- Never guess an address.
- Never claim something you do not hold.

Everything here is built so those are the defaults rather than good intentions.
