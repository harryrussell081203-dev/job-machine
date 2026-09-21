"""Run one agent, or list them.

    python -m growth.run --list
    python -m growth.run crawl_health --dry-run
    python -m growth.run crawl_health

Every agent is `run(state, **deps) -> (state, Result)`. This loads its state,
calls it, prints what it says, and writes the state back unless the run was a
dry run or the agent failed closed.

THE ONE RULE WORTH STATING HERE.

A failed agent's state is NOT written. An agent that could not do its job
returns the state it was handed, and committing that would be harmless - but
committing a state file that a half-working agent partially updated is how a
pipeline starts lying about what it has done. So the choice is made once, in
one place, rather than trusted to every agent.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from . import load, save

# Name -> (module, what it needs from the command line). Explicit rather than
# discovered by scanning the directory: an agent that appears in the list
# because a file was dropped in is an agent nobody decided to run.
AGENTS = {
    "crawl_health": ("growth.agents.crawl_health",
                     "Fetch every URL in the sitemap and report what a "
                     "machine actually got back."),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("agent", nargs="?", choices=sorted(AGENTS))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="read everything, write nothing")
    ap.add_argument("--base", default="https://recruited.org.uk",
                    help="the site to act on")
    args = ap.parse_args(argv)

    if args.list or not args.agent:
        for name, (_, what) in sorted(AGENTS.items()):
            print(f"{name}\n    {what}\n")
        return 0

    module_name, _ = AGENTS[args.agent]
    agent = importlib.import_module(module_name)

    state = load(args.agent)
    state, result = agent.run(state, base=args.base)

    print(f"[{args.agent}] {result.note}")
    for line in result.for_review:
        print(f"  ! {line}")

    if not result.ok:
        print(f"[{args.agent}] failed closed - state not written")
        return 1
    if args.dry_run:
        print(f"[{args.agent}] --dry-run: state not written")
        return 0

    print(f"[{args.agent}] wrote {save(args.agent, state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
