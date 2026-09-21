"""The acquisition engine: agents that run on a cron and leave something behind.

WHAT AN AGENT IS HERE.

A module with one entrypoint:

    run(state: dict, **deps) -> dict

It takes the committed state, does its work, returns the new state. The caller
writes it back and commits. That shape is the whole contract, and three
properties follow from it:

  INDEPENDENTLY RUNNABLE. Every agent is `python -m growth.run <name>`, with
  --dry-run meaning read everything, write nothing. An agent that can only be
  exercised by the whole pipeline is one nobody debugs.

  IDEMPOTENT. Running twice does what running once did. Every agent that
  reaches outside - submitting a URL, writing a page - records what it did and
  checks before repeating. The scheduler is lossy and GitHub drops crons, so
  "it ran twice" is a normal Tuesday rather than an incident.

  FAIL CLOSED. An agent that cannot do its job returns the state it was given
  and says why. It never publishes a partial result. The failure mode this
  exists to prevent is a broken harvester quietly emitting zero pages, which
  looks exactly like a quiet week.

DEPENDENCIES ARE INJECTED, ALWAYS.

Anything that touches the network or the clock arrives as a keyword argument
with a real default. That is not ceremony - it is the only reason the tests
can be honest about agents whose entire job is fetching things.

STATE IS A COMMITTED FILE, NOT A DATABASE.

growth/data/*.json, in the repository, versioned by git. Slower and clumsier
than a database and worth it: the history of what the engine decided is
readable in `git log`, it cannot be silently mutated, and restoring it is a
checkout. Nothing here is hot enough to need better.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out")

# Everything this engine sends identifies itself and says where to complain.
# Honest identification is the difference between a crawler and a nuisance,
# and it is the thing that keeps us welcome on the sites we read.
USER_AGENT = ("RecruitedGrowthBot/1.0 "
              "(+https://recruited.org.uk/about-the-crawler)")


@dataclass
class Result:
    """What an agent hands back besides the state.

    `ok` false means the agent could not do its job. The caller does not
    commit a run that failed closed, because a state file written by a broken
    agent is worse than yesterday's.
    """

    ok: bool = True
    note: str = ""
    # Counts worth putting in the weekly report. Free-form on purpose: what is
    # worth counting differs per agent and a fixed schema would be guessed.
    counts: dict = field(default_factory=dict)
    # Things a human has to decide. Never acted on automatically.
    for_review: list = field(default_factory=list)

    def say(self, note: str, **counts) -> "Result":
        self.note = note
        self.counts.update(counts)
        return self

    def failed(self, why: str) -> "Result":
        self.ok = False
        self.note = why
        return self


def path_for(name: str) -> str:
    return os.path.join(DATA, f"{name}.json")


def load(name: str, default=None):
    """Read a state file. Missing is the default, unreadable is an error.

    The distinction matters. A file that has never been written is a first
    run; a file that exists and will not parse is damage, and continuing with
    a default would silently discard whatever it held.
    """
    path = path_for(name)
    if not os.path.exists(path):
        return {} if default is None else default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(name: str, state) -> str:
    """Write a state file, atomically, sorted, with a trailing newline.

    Sorted and indented because these are read in diffs by a person. An
    unordered dump produces a diff where every line moved and none of them
    changed, which is a diff nobody reads twice.
    """
    os.makedirs(DATA, exist_ok=True)
    path = path_for(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path
