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

Any host that runs a Python process and gives you a persistent disk. The
database is a single SQLite file, so **it needs a volume that survives
restarts** — without one, every deploy wipes your customers.

- **Railway / Render / Fly.io** — attach a volume, mount it at `/data`, set
  `DB_PATH=/data/jobmachine.db`. Around $5/month.
- The `Procfile` already has the right start command.

**Run one worker.** SQLite is a single file, and several worker processes
writing to it will eventually give you `database is locked` under load. One
worker will carry this app a very long way; when it genuinely will not, that
is the moment to move to Postgres, not before. Do not add `--workers`.

Set every variable from `.env.example` in the host's environment panel. The
app refuses to boot in production without `SECRET_KEY`, and refuses to boot
with billing on but Stripe keys missing — both deliberately, because a paywall
that silently defaults to open is not a thing you notice from the outside.

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

| | |
| --- | --- |
| Hosting + volume | ~$5/month |
| Stripe | 1.5% + 20p per transaction |
| Adzuna, Reed, Gemini | free tiers, but see below |
| Domain | ~£10/year |

Those free tiers are for personal use. Once paying customers depend on them
you need commercial terms from each provider — check before you launch, not
after. That is the single most likely thing to bite this.
