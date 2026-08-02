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
  python tools/merge_state.py /tmp/theirs.json /tmp/ours.json data/state.json || {
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
