# The acquisition engine

Agents that run on a cron and leave something behind. Not a marketing plan —
code that produces indexed pages, permanent links and citable data without
anybody driving it.

## The contract

Every agent is one module with one entrypoint:

```python
run(state: dict, **deps) -> tuple[dict, Result]
```

It takes the committed state, does its work, returns the new state and a
`Result` saying what happened. `growth/run.py` loads the state, calls the
agent, prints the result and writes the state back.

Three properties follow, and each exists because of a specific failure:

**Independently runnable.** `python -m growth.run <name>`, with `--dry-run`
meaning read everything and write nothing. An agent that can only be exercised
by the whole pipeline is one nobody debugs.

**Idempotent.** Running twice does what running once did. GitHub's scheduler
is lossy and drops crons, so "it ran twice" is a normal Tuesday rather than an
incident.

**Fail closed.** An agent that cannot do its job returns the state it was
given and says why. `run.py` does not write the state of a failed run — that
decision is made once, in one place, rather than trusted to every agent. The
failure this prevents is a broken harvester quietly emitting zero pages, which
on a dashboard looks exactly like a quiet week.

**Dependencies are injected.** Anything touching the network or the clock
arrives as a keyword argument with a real default. Not ceremony: it is the
only reason the tests can be honest about agents whose entire job is fetching
things. The clock counts — how long a page took is one of the things
`crawl_health` exists to catch, and a measurement read from the wall clock
cannot be tested.

## State

`growth/data/*.json`, committed, versioned by git. Slower and clumsier than a
database and worth it: the history of what the engine decided is readable in
`git log`, it cannot be silently mutated, and restoring it is a checkout.
Written sorted and indented because these are read as diffs by a person — an
unordered dump produces a diff where every line moved and none of them
changed.

## Identifying ourselves

Everything this engine sends carries:

```
RecruitedGrowthBot/1.0 (+https://recruited.org.uk/about-the-crawler)
```

Honest identification is the difference between a crawler and a nuisance, and
it is what keeps us welcome on the sites we read. A test fails if any request
goes out without it.

---

## The agents

### `crawl_health` — what a machine actually gets

| | |
| --- | --- |
| **Schedule** | Daily |
| **Reads** | `sitemap.xml` on the live site, then every URL in it |
| **Writes** | `growth/data/crawl_health.json` |
| **Publishes** | Nothing |

The acceptance criteria written as code rather than as a paragraph in a brief.
Everything else in this engine assumes the pages work: that a crawler asking
for `/answers/why-no-reply` gets the whole answer in one `GET`, that the schema
parses, that the address it was reached at is the address the page claims.
None of that is visible to a person reading the site and all of it breaks
silently.

It checks five things per URL:

- **Reachable.** A URL in the sitemap that 404s tells every crawler the
  sitemap is unreliable, and they discount the whole file.
- **Full text without JavaScript.** Fetched with a plain client. A page that
  assembles itself in a browser is a page an assistant cannot quote.
- **Schema parses.** Markup that does not parse is markup nothing acts on, and
  a substring check would never notice.
- **Canonical agrees.** A page whose canonical points elsewhere is a page
  asking not to be indexed. Compared without the query string, because
  dropping that is the whole job of the tag.
- **Cold start.** The site is on Render's free tier: it sleeps after fifteen
  minutes and takes 30–60 seconds to wake. A crawler that times out records
  that against the domain, so a page can succeed and still be a problem.
  Anything over 5 seconds is reported.

**Failure mode.** If the sitemap itself cannot be fetched or does not parse,
it returns `ok=False` and keeps the previous state. It does not report "0
problems" — a run that checked nothing and a run that found nothing wrong
produce the same reassuring number, and only one of them is true.

It keeps the last 30 runs, so a page broken for a month is distinguishable
from one broken this morning.

---

## Not built yet

Written down so the gap is visible rather than implied.

| | Status |
| --- | --- |
| `harvest_registers` | Filter and parser exist as `product/tools/find_orgs.py`. No harvest, no cron, no provenance or licence fields. The register URLs are unverified configuration — this sandbox has no web egress |
| `harvest_jobs` | Not started. **Adzuna's terms forbid republishing vacancy counts or average salaries in aggregate**, which removes two of the planned page families. ONS/NOMIS replaces both under OGL and is better |
| `page_builder` | Not started |
| `tool_builder` | Not started |
| `linker` | Not started |
| `syndicator` | Not started |
| `publisher` | Partly: IndexNow, sitemap `lastmod` and `llms.txt` live in the app (`product/app/`) because they are served by it. No sitemap index, no feeds, no `llms-full.txt` |
| `listener` | Not started |
| `analyst` | Not started. Needs Search Console and Bing Webmaster, which need accounts |

### Two honest divergences from the brief

**Phase 0 was built in the app, not here.** Canonical tags, `robots.txt`,
schema, IndexNow and per-crawler logging are repairs to a live server-rendered
site, and they belong in the thing that serves those pages. The consequence is
that they are not agents and do not run on a cron — `crawl_health` is what
checks they still work.

**Per-crawler logging writes to Supabase, not to a committed file.** The brief
says state lives in the repo. This one does not, because the data arrives on
every request to a running web app rather than on a schedule. `analyst` will
have to read it from there.
