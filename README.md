# Recruited

Everything that runs lives in [`product/`](product/README.md): the website at
recruited.org.uk and the sweep that finds jobs, writes to the people hiring,
follows up once and tells each user what happened.

Harry's own job hunt runs **as a Recruited account** (user 2). A separate
personal machine used to live in this directory. It was removed on
23 September 2026, after old trigger branches re-ran it with a stale memory
and wrote to agencies and charities that had already heard from him. Its
record of everyone ever written to was imported into that account first, by
company name and by domain. Its code is still in git history if it's ever
needed.

| Path | What it is |
| --- | --- |
| `product/` | The app, the sweep and their tests |
| `growth/` | Scheduled checks on the public site (`growth.yml`) |
| `.github/workflows/sweep.yml` | The only thing here that sends email. Runs on a schedule or by hand, **never on a push** |
| `GROWTH.md`, `GROWTH_LOG.md`, `posts.md` | How Recruited finds users, and what was tried |
| `data/track_record.json` | The personal machine's final published totals (171 applications, 32 replies). The landing page quotes them. Frozen, and only aggregate numbers |
| `GOING-PUBLIC.md` | What has to happen before this repository can be made public |
