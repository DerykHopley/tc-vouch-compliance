#!/usr/bin/env bash
# Merge a PR with a merge commit once every CI check on it has passed.
# GitHub's free plan has no branch protection on private repos, so `gh pr merge --auto`
# would not wait for CI; this script is the gate instead.
# Copied from the rig (~/development/rig) by project-kickoff; a kickoff upgrade compares it with the rig's.
#
# Usage: scripts/merge-on-green.sh <pr-number> [--conflicts-ok <spec branch>]...
#   A PR into main is first checked against every open spec branch (origin/spec/*): if its merge
#   would make one conflict, the gate refuses and names the branch and files (#235). After the
#   spec's thread has been told, --conflicts-ok <spec branch> (repeatable) lets it merge anyway.
# Env:   MERGE_TIMEOUT  how long to wait for checks (default 20m, about twice the fast lane)
#        SIZE_TARGET    changed lines a PR aims to stay under, lockfiles left out (default 400).
#                       Over it the PR still merges; the line it prints says so.
set -euo pipefail

usage='usage: scripts/merge-on-green.sh <pr-number> [--conflicts-ok <spec branch>]...'
pr=""
conflicts_ok=()
while [ $# -gt 0 ]; do
  case "$1" in
    --conflicts-ok) conflicts_ok+=("${2:?$usage}"); shift 2 ;;
    *) pr=$1; shift ;;
  esac
done
[ -n "$pr" ] || { echo "$usage" >&2; exit 2; }
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

# A PR into main mustn't make an open spec branch conflict (#235): its merge result, built with
# merge-tree (nothing is checked out or written to a branch), against each origin/spec/* branch.
# Run before the CI wait, and again just before merging, as main or a spec may move meanwhile.
base=$(gh pr view "$pr" --json baseRefName --jq .baseRefName)
check_open_specs() {
  [ "$base" = main ] || return 0
  git fetch -q --prune origin  # --prune: a spec branch deleted on GitHub is no longer open
  git cat-file -e "$head^{commit}" 2>/dev/null || git fetch -q origin "pull/$pr/head"
  local tree result spec name status out allowed ok
  if ! tree=$(git merge-tree --write-tree origin/main "$head"); then
    echo "PR #$pr: it conflicts with main itself; not merging." >&2
    exit 1
  fi
  result=$(GIT_AUTHOR_NAME=merge-on-green GIT_AUTHOR_EMAIL=gate@localhost \
    GIT_COMMITTER_NAME=merge-on-green GIT_COMMITTER_EMAIL=gate@localhost \
    git commit-tree "$tree" -p origin/main -p "$head" -m "merge-on-green: PR #$pr's merge result")
  local conflicting=() blocked=() files=()
  while read -r spec; do
    [ -n "$spec" ] || continue
    name=${spec#origin/}
    status=0
    out=$(git merge-tree --write-tree --name-only "$spec" "$result") || status=$?
    [ "$status" -eq 0 ] && continue
    [ "$status" -eq 1 ] || { echo "PR #$pr: git merge-tree failed on $name; not merging." >&2; exit 1; }
    mapfile -t files < <(printf '%s\n' "$out" | sed -n '2,/^$/p' | sed '/^$/d')
    conflicting+=("$name")
    allowed=no
    for ok in "${conflicts_ok[@]+"${conflicts_ok[@]}"}"; do [ "$ok" = "$name" ] && allowed=yes; done
    if [ "$allowed" = yes ]; then
      echo "PR #$pr: would make $name conflict (allowed by --conflicts-ok) in:"
      printf '  %s\n' "${files[@]}"
    else
      { echo "PR #$pr: merging it into main would make $name conflict in:"; printf '  %s\n' "${files[@]}"; } >&2
      blocked+=("$name")
    fi
  done < <(git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/spec/')
  if [ "${#blocked[@]}" -gt 0 ]; then
    # Every conflicting spec, those already allowed too, so the command can be run as it stands.
    echo "Not merging. Tell each spec's thread first; then, to merge anyway:" \
      "scripts/merge-on-green.sh $pr$(printf ' --conflicts-ok %s' "${conflicting[@]}")" >&2
    exit 1
  fi
  if [ "${#conflicting[@]}" -eq 0 ]; then
    echo "PR #$pr: no conflict with open specs"
  fi
}
check_open_specs

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

check_open_specs  # again: main or a spec may have moved during the wait
gh pr merge "$pr" --merge --match-head-commit "$head"
echo "PR #$pr merged."
