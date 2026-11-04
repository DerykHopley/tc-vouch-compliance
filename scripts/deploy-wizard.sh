#!/usr/bin/env bash
# Deploy Vouch to Google Cloud Run, step by step. DEPLOY.md has the same steps by hand; --help lists them.
# Its plain answers (project id, region, image tag) stay in deploy/cloudrun/wizard.env, git-ignored.
# It never shows or stores a secret: each goes from the keyring to Secret Manager through a pipe
# (secret-tool lookup ... | gcloud secrets versions add ... --data-file=-), never an argument or a file.

set -euo pipefail

# Each step's name and what it does: --help and the "2/11" counter both come from these.
STEPS=("Sign in" "Keyring" "Project" "Artifact Registry" "Build and push" "Secret Manager" "Access"
  "Database" "Deploy" "URL" "Health check")
WHAT=(
  "gcloud is signed in (or it stops with the sign-in command)"
  "every keyring entry the deploy needs is there (or it stops, naming them, before anything changes)"
  "the project id, asked once; the Cloud Run, Artifact Registry, Secret Manager and Cloud SQL Admin APIs on"
  "the image registry \"vouch\" in the region, and docker allowed to push to it"
  "the image from the Dockerfile, tagged v1, v2, ..."
  "a secret each for the four secret settings, a new version only when the keyring's value changed"
  "the service's account may read those secrets (Secret Accessor) and reach Cloud SQL (Cloud SQL Client)"
  "you confirm vouch-db-setup has run against the instance (it says how)"
  "Cloud Run service \"vouch\": public, min 0 and max 1 instance, the secrets and the Cloud SQL instance"
  "print the service's address"
  "GET /api/health answers {\"status\": \"ok\", \"database\": \"reachable\"} (or it stops with the logs command)"
)

# The secret settings: env var (also the secret's name in Secret Manager) and its keyring entry.
SECRET_ENVS=(CLERK_SECRET_KEY CLERK_JWT_KEY OPENROUTER_API_KEY VOUCH_DATABASE_URL)
SECRET_ENTRIES=(clerk-vouch-secret clerk-vouch-jwt-key openrouter cloudsql-vouch-run-url)
# Not a secret (the browser gets it), so a plain env var; it still comes from the keyring.
PUBLISHABLE_ENTRY=clerk-vouch-publishable

usage() {
  cat <<'EOF'
Deploy Vouch to Google Cloud Run, step by step (DEPLOY.md has the same steps by hand).

  scripts/deploy-wizard.sh             # each step: what it does, the command, then "Run it?"
  scripts/deploy-wizard.sh --dry-run   # print every command; run none, write nothing
  scripts/deploy-wizard.sh --help

It skips what already exists, so it is safe to run again. Its plain answers (project id, region,
image tag) stay in deploy/cloudrun/wizard.env, git-ignored. Secrets go from the keyring to Secret
Manager through a pipe: never shown, never an argument, never in a file.

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
SQL_NAME=vouch-db

if [[ -t 1 ]]; then B=$'\e[1m'; D=$'\e[2m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
else B= D= G= Y= R= N=; fi
STEP=0
step() { printf '\n%s▸ %s/%s · %s%s\n' "$B" "$((STEP + 1))" "${#STEPS[@]}" "${STEPS[STEP]}" "$N"; STEP=$((STEP + 1)); }
say()  { printf '  %s\n' "$1"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
stop() { printf '\n  %s✗ %s%s\n\n' "$R" "$1" "$N" >&2; exit 1; }
failed() {
  stop "Step $STEP/${#STEPS[@]}, ${STEPS[STEP - 1]}, failed (above). Fix it, then run scripts/deploy-wizard.sh again: it skips what is done."
}

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

# confirm "what it does" "the command as shown": prints both and asks. True to go ahead; otherwise
# it sets RAN to no (skipped) or dry.
confirm() {
  printf '\n  %s%s%s\n' "$B" "$1" "$N"; printf '  %s$ %s%s\n' "$D" "$2" "$N"
  if [[ $DRY == yes ]]; then say "(dry run: not run)"; RAN=dry; return 1; fi
  local reply=""
  printf '  %s? Run it? [y/N]%s ' "$Y" "$N"; read -r reply || true
  if [[ ! "$reply" =~ ^[Yy] ]]; then say "skipped"; RAN=no; return 1; fi
}

# run "what it does" cmd...: shows the command and asks before running it. Sets RAN to yes, no (skipped) or dry.
run() {
  local what="$1"; shift
  confirm "$what" "$(quoted "$@")" || return 0
  "$@" || failed
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

# in_keyring ENTRY: true when the keyring holds a value for it. Only its length leaves the pipe.
in_keyring() {
  local bytes
  bytes=$(secret-tool lookup service "$1" 2>/dev/null | wc -c) || true
  (( ${bytes:-0} > 0 ))
}

# unchanged ENTRY SECRET: true when the secret's latest version holds the keyring entry's value.
# Both values go only through pipes into sha256sum: the wizard compares digests, never the values.
unchanged() {
  local mine theirs
  mine=$(secret-tool lookup service "$1" 2>/dev/null | sha256sum) || return 1
  theirs=$(gcloud secrets versions access latest --secret="$2" --project="$PROJECT_ID" 2>/dev/null \
    | sha256sum) || return 1
  [[ "$mine" == "$theirs" ]]
}

printf '%sDeploy Vouch to Google Cloud Run%s%s\n' "$B" "$N" "$([[ $DRY == yes ]] && echo '  (dry run: nothing runs)')"
if [[ $DRY == no ]]; then
  for tool in gcloud docker curl secret-tool; do
    command -v "$tool" >/dev/null 2>&1 || stop "$tool is missing: install it (DEPLOY.md, Before you start), then run this again."
  done
fi

step  # Sign in
ACCOUNT=$(query gcloud auth list --filter=status:ACTIVE --format='value(account)')
if [[ $DRY == no ]]; then
  [[ -n "$ACCOUNT" ]] || stop "gcloud isn't signed in. Run: gcloud auth login   (then this wizard again)"
  ok "signed in as $ACCOUNT"
fi

step  # Keyring: before anything changes, so a missing entry stops a run that has done nothing.
if [[ $DRY == yes ]]; then
  for entry in "${SECRET_ENTRIES[@]}" "$PUBLISHABLE_ENTRY"; do
    printf '  %scheck:%s' "$D" "$N"; printf '  %s$ secret-tool lookup service %s | wc -c%s\n' "$D" "$entry" "$N"
  done
  PUBLISHABLE="<from $PUBLISHABLE_ENTRY>"
else
  MISSING=()
  for entry in "${SECRET_ENTRIES[@]}" "$PUBLISHABLE_ENTRY"; do in_keyring "$entry" || MISSING+=("$entry"); done
  (( ${#MISSING[@]} == 0 )) || stop "The keyring has no ${MISSING[*]}. Nothing has changed. Store each with
    secret-tool store --label=<entry> service <entry>   (it reads the value; #28, #30), then run this again."
  PUBLISHABLE=$(secret-tool lookup service "$PUBLISHABLE_ENTRY")
  ok "the keyring has all ${#SECRET_ENTRIES[@]} secrets and the publishable key"
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
APIS=(run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com sqladmin.googleapis.com)
MISSING=()
ON=$(query gcloud services list --enabled --project="$PROJECT_ID" --format='value(config.name)')
for api in "${APIS[@]}"; do [[ $'\n'"$ON"$'\n' == *$'\n'"$api"$'\n'* ]] || MISSING+=("$api"); done
if (( ${#MISSING[@]} == 0 )); then ok "Cloud Run, Artifact Registry, Secret Manager and Cloud SQL Admin are on"
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

step  # Secret Manager
say "Each value goes from the keyring to Secret Manager through a pipe: it is never shown."
for i in "${!SECRET_ENVS[@]}"; do
  secret="${SECRET_ENVS[i]}"; entry="${SECRET_ENTRIES[i]}"
  if has gcloud secrets describe "$secret" --project="$PROJECT_ID"; then ok "secret $secret exists"
  else
    run "Create the secret $secret (empty until the next command)" \
      gcloud secrets create "$secret" --replication-policy=automatic --project="$PROJECT_ID"
    [[ $RAN == no ]] && { say "Not created, so $entry's value isn't put in."; continue; }
  fi
  if [[ $DRY == no ]] && unchanged "$entry" "$secret"; then ok "$secret already holds $entry's value"; continue; fi
  add=(gcloud secrets versions add "$secret" --data-file=- --project="$PROJECT_ID")
  if confirm "Put $entry's value in $secret (a new version)" "secret-tool lookup service $entry | $(quoted "${add[@]}")"; then
    secret-tool lookup service "$entry" | "${add[@]}" || failed
    ok "done"; RAN=yes
  fi
done

step  # Access
PROJECT_NUMBER=$(query gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
[[ $DRY == yes ]] && PROJECT_NUMBER=PROJECT_NUMBER
[[ -n "$PROJECT_NUMBER" ]] || stop "gcloud didn't give project $PROJECT_ID's number. Run this again; if it repeats, check: gcloud projects describe $PROJECT_ID"
ACCOUNT_NOW=$(query gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" \
  --format='value(spec.template.spec.serviceAccountName)')
# A service with no account of its own runs as Compute Engine's default one (Cloud Run's service identity page).
RUN_AS="${ACCOUNT_NOW:-$PROJECT_NUMBER-compute@developer.gserviceaccount.com}"
say "The service runs as $RUN_AS. Granting a role it already has changes nothing."
for secret in "${SECRET_ENVS[@]}"; do
  run "Let it read $secret (Secret Manager Secret Accessor, on this secret only)" \
    gcloud secrets add-iam-policy-binding "$secret" --member="serviceAccount:$RUN_AS" \
    --role=roles/secretmanager.secretAccessor --project="$PROJECT_ID" --quiet
done
run "Let it connect to Cloud SQL (Cloud SQL Client, on the project)" \
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$RUN_AS" \
  --role=roles/cloudsql.client --condition=None --quiet

step  # Database
SQL_INSTANCE=$(query gcloud sql instances describe "$SQL_NAME" --project="$PROJECT_ID" --format='value(connectionName)')
[[ $DRY == yes ]] && SQL_INSTANCE="$PROJECT_ID:$REGION:$SQL_NAME"
[[ -n "$SQL_INSTANCE" ]] || stop "Project $PROJECT_ID has no Cloud SQL instance $SQL_NAME (#29), or you can't see it: gcloud sql instances list --project=$PROJECT_ID"
ok "Cloud SQL instance $SQL_INSTANCE"
say "The app needs vouch-db-setup to have run once against it (and after a LangGraph upgrade)."
say "From your machine, through the Cloud SQL Auth Proxy (DEPLOY.md, The database):"
printf '    %-52s # terminal 1: leave it running\n' "cloud-sql-proxy $SQL_INSTANCE --port 5432"
printf '    %-52s # terminal 2: .envrc gives VOUCH_DATABASE_URL\n' "uv run vouch-db-setup"
if [[ $DRY == no ]]; then
  reply=""
  printf '  %s? Has vouch-db-setup run against %s? [y/N]%s ' "$Y" "$SQL_INSTANCE" "$N"; read -r reply || true
  [[ "$reply" =~ ^[Yy] ]] || stop "Run vouch-db-setup (above) first, then this wizard again: it skips what is done."
fi

step  # Deploy
# Sign-in accepts session tokens only from the service's own address: the one it has, or for a new
# service the address Cloud Run will give it (https://SERVICE-PROJECT_NUMBER.REGION.run.app).
URL_NOW=$(query gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')
PARTIES="${URL_NOW:-https://$SERVICE-$PROJECT_NUMBER.$REGION.run.app}"
SECRETS_ARG=""
for secret in "${SECRET_ENVS[@]}"; do SECRETS_ARG+="${SECRETS_ARG:+,}$secret=$secret:latest"; done
say "Public, asleep when unused (min 0), never more than one instance (max 1), on OpenRouter, signed in"
say "through Clerk. The secrets come from Secret Manager as env vars; sign-in accepts $PARTIES."
run "Deploy the service" gcloud run deploy "$SERVICE" --image="$IMAGE:$TAG" --region="$REGION" \
  --project="$PROJECT_ID" --allow-unauthenticated --min-instances=0 --max-instances=1 --max=1 \
  --set-secrets="$SECRETS_ARG" \
  --set-env-vars="CLERK_PUBLISHABLE_KEY=$PUBLISHABLE,CLERK_AUTHORIZED_PARTIES=$PARTIES" \
  --add-cloudsql-instances="$SQL_INSTANCE" --quiet

step  # URL
LOGS=(gcloud logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\"" \
  --project="$PROJECT_ID" --limit=50 --freshness=1h)
URL=$(query gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')
if [[ $DRY == yes ]]; then URL="https://the-service-address"
else
  [[ -n "$URL" ]] || stop "Cloud Run has no address for $SERVICE: was it deployed? The log: $(quoted "${LOGS[@]}")"
  [[ "$URL" == "$PARTIES" ]] || stop "Cloud Run gave the service $URL, but sign-in was set to accept $PARTIES. Run scripts/deploy-wizard.sh again: it uses the service's own address."
fi
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
