#!/usr/bin/env bash
# Deploy Vouch to Google Cloud Run, step by step. DEPLOY.md has the same steps by hand; --help lists them.
# Its plain answers (project id, region) stay in deploy/cloudrun/wizard.env, git-ignored.
# It never shows or stores a secret: each goes from the keyring to Secret Manager through a pipe
# (secret-tool lookup ... | gcloud secrets versions add ... --data-file=-), never an argument or a file.

set -euo pipefail

# Each step's name and what it does: --help and the "2/11" counter both come from these.
STEPS=("Sign in" "Keyring" "Project" "Artifact Registry" "Build and push" "Secret Manager" "Access"
  "Database" "Deploy" "URL" "Health check")
WHAT=(
  "gcloud is signed in (or it stops with the sign-in command)"
  "every keyring entry the deploy needs holds a usable value (or it stops, naming them, before anything changes)"
  "the project id, asked once; the Cloud Run, Artifact Registry, Secret Manager and Cloud SQL Admin APIs on"
  "the image registry \"vouch\" in the region, and docker allowed to push to it"
  "the image from the Dockerfile, tagged one past the registry's highest vN (v1 when it has none)"
  "a secret each for the four secret settings, a new version only when the keyring's value changed"
  "the service's account may read those secrets (Secret Accessor) and reach Cloud SQL (Cloud SQL Client)"
  "with --db-ready, go on; without, print how to run vouch-db-setup and stop"
  "Cloud Run service \"vouch\": public, min 0 and max 1 instance, each secret's version pinned, the Cloud SQL instance"
  "print the service's address"
  "GET /api/health answers {\"status\": \"ok\", \"database\": \"reachable\"} (or it stops: needs-setup with the vouch-db-setup commands, else the logs command)"
)

# The secret settings, as ENV:entry: the env var (also the secret's name in Secret Manager) and the
# keyring entry its value comes from.
SECRETS=(CLERK_SECRET_KEY:clerk-vouch-secret CLERK_JWT_KEY:clerk-vouch-jwt-key
  OPENROUTER_API_KEY:openrouter VOUCH_DATABASE_URL:cloudsql-vouch-run-url)
# Not a secret (the browser gets it), so a plain env var; it still comes from the keyring.
PUBLISHABLE_ENTRY=clerk-vouch-publishable
PUBLISHABLE_FORM='^pk_(test|live)_[A-Za-z0-9+/=_$-]+$'

# gcloud's HTTP log would hold a secret's payload, on the screen or in ~/.config/gcloud/logs. This
# env var beats a `gcloud config set core/log_http true` (cloud.google.com/sdk/docs/properties).
export CLOUDSDK_CORE_LOG_HTTP=false
# Debug verbosity can print request bodies too; this beats `core/verbosity debug` the same way.
export CLOUDSDK_CORE_VERBOSITY=warning

usage() {
  cat <<'EOF'
Deploy Vouch to Google Cloud Run, step by step (DEPLOY.md has the same steps by hand).

  scripts/deploy-wizard.sh             # each step: what it does, the command, then "Run it?"
  scripts/deploy-wizard.sh --db-ready  # the same, once vouch-db-setup has run against the instance
  scripts/deploy-wizard.sh --dry-run   # print every command; run only the tag list; write nothing
  scripts/deploy-wizard.sh --help

Without --db-ready it stops at the Database step and prints how to run vouch-db-setup.
It skips what already exists, so it is safe to run again. Its plain answers (project id, region)
stay in deploy/cloudrun/wizard.env, git-ignored; the image tag comes from the registry. Secrets go
from the keyring to Secret Manager through a pipe: never shown, never an argument, never in a file.

The steps:
EOF
  local i
  for i in "${!STEPS[@]}"; do printf '  %s. %-18s %s\n' "$((i + 1))" "${STEPS[i]}" "${WHAT[i]}"; done
}

DRY=no
DB_READY=no
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=yes ;;
    --db-ready) DB_READY=yes ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option %s (see --help).\n' "$arg" >&2; exit 2 ;;
  esac
done

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

# saved KEY / save KEY VALUE / forget KEY: the plain answers in $WIZARD_ENV (never a secret). save never runs in a dry run.
saved() {
  local line value=""
  [[ -f "$WIZARD_ENV" ]] || return 0
  while IFS= read -r line; do [[ "$line" == "$1="* ]] && value="${line#*=}"; done < "$WIZARD_ENV"
  printf '%s' "$value"
}
forget() {
  local tmp; tmp=$(mktemp)
  grep -vE "^${1}=" "$WIZARD_ENV" > "$tmp" || true
  mv "$tmp" "$WIZARD_ENV"
}
save() {
  mkdir -p "$(dirname "$WIZARD_ENV")"; touch "$WIZARD_ENV"
  forget "$1"; printf '%s=%s\n' "$1" "$2" >> "$WIZARD_ENV"
}

# confirm "what it does" "the command as shown": prints both and asks. True to go ahead; otherwise
# it sets RAN to no (skipped) or dry. The wizard's one yes/no prompt.
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

# service_field FIELD: one field of the Cloud Run service (empty when there is no service yet).
service_field() {
  query gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --format="value($1)"
}

# keyring_holds ENTRY: true when the keyring's value for ENTRY looks usable: something besides
# whitespace; for the JWT key, a line starting "-----BEGIN PUBLIC KEY-----" (a PEM on several lines
# or on one, as the app reads either); for the publishable key, one line of pk_test_ or pk_live_.
# The value stays in the pipe: awk prints only yes or no.
keyring_holds() {
  local kind=secret verdict
  [[ $1 == clerk-vouch-jwt-key ]] && kind=pem
  [[ $1 == "$PUBLISHABLE_ENTRY" ]] && kind=publishable
  verdict=$(secret-tool lookup service "$1" 2>/dev/null | awk -v kind="$kind" -v form="$PUBLISHABLE_FORM" '
    { sub(/\r$/, "") }
    /[^[:space:]]/ { any = 1 }
    index($0, "-----BEGIN PUBLIC KEY-----") == 1 { pem = 1 }  # one line or several (#125)
    NR == 1 && $0 ~ form { pk = 1 }
    END {
      ok = any
      if (kind == "pem") ok = any && pem
      if (kind == "publishable") ok = pk && NR == 1
      print (ok ? "yes" : "no")
    }') || verdict=no
  [[ $verdict == yes ]]
}

# secret_matches_keyring ENTRY SECRET VERSION: true when that version holds the keyring entry's value.
# Both values go only through pipes into sha256sum: the wizard compares digests, never the values.
secret_matches_keyring() {
  local mine theirs
  mine=$(secret-tool lookup service "$1" 2>/dev/null | sha256sum) || return 1
  theirs=$(gcloud secrets versions access "$3" --secret="$2" --project="$PROJECT_ID" 2>/dev/null \
    | sha256sum) || return 1
  [[ "$mine" == "$theirs" ]]
}

# version_number NAME: the number at the end of a version's name (projects/.../versions/3), or nothing.
version_number() { [[ $1 =~ /versions/([0-9]+)$ ]] && printf '%s' "${BASH_REMATCH[1]}"; return 0; }

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
ENTRIES=()
for pair in "${SECRETS[@]}"; do ENTRIES+=("${pair#*:}"); done
ENTRIES+=("$PUBLISHABLE_ENTRY")
if [[ $DRY == yes ]]; then
  for entry in "${ENTRIES[@]}"; do printf '  %scheck:%s' "$D" "$N"; show secret-tool lookup service "$entry"; done
  PUBLISHABLE="<from $PUBLISHABLE_ENTRY>"
else
  BAD=()
  for entry in "${ENTRIES[@]}"; do keyring_holds "$entry" || BAD+=("$entry"); done
  (( ${#BAD[@]} == 0 )) || stop "The keyring has nothing usable under ${BAD[*]}. Nothing has changed.
    Each needs a value; clerk-vouch-jwt-key a PEM starting -----BEGIN PUBLIC KEY----- (on one line
    or several); clerk-vouch-publishable one pk_test_ or pk_live_ key. Store each with
    scripts/store-secret.sh <entry> < <file> (it stores the value whole, multi-line too, and says
    only its size; #130), then run this again."
  PUBLISHABLE=$(secret-tool lookup service "$PUBLISHABLE_ENTRY")
  [[ $PUBLISHABLE =~ $PUBLISHABLE_FORM ]] \
    || stop "$PUBLISHABLE_ENTRY changed since the check and isn't a pk_test_ or pk_live_ key now (not shown). Run this again."
  ok "the keyring has all ${#SECRETS[@]} secrets and the publishable key"
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
# REGION goes into the sign-in address (#178), so it must be a region's name and nothing more (#192).
[[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] \
  || stop "REGION in $WIZARD_ENV is \"$REGION\", which isn't a Cloud Run region (such as europe-west1). Fix it, then run this again."
# The course's Discord server (#152), set by hand in $WIZARD_ENV (#153): its members may sign in.
DISCORD_SERVER_ID=$(saved DISCORD_SERVER_ID)
[[ -z "$DISCORD_SERVER_ID" || "$DISCORD_SERVER_ID" =~ ^[0-9]{17,20}$ ]] \
  || stop "DISCORD_SERVER_ID in $WIZARD_ENV isn't a Discord server id (17 to 20 digits: Discord's Developer Mode, Copy Server ID)."
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
# The tags come from the registry, never from a file in this checkout, so two checkouts can't hand
# out the same tag (#135). The read runs in a dry run too, so it names the real tag; with no gcloud
# there, the dry run says v1 and that the registry wasn't read.
# Not `query`: it would hide the error that tells "not there yet" from "can't read".
LAST=""; TAG=v1; REGISTRY_READ=yes
LIST_TAGS=(gcloud artifacts docker tags list "$IMAGE" --format='value(tag)')
if [[ $DRY == no ]] || command -v gcloud >/dev/null 2>&1; then
  show "${LIST_TAGS[@]}"
  if LISTED=$("${LIST_TAGS[@]}" 2>&1); then :
  elif [[ "$LISTED" == *NOT_FOUND* ]]; then LISTED=""  # the image isn't in the registry yet
  elif [[ $DRY == yes ]]; then LISTED=""; REGISTRY_READ=no
  else stop "Couldn't read the registry's tags for $IMAGE (above: ${LISTED##*$'\n'}). Nothing was built: run this again."
  fi
  while IFS= read -r listed; do
    [[ "${listed##*/}" =~ ^v([0-9]+)$ ]] || continue
    (( 10#${BASH_REMATCH[1]} > 10#${LAST:-0} )) && LAST=$((10#${BASH_REMATCH[1]}))
  done <<< "$LISTED"
else REGISTRY_READ=no
fi
[[ -n "$LAST" ]] && TAG="v$((LAST + 1))" && LAST="v$LAST"
if [[ $REGISTRY_READ == yes ]]; then say "The image from the Dockerfile, as $TAG (the registry's highest: ${LAST:-none})."
else say "The image from the Dockerfile, as $TAG (the registry wasn't read: gcloud can't reach it here)."; fi
[[ $DRY == no && -n "$(saved TAG)" ]] && forget TAG  # the tag lived in wizard.env before #135
run "Build the image (a few minutes the first time)" docker build -t "$IMAGE:$TAG" .
PUSHED=no
if [[ $RAN == no ]]; then say "Not built, so not pushed."
else run "Push it" docker push "$IMAGE:$TAG"; PUSHED=$RAN; fi
# Deploy only an image that was really pushed: the new one, else the registry's highest, else nothing.
if [[ $PUSHED == no && -n "$LAST" ]]; then TAG="$LAST"; say "No new image pushed: the deploy uses the registry's highest, $LAST."
elif [[ $PUSHED == no ]]; then stop "No image has been pushed yet, so there is nothing to deploy. Run the wizard again and build and push."
fi

step  # Secret Manager
say "Each value goes from the keyring to Secret Manager through a pipe: it is never shown."
say "The deploy pins the version each secret ends with, so a new value reaches the app only through it."
PINNED=()
for pair in "${SECRETS[@]}"; do
  secret="${pair%%:*}"; entry="${pair#*:}"; version=""
  if [[ $DRY == yes ]]; then has gcloud secrets describe "$secret" --project="$PROJECT_ID" || true; found=no
  elif err=$(gcloud secrets describe "$secret" --project="$PROJECT_ID" 2>&1 >/dev/null); then found=yes
  elif [[ $err == *NOT_FOUND* ]]; then found=no
  else
    stop "Couldn't check the secret $secret: ${err%%$'\n'*}
    If Secret Manager's API was just turned on, wait a minute and run scripts/deploy-wizard.sh again."
  fi
  if [[ $found == no ]]; then
    run "Create the secret $secret (empty until the next command)" \
      gcloud secrets create "$secret" --replication-policy=automatic --project="$PROJECT_ID"
    [[ $RAN == no ]] && stop "$secret wasn't created, and the deploy needs it. Run the wizard again and answer y."
  else
    version=$(version_number "$(query gcloud secrets versions describe latest --secret="$secret" \
      --project="$PROJECT_ID" --format='value(name)')")
    if [[ -n $version ]] && secret_matches_keyring "$entry" "$secret" "$version"; then
      ok "$secret version $version already holds $entry's value"; PINNED+=("$secret=$secret:$version"); continue
    fi
    version=""
  fi
  add=(gcloud secrets versions add "$secret" --data-file=- --project="$PROJECT_ID" --format='value(name)')
  if confirm "Put $entry's value in $secret (a new version)" "secret-tool lookup service $entry | $(quoted "${add[@]}")"; then
    # Checked again after the prompt, right before the add: the entry may have changed meanwhile.
    if [[ $DRY == no ]]; then
      keyring_holds "$entry" || stop "The keyring's $entry is empty or unreadable now (locked?). Nothing was added to $secret. Unlock or fix it, then run this again."
    fi
    if ! added=$(secret-tool lookup service "$entry" | "${add[@]}"); then
      # The pipe broke: a version made from it may hold part of the value, or nothing. Disable it.
      bad=$(version_number "$added")
      [[ -n $bad ]] || failed
      gcloud secrets versions disable "$bad" --secret="$secret" --project="$PROJECT_ID" --quiet \
        || stop "Putting $entry's value in $secret failed, and disabling version $bad failed too. Disable it by hand:
    gcloud secrets versions disable $bad --secret=$secret --project=$PROJECT_ID"
      stop "Putting $entry's value in $secret failed (above), so its version $bad is disabled. Fix it, then run scripts/deploy-wizard.sh again."
    fi
    version=$(version_number "$added")
    [[ -n $version ]] || version=$(version_number "$(gcloud secrets versions describe latest --secret="$secret" \
      --project="$PROJECT_ID" --format='value(name)')")
    [[ -n $version ]] || failed
    ok "done: $secret version $version"
  elif [[ $RAN == dry ]]; then version="<version>"
  else stop "Nothing was put in $secret, and the deploy needs it. Run the wizard again and answer y."
  fi
  PINNED+=("$secret=$secret:$version")
done

step  # Access
PROJECT_NUMBER=$(query gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
[[ $DRY == yes ]] && PROJECT_NUMBER=PROJECT_NUMBER
[[ "$PROJECT_NUMBER" =~ ^[0-9]+$ || $DRY == yes ]] || stop "gcloud didn't give project $PROJECT_ID's number (digits only), it gave \"$PROJECT_NUMBER\". Run this again; if it repeats, check: gcloud projects describe $PROJECT_ID"
ACCOUNT_NOW=$(service_field spec.template.spec.serviceAccountName)
# A service with no account of its own runs as Compute Engine's default one (Cloud Run's service identity page).
RUN_AS="${ACCOUNT_NOW:-$PROJECT_NUMBER-compute@developer.gserviceaccount.com}"
say "The service runs as $RUN_AS. Granting a role it already has changes nothing."
for pair in "${SECRETS[@]}"; do
  run "Let it read ${pair%%:*} (Secret Manager Secret Accessor, on this secret only)" \
    gcloud secrets add-iam-policy-binding "${pair%%:*}" --member="serviceAccount:$RUN_AS" \
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
# setup_commands: how to run vouch-db-setup against the instance, from the owner's machine.
setup_commands() {
  printf '    %-52s # terminal 1: leave it running\n' "cloud-sql-proxy $SQL_INSTANCE --port 5432"
  printf '    %-52s # terminal 2: .envrc gives VOUCH_DATABASE_URL\n' "uv run vouch-db-setup"
}
RERUN_DB_READY="scripts/deploy-wizard.sh --db-ready   (it skips what is done)"
say "The app needs vouch-db-setup to have run once against it (and after a LangGraph upgrade)."
say "From your machine, through the Cloud SQL Auth Proxy (DEPLOY.md, The database):"
setup_commands
if [[ $DB_READY == yes ]]; then ok "vouch-db-setup has run (--db-ready)"
elif [[ $DRY == no ]]; then
  stop "Run vouch-db-setup (above), then: $RERUN_DB_READY"
fi

step  # Deploy
# Sign-in accepts session tokens from one address only, Cloud Run's fixed one (#178): it is known
# before the service exists and never changes. Cloud Run may also serve the service at a hashed
# address, which is what status.url gives on the live one; sign-in there is refused, so it is
# never the one to share.
SHARE_URL="https://$SERVICE-$PROJECT_NUMBER.$REGION.run.app"
SECRETS_ARG=$(IFS=,; printf '%s' "${PINNED[*]}")
ENV_VARS="CLERK_PUBLISHABLE_KEY=$PUBLISHABLE,CLERK_AUTHORIZED_PARTIES=$SHARE_URL"
if [[ -n "$DISCORD_SERVER_ID" ]]; then
  ENV_VARS+=",DISCORD_SERVER_ID=$DISCORD_SERVER_ID"
  say "Sign-in lets in members of Discord server $DISCORD_SERVER_ID, invited users and developers."
else
  say "${Y}No DISCORD_SERVER_ID in $WIZARD_ENV: Clerk's own restrictions alone decide who signs in."
  say "With Clerk's sign-up open (#153), that is anyone with a Discord account.${N}"
fi
say "Public, asleep when unused (min 0), never more than one instance (max 1), on OpenRouter, signed in"
say "through Clerk. Each secret's version is pinned; sign-in accepts $SHARE_URL alone."
# A request may take 660 s (#432): a profile pass's model call takes up to 600 s
# (vouch.model_choice.PROFILE_PASS_TIMEOUT_SECONDS), plus a margin; Cloud Run's default of 300 s
# would cut a pass first. Cloud Run takes up to 60 minutes.
run "Deploy the service" gcloud run deploy "$SERVICE" --image="$IMAGE:$TAG" --region="$REGION" \
  --project="$PROJECT_ID" --allow-unauthenticated --min-instances=0 --max-instances=1 --max=1 \
  --timeout=660 \
  --set-secrets="$SECRETS_ARG" \
  --set-env-vars="$ENV_VARS" \
  --add-cloudsql-instances="$SQL_INSTANCE" --quiet
# Health on the old revision would pass, so a skipped deploy is never Done (#123).
[[ $RAN == no ]] && stop "Nothing was deployed: the service still runs its last revision, without this run's image or secret versions. Run scripts/deploy-wizard.sh --db-ready again and answer y to the deploy."

step  # URL
LOGS=(gcloud logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\"" \
  --project="$PROJECT_ID" --limit=50 --freshness=1h)
# status.url only shows the service exists; it may be the hashed address, which isn't shared (#178).
if [[ $DRY == no ]]; then
  [[ -n "$(service_field status.url)" ]] || stop "Cloud Run has no address for $SERVICE: was it deployed? The log: $(quoted "${LOGS[@]}")"
fi
ok "Vouch is at $SHARE_URL (share this one: sign-in works only there)"

step  # Health check
HEALTH=(curl -fsS --max-time 90 "$SHARE_URL/api/health")
if [[ $DRY == yes ]]; then show "${HEALTH[@]}"
else
  say "The first answer can take 10 to 30 s: it starts the container."
  healthy=no
  for _ in 1 2 3; do
    body=$("${HEALTH[@]}" 2>/dev/null || true)
    # From M3 the live app always has a database (#33): ok without it isn't a working deploy.
    if [[ "$body" =~ \"status\"[[:space:]]*:[[:space:]]*\"ok\" &&
          "$body" =~ \"database\"[[:space:]]*:[[:space:]]*\"reachable\" ]]; then healthy=yes; break; fi
    # The database answers, but has no tables (#123): a retry won't change that.
    [[ "$body" =~ \"database\"[[:space:]]*:[[:space:]]*\"needs-setup\" ]] \
      && stop "The database answers, but vouch-db-setup hasn't run on it, so every assessment would fail. Run it:
$(setup_commands)
    then: $RERUN_DB_READY"
  done
  [[ $healthy == yes ]] || stop "$SHARE_URL/api/health didn't answer ok with the database reachable. The log says why: $(quoted "${LOGS[@]}")"
  ok "$SHARE_URL/api/health answers ok, the database reachable"
fi

printf '\n%s%s✓ Done.%s %s\n\n' "$B" "$G" "$N" \
  "$([[ $DRY == yes ]] && echo 'Dry run: nothing ran, nothing written.' || echo "Open $SHARE_URL")"
