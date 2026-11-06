#!/usr/bin/env bash
# CI's last job on a red run on main (#241): opens one Fix ticket, "Main is red: <jobs>", with the
# run link, the commit and the failing test ids; if one is already open, comments on it instead, so
# a red streak makes one issue. Automates CLAUDE.md's "full-lane red" rule.
#
# Env (set by ci.yml's main-red job): GH_TOKEN (GITHUB_TOKEN: issues write, actions read),
#   REPO (owner/name), RUN_ID, RUN_URL, SHA.
set -euo pipefail

: "${REPO:?}" "${RUN_ID:?}" "${RUN_URL:?}" "${SHA:?}"
PREFIX="Main is red:"

# The run's failed jobs, "id<TAB>name" per line. Each one's own log is complete once it has
# finished, while this job still runs (the whole run's log isn't until the run ends).
# A job that ran out of time ends cancelled or timed out, not failed: red all the same.
failed=$(gh api "repos/$REPO/actions/runs/$RUN_ID/jobs" --paginate \
  --jq '.jobs[] | select(.conclusion == "failure" or .conclusion == "cancelled" or .conclusion == "timed_out")
    | "\(.id)\t\(.name)"')

names=()
tests=""
while IFS=$'\t' read -r id name; do
  [ -n "$id" ] || continue
  names+=("$name")
  # pytest's summary lines, "FAILED <test id> - <reason>", and Vitest's, "FAIL  <file> > <test> [12ms]",
  # after the log's timestamp, colour codes taken out.
  found=$(gh api --allow-escape-sequences "repos/$REPO/actions/jobs/$id/logs" 2>/dev/null \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | sed -nE 's/.*FAILED ([^ ]+).*/\1/p; s/.* FAIL  (.+[^ ])$/\1/p' \
    | sed -E 's/ [0-9]+ms$//' || true)
  tests+="$found"$'\n'
done <<< "$failed"

jobs="the run"
if [ "${#names[@]}" -gt 0 ]; then
  jobs=$(printf '%s, ' "${names[@]}")
  jobs=${jobs%, }
fi
failing=$(printf '%s' "$tests" | sed '/^$/d' | sort -u | sed 's/^/- `/; s/$/`/')
[ -n "$failing" ] || failing="- none named in the logs (a job may have been cancelled or timed out): see the run"

details="**Run:** $RUN_URL
**Commit:** \`${SHA:0:7}\`
**Failed:** $jobs

**Failing tests:**
$failing"

existing=$(gh issue list --repo "$REPO" --state open --search "in:title \"$PREFIX\"" --json number,title \
  --jq "[.[] | select(.title | startswith(\"$PREFIX\"))][0].number // empty")

if [ -n "$existing" ]; then
  printf 'Main is red again.\n\n%s\n' "$details" | gh issue comment "$existing" --repo "$REPO" --body-file -
  echo "Commented on #$existing."
  exit 0
fi

gh issue create --repo "$REPO" --title "$PREFIX $jobs" --label bug --label ready-for-agent --body-file - <<EOF
## Parent

Fixes lane: a full-lane red on main (CLAUDE.md, CI). Opened by CI's main-red job (#241).

## What happens now

$details

## What should happen

Main's full lane is green: each failing test passes for its cause, named in the PR (the app or the
test). A test that passed on rerun is still unexplained: say so, and diagnose it.

## Done when

- [ ] The failing tests above pass on main. **Check:** main's next full lane is green.
- [ ] The PR names the cause of each failure. **Check:** read the PR.

## Needs

None

## Out of scope

Other tests than those that failed in this run.
EOF
