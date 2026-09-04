#!/usr/bin/env bash
# Commit data/ back to the target branch, merging rather than rebasing.
#
# Several workflows write data/state.json and can finish at the same moment. A
# rebase turns that into a conflict, and the old retry loop then span on
# unmerged files until it gave up. This fetches whatever is on the branch now,
# merges the two states as JSON, and pushes - retrying the whole cycle if
# someone else lands a commit in between.
#
# Usage: commit-state.sh <target-branch> <commit-message-prefix>
set +e

TARGET="$1"
PREFIX="${2:-state}"

git config user.name "job-machine"
git config user.email "actions@github.com"

if [ ! -f data/state.json ]; then
  echo "no state file to commit"
  exit 0
fi
cp data/state.json /tmp/ours.json
# Every other data file this run WROTE, kept aside for the same reason: the
# reset below restores the target branch's data/, so anything the run produced
# that is not state.json would simply vanish. The Covenant list is built by a
# tool and written straight to data/, and it went missing exactly this way.
#
# "Wrote" is the load-bearing word, and it used to be wrong. This copied the
# whole of data/ aside and then put every differing file back over the target
# branch - which cannot tell a file this run PRODUCED from one it merely had
# CHECKED OUT at an older commit. So any run whose checkout predated a hand
# edit silently reverted that edit and committed the revert as fresh output.
#
# That is not hypothetical. Commit 39c0381 reverted data/answers.json to a
# months-old copy, putting the false claim "DV (Developed Vetting)" back into
# a public repo for three days, and took 50 entries out of data/targets.json
# in the same breath.
#
# HEAD here is still the commit this run checked out, so diffing against it
# gives exactly the files the run itself changed - and nothing else.
CHANGED=$(mktemp)
{
  git diff --name-only HEAD -- data/
  git ls-files --others --exclude-standard -- data/
} | sort -u > "$CHANGED"
rm -rf /tmp/ourdata && mkdir -p /tmp/ourdata
while IFS= read -r path; do
  [ -f "$path" ] || continue
  cp "$path" "/tmp/ourdata/$(basename "$path")"
done < "$CHANGED"
# Keep our merge script too. The reset below checks out the target branch,
# which replaces tools/ with the target's copy - so a run on a branch that
# adds a new state field would have that field merged by the OLD script and
# silently dropped. That is exactly what happened to the ATS board cache.
cp tools/merge_state.py /tmp/merge_state.py

for attempt in 1 2 3 4 5; do
  git fetch origin "$TARGET" || { sleep $((2 ** attempt)); continue; }

  # start from exactly what is on the branch, then merge our run into it
  git reset --hard "origin/$TARGET" >/dev/null || { sleep $((2 ** attempt)); continue; }
  if [ -f data/state.json ]; then
    cp data/state.json /tmp/theirs.json
  else
    echo '{}' > /tmp/theirs.json
  fi
  mkdir -p data
  # put back everything else this run wrote, then merge state.json properly
  for f in /tmp/ourdata/*; do
    # A run that touched nothing but state.json leaves this empty, and an
    # unmatched glob would otherwise arrive here as the literal string.
    [ -e "$f" ] || continue
    name=$(basename "$f")
    [ "$name" = "state.json" ] && continue
    if ! cmp -s "$f" "data/$name"; then
      cp "$f" "data/$name"
      echo "keeping data/$name from this run"
    fi
  done
  python /tmp/merge_state.py /tmp/theirs.json /tmp/ours.json data/state.json || {
    echo "merge failed, falling back to our own state"
    cp /tmp/ours.json data/state.json
  }

  git add data/
  if git diff --cached --quiet; then
    echo "no state changes"
    exit 0
  fi
  git commit -m "$PREFIX $(date -u +%F_%H%M)" >/dev/null || {
    sleep $((2 ** attempt)); continue; }
  if git push origin "HEAD:refs/heads/$TARGET"; then
    echo "state pushed to $TARGET"
    exit 0
  fi
  echo "push race on attempt $attempt, retrying"
  sleep $((2 ** attempt))
done

echo "could not push state" >&2
exit 1
