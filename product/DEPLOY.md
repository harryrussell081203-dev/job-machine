# Putting it online

About an hour, most of it waiting for Stripe and DNS.

## 1. Try it locally first

```bash
cd product
pip install -r requirements.txt
DEV_MODE=1 BILLING_ENABLED=0 SECRET_KEY=dev uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. Sign-in links print to the terminal instead of
being emailed, and the paywall is open, so you can click through everything
before spending a penny.

## 2. Stripe

### The quick route: a Payment Link

If you already have a Payment Link (`https://buy.stripe.com/...`), set
`STRIPE_PAYMENT_LINK` to it and skip the secret key entirely. The app never
calls the Stripe API in this mode - it just sends people to a page Stripe
already hosts, with `?client_reference_id=<user id>` appended so the webhook
knows whose payment it was.

You still need the webhook (step 3 below). Access is granted only by a
verified webhook, and that does not change because the page came from a link.

**Check whether your link is recurring or one-off.** A subscription reports
its own period end and renews. A one-off payment does not, so the app grants
`ONE_OFF_ACCESS_DAYS` (30 by default) and then closes again - deliberately not
forever, because lifetime access from a single charge is an expensive way to
find out the link was set up wrong.

### The API route

Skip this if you are using a Payment Link.

1. Create a **product** with a **recurring monthly price**. Copy the price id
   (`price_...`) into `STRIPE_PRICE_ID`.
2. Copy your secret key (`sk_live_...`) into `STRIPE_SECRET_KEY`.
3. Add a **webhook endpoint** pointing at `https://your-domain/webhooks/stripe`,
   subscribed to:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
4. Copy the webhook's signing secret (`whsec_...`) into
   `STRIPE_WEBHOOK_SECRET`.

The webhook is not optional. Access is granted **only** when Stripe confirms
payment on a signed webhook — never when a browser lands on the success page,
because anyone can type that URL. Skip step 3 and nobody who pays will ever
get in.

Test it with Stripe's CLI before going live:

```bash
stripe listen --forward-to localhost:8000/webhooks/stripe
stripe trigger checkout.session.completed
```

## 3. Somewhere to run it

There are two routes. **The free one is the one to start with** — it costs
nothing, has no card attached, and is a real deployment, not a demo.

### The free route: Render + Supabase

The catch with every free host is that it has **no disk that survives a
deploy**. Put SQLite on one and your customer table is deleted the next time
you push. So the app process goes on a free host and the data goes somewhere
else that outlives it.

**Supabase** for the database (free, 500MB, no card):

1. supabase.com → new project. Pick the region nearest your users
   (`eu-west-1` for the UK). Save the database password it shows you — it is
   shown once.
2. Project Settings → Database → Connection string → **URI**. It looks like
   `postgresql://postgres:PASSWORD@db.xxxx.supabase.co:5432/postgres`.
3. That whole string becomes `DATABASE_URL` in the app's environment.

That is the entire integration. Set `DATABASE_URL` and the app uses Postgres;
leave it unset and it uses the local SQLite file. Same code, same tests, same
SQL — see `app/store.py`.

**Render** for the app (free, no card):

1. render.com → New → Web Service → connect this repository.
2. Root directory `product`, build `pip install -r requirements.txt`,
   start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
3. Add every variable from `.env.example`, plus `DATABASE_URL`.
4. You get `your-app.onrender.com` free, with HTTPS. Use that as `BASE_URL`.

Two things about the free tier, both real:

- **It sleeps after 15 minutes idle**, and the next request takes about 50
  seconds to wake it. Fine for early customers, embarrassing at scale. Note
  that Stripe retries failed webhooks for 3 days, so a sleeping app does not
  lose you a payment.
- **A free Supabase project pauses itself after about a week with no
  queries.** A paused database means nobody can sign in, and it fails
  quietly. The `Keep the database awake` step in
  `.github/workflows/run.yml` is there for exactly this: set `DATABASE_URL`
  as a repository secret and the scheduled run touches the database three
  times a weekday. Without a `DATABASE_URL` secret the step prints a line and
  skips.

### The paid route: one host with a volume

Around $5/month, and worth it once people are paying you.

- **Railway / Render / Fly.io** — attach a volume, mount it at `/data`, set
  `DB_PATH=/data/jobmachine.db` and leave `DATABASE_URL` unset. The
  `Procfile` already has the right start command.
- **Run one worker** on this route. SQLite is a single file, and several
  worker processes writing to it will eventually give you
  `database is locked` under load. Do not add `--workers`.

On Postgres that caveat goes away — several workers are fine, because the
database is doing the locking rather than a file.

### Either way

Set every variable from `.env.example` in the host's environment panel. The
app refuses to boot in production without `SECRET_KEY`, and refuses to boot
with billing on but Stripe settings missing — both deliberately, because a
paywall that silently defaults to open is not a thing you notice from the
outside.

## 3b. Two keys the new features need

**`CREDENTIAL_KEY`** — required for automatic sending. It encrypts each user's
mail password, and it lives in the environment, never in the database, so a
leaked backup yields ciphertext.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set it in Render **and** in GitHub Secrets — the scheduled sweep sends mail
too, so it needs to read the same credentials.

Two things about it worth knowing before you lose it:

- **Changing it makes every stored mail password unreadable.** Users are told
  to reconnect rather than being left wondering why nothing sends, but they do
  all have to reconnect. Keep it somewhere safe.
- **Without it the app still runs.** Letters are still written, and users send
  them by hand in one click. The setup screen says automatic sending is
  unavailable rather than quietly storing passwords in the clear.

**The scheduled sweep.** `.github/workflows/sweep.yml` runs the machine for
every subscriber three times a weekday on GitHub's free hardware. It needs the
same secrets as the app: `DATABASE_URL`, `CREDENTIAL_KEY`, `SECRET_KEY`,
`BASE_URL`, the Stripe pair, and the three job-search API keys. It refuses to
run without `DATABASE_URL`, because a sweep against an empty throwaway
database would find no users and report success.

Check it before trusting it:

```bash
python -m app.sweep --dry-run    # who would be written to, and from where
```

## 3c. When the first deploy fails

It usually does, and it is nearly always one of five things. Read the last
twenty lines of the Render log and match:

| The log says | What is wrong | Fix |
| --- | --- | --- |
| `Could not open requirements file` | Root Directory is not set | Settings &rarr; Root Directory = `product` |
| `ModuleNotFoundError: No module named 'app'` | same | as above |
| `gunicorn: not found`, or a `wsgi` error | Start Command is still the Django placeholder Render suggests | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| `RuntimeError: SECRET_KEY is not set` | missing env var | add it; Render can generate one |
| `RuntimeError: billing is on but STRIPE_WEBHOOK_SECRET not set` | missing env var | add the `whsec_...` from Stripe |

The last two are the app refusing to boot rather than starting misconfigured.
That is deliberate: an app that quietly comes up with an open paywall or a
guessable signing key is not something you notice from the outside.

**Python version.** Nothing here pins one, so Render uses its own default.
The code is developed and tested on 3.11. If the build fails while installing
dependencies rather than while running the app, set `PYTHON_VERSION` in the
environment panel to a 3.11 release and redeploy.

**Region.** Put the web service near the database. Frankfurt against a
Supabase project in `eu-west-1` (Ireland) works, but every request opens its
own connection, so the round trip is paid each time. Same region is worth
choosing when you create the service, and not worth rebuilding for later.

## 4. Mail for sign-in links

`APP_SMTP_*` is the app talking to its own customers, and is separate from
anything a user sends about a job. A Gmail app password works to start. Move
to a transactional provider when link delivery starts landing in spam.

## 5. Rate limits are already on

`/login` is capped at 5 emails an hour to any one address and 20 an hour from
any one machine, because otherwise the app is a free email cannon anyone can
point at any inbox — and the first casualty is your own sending account
getting suspended, which locks out every real customer at once.

Over-limit requests look identical to successful ones on purpose. Saying
"rate limited" would confirm an address exists and tell an abuser exactly what
they hit.

If you sit behind a proxy that is *not* setting `X-Forwarded-For`, the per-IP
limit collapses to one bucket for everybody. Check your host does set it.

## 6. Before you charge anyone

- Sign up as a real customer with a real card. Cancel it. Check both worked.
- Confirm a cancelled subscription actually closes access.
- Confirm the sign-in link email arrives and is not in spam.
- **Connect your own mail account and send one real letter to yourself.**
  Check it arrives, the CV is attached, and the From line has your name on it.
- **Turn on automatic sending and watch one full cycle** with the holding
  window set long, so you can see the letter before it goes.
- Read ten generated letters end to end.

## What you still owe your users

Not optional if you are taking money in the UK:

- **A privacy policy.** You are processing personal data — theirs, and the
  contact details of people at employers.
- **Terms**, saying plainly what the service does and does not promise. It
  does not promise anybody a job.
- **A way to delete an account and its data**, and to have it honoured.
- **Working cancellation.** The account page opens Stripe's own portal, which
  covers it, but check it works on your account.

## Costs, honestly

| | Free route | Paid route |
| --- | --- | --- |
| App hosting | £0 (Render free, sleeps) | ~$5/month |
| Database | £0 (Supabase free, 500MB) | included in the volume |
| Domain | £0 (`your-app.onrender.com`) | ~£10/year |
| Stripe | 1.5% + 20p per transaction | same |
| Adzuna, Reed, Gemini | free tiers, but see below | same |

So the free column really is £0 until somebody pays you. Already own a
domain? A subdomain of it costs nothing: point a CNAME at the Render app and
set `BASE_URL` to match.

Those free tiers are for personal use. Once paying customers depend on them
you need commercial terms from each provider — check before you launch, not
after. That is the single most likely thing to bite this.
