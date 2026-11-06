#!/usr/bin/env bash
# Merge a PR with a merge commit once every CI check on it has passed.
# GitHub's free plan has no branch protection on private repos, so `gh pr merge --auto`
# would not wait for CI; this script is the gate instead.
# Copied from the rig (~/development/rig) by project-kickoff; a kickoff upgrade compares it with the rig's.
#
# Usage: scripts/merge-on-green.sh <pr-number> [--conflicts-ok <spec-branch>]...
#        For a PR into main, the gate refuses when the PR's merge result would conflict with an
#        open spec branch (origin/spec/* not yet in main), in a file main alone doesn't already
#        conflict in. --conflicts-ok spec/<name> lets one
#        named spec conflict; give it only after that spec's thread has been told.
# Env:   MERGE_TIMEOUT  how long to wait for checks (default 20m, about twice the fast lane)
#        SIZE_TARGET    changed lines a PR aims to stay under, lockfiles left out (default 400).
#                       Over it the PR still merges; the line it prints says so.
set -euo pipefail

usage="usage: scripts/merge-on-green.sh <pr-number> [--conflicts-ok <spec-branch>]..."
pr=${1:?$usage}
shift
conflicts_ok=()
while [ $# -gt 0 ]; do
  case "$1" in
    --conflicts-ok)
      [ $# -ge 2 ] || { echo "$usage" >&2; exit 2; }
      conflicts_ok+=("$2"); shift 2 ;;
    *) echo "$usage" >&2; exit 2 ;;
  esac
done
limit=${MERGE_TIMEOUT:-20m}

# Pin the commit being checked, so a push during the wait can't be merged unchecked.
{ read -r base; read -r head_branch; read -r head; } < <(
  gh pr view "$pr" --json baseRefName,headRefName,headRefOid --jq '.baseRefName, .headRefName, .headRefOid')
echo "PR #$pr at ${head:0:7} into $base: waiting for checks (limit $limit)"

# Open specs: a PR into main must not make an open spec branch conflict (#235). The PR's merge
# result is built locally (main + the PR) and test-merged into each origin/spec/* branch that
# main doesn't already contain. A PR into a spec branch (a ticket) isn't checked.
check_open_specs() {
  [ "$base" = main ] || return 0
  # --prune, so a spec branch deleted on origin stops counting as open.
  if ! git fetch -q --prune origin main '+refs/heads/spec/*:refs/remotes/origin/spec/*' \
    || ! git fetch -q origin "$head_branch" || ! git cat-file -e "$head^{commit}" 2>/dev/null; then
    echo "PR #$pr: could not fetch main, the spec branches or the PR's head ${head:0:7}; not merging." >&2
    return 1
  fi
  local spec specs=()
  while read -r spec; do
    git merge-base --is-ancestor "$spec" origin/main || specs+=("$spec")
  done < <(git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/spec/')
  if [ ${#specs[@]} -eq 0 ]; then
    echo "PR #$pr: no open spec branches"
    return 0
  fi

  # git merge-tree exits 0 when clean, 1 on a conflict, and higher on an error.
  local out rc=0 merged
  out=$(git merge-tree --write-tree origin/main "$head") || rc=$?
  if [ $rc -eq 1 ]; then
    echo "PR #$pr: it conflicts with main itself; not merging." >&2
    return 1
  elif [ $rc -ne 0 ]; then
    echo "PR #$pr: git merge-tree failed (exit $rc); not merging." >&2
    return 1
  fi
  merged=$(git -c user.name=merge-gate -c user.email=merge-gate@localhost \
    commit-tree "${out%%$'\n'*}" -p origin/main -p "$head" -m "merge-gate: PR #$pr into main")

  local name allowed spec_ok before refused=()
  for spec in "${specs[@]}"; do
    rc=0
    out=$(git merge-tree --write-tree --name-only "$merged" "$spec") || rc=$?
    [ $rc -eq 0 ] && continue
    name=${spec#origin/}
    if [ $rc -ne 1 ]; then
      echo "PR #$pr: git merge-tree against $name failed (exit $rc); not merging." >&2
      return 1
    fi
    # The output is the tree, the conflicting files, a blank line, then messages.
    out=$(printf '%s\n' "$out" | sed -n '2,/^$/{/^$/!p}')
    # Only conflicts this PR adds count: one main already has with the spec isn't the PR's.
    rc=0
    before=$(git merge-tree --write-tree --name-only origin/main "$spec") || rc=$?
    if [ $rc -eq 1 ]; then
      before=$(printf '%s\n' "$before" | sed -n '2,/^$/{/^$/!p}')
      out=$(grep -vxF -f <(printf '%s\n' "$before") <<<"$out" || true)
      [ -n "$out" ] || continue
    elif [ $rc -ne 0 ]; then
      echo "PR #$pr: git merge-tree of main against $name failed (exit $rc); not merging." >&2
      return 1
    fi
    out=$(sed 's/^/    /' <<<"$out")
    allowed=0
    for spec_ok in "${conflicts_ok[@]}"; do [ "${spec_ok#origin/}" = "$name" ] && allowed=1; done
    if [ $allowed = 1 ]; then
      echo "PR #$pr: conflicts with $name, allowed by --conflicts-ok:"
      echo "$out"
    else
      echo "PR #$pr: merging it into main would make $name conflict in:" >&2
      echo "$out" >&2
      refused+=("--conflicts-ok $name")
    fi
  done
  if [ ${#refused[@]} -gt 0 ]; then
    echo "PR #$pr: not merging. Tell each spec's thread first; then the override is: ${refused[*]}" >&2
    return 1
  fi
  echo "PR #$pr: no conflict with open specs, beyond any --conflicts-ok"
}

# Fail fast, before the wait for checks; it runs again just before the merge, as main may move.
check_open_specs

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

check_open_specs
gh pr merge "$pr" --merge --match-head-commit "$head"
echo "PR #$pr merged."
