#!/usr/bin/env bash
# The browser check: start throwaway servers on free ports, run the checks, stop the servers.
#   - the API on the bundled demo script (the scripted fake model: no key, no paid call);
#   - a second API on a broken fake script, for the error event (tests route the assessment call to it);
#   - the Vite dev server, proxying /api to the first API (vite.config.ts, VOUCH_API).
# Never touches the owner's servers on 8000/5173: the OS picks the ports.
#
#   frontend/e2e/run.sh                       # Chromium
#   frontend/e2e/run.sh --browser firefox     # extra arguments go to pytest
#
# Needs: backend deps (uv sync), frontend deps (npm ci), and the browser:
#   uv run --project frontend/e2e playwright install chromium
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LOG_DIR="${E2E_LOG_DIR:-$(mktemp -d)}"
PIDS=()
cleanup() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT

read -r API_PORT FAILING_PORT UI_PORT < <(python3 -c '
import socket
socks = [socket.socket() for _ in range(3)]
for s in socks: s.bind(("127.0.0.1", 0))
print(*(s.getsockname()[1] for s in socks))')

(cd "$ROOT" && env -u ASSESS_FAKE_SCRIPT -u OPENROUTER_API_KEY \
  uv run uvicorn vouch.api:app --port "$API_PORT") >"$LOG_DIR/api.log" 2>&1 &
PIDS+=($!)
(cd "$ROOT" && env -u OPENROUTER_API_KEY ASSESS_FAKE_SCRIPT="$ROOT/tests/scripts/broken.json" \
  uv run uvicorn vouch.api:app --port "$FAILING_PORT") >"$LOG_DIR/failing-api.log" 2>&1 &
PIDS+=($!)
(cd "$ROOT/frontend" && VOUCH_API="http://127.0.0.1:$API_PORT" \
  exec node_modules/.bin/vite --port "$UI_PORT" --strictPort) >"$LOG_DIR/ui.log" 2>&1 &
PIDS+=($!)

export UI_URL="http://localhost:$UI_PORT"
export API_URL="http://127.0.0.1:$API_PORT"
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
wait_for "$API_URL/api/health"
wait_for "$FAILING_API_URL/api/health"
wait_for "$UI_URL/api/health"

echo "Browser check on $UI_URL (API $API_URL, failing API $FAILING_API_URL): pytest $*"
cd "$HERE"
uv run --project "$HERE" pytest -q "$@"
