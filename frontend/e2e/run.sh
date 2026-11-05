#!/usr/bin/env bash
# The browser check, against the built front end served by the backend on one origin, as on
# Cloud Run. It starts throwaway servers on free ports, runs the checks, and stops the servers:
#   - Vouch: the API on the bundled demo script (the scripted fake model: no key, no paid call)
#     serving frontend/dist. With VOUCH_IMAGE set, that image's container instead of uvicorn,
#     in local auth mode (the image's own default is clerk);
#   - a second Vouch the same way, for the Dossier's checks (#40): its own assessments and its own
#     request-rate budget, as local mode's one user would otherwise spend the 60 a minute across the suite;
#   - a third Vouch on a profiles folder of its own, for the profile switcher's checks (#47): Nordlicht,
#     a second test profile and a broken one (tests/profiles_dir.py), and again its own request-rate budget;
#   - a fourth the same way, for the working paper's checks (#41), for the same reason;
#   - a fifth, for the chat's checks (#44), for the same reason;
#   - an API on a broken fake script, for the error event (tests route the assessment call to it).
# Never touches the owner's servers on 8000/5173: the OS picks the ports.
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
cleanup() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  if [ -n "${VOUCH_IMAGE:-}" ]; then
    "$CONTAINER" rm -f "$NAME" "$NAME-dossier" "$NAME-profiles" "$NAME-paper" "$NAME-chat" >/dev/null 2>&1 || true
  fi
  wait 2>/dev/null || true
  rm -rf "$PROFILES_DIR"
}
PROFILES_DIR="$(mktemp -d)"
trap cleanup EXIT

read -r VOUCH_PORT DOSSIER_PORT PROFILES_PORT PAPER_PORT CHAT_PORT FAILING_PORT < <(python3 -c '
import socket
socks = [socket.socket() for _ in range(6)]
for s in socks: s.bind(("127.0.0.1", 0))
print(*(s.getsockname()[1] for s in socks))')

if [ -z "${VOUCH_IMAGE:-}" ]; then
  (cd "$ROOT/frontend" && npm run build) >"$LOG_DIR/build.log" 2>&1 \
    || { echo "The front-end build failed:" >&2; tail -n 30 "$LOG_DIR/build.log" >&2; exit 1; }
fi
start_vouch() { # port, log name, container name, then a profiles folder if not the fixtures
  if [ -n "${VOUCH_IMAGE:-}" ]; then
    local profiles=()
    if [ -n "${4:-}" ]; then profiles=(-v "$4:/profiles:ro,Z" -e VOUCH_PROFILES_DIR=/profiles); fi
    "$CONTAINER" run --rm --name "$3" -p "127.0.0.1:$1:8080" -e VOUCH_AUTH_MODE=local "${profiles[@]}" "$VOUCH_IMAGE" \
      >"$LOG_DIR/$2.log" 2>&1 &
  else
    (cd "$ROOT" && env -u ASSESS_FAKE_SCRIPT -u OPENROUTER_API_KEY VOUCH_AUTH_MODE=local VOUCH_FRONTEND="$ROOT/frontend/dist" \
      VOUCH_PROFILES_DIR="${4:-}" uv run uvicorn vouch.api:app --port "$1") >"$LOG_DIR/$2.log" 2>&1 &
  fi
  PIDS+=($!)
}
(cd "$ROOT" && uv run python tests/profiles_dir.py "$PROFILES_DIR/profiles") >"$LOG_DIR/profiles.log" 2>&1 \
  || { echo "The profile switcher's profiles could not be made:" >&2; tail -n 30 "$LOG_DIR/profiles.log" >&2; exit 1; }
chmod -R a+rX "$PROFILES_DIR" # the container runs as nobody
start_vouch "$VOUCH_PORT" vouch "$NAME"
start_vouch "$DOSSIER_PORT" dossier "$NAME-dossier"
start_vouch "$PROFILES_PORT" profiles "$NAME-profiles" "$PROFILES_DIR/profiles"
start_vouch "$PAPER_PORT" paper "$NAME-paper"
start_vouch "$CHAT_PORT" chat "$NAME-chat"
(cd "$ROOT" && env -u OPENROUTER_API_KEY -u VOUCH_FRONTEND VOUCH_AUTH_MODE=local ASSESS_FAKE_SCRIPT="$ROOT/tests/scripts/broken.json" \
  uv run uvicorn vouch.api:app --port "$FAILING_PORT") >"$LOG_DIR/failing-api.log" 2>&1 &
PIDS+=($!)

export VOUCH_URL="http://localhost:$VOUCH_PORT"
export DOSSIER_URL="http://localhost:$DOSSIER_PORT"
export PROFILES_URL="http://localhost:$PROFILES_PORT"
export PAPER_URL="http://localhost:$PAPER_PORT"
export CHAT_URL="http://localhost:$CHAT_PORT"
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
wait_for "$DOSSIER_URL/api/health"
wait_for "$PROFILES_URL/api/health"
wait_for "$PAPER_URL/api/health"
wait_for "$CHAT_URL/api/health"
wait_for "$FAILING_API_URL/api/health"

echo "Browser check on $VOUCH_URL (${VOUCH_IMAGE:-built front end}; failing API $FAILING_API_URL): pytest $*"
cd "$HERE"
uv run --project "$HERE" pytest -q "$@"
