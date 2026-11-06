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

value=$(mktemp)
back=$(mktemp)
trap 'rm -f "$value" "$back"' EXIT
chmod 600 "$value" "$back"
cat > "$value"

if ! grep -q '[^[:space:]]' "$value"; then
  echo "store-secret: the value for $service is empty; nothing was stored. Pipe it in." >&2
  exit 1
fi
if grep -q -- '-----BEGIN ' "$value" && ! grep -q -- '-----END ' "$value"; then
  echo "store-secret: the value for $service starts a PEM (-----BEGIN) but has no -----END line;" \
    "nothing was stored. Pipe the whole file in." >&2
  exit 1
fi

secret-tool store --label="$service" service "$service" < "$value"
secret-tool lookup service "$service" > "$back" || true
# Trailing newlines aside: whether the keyring keeps a final newline isn't the value's content.
if [ "$(cat "$value")" != "$(cat "$back")" ]; then
  echo "store-secret: $service reads back different from what was given; check the keyring." >&2
  exit 1
fi

chars=$(wc -m < "$value" | tr -d ' ')
lines=$(awk 'END { print NR }' "$value")
echo "stored $service: $chars characters, $lines lines"
