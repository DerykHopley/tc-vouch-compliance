#!/usr/bin/env bash
# The browser check, against the built front end served by the backend on one origin, as on
# Cloud Run. It starts throwaway servers on free ports, runs the checks, and stops the servers:
#   - Vouch: the API on the bundled demo script (the scripted fake model: no key, no paid call)
#     serving frontend/dist. With VOUCH_IMAGE set, that image's container instead of uvicorn,
#     in local auth mode (the image's own default is clerk);
#   - a second API on a broken fake script, for the error event (tests route the assessment call to it).
# Never touches the owner's servers on 8000/5173: the OS picks the ports.
# Every server runs with a request-rate limit high enough for the whole suite (VOUCH_RATE_REQUESTS):
# the suite is local mode's one user, and the default 60 a minute would answer it 429 (#138).
#
#   frontend/e2e/run.sh                                  # build the front end, Chromium
#   frontend/e2e/run.sh --browser firefox                # extra arguments go to pytest
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
RATE="${VOUCH_RATE_REQUESTS:-100000}"
cleanup() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  if [ -n "${VOUCH_IMAGE:-}" ]; then "$CONTAINER" rm -f "$NAME" >/dev/null 2>&1 || true; fi
  wait 2>/dev/null || true
}
trap cleanup EXIT

read -r VOUCH_PORT FAILING_PORT < <(python3 -c '
import socket
socks = [socket.socket() for _ in range(2)]
for s in socks: s.bind(("127.0.0.1", 0))
print(*(s.getsockname()[1] for s in socks))')

if [ -n "${VOUCH_IMAGE:-}" ]; then
  "$CONTAINER" run --rm --name "$NAME" -p "127.0.0.1:$VOUCH_PORT:8080" -e VOUCH_AUTH_MODE=local -e VOUCH_RATE_REQUESTS="$RATE" "$VOUCH_IMAGE" \
    >"$LOG_DIR/vouch.log" 2>&1 &
else
  (cd "$ROOT/frontend" && npm run build) >"$LOG_DIR/build.log" 2>&1 \
    || { echo "The front-end build failed:" >&2; tail -n 30 "$LOG_DIR/build.log" >&2; exit 1; }
  (cd "$ROOT" && env -u ASSESS_FAKE_SCRIPT -u OPENROUTER_API_KEY VOUCH_AUTH_MODE=local VOUCH_RATE_REQUESTS="$RATE" VOUCH_FRONTEND="$ROOT/frontend/dist" \
    uv run uvicorn vouch.api:app --port "$VOUCH_PORT") >"$LOG_DIR/vouch.log" 2>&1 &
fi
PIDS+=($!)
(cd "$ROOT" && env -u OPENROUTER_API_KEY -u VOUCH_FRONTEND VOUCH_AUTH_MODE=local VOUCH_RATE_REQUESTS="$RATE" ASSESS_FAKE_SCRIPT="$ROOT/tests/scripts/broken.json" \
  uv run uvicorn vouch.api:app --port "$FAILING_PORT") >"$LOG_DIR/failing-api.log" 2>&1 &
PIDS+=($!)

export VOUCH_URL="http://localhost:$VOUCH_PORT"
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
wait_for "$VOUCH_URL/api/health"
wait_for "$VOUCH_URL/"
wait_for "$FAILING_API_URL/api/health"

echo "Browser check on $VOUCH_URL (${VOUCH_IMAGE:-built front end}; failing API $FAILING_API_URL): pytest $*"
cd "$HERE"
uv run --project "$HERE" pytest -q "$@"
