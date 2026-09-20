# Making this repository public, safely

The point of going public is GitHub Actions minutes. A private repository on
the free plan gets 2,000 a month; a public one is unlimited. On 20 September
this repository had used 1,942 with ten days left, which is what started this.

It cannot simply be flipped. What follows is why, and the order that works.

## What is in here that must not be published

Publishing a repository publishes its entire history, not its current state.
Deleting or encrypting a file today leaves every earlier version readable by
anyone who clones it.

A scan of the working tree:

| File | Contents |
| --- | --- |
| `data/state.json` | **579 email addresses, 141 phone numbers**, 176 letters sent, 33 replies received. 18MB, committed 123 times |
| `cv/Harry_Russell_CV.pdf` / `.docx` | Home address, phone number |
| `data/answers.json` | Personal claims about his history. This is the file that put "DV (Developed Vetting)" into a public repository for three days |
| `data/situation.json`, `goals.json` | Personal circumstances |
| `data/agencies.json`, `do_not_contact.json`, `support_orgs.json` | A handful of contact addresses each |

The 579 addresses are the serious one. They are named people at real UK
employers, collected from their own websites for the purpose of writing to
them once. Publishing that list, alongside what was written to them and what
they wrote back, is a disclosure with no lawful basis under UK GDPR, and it is
the exact thing the product tells its users it never does.

## Why "encrypt it" does not work here

Encrypting `data/state.json` going forward is sound in principle and fails on
arithmetic. It is 18MB. Plaintext JSON delta-compresses well, which is why 123
versions of it fit in a 315MB `.git`. Encrypted data does not compress and
does not delta - every commit would add a fresh 18MB blob, and the machine
commits state around twenty-five times a weekday. That is roughly 450MB a day,
into a repository that would pass GitHub's 5GB limit inside a fortnight.

## The order that works

### Phase A - move state out, while private

State moves to a second, permanently private repository, and this one stops
tracking it. Nothing is rewritten yet, so every step is reversible.

**Three things only Harry can do.** The GitHub App this runs under cannot
create repositories or manage keys.

1. Create a private repository `job-machine-state`. No README, no licence,
   empty.
2. Generate a deploy key with **write** access on that repository:
   `ssh-keygen -t ed25519 -C "job-machine state" -f state_key -N ""`, add
   `state_key.pub` to `job-machine-state` under Settings > Deploy keys with
   "Allow write access" ticked.
3. Add the private half (`state_key`) to **this** repository as the secret
   `STATE_DEPLOY_KEY`.

A deploy key rather than a personal access token on purpose: it reaches one
repository and nothing else. A PAT in a repository that is about to be public
is a much larger blast radius for the same job.

Then the workflows check `data/` out from that repository at the start of a
run and push it back at the end, and `commit-state.sh` keeps its merge
behaviour unchanged - it is the thing that stops two concurrent runs from
clobbering each other's state, and it is worth preserving exactly.

One exception stays in this repository: `data/track_record.json`. It is four
integers, it is already published at `/numbers`, and the live site reads it at
runtime.

### Phase B - only after Phase A has survived a real run

Do not run this the same day. If the state fetch is broken, the machine loses
its memory of who has been written to and re-applies to 135 employers, and the
first sign of that is somebody replying "you already contacted us".

Wait for at least one full scheduled run to complete, read the committed
state, and confirm the job count has not gone backwards.

```bash
pip install git-filter-repo
git clone --mirror https://github.com/harryrussell081203-dev/job-machine backup.git

git filter-repo \
  --path data/state.json --path cv/ --path data/answers.json \
  --path data/situation.json --path data/goals.json \
  --path data/agencies.json --path data/do_not_contact.json \
  --path data/support_orgs.json --path data/learned.json \
  --invert-paths
```

Then force-push. **Every commit SHA changes**, so PR #81 and any open branch
have to be recreated from the rewritten history.

### Phase C - flip it

Settings > General > Change visibility. Harry's own action.

Before flipping, check GitHub's secret scanning results on the repository, and
rotate anything it finds. Secrets have only ever been repository secrets
rather than committed files, but the cost of being wrong once is unbounded and
the check takes a minute.

## What this does not block

The growth estate does not wait for any of this. It lives in its own
repository, public from birth, containing only material that was always meant
to be public - generated pages, OGL register data, the aggregate figures
already on the site. Unlimited Actions minutes there from day one, and no
personal data has ever been in it to purge.
