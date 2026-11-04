#!/usr/bin/env bash
# Deploy Vouch to Google Cloud Run, step by step (DEPLOY.md has the same steps by hand).
#
#   scripts/deploy-wizard.sh             # each step: what it does, the command, then "Run it?"
#   scripts/deploy-wizard.sh --dry-run   # print every command; run none (no gcloud, docker or curl)
#   scripts/deploy-wizard.sh --help
#
# The steps:
#   1. Sign in        gcloud is signed in (or it stops with the sign-in command)
#   2. Project        the project id, asked once; the Cloud Run and Artifact Registry APIs on
#   3. Artifact Registry   the image registry "vouch" in the region, and docker allowed to push to it
#   4. Build and push the image from the Dockerfile, tagged v1, v2, ...
#   5. Deploy         Cloud Run service "vouch": public, min 0 and max 1 instance, the fake model only
#   6. URL            print the service's address
#   7. Health check   GET /api/health answers ok (or it stops with the logs command)
#
# It skips what already exists, so it is safe to run again. Its plain answers (project id, region,
# image tag) stay in deploy/cloudrun/wizard.env, git-ignored. It never asks for or stores a secret:
# Vouch needs none until sign-in (M3).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
WIZARD_ENV="${WIZARD_ENV:-deploy/cloudrun/wizard.env}"
SERVICE=vouch
REPO=vouch
DRY=no

case "${1:-}" in
  --dry-run) DRY=yes ;;
  -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) printf 'Unknown option %s (see --help).\n' "$1"; exit 2 ;;
esac

if [[ -t 1 ]]; then B=$'\e[1m'; D=$'\e[2m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
else B= D= G= Y= R= N=; fi
STEP=0
step() { STEP=$((STEP + 1)); printf '\n%s▸ %s/7 · %s%s\n' "$B" "$STEP" "$1" "$N"; }
say()  { printf '  %s\n' "$1"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
stop() { printf '\n  %s✗ %s%s\n\n' "$R" "$1" "$N"; exit 1; }

# saved KEY / save KEY VALUE: the plain answers in $WIZARD_ENV (never a secret)
saved() { grep -E "^${1}=" "$WIZARD_ENV" 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
save() {
  mkdir -p "$(dirname "$WIZARD_ENV")"; touch "$WIZARD_ENV"
  local tmp; tmp=$(mktemp)
  grep -vE "^${1}=" "$WIZARD_ENV" > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"; mv "$tmp" "$WIZARD_ENV"
}

# run "what it does" cmd...: shows the command and asks before running it; a dry run only prints it
run() {
  local what="$1"; shift
  printf '\n  %s%s%s\n  %s$ %s%s\n' "$B" "$what" "$N" "$D" "$*" "$N"
  if [[ $DRY == yes ]]; then say "(dry run: not run)"; return 0; fi
  local reply=""
  printf '  %s? Run it? [y/N]%s ' "$Y" "$N"; read -r reply || true
  [[ "$reply" =~ ^[Yy] ]] || { say "skipped"; return 0; }
  "$@" || stop "That failed (above). Fix it, then run scripts/deploy-wizard.sh again: it skips what is done."
  ok "done"
}

# has "what" cmd...: true when a read-only check finds it. A dry run prints the check and assumes not.
has() {
  local what="$1"; shift
  if [[ $DRY == yes ]]; then printf '  %scheck %s: $ %s%s\n' "$D" "$what" "$*" "$N"; return 1; fi
  "$@" >/dev/null 2>&1
}

printf '%sDeploy Vouch to Google Cloud Run%s%s\n' "$B" "$N" "$([[ $DRY == yes ]] && echo '  (dry run: nothing runs)')"
for tool in gcloud docker curl; do
  command -v "$tool" >/dev/null 2>&1 || stop "$tool is missing: install it (DEPLOY.md, Before you start), then run this again."
done

# ── 1 ──
step "Sign in"
if [[ $DRY == yes ]]; then
  printf '  %scheck signed in: $ gcloud auth list --filter=status:ACTIVE --format=value(account)%s\n' "$D" "$N"
else
  ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)
  [[ -n "$ACCOUNT" ]] || stop "gcloud isn't signed in. Run: gcloud auth login   (then this wizard again)"
  ok "signed in as $ACCOUNT"
fi

# ── 2 ──
step "Project"
PROJECT_ID=$(saved PROJECT_ID)
if [[ -z "$PROJECT_ID" ]]; then
  say "The Google Cloud project's id (from #7; it's kept in $WIZARD_ENV, so this is asked once)."
  printf '  %sProject id:%s ' "$B" "$N"; read -r PROJECT_ID || true
fi
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] \
  || stop "\"$PROJECT_ID\" isn't a project id (6 to 30 lower-case letters, digits and hyphens)."
REGION=$(saved REGION); REGION="${REGION:-europe-west1}"
save PROJECT_ID "$PROJECT_ID"; save REGION "$REGION"
ok "project $PROJECT_ID, region $REGION (in $WIZARD_ENV)"
has "the project" gcloud projects describe "$PROJECT_ID" || [[ $DRY == yes ]] \
  || stop "Project $PROJECT_ID isn't there or you can't see it. Create it first (#7, DEPLOY.md), or fix $WIZARD_ENV."
APIS="run.googleapis.com artifactregistry.googleapis.com"
if [[ $DRY == yes ]]; then
  has "the APIs" gcloud services list --enabled --project="$PROJECT_ID" || MISSING=" $APIS"
else
  ON=$(gcloud services list --enabled --project="$PROJECT_ID" --format='value(config.name)' 2>/dev/null || true)
  MISSING=""; for api in $APIS; do grep -qx "$api" <<<"$ON" || MISSING+=" $api"; done
fi
if [[ -z "$MISSING" ]]; then ok "Cloud Run and Artifact Registry are on"
else
  # shellcheck disable=SC2086
  run "Turn on the APIs (a minute)" gcloud services enable $MISSING --project="$PROJECT_ID"
fi

# ── 3 ──
step "Artifact Registry"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE"
if has "the registry" gcloud artifacts repositories describe "$REPO" --location="$REGION" --project="$PROJECT_ID"; then
  ok "registry $REPO exists"
else
  run "Create the image registry" gcloud artifacts repositories create "$REPO" --repository-format=docker \
    --location="$REGION" --project="$PROJECT_ID"
fi
if has "docker may push" grep -q "$REGION-docker.pkg.dev" "$HOME/.docker/config.json"; then ok "docker may push there"
else run "Let docker push to the registry" gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet; fi

# ── 4 ──
step "Build and push"
PREV=$(saved TAG); TAG=v1
[[ "$PREV" =~ ^v([0-9]+)$ ]] && TAG="v$(( BASH_REMATCH[1] + 1 ))"
say "The image from the Dockerfile, as $TAG (the last one pushed: ${PREV:-none})."
run "Build the image (a few minutes the first time)" docker build -t "$IMAGE:$TAG" .
run "Push it" docker push "$IMAGE:$TAG"
[[ $DRY == yes ]] || save TAG "$TAG"

# ── 5 ──
step "Deploy"
say "Public, asleep when unused (min 0), never more than one instance (max 1), on the scripted fake model."
run "Deploy the service" gcloud run deploy "$SERVICE" --image="$IMAGE:$TAG" --region="$REGION" \
  --project="$PROJECT_ID" --allow-unauthenticated --min-instances=0 --max-instances=1 --quiet

# ── 6 ──
step "URL"
LOGS="gcloud logging read 'resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\"' --project=$PROJECT_ID --limit=50 --freshness=1h"
if [[ $DRY == yes ]]; then
  printf '  %s$ gcloud run services describe %s --region=%s --project=%s --format=value(status.url)%s\n' \
    "$D" "$SERVICE" "$REGION" "$PROJECT_ID" "$N"
  URL="https://<the service's address>"
else
  URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" \
          --format='value(status.url)' 2>/dev/null || true)
  [[ -n "$URL" ]] || stop "Cloud Run has no address for $SERVICE: was it deployed? The log: $LOGS"
fi
ok "Vouch is at $URL"

# ── 7 ──
step "Health check"
if [[ $DRY == yes ]]; then
  printf '  %s$ curl -fsS --max-time 90 %s/api/health%s\n' "$D" "$URL" "$N"
else
  say "The first answer can take 10 to 30 s: it starts the container."
  healthy=no
  for _ in 1 2 3; do
    if curl -fsS --max-time 90 "$URL/api/health" 2>/dev/null | grep -q '"ok"'; then healthy=yes; break; fi
  done
  [[ $healthy == yes ]] || stop "$URL/api/health didn't answer ok. The log says why: $LOGS"
  ok "$URL/api/health answers ok"
fi

printf '\n%s%s✓ Done.%s %s\n\n' "$B" "$G" "$N" \
  "$([[ $DRY == yes ]] && echo 'Dry run: nothing ran.' || echo "Open $URL")"
