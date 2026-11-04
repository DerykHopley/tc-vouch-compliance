#!/usr/bin/env bash
# Merge a PR with a merge commit once every CI check on it has passed.
# GitHub's free plan has no branch protection on private repos, so `gh pr merge --auto`
# would not wait for CI; this script is the gate instead.
# Copied from the rig (~/development/rig) by project-kickoff; a kickoff upgrade compares it with the rig's.
#
# Usage: scripts/merge-on-green.sh <pr-number>
# Env:   MERGE_TIMEOUT  how long to wait for checks (default 20m, about twice the fast lane)
#        SIZE_TARGET    changed lines a PR aims to stay under, lockfiles left out (default 400).
#                       Over it the PR still merges; the line it prints says so.
set -euo pipefail

pr=${1:?usage: scripts/merge-on-green.sh <pr-number>}
limit=${MERGE_TIMEOUT:-20m}

# Pin the commit being checked, so a push during the wait can't be merged unchecked.
head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
echo "PR #$pr at ${head:0:7}: waiting for checks (limit $limit)"

# Size: count changed lines from GitHub's own per-file numbers (never a possibly stale
# local main), leaving out lockfiles. 400 is a target, not a wall: the ticket loop gives a
# bigger PR a deeper review before it gets here, and the size line goes in the report.
target=${SIZE_TARGET:-400}
lockfiles='(^|/)(uv\.lock|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|poetry\.lock|Cargo\.lock|bun\.lockb|Gemfile\.lock|composer\.lock|go\.sum)$'
size=$(gh pr view "$pr" --json files --jq '.files[] | "\(.additions + .deletions) \(.path)"' \
  | LOCKFILES="$lockfiles" awk '$2 !~ ENVIRON["LOCKFILES"] { n += $1 } END { print n + 0 }')
if [ "$size" -gt "$target" ]; then
  echo "PR #$pr: $size changed lines without lockfiles, over the $target target; merging (name it in the report)"
else
  echo "PR #$pr: $size changed lines without lockfiles (target $target)"
fi

# Checks take a few seconds to appear after a push; a PR with no checks never merges.
for _ in $(seq 1 30); do
  if [ "$(gh pr checks "$pr" --json name --jq length 2>/dev/null || echo 0)" -gt 0 ]; then
    break
  fi
  sleep 5
done
if [ "$(gh pr checks "$pr" --json name --jq length 2>/dev/null || echo 0)" -eq 0 ]; then
  echo "PR #$pr: no checks reported after 2.5 minutes; not merging." >&2
  exit 1
fi

if ! timeout "$limit" gh pr checks "$pr" --watch --fail-fast --interval 15 >/dev/null; then
  echo "PR #$pr: a check failed or did not finish within $limit; not merging." >&2
  gh pr checks "$pr" >&2 || true
  exit 1
fi

gh pr merge "$pr" --merge --match-head-commit "$head"
echo "PR #$pr merged."
