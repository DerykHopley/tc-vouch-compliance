#!/usr/bin/env bash
# The Postgres tests locally (#245), against a throwaway postgres:16 container on a free port,
# removed on exit (also on Ctrl-C or a failure). Never port 5432: the owner's Cloud SQL proxy
# uses it. Without arguments, the tests CI's database job runs (the postgres marker, #259),
# so the two can't drift; with arguments, those go to pytest instead.
#
#   scripts/test-db.sh                          # CI's database tests: -m postgres
#   scripts/test-db.sh tests/test_database.py   # these, with any pytest options
#   CONTAINER=podman scripts/test-db.sh         # the container runtime (default: docker, else podman)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER=${CONTAINER:-$(command -v docker >/dev/null 2>&1 && echo docker || echo podman)}
if ! command -v "$CONTAINER" >/dev/null 2>&1; then
  echo "test-db: no container runtime: $CONTAINER isn't installed. Install docker or podman (or set CONTAINER)." >&2
  exit 1
fi

# A free port from the OS (its ephemeral range never holds 5432; the loop makes sure).
port=5432
while [ "$port" = 5432 ]; do
  port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
done

name="vouch-test-db-$$"
cleanup() { "$CONTAINER" rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
trap 'exit 130' INT TERM

"$CONTAINER" run -d --rm --name "$name" -e POSTGRES_PASSWORD=throwaway \
  -p "127.0.0.1:$port:5432" docker.io/library/postgres:16 >/dev/null
# Over TCP: the image first runs a socket-only server while it sets the database up, then restarts.
for _ in $(seq 1 60); do
  "$CONTAINER" exec "$name" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break
  "$CONTAINER" inspect "$name" >/dev/null 2>&1 \
    || { echo "test-db: the container $name stopped while Postgres started." >&2; exit 1; }
  sleep 1
done
"$CONTAINER" exec "$name" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 \
  || { echo "test-db: Postgres didn't start within a minute in $name." >&2; exit 1; }

strict=no
if [ $# -eq 0 ]; then
  # The tests CI's database job runs: every one carrying the postgres marker (#259), and as in
  # CI a skipped test fails the run: without the database a test skips, which proves nothing.
  set -- -m postgres -rs
  strict=yes
fi

echo "test-db: postgres:16 on 127.0.0.1:$port ($name): pytest $*"
cd "$ROOT"
out="$(mktemp)"
trap 'rm -f "$out"; cleanup' EXIT
status=0
VOUCH_TEST_DATABASE_URL="postgresql://postgres:throwaway@127.0.0.1:$port/postgres" uv run pytest "$@" \
  | tee "$out" || status=${PIPESTATUS[0]}
if [ "$status" -eq 0 ] && [ "$strict" = yes ] && grep -q SKIPPED "$out"; then
  echo "test-db: a database test was skipped (listed above); CI fails on that too." >&2
  status=1
fi
exit "$status"
