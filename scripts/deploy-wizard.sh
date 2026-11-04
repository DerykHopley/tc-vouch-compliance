#!/usr/bin/env bash
# Deploy Vouch to Google Cloud Run, step by step. DEPLOY.md has the same steps by hand; --help lists them.
# Its plain answers (project id, region, image tag) stay in deploy/cloudrun/wizard.env, git-ignored.
# It never asks for or stores a secret: Vouch needs none until sign-in (M3).

set -euo pipefail

# Each step's name and what it does: --help and the "2/7" counter both come from these.
STEPS=("Sign in" "Project" "Artifact Registry" "Build and push" "Deploy" "URL" "Health check")
WHAT=(
  "gcloud is signed in (or it stops with the sign-in command)"
  "the project id, asked once; the Cloud Run and Artifact Registry APIs on"
  "the image registry \"vouch\" in the region, and docker allowed to push to it"
  "the image from the Dockerfile, tagged v1, v2, ..."
  "Cloud Run service \"vouch\": public, min 0 and max 1 instance, the fake model only"
  "print the service's address"
  "GET /api/health answers {\"status\": \"ok\", \"database\": \"reachable\"} (or it stops with the logs command)"
)

usage() {
  cat <<'EOF'
Deploy Vouch to Google Cloud Run, step by step (DEPLOY.md has the same steps by hand).

  scripts/deploy-wizard.sh             # each step: what it does, the command, then "Run it?"
  scripts/deploy-wizard.sh --dry-run   # print every command; run none, write nothing
  scripts/deploy-wizard.sh --help

It skips what already exists, so it is safe to run again. Its plain answers (project id, region,
image tag) stay in deploy/cloudrun/wizard.env, git-ignored. It never asks for or stores a secret.

The steps:
EOF
  local i
  for i in "${!STEPS[@]}"; do printf '  %s. %-18s %s\n' "$((i + 1))" "${STEPS[i]}" "${WHAT[i]}"; done
}

DRY=no
case "${1:-}" in
  --dry-run) DRY=yes ;;
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) printf 'Unknown option %s (see --help).\n' "$1" >&2; exit 2 ;;
esac

# Only bash builtins up to the first real command, so a dry run works with no gcloud, docker or curl.
HERE="${BASH_SOURCE[0]%/*}"; [[ "$HERE" == "${BASH_SOURCE[0]}" ]] && HERE=.
cd "$HERE/.."
WIZARD_ENV="${WIZARD_ENV:-deploy/cloudrun/wizard.env}"
SERVICE=vouch
REPO=vouch

if [[ -t 1 ]]; then B=$'\e[1m'; D=$'\e[2m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
else B= D= G= Y= R= N=; fi
STEP=0
step() { printf '\n%s▸ %s/%s · %s%s\n' "$B" "$((STEP + 1))" "${#STEPS[@]}" "${STEPS[STEP]}" "$N"; STEP=$((STEP + 1)); }
say()  { printf '  %s\n' "$1"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
stop() { printf '\n  %s✗ %s%s\n\n' "$R" "$1" "$N" >&2; exit 1; }

# quoted cmd...: the command as it runs, quoted so it pastes into a shell
quoted() { local s; s=$(printf '%q ' "$@"); printf '%s' "${s% }"; }
show() { printf '  %s$ %s%s\n' "$D" "$(quoted "$@")" "$N"; }

# saved KEY / save KEY VALUE: the plain answers in $WIZARD_ENV (never a secret). save never runs in a dry run.
saved() {
  local line value=""
  [[ -f "$WIZARD_ENV" ]] || return 0
  while IFS= read -r line; do [[ "$line" == "$1="* ]] && value="${line#*=}"; done < "$WIZARD_ENV"
  printf '%s' "$value"
}
save() {
  mkdir -p "$(dirname "$WIZARD_ENV")"; touch "$WIZARD_ENV"
  local tmp; tmp=$(mktemp)
  grep -vE "^${1}=" "$WIZARD_ENV" > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"; mv "$tmp" "$WIZARD_ENV"
}

# run "what it does" cmd...: shows the command and asks before running it. Sets RAN to yes, no (skipped) or dry.
run() {
  local what="$1"; shift
  printf '\n  %s%s%s\n' "$B" "$what" "$N"; show "$@"
  if [[ $DRY == yes ]]; then say "(dry run: not run)"; RAN=dry; return 0; fi
  local reply=""
  printf '  %s? Run it? [y/N]%s ' "$Y" "$N"; read -r reply || true
  if [[ ! "$reply" =~ ^[Yy] ]]; then say "skipped"; RAN=no; return 0; fi
  "$@" || stop "That failed (above). Fix it, then run scripts/deploy-wizard.sh again: it skips what is done."
  ok "done"; RAN=yes
}

# has cmd...: true when a read-only check finds it. A dry run prints the check and assumes not.
has() {
  if [[ $DRY == yes ]]; then printf '  %scheck:%s' "$D" "$N"; show "$@"; return 1; fi
  "$@" >/dev/null 2>&1
}

# query cmd...: a read-only query's output (empty on failure). A dry run prints it (to fd 3, the screen) and returns nothing.
exec 3>&1
query() {
  if [[ $DRY == yes ]]; then printf '  %scheck:%s' "$D" "$N" >&3; show "$@" >&3; return 0; fi
  "$@" 2>/dev/null || true
}

printf '%sDeploy Vouch to Google Cloud Run%s%s\n' "$B" "$N" "$([[ $DRY == yes ]] && echo '  (dry run: nothing runs)')"
if [[ $DRY == no ]]; then
  for tool in gcloud docker curl; do
    command -v "$tool" >/dev/null 2>&1 || stop "$tool is missing: install it (DEPLOY.md, Before you start), then run this again."
  done
fi

step  # Sign in
ACCOUNT=$(query gcloud auth list --filter=status:ACTIVE --format='value(account)')
if [[ $DRY == no ]]; then
  [[ -n "$ACCOUNT" ]] || stop "gcloud isn't signed in. Run: gcloud auth login   (then this wizard again)"
  ok "signed in as $ACCOUNT"
fi

step  # Project
PROJECT_ID=$(saved PROJECT_ID)
if [[ -z "$PROJECT_ID" ]]; then
  say "The Google Cloud project's id (from #7; it's kept in $WIZARD_ENV, so this is asked once)."
  printf '  %sProject id:%s ' "$B" "$N"; read -r PROJECT_ID || true; [[ -t 0 ]] || printf '\n'
fi
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] \
  || stop "\"$PROJECT_ID\" isn't a project id (6 to 30 lower-case letters, digits and hyphens)."
REGION=$(saved REGION); REGION="${REGION:-europe-west1}"
if [[ $DRY == no ]]; then save PROJECT_ID "$PROJECT_ID"; save REGION "$REGION"; fi
ok "project $PROJECT_ID, region $REGION$([[ $DRY == no ]] && echo " (in $WIZARD_ENV)")"
has gcloud projects describe "$PROJECT_ID" || [[ $DRY == yes ]] \
  || stop "Project $PROJECT_ID isn't there or you can't see it. Create it first (#7, DEPLOY.md), or fix $WIZARD_ENV."
APIS=(run.googleapis.com artifactregistry.googleapis.com)
MISSING=()
ON=$(query gcloud services list --enabled --project="$PROJECT_ID" --format='value(config.name)')
for api in "${APIS[@]}"; do [[ $'\n'"$ON"$'\n' == *$'\n'"$api"$'\n'* ]] || MISSING+=("$api"); done
if (( ${#MISSING[@]} == 0 )); then ok "Cloud Run and Artifact Registry are on"
else run "Turn on the APIs (a minute)" gcloud services enable "${MISSING[@]}" --project="$PROJECT_ID"; fi

step  # Artifact Registry
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE"
if has gcloud artifacts repositories describe "$REPO" --location="$REGION" --project="$PROJECT_ID"; then
  ok "registry $REPO exists"
else
  run "Create the image registry" gcloud artifacts repositories create "$REPO" --repository-format=docker \
    --location="$REGION" --project="$PROJECT_ID"
fi
if has grep -q "$REGION-docker.pkg.dev" "${HOME:-~}/.docker/config.json"; then ok "docker may push there"
else run "Let docker push to the registry" gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet; fi

step  # Build and push
PREV=$(saved TAG); TAG=v1
[[ "$PREV" =~ ^v([0-9]+)$ ]] && TAG="v$(( BASH_REMATCH[1] + 1 ))"
say "The image from the Dockerfile, as $TAG (the last one pushed: ${PREV:-none})."
run "Build the image (a few minutes the first time)" docker build -t "$IMAGE:$TAG" .
PUSHED=no
if [[ $RAN == no ]]; then say "Not built, so not pushed."
else run "Push it" docker push "$IMAGE:$TAG"; PUSHED=$RAN; fi
# Deploy only an image that was really pushed: the new one, else the last one, else nothing.
if [[ $PUSHED == yes ]]; then save TAG "$TAG"
elif [[ $PUSHED == no && -n "$PREV" ]]; then TAG="$PREV"; say "No new image pushed: the deploy uses the last one, $PREV."
elif [[ $PUSHED == no ]]; then stop "No image has been pushed yet, so there is nothing to deploy. Run the wizard again and build and push."
fi

step  # Deploy
say "Public, asleep when unused (min 0), never more than one instance (max 1), on the scripted fake model."
run "Deploy the service" gcloud run deploy "$SERVICE" --image="$IMAGE:$TAG" --region="$REGION" \
  --project="$PROJECT_ID" --allow-unauthenticated --min-instances=0 --max-instances=1 --max=1 --quiet

step  # URL
LOGS=(gcloud logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\"" \
  --project="$PROJECT_ID" --limit=50 --freshness=1h)
URL=$(query gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')
if [[ $DRY == yes ]]; then URL="https://the-service-address"
else [[ -n "$URL" ]] || stop "Cloud Run has no address for $SERVICE: was it deployed? The log: $(quoted "${LOGS[@]}")"; fi
ok "Vouch is at $URL"

step  # Health check
HEALTH=(curl -fsS --max-time 90 "$URL/api/health")
if [[ $DRY == yes ]]; then show "${HEALTH[@]}"
else
  say "The first answer can take 10 to 30 s: it starts the container."
  healthy=no
  for _ in 1 2 3; do
    body=$("${HEALTH[@]}" 2>/dev/null || true)
    # From M3 the live app always has a database (#33): ok without it isn't a working deploy.
    if [[ "$body" =~ \"status\"[[:space:]]*:[[:space:]]*\"ok\" &&
          "$body" =~ \"database\"[[:space:]]*:[[:space:]]*\"reachable\" ]]; then healthy=yes; break; fi
  done
  [[ $healthy == yes ]] || stop "$URL/api/health didn't answer ok with the database reachable. The log says why: $(quoted "${LOGS[@]}")"
  ok "$URL/api/health answers ok, the database reachable"
fi

printf '\n%s%s✓ Done.%s %s\n\n' "$B" "$G" "$N" \
  "$([[ $DRY == yes ]] && echo 'Dry run: nothing ran, nothing written.' || echo "Open $URL")"
