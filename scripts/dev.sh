#!/usr/bin/env bash
# Run Vouch locally: the API (reloading on change, on the scripted fake model) and the Vite dev
# server, which passes /api to it. Prints the URL to open; Ctrl-C stops both.
#
#   scripts/dev.sh                                  # API on 8000, front end on 5173
#   scripts/dev.sh --api-port 8010 --web-port 5180  # when those are taken
#
# A port already in use stops the run before anything starts. The API never builds the real
# model: OPENROUTER_API_KEY is dropped, and ASSESS_FAKE_SCRIPT (if set) picks the fake's script.
# Needs: bash 4.4 or newer, backend deps (uv sync) and front-end deps (npm ci in frontend/).
# `wait -n` and an empty array under `set -u` need bash 4.4; macOS ships 3.2.
if ((BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4))); then
  echo "scripts/dev.sh needs bash 4.4 or newer; this is $BASH_VERSION. On macOS: brew install bash" >&2
  exit 1
fi
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT=8000
WEB_PORT=5173

usage() {
  echo "Usage: scripts/dev.sh [--api-port <port>] [--web-port <port>]" >&2
  exit 2
}
is_port() { [[ "$1" =~ ^[0-9]+$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535)); }

while (($#)); do
  case "$1" in
    --api-port) [ $# -ge 2 ] && is_port "$2" || usage; API_PORT=$((10#$2)); shift 2 ;;
    --web-port) [ $# -ge 2 ] && is_port "$2" || usage; WEB_PORT=$((10#$2)); shift 2 ;;
    -h | --help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done
if [ "$API_PORT" -eq "$WEB_PORT" ]; then
  echo "The API and front-end ports must differ (both $API_PORT)." >&2
  exit 2
fi

# Taken when anything answers on either loopback: a Vite on `localhost` may listen on ::1 only.
# (tests/test_dev_command.py probes the same way.)
port_free() {
  python3 -c '
import socket, sys
for host in ("127.0.0.1", "::1"):
    try: socket.create_connection((host, int(sys.argv[1])), timeout=1).close()
    except OSError: continue
    sys.exit(1)' "$1"
}
for pair in "api-port $API_PORT" "web-port $WEB_PORT"; do
  read -r flag port <<<"$pair"
  if ! port_free "$port"; then
    echo "Port $port is already in use (another server?). Choose another: --$flag <another port>" >&2
    exit 1
  fi
done

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "The front-end deps are missing. Install them first: (cd frontend && npm ci)" >&2
  exit 1
fi

# Each server in its own process group (job control), so stopping a group also stops the
# children it spawned: uvicorn's reload worker, and the node process behind `npm run dev`.
set -m
PIDS=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${PIDS[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  for _ in $(seq 1 50); do
    alive=0
    for pid in "${PIDS[@]}"; do kill -0 -- "-$pid" 2>/dev/null && alive=1; done
    [ "$alive" -eq 0 ] && break
    sleep 0.1
  done
  for pid in "${PIDS[@]}"; do kill -KILL -- "-$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap cleanup EXIT

(cd "$ROOT" && exec env -u OPENROUTER_API_KEY -u VOUCH_FRONTEND \
  uv run uvicorn vouch.api:app --host 127.0.0.1 --port "$API_PORT" \
  --reload --reload-dir src) </dev/null &
PIDS+=($!)
(cd "$ROOT/frontend" && exec env VOUCH_API="http://127.0.0.1:$API_PORT" \
  npm run dev -- --host 127.0.0.1 --port "$WEB_PORT" --strictPort) </dev/null &
PIDS+=($!)

URL="http://127.0.0.1:$WEB_PORT/"
# The API first, then through the Vite proxy: probing Vite before the API is up logs a proxy error.
wait_for() {
  for _ in $(seq 1 120); do
    if curl -fsS "$1" >/dev/null 2>&1; then return 0; fi
    for pid in "${PIDS[@]}"; do
      kill -0 "$pid" 2>/dev/null || {
        echo "A server stopped while starting; see its output above. If it says its port is" \
          "in use, the port may have been taken just now: rerun, or choose another port." >&2
        exit 1
      }
    done
    sleep 0.5
  done
  echo "$1 didn't answer within 60 s; see the output above." >&2
  exit 1
}
wait_for "http://127.0.0.1:$API_PORT/api/health"
wait_for "${URL}api/health"

echo
echo "Vouch is running: $URL   (API on port $API_PORT; Ctrl-C stops both)"
echo

# Either server exiting on its own ends the run, and the other one with it.
wait -n || true  # a crashed server's non-zero status must not end the run before the message
echo "A server stopped; stopping the other." >&2
exit 1
