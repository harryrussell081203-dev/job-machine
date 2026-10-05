"""Build the role-landing pages a jobseeker typing "<role> jobs UK" would find.

WHY THESE PAGES ARE WORTH BUILDING.

The directory at /employers lists every company this site has read and found
a shared inbox on. It is sorted A to Z - useful if you already know the
employer, useless if you are searching by what you do. Somebody typing
"software engineer jobs UK" is not looking for Acme Ltd's careers address;
they are looking for the companies hiring software engineers. That is a
different page, grouped by role rather than by company, and this builder
produces one of them per role in the seed list.

Each page lists the real UK employers hiring for that role, with the real
job inbox each one publishes, and a link back to the employer's directory
page where the address was found. Nothing on the page is invented: every
figure, every email, every URL came from the directory, which was itself
read from the employer's own website. If the directory does not have an
employer for a role, the page is not built.

WHY ONLY ROLES WITH ENOUGH EMPLOYERS GET PUBLISHED.

A page with two employers is a thin page, and a thin page is a page an
assistant will not quote and a search engine will not rank. The threshold
is `MIN_EMPLOYERS` and it is deliberate: below it the role is skipped on
this run and may qualify on the next, when the directory has grown. A role
that was above the threshold and falls below it has its page removed, so
the sitemap never points at an empty page.

WHY THE AGENT WRITES HTML FILES RATHER THAN TOUCHING THE APP.

The product app serves `/employers/{slug}` from a template that reads the
Supabase directory. Role pages will one day do the same, but shipping the
builder before shipping the route is the correct order: it lets us look at
real output, decide the shape, and only then integrate. For now the pages
land in `growth/out/pages/roles/` as self-contained HTML, and a follow-up
PR adds `/roles/{slug}` and the sitemap entry. Treating "produce the
artifact" and "serve it live" as two steps is the same discipline that
makes `crawl_health` publish nothing.

FAIL CLOSED MEANS THE SAME THING HERE AS EVERYWHERE.

If the directory cannot be fetched - the free instance is cold, the
endpoint 404s, the JSON does not parse - this returns the previous state
untouched and `ok=False`. It never removes a page because of a bad fetch,
because a run that saw nothing and a run that found an empty directory
produce the same empty result for different reasons.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time

from .. import OUT, USER_AGENT, Result

# One page per role in this list that has >= MIN_EMPLOYERS matching
# employers in the directory. Edit here to add or drop a role.
DEFAULT_ROLES = [
    "software engineer", "software developer", "backend developer",
    "frontend developer", "full stack developer", "data analyst",
    "data engineer", "data scientist", "machine learning engineer",
    "project manager", "product manager", "business analyst",
    "accountant", "paralegal", "marketing manager",
    "customer service", "operations manager", "warehouse operative",
    "nurse", "teaching assistant", "receptionist", "graduate scheme",
]

# Below this a role is not published. Three employers is where a role
# page starts to read as a resource rather than a stub.
MIN_EMPLOYERS = 3

PAGES_SUBDIR = os.path.join("pages", "roles")


def run(state: dict, *, base: str = "https://recruited.org.uk",
        get=None, now=None, roles=None, min_employers=None,
        out_dir=None) -> "tuple[dict, Result]":
    """Fetch the directory, group by role, write one page per qualifying role."""
    result = Result()
    if get is None:                       # pragma: no cover - real network
        import requests
        get = requests.get
    now = now or (lambda: int(time.time()))
    roles = list(roles) if roles is not None else DEFAULT_ROLES
    threshold = MIN_EMPLOYERS if min_employers is None else min_employers
    out_dir = out_dir if out_dir is not None else OUT

    stamp = now()

    url = base.rstrip("/") + "/employers.json"
    try:
        r = get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    except Exception as e:
        return state, result.failed(
            f"could not fetch {url}: {type(e).__name__}: {e}")
    code = getattr(r, "status_code", 0)
    if code != 200:
        return state, result.failed(f"{url} returned {code}")
    try:
        data = r.json() if callable(getattr(r, "json", None)) \
            else json.loads(r.text)
    except Exception as e:
        return state, result.failed(f"{url} did not parse: {e}")

    employers = data.get("employers") or []
    if not employers:
        # No failure. The directory may legitimately be empty on a brand-
        # new deploy, and that is not a reason to wipe what we built last
        # time. Keep state, say so.
        return state, result.say("directory is empty", pages=0)

    state = dict(state)
    state.setdefault("pages", {})
    old_pages = dict(state["pages"])
    new_pages = {}
    writes = 0
    unchanged = 0

    pages_dir = os.path.join(out_dir, PAGES_SUBDIR)

    for role in roles:
        matches = _match(role, employers)
        if len(matches) < threshold:
            continue
        slug = _slug(role)
        if not slug:
            continue
        body = _render(role, matches, base=base)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        prior = old_pages.get(slug)
        if prior and prior.get("hash") == digest:
            new_pages[slug] = prior
            unchanged += 1
            continue
        os.makedirs(pages_dir, exist_ok=True)
        _write(os.path.join(pages_dir, f"{slug}.html"), body)
        new_pages[slug] = {
            "role": role, "slug": slug, "hash": digest,
            "employer_count": len(matches), "built_at": stamp,
            "url": f"{base.rstrip('/')}/roles/{slug}",
        }
        writes += 1

    # A role that was above the threshold last run and is below it now has
    # its page removed. The sitemap must not keep pointing at it.
    removed = []
    for slug in old_pages:
        if slug in new_pages:
            continue
        path = os.path.join(pages_dir, f"{slug}.html")
        if os.path.exists(path):
            os.remove(path)
        removed.append(slug)

    state["pages"] = new_pages
    state["base"] = base
    state["built_at"] = stamp

    # A publisher-friendly manifest at a known path, so the follow-up route
    # and the sitemap have one place to read from.
    if new_pages or removed:
        os.makedirs(pages_dir, exist_ok=True)
        manifest = {
            "built_at": stamp,
            "base": base,
            "pages": [new_pages[s] for s in sorted(new_pages)],
        }
        _write(os.path.join(pages_dir, "index.json"),
               json.dumps(manifest, indent=1, sort_keys=True) + "\n")

    for slug in removed:
        result.for_review.append(
            f"removed /roles/{slug}: fell below {threshold} employers")

    return state, result.say(
        f"{len(new_pages)} page(s), {writes} written, "
        f"{unchanged} unchanged, {len(removed)} removed",
        pages=len(new_pages), written=writes, unchanged=unchanged,
        removed=len(removed))


def _slug(text: str) -> str:
    """Lowercase hyphenated slug, with a cap short enough for a URL."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80]


def _match(role: str, employers: list) -> list:
    """Every employer whose directory roles mention this role (substring,
    case-insensitive). Sorted by name so the page is diff-stable.

    Role entries may be bare strings or dicts with a `title` field - the
    directory stores dicts, but tests and other callers may hand us
    strings. Anything without a title is skipped silently rather than
    guessed at.
    """
    needle = (role or "").lower()
    if not needle:
        return []
    out = []
    for e in employers:
        for r in e.get("roles") or []:
            title = r if isinstance(r, str) else (r or {}).get("title") or ""
            if needle in title.lower():
                out.append(e)
                break
    out.sort(key=lambda e: (e.get("name") or "").lower())
    return out


def _write(path: str, body: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, path)


def _render(role: str, matches: list, *, base: str) -> str:
    """One self-contained HTML file.

    The opening paragraph names the figure - "N UK employers" - because
    assistants quote first paragraphs and a number is what makes a line
    citable. Everything after it is drawn from the directory, so no claim
    on this page is unsourced.
    """
    slug = _slug(role)
    role_h = role[:1].upper() + role[1:]
    n = len(matches)
    site = base.rstrip("/")
    canonical = f"{site}/roles/{slug}"

    title = f"{role_h} jobs: UK employers you can email directly"
    desc = (f"{n} UK employers publish a job application email address for "
            f"{role_h.lower()} roles on their own website. Each address, "
            "and the page it is printed on.")

    item_list = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": title,
        "numberOfItems": n,
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1,
             "item": {"@type": "Organization",
                      "name": e.get("name") or e.get("slug", ""),
                      "url": f"{site}/employers/{e['slug']}"}}
            for i, e in enumerate(matches)
        ],
    }

    rows = []
    for e in matches:
        name = html.escape(e.get("name") or e.get("slug", ""))
        email = (e.get("hiring_email") or e.get("general_email") or "")
        detail = f"{site}/employers/{e['slug']}"
        line = f'<li><a href="{html.escape(detail)}">{name}</a>'
        if email:
            line += f' &mdash; <code>{html.escape(email)}</code>'
        line += "</li>"
        rows.append(line)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{html.escape(canonical)}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<script type="application/ld+json">{json.dumps(item_list, ensure_ascii=False)}</script>
</head>
<body>
<header><a href="{html.escape(site)}">Recruited</a> &rsaquo; <a href="{html.escape(site)}/roles">Roles</a></header>
<h1>{html.escape(title)}</h1>
<p>{html.escape(desc)}</p>
<p>These are the companies this site has read directly. Every address was
printed on the company's own website, and the <a href="{html.escape(site)}/employers">employers directory</a> shows which page.</p>
<ul>
{chr(10).join(rows)}
</ul>
<p>The <a href="{html.escape(site)}/playbook">playbook</a> is the method behind
this: the specific letters, when to follow up, and the numbers behind each
rule.</p>
</body>
</html>
"""
