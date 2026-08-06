"""
What the machine has learned about its own work, and what it has not.

THE HONEST VERSION OF 'IT GETS BETTER EVERY DAY'
------------------------------------------------
Twenty-eight emails and five replies. Any model fitted to that would be
fitting noise, and a machine confidently acting on noise is worse than one
that does nothing: it would keep the template that got lucky and bin the one
that did not, and never find out it was wrong.

So this does not fit a model. It measures, and it refuses to draw a
conclusion until the numbers can carry one:

  MEASURE     reply rate by template, by contact tier, by hour of day, by
              agency versus employer, by score band. Every send is already
              recorded with all of it - none of this needed new plumbing,
              only somebody to read it.
  DECIDE      only where a bucket has MIN_SAMPLE sends and its Wilson lower
              bound beats another bucket's upper bound. That is a real
              difference rather than a run of luck.
  SAY SO      when there is not enough data, it says how many more sends it
              needs. A learning system that cannot tell you it does not know
              yet is not learning, it is guessing with extra steps.

WHERE THE REAL GAINS ARE TODAY
------------------------------
Not in the reply rate, which is 18% and above the 2-5% a blind application
gets. In the portal:

  THE UNANSWERED QUESTIONS. Every time the agent hits a field it cannot
  ground, it writes the exact wording into portal_flags. Those flags are a
  list of the questions standing between Harry and a submitted application,
  in frequency order, and each one is a line he could add to
  data/answers.json in ten seconds. Five applications have stalled on a
  salary dropdown with no '35000' option. That is not a modelling problem,
  it is a missing answer, and it is worth more than any amount of tuning.

  WHICH PLATFORMS FINISH. Attempts are recorded with their ATS. Working the
  queue in the order of what actually completes means the same hour of
  browser time produces more submitted applications, and that ordering gets
  better every time a run adds evidence.

    python learn.py              # what the data says, and what it cannot
    python learn.py --write      # save the verdicts to data/learned.json
    python learn.py --questions  # the answer-bank gaps, worst first
"""
import argparse
import collections
import json
import math
import os
import sys

import job_machine as jm

LEARNED_PATH = os.path.join(jm.ROOT, "data", "learned.json")
# Below this, a bucket is an anecdote. Chosen so that one lucky reply cannot
# carry a verdict: at n=8, a single reply is 12% and the interval is still
# wider than any difference worth acting on.
MIN_SAMPLE = jm.env_int("LEARN_MIN_SAMPLE", 8)
CONFIDENCE_Z = 1.96


def wilson(successes, trials, z=CONFIDENCE_Z):
    """(low, high) for a proportion. The interval, not the point estimate.

    A point estimate of '33% reply rate' off three sends is a lie told with
    a number. The interval says 1% to 79%, which is the truth."""
    if not trials:
        return 0.0, 1.0
    phat = successes / trials
    denom = 1 + z * z / trials
    centre = (phat + z * z / (2 * trials)) / denom
    margin = (z / denom) * math.sqrt(
        phat * (1 - phat) / trials + z * z / (4 * trials * trials))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def sent_emails(state):
    return [j for j in state.get("jobs", {}).values() if j.get("sent_at")]


def is_reply(job):
    """A human wrote back. An autoresponder did not."""
    return bool(job.get("replied_at")) and \
        job.get("reply_category") != "auto_acknowledgement"


TIER_NAMES = {3: "named person", 2: "hiring inbox", 1: "generic inbox"}


def dimensions(job):
    """Every way of slicing one sent email."""
    hour = (jm.parse_ts(job.get("sent_at")) or jm.uk_now()).hour
    return {
        "template": job.get("template_family") or "unknown",
        "contact": TIER_NAMES.get(job.get("email_tier"), "unknown"),
        "recipient": "agency" if jm.is_agency(job) else "employer",
        "hour": f"{hour:02d}:00",
        "score": f"{(job.get('score') or 0) // 10 * 10}s",
    }


def buckets(state):
    """{dimension: {value: {sent, replies, low, high}}}"""
    out = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"sent": 0, "replies": 0}))
    for job in sent_emails(state):
        for dimension, value in dimensions(job).items():
            cell = out[dimension][value]
            cell["sent"] += 1
            cell["replies"] += int(is_reply(job))
    for dimension in out:
        for value, cell in out[dimension].items():
            cell["rate"] = cell["replies"] / cell["sent"]
            cell["low"], cell["high"] = wilson(cell["replies"], cell["sent"])
    return {d: dict(v) for d, v in out.items()}


def verdicts(state):
    """Only the conclusions the data can carry. Usually none, at first.

    A verdict needs two buckets that both cleared MIN_SAMPLE and whose
    intervals do not overlap. Anything less is a difference you would see
    from a coin."""
    found, pending = [], []
    for dimension, values in buckets(state).items():
        big = {v: c for v, c in values.items() if c["sent"] >= MIN_SAMPLE}
        if len(big) < 2:
            needed = MIN_SAMPLE * 2 - sum(c["sent"] for c in values.values())
            pending.append({
                "dimension": dimension,
                "why": f"{len(big)} of {len(values)} option(s) have "
                       f"{MIN_SAMPLE}+ sends",
                "sends_needed": max(needed, MIN_SAMPLE)})
            continue
        best = max(big.items(), key=lambda kv: kv[1]["low"])
        worst = min(big.items(), key=lambda kv: kv[1]["high"])
        if best[0] != worst[0] and best[1]["low"] > worst[1]["high"]:
            found.append({
                "dimension": dimension,
                "prefer": best[0], "avoid": worst[0],
                "evidence": f"{best[0]} {best[1]['replies']}/{best[1]['sent']} "
                            f"vs {worst[0]} {worst[1]['replies']}/"
                            f"{worst[1]['sent']}"})
        else:
            pending.append({
                "dimension": dimension,
                "why": "the intervals still overlap - no real difference yet",
                "sends_needed": MIN_SAMPLE})
    return {"verdicts": found, "not_yet": pending}


# ======================================================================
# THE PORTAL: WHAT ACTUALLY STOPS IT
# ======================================================================
def unanswered_questions(state):
    """The exact questions that stalled an application, worst first.

    This is the highest-value thing in this file. Every flag is a field the
    agent could not ground, written in the form's own words, and every one of
    them is a line Harry could add to data/answers.json in ten seconds. It is
    not a modelling problem. It is a missing answer."""
    counts = collections.Counter()
    examples = {}
    for job in state.get("jobs", {}).values():
        for flag in job.get("portal_flags") or []:
            # Timeouts are a bug in the agent, not a question he needs to
            # answer, and they were fixed by the visibility change.
            if "TimeoutError" in flag:
                continue
            key = flag.strip()[:90]
            counts[key] += 1
            examples.setdefault(key, job.get("company"))
    return [{"question": q, "times": n, "first_seen_at": examples[q]}
            for q, n in counts.most_common()]


def platform_success(state):
    """Reaching a form, and finishing one, per ATS.

    Working the queue in this order means the same hour of browser time
    produces more submitted applications - and the order improves on its own
    every time a run adds evidence."""
    out = collections.defaultdict(
        lambda: {"tried": 0, "reached": 0, "submitted": 0})
    for job in state.get("jobs", {}).values():
        if not job.get("portal_attempted_at"):
            continue
        cell = out[job.get("ats") or "employer's own site"]
        cell["tried"] += 1
        cell["reached"] += int(bool(job.get("portal_filled")
                                    or job.get("captcha_answers")))
        # An application Harry finished by hand is a real application, and
        # every count of 'how many have we applied for' should say so. It is
        # NOT evidence that the agent finishes this platform - a portal that
        # always needs a human at the end would otherwise be ranked as though
        # the machine sails through it, and the queue would be worked in
        # exactly the wrong order.
        cell["submitted"] += int(job.get("status") == "portal_submitted"
                                 and not job.get("finished_by_hand_at"))
    for cell in out.values():
        cell["reach_rate"] = cell["reached"] / max(cell["tried"], 1)
        cell["finish_rate"] = cell["submitted"] / max(cell["tried"], 1)
    return dict(out)


def platform_order(state):
    """{ats: weight} - highest expected value first.

    Ranked by how often a platform is finished, then by how often a form is
    even reached, with anything untried given the benefit of the doubt so a
    new platform is not starved of the evidence it needs to be judged."""
    stats = platform_success(state)
    weights = {}
    for name, cell in stats.items():
        if cell["tried"] < 3:
            weights[name] = 0.5          # unproven, not condemned
            continue
        weights[name] = cell["finish_rate"] + 0.3 * cell["reach_rate"]
    return weights


def write(state):
    learned = {
        "_README": [
            "Written by learn.py from the machine's own records. Do not edit "
            "by hand - it is overwritten on every run that learns anything.",
            "A verdict appears here only when two buckets each have "
            f"{MIN_SAMPLE}+ sends and their confidence intervals do not "
            "overlap. Until then 'not_yet' says what is missing.",
        ],
        "updated_at": jm.now(),
        "emails": {"sent": len(sent_emails(state)),
                   "human_replies": sum(1 for j in sent_emails(state)
                                        if is_reply(j))},
        **verdicts(state),
        "platform_order": platform_order(state),
        "answer_gaps": unanswered_questions(state)[:20],
    }
    with open(LEARNED_PATH, "w") as f:
        json.dump(learned, f, indent=2)
        f.write("\n")
    return learned


def load_learned():
    try:
        with open(LEARNED_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def report(state):
    stats = buckets(state)
    sent = sent_emails(state)
    human = sum(1 for j in sent if is_reply(j))
    low, high = wilson(human, len(sent))
    print(f"[learn] {len(sent)} sent, {human} human repl"
          f"{'y' if human == 1 else 'ies'} "
          f"({human / max(len(sent), 1):.0%}, true range "
          f"{low:.0%}-{high:.0%})")
    for dimension, values in sorted(stats.items()):
        print(f"\n  {dimension}")
        for value, cell in sorted(values.items(),
                                  key=lambda kv: -kv[1]["rate"]):
            mark = "" if cell["sent"] >= MIN_SAMPLE else "  (too few to judge)"
            print(f"    {value[:24]:26} {cell['replies']}/{cell['sent']:<3} "
                  f"{cell['rate']:>4.0%}  [{cell['low']:.0%}-{cell['high']:.0%}]"
                  f"{mark}")
    outcome = verdicts(state)
    print("\n[learn] what the data can carry:")
    for verdict in outcome["verdicts"]:
        print(f"    PREFER {verdict['prefer']} over {verdict['avoid']} "
              f"({verdict['evidence']})")
    if not outcome["verdicts"]:
        print("    nothing yet - every difference so far is inside the noise")
    for waiting in outcome["not_yet"]:
        print(f"    {waiting['dimension']}: {waiting['why']}, needs about "
              f"{waiting['sends_needed']} more sends")
    return outcome


def report_questions(state):
    gaps = unanswered_questions(state)
    if not gaps:
        print("[learn] no application has stalled on a question yet")
        return
    print(f"[learn] {len(gaps)} question(s) have stopped an application. "
          f"Each one is a line in data/answers.json:")
    for gap in gaps[:20]:
        print(f"    {gap['times']}x  {gap['question']}")
        print(f"          first at {gap['first_seen_at']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="save the verdicts to data/learned.json")
    ap.add_argument("--questions", action="store_true",
                    help="the answer-bank gaps, worst first")
    args = ap.parse_args(argv)
    state = jm.load()
    if args.questions:
        report_questions(state)
        return 0
    report(state)
    report_questions(state)
    if args.write:
        learned = write(state)
        print(f"\n[learn] written to {LEARNED_PATH} "
              f"({len(learned['verdicts'])} verdict(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
