#!/usr/bin/env bash
# The browser check, against the built front end served by the backend on one origin, as on
# Cloud Run. It starts throwaway servers on free ports, runs the checks, and stops the servers:
#   - Vouch: the API on the bundled demo script (the scripted fake model: no key, no paid call)
#     serving frontend/dist. With VOUCH_IMAGE set, that image's container instead of uvicorn,
#     in local auth mode (the image's own default is clerk);
#   - a second Vouch the same way, for the Dossier's checks (#40): its own assessments and its own
#     request-rate budget, as local mode's one user would otherwise spend the 60 a minute across the suite;
#   - a third Vouch on a profiles folder of its own, for the profile switcher's checks (#47): Nordlicht,
#     a second test profile and a broken one (tests/profiles_dir.py);
#   - a fourth the same way, for the working paper's checks (#41), for the same reason;
#   - a fifth, for the chat's checks (#44), for the same reason;
#   - a sixth, for the readiness report's checks (#234), for the same reason;
#   - a seventh, for the fresh start's check (#285): starting Nordlicht afresh would hide the other
#     servers' seeded progress from their checks;
#   - an eighth, for the profile pass's checks (#390): a pass assesses all 12 controls, which would
#     change the counts the other servers' checks read;
#   - a ninth, for the review rounds' checks (#402): closing a round changes the hero and the
#     comparison the other servers' checks read, and its control's accept changes the counts;
#   - a tenth, for the improvement plan's checks (#410): its goal is Internal improvement, and its
#     control ends Closed, which the other servers' checks would read;
#   - an eleventh, for the open questions' checks (#414): its control is accepted, questioned and
#     assessed again, which would change the counts and the cards the other servers' checks read;
#   - a twelfth, for the improvement report's check (#420): its goal is Internal improvement and a
#     round is closed, which would change the report the sixth server's checks read;
#   - a thirteenth, for company setup's create (#409): the company it makes stays in that process,
#     and would add a profile to the other servers' switchers. Its three creates (#409, #424,
#     #454) each run a pass: the passes' daily limit (3), so a fourth goes on a server of its own;
#   - a fourteenth, for company setup's first profile pass (#447): every create on the thirteenth
#     now runs a pass, and the passes' daily limit (3) would refuse story 5's own;
#   - a fifteenth, for removing a company made in setup (#418): its three creates each run a pass
#     (#447), which with the thirteenth's three would pass the daily limit (3);
#   - a sixteenth, for adding documents (#585): an added document is kept for its company and found
#     by every search on that server, which would change what the other servers' checks read;
#   - an API on a broken fake script, for the error event (tests route the assessment call to it).
# Never touches the owner's servers on 8000/5173: the OS picks the ports.
# Every server runs with a request-rate limit high enough for the whole suite (VOUCH_RATE_REQUESTS):
# the suite is local mode's one user, and the default 60 a minute would answer it 429 (#138).
#
#   frontend/e2e/run.sh                                  # build the front end, Chromium
#   frontend/e2e/run.sh --browser firefox                # extra arguments go to pytest
#   frontend/e2e/run.sh frontend/e2e/test_chat.py        # a test path from the repo's root works too
#   E2E_WARNINGS=1 frontend/e2e/run.sh                   # pytest's warnings (off by default)
#   VOUCH_IMAGE=vouch:dev frontend/e2e/run.sh            # the built container (CONTAINER=podman to switch)
#
# Needs: backend deps (uv sync), frontend deps (npm ci) unless VOUCH_IMAGE is set, and the browser:
#   uv run --project frontend/e2e playwright install chromium
# For day-to-day work the Vite dev server still proxies /api to a local API: `npm run dev`.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LOG_DIR="${E2E_LOG_DIR:-$(mktemp -d)}"
CONTAINER="${CONTAINER:-docker}"
NAME="vouch-e2e-$$"
PIDS=()
CONTAINERS=()
RATE="${VOUCH_RATE_REQUESTS:-100000}"
# The Vouch servers above, in order, each as its name (for its log and container) and the variable
# its URL goes in. A new server is one more entry here.
SERVERS=(
  vouch:VOUCH_URL dossier:DOSSIER_URL profiles:PROFILES_URL paper:PAPER_URL chat:CHAT_URL
  report:REPORT_URL afresh:AFRESH_URL pass:PASS_URL rounds:ROUNDS_URL plan:PLAN_URL
  questions:QUESTIONS_URL improvement:IMPROVEMENT_URL setup:SETUP_URL firstpass:FIRST_PASS_URL
  removal:REMOVAL_URL documents:DOCUMENTS_URL
)
cleanup() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  if [ ${#CONTAINERS[@]} -gt 0 ]; then
    "$CONTAINER" rm -f "${CONTAINERS[@]}" >/dev/null 2>&1 || true
  fi
  wait 2>/dev/null || true
  rm -rf "$PROFILES_DIR"
}
PROFILES_DIR="$(mktemp -d)"
trap cleanup EXIT

# A free port for each server, then one for the failing API.
read -r -a PORTS < <(python3 -c '
import socket, sys
socks = [socket.socket() for _ in range(int(sys.argv[1]))]
for s in socks: s.bind(("127.0.0.1", 0))
print(*(s.getsockname()[1] for s in socks))' $((${#SERVERS[@]} + 1)))
FAILING_PORT=${PORTS[${#SERVERS[@]}]}

if [ -z "${VOUCH_IMAGE:-}" ]; then
  (cd "$ROOT/frontend" && npm run build) >"$LOG_DIR/build.log" 2>&1 \
    || { echo "The front-end build failed:" >&2; tail -n 30 "$LOG_DIR/build.log" >&2; exit 1; }
fi
start_vouch() { # port, log name, container name, then a profiles folder if not the fixtures
  if [ -n "${VOUCH_IMAGE:-}" ]; then
    local profiles=()
    if [ -n "${4:-}" ]; then profiles=(-v "$4:/profiles:ro,Z" -e VOUCH_PROFILES_DIR=/profiles); fi
    CONTAINERS+=("$3")
    "$CONTAINER" run --rm --name "$3" -p "127.0.0.1:$1:8080" -e VOUCH_AUTH_MODE=local -e VOUCH_RATE_REQUESTS="$RATE" \
      "${profiles[@]}" "$VOUCH_IMAGE" >"$LOG_DIR/$2.log" 2>&1 &
  else
    (cd "$ROOT" && env -u ASSESS_FAKE_SCRIPT -u OPENROUTER_API_KEY VOUCH_AUTH_MODE=local VOUCH_RATE_REQUESTS="$RATE" VOUCH_FRONTEND="$ROOT/frontend/dist" \
      VOUCH_PROFILES_DIR="${4:-}" uv run uvicorn vouch.api:app --port "$1") >"$LOG_DIR/$2.log" 2>&1 &
  fi
  PIDS+=($!)
}
(cd "$ROOT" && uv run python tests/profiles_dir.py "$PROFILES_DIR/profiles") >"$LOG_DIR/profiles.log" 2>&1 \
  || { echo "The profile switcher's profiles could not be made:" >&2; tail -n 30 "$LOG_DIR/profiles.log" >&2; exit 1; }
chmod -R a+rX "$PROFILES_DIR" # the container runs as nobody
for i in "${!SERVERS[@]}"; do
  server=${SERVERS[$i]%%:*}
  folder=()
  if [ "$server" = profiles ]; then folder=("$PROFILES_DIR/profiles"); fi
  start_vouch "${PORTS[$i]}" "$server" "$NAME-$server" "${folder[@]+"${folder[@]}"}"
  export "${SERVERS[$i]#*:}=http://localhost:${PORTS[$i]}"
done
(cd "$ROOT" && env -u OPENROUTER_API_KEY -u VOUCH_FRONTEND VOUCH_AUTH_MODE=local VOUCH_RATE_REQUESTS="$RATE" ASSESS_FAKE_SCRIPT="$ROOT/tests/scripts/broken.json" \
  uv run uvicorn vouch.api:app --port "$FAILING_PORT") >"$LOG_DIR/failing-api.log" 2>&1 &
PIDS+=($!)

export FAILING_API_URL="http://127.0.0.1:$FAILING_PORT"

wait_for() {
  for _ in $(seq 1 60); do
    if curl -fsS "$1" >/dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  echo "$1 didn't answer within 30 s. Logs:" >&2
  tail -n 30 "$LOG_DIR"/*.log >&2
  return 1
}
for server in "${SERVERS[@]}"; do
  url_var=${server#*:}
  wait_for "${!url_var}/api/health"
done
wait_for "$VOUCH_URL/"
wait_for "$FAILING_API_URL/api/health"

# Test paths work from the repo's root too (frontend/e2e/test_x.py), not only from here (#246).
# Warnings are off by default, so the last line is pytest's result; E2E_WARNINGS=1 shows them.
args=()
for arg in "$@"; do
  arg=${arg#./}
  args+=("${arg#frontend/e2e/}")
done
[ "${E2E_WARNINGS:-0}" = 1 ] || args=(-p no:warnings "${args[@]+"${args[@]}"}")
echo "Browser check on $VOUCH_URL (${VOUCH_IMAGE:-built front end}; failing API $FAILING_API_URL): pytest ${args[*]+"${args[*]}"}"
cd "$HERE"
uv run --project "$HERE" pytest -q "${args[@]+"${args[@]}"}"
