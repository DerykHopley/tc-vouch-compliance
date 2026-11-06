#!/usr/bin/env bash
# Store a secret in the keyring whole, multi-line too, and say only its size (#130).
# `secret-tool store` typed at its prompt keeps only the first line, so a PEM key (Clerk's
# CLERK_JWT_KEY) was once stored as its 26-character header. Pipe the value in instead:
#
#   scripts/store-secret.sh clerk-vouch-jwt-key < jwt-key.pem
#   pbpaste | scripts/store-secret.sh openrouter        # or any command that prints it
#
# It refuses an empty value, and a PEM without its END line; it reads the entry back and checks
# it matches. The value is never printed, only "stored <service>: N characters, M lines".
set -euo pipefail

service=${1:-}
if [ -z "$service" ] || [ $# -ne 1 ]; then
  echo "usage: scripts/store-secret.sh <service> < value   (the keyring entry: service <service>)" >&2
  exit 2
fi

# Typed at a terminal it would show on screen, and stay in its scrollback: piped only.
if [ -t 0 ]; then
  echo "store-secret: pipe the value in (scripts/store-secret.sh $service < file); typed here it" \
    "would show on screen. Nothing was stored." >&2
  exit 2
fi

value=$(mktemp)  # mktemp makes them 0600
back=$(mktemp)
trap 'rm -f "$value" "$back"' EXIT
cat > "$value"

if ! grep -q '[^[:space:]]' "$value"; then
  echo "store-secret: the value for $service is empty; nothing was stored. Pipe it in." >&2
  exit 1
fi
# A PEM, by either of its lines, needs both, BEGIN first: a cut one is refused.
if grep -q -- '-----\(BEGIN\|END\) ' "$value" \
  && ! awk '/-----BEGIN /{ begun = 1 } /-----END / && begun { ended = 1 } END { exit !ended }' "$value"; then
  echo "store-secret: the value for $service is a PEM without both its -----BEGIN and -----END" \
    "lines, in that order; nothing was stored. Pipe the whole file in." >&2
  exit 1
fi

secret-tool store --label="$service" service "$service" < "$value"
secret-tool lookup service "$service" > "$back" || true
# Exactly: piped in, secret-tool stores stdin whole to EOF, newlines too, and lookup adds none
# off a terminal (secret-tool(1), libsecret 0.21).
if ! cmp -s "$value" "$back"; then
  echo "store-secret: $service reads back different from what was given; check the keyring." >&2
  exit 1
fi

chars=$(wc -m < "$value" | tr -d ' ')
lines=$(awk 'END { print NR }' "$value")
echo "stored $service: $chars characters, $lines lines"
