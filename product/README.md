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

## Status — read this before you start

Being straight about where this is, because half-finished is worse than
honest.

**Working now:**

- **The app** (`app/`) — sign-in without passwords, a Stripe paywall, a
  profile form, and a drafts screen you work through one click at a time.
  Run it locally in one command; see [DEPLOY.md](DEPLOY.md) to put it online.
- `jobseeker/profile.py` — your whole search in one file, with validation
  that refuses a profile which would send wrong letters
- `jobseeker/wizard.py` — a terminal setup that writes that file and
  **calls every API to prove your keys work**
- `PLAYBOOK.md` — complete, and free to everyone at `/playbook`

**Not done yet:**

- **The pipeline that fills the drafts screen.** Harvesting listings, scoring
  them and discovering addresses is the original engine's 3,300 lines, and it
  still has its author's details compiled in. Until it is ported, the app runs
  end to end but the drafts table is filled by hand.

So: the product's shell is real and the money path works. The engine is the
next piece.

## How sending works, and why

The app writes the letter. **You press send**, from your own mail client, in
one click.

That is a deliberate choice, not a missing feature. Sending as you would need
either Gmail OAuth — `gmail.send` is a Google restricted scope needing an
annual five-figure security assessment — or your mail password kept on our
server. The first is closed to a small operator and the second is one breach
away from disaster.

Hand-off keeps the letter coming from your address, with your signature and
your reputation, which is also the reason these get answered at all. Automatic
sending is implemented (`app/delivery.py`) for anyone who decides the
trade-off differently.

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
python -m unittest discover -s tests        # 22, the profile layer
python -m unittest app.tests.test_app       # 25, the app
```

47 tests. The profile ones are almost all about *refusing* bad profiles rather
than accepting good ones, because a profile that loads but is subtly wrong does
not crash — it sends confident, inaccurate mail to real employers.

The app ones concentrate on the two things that are expensive to get wrong: a
sign-in link must not work twice, and nobody becomes a paying customer without
a cryptographically verified Stripe webhook.

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
