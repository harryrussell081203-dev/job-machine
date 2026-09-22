# Growth log

What shipped, and what to check. Newest first.

Every number here comes from the live database at the moment it was written.
Nothing is estimated and nothing is rounded up — where a figure is small it is
printed small, because the whole argument for this product is that its
published numbers are checkable.

---

## 2026-09-21 — Phase 0: fix and measure

### The baseline, from Supabase

| | |
| --- | --- |
| Signed up | **10** |
| Uploaded a CV | **3** |
| Connected a mailbox and sent anything | **1** |
| Letters sent | 23 |
| Replies | 9 |
| Came back after 7 days | 1 of the 4 accounts old enough to have |

The one account that has ever sent a letter is Harry's. **No other person has
ever sent anything through Recruited.** Seven of the ten signed up in the last
ten days, so arrivals are working; the step that is not working is connecting
a mailbox.

That is the number this phase existed to surface, and it changes what is worth
building next. Pouring a thousand people into a funnel where nine in ten stop
at one screen produces nine hundred people who conclude the product does not
work.

### What shipped

**`app/attribution.py` — where somebody came from, on first touch.**
Signing in is a link in an email, so the request that creates the account
carries no referrer and no campaign. Read it there and every user is "direct",
including the ones a channel worked for. So the source is captured on the page
they land on, kept in a first-party cookie for 30 days, and stamped onto the
account when it is created. First touch, not last: somebody who arrives from a
video, reads for a week and comes back through a search was got by the video.

**`events` table — the funnel, firsts only.**
`signed_up`, `onboarded`, `first_email_sent`, `first_reply`,
`first_interview`, `referred_user`. Unique on `(user_id, kind, ref)`, so
recording the same step twice is a no-op. Most of these could be derived from
the other tables today; what cannot be derived is *when*, once a row is
deleted or replaced, and every retention question is a question about when.

**`app/funnel.py` — one place that knows somebody moved forward.**
Records the event and pays the referrer in the same call, because splitting
them across two call sites is how they drift apart. Nothing in it can raise:
every caller is mid-upload or mid-send, and a gap in a report is worth less
than a letter going out.

**`app/referrals.py` — the reward.** See below.

**`tools/metrics.py`** — the funnel, users by source, letters and replies per
sender, 7-day retention, referrals. `--json` for the weekly email.

### One bug worth recording

`record_event` originally decided "was this the first" by writing the row and
reading the timestamp back. Two calls in the same second store the same
timestamp, so it answered **true twice** — and the cost of that is a referral
month paid twice on any retry. It uses `ON CONFLICT DO NOTHING RETURNING id`
now, so the database answers the question rather than the code inferring it.
The test that caught it does exactly what a retry does.

### To check

- The new columns are added by `_ADDED_COLUMNS` on the next deploy. Confirm
  `users.utm_source` and the `events` table exist in Supabase afterwards.
- Attribution needs the privacy notice updated: this is the first cookie the
  site sets that is not the session. The page-view counter still sets none.
- `product sweep` last ran on 18 September. It is **not failing** — the last
  five runs succeeded; its schedule was cut during the Actions minutes
  emergency and the repo being public has since made minutes unlimited. It
  wants turning back on.

---

## The referral reward, and why it is not "+5 emails a day"

The brief asked for a higher daily sending limit as the referral reward. That
is wrong twice over.

**It is not what anybody wants.** Nobody's problem is that they cannot send
enough. This product exists because four hundred Easy Apply applications got
auto-rejected, and its whole argument is that twenty careful letters beat four
hundred careless ones. "Send more" contradicts the landing page.

**And it is harmful.** Users send from their own Gmail. Raising somebody's
volume as a prize pushes their personal mailbox toward its limits and toward a
spam reputation they keep long after they stop using this.

**So the reward is the thing we sell: a free month.** Marginal cost nothing,
real money to somebody out of work — which is exactly who this is for — and it
needs no new concept in the code, because `is_paid()` already honours
`paid_until`.

Two tiers, because a reward gated on a step nobody reaches is not a reward:

| The person they referred | The referrer gets |
| --- | --- |
| Signs up and uploads a CV | 1 month |
| Connects a mailbox and sends | 1 more month |

A signup alone pays nothing — it is the only step somebody can manufacture in
bulk from one keyboard. Uploading a CV and connecting a working mailbox are
both real work, and the second needs a mailbox nobody else holds. Capped at 12
months; past the cap the event is still recorded and the month is not paid,
because the event is the history and the cap is a decision about it.

---

## Phase 3, and the Adzuna problem

Adzuna's terms forbid republishing **vacancy counts and average salaries in
aggregate**, which is most of what the brief's `/jobs/[role]-jobs-[city]` pages
were going to be. They do not forbid linking to individual listings with
attribution. So the pages change shape rather than dying:

| Page element | Source |
| --- | --- |
| Pay figures | **ONS ASHE** — median pay by occupation × region, Open Government Licence |
| How many jobs | **ONS vacancy series** by industry, same licence |
| Who is hiring | **Armed Forces Covenant register** (`tools/covenant_list.py`, already built) + Companies House |
| Live roles | Adzuna, **linked with attribution, never counted** |
| How to reach them | **Our own data** — the only part nobody else can copy |

This is a better page than the one that was asked for. ONS-sourced pay is
citable by an assistant answering a question; an Adzuna average is not. And
"employers in Aberdeen who signed the Armed Forces Covenant" is a real query
with near-zero competition that the scraper for already exists.

Not built yet. Phases 1 and 3 are next, and Phase 1 first — activation is
where the ten users are stuck.
