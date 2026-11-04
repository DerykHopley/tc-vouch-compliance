# Deploy Vouch to Google Cloud Run

How to put Vouch on a public URL at no cost, and redeploy it after a change. Adapted from Patch's
`DEPLOY.md` (sprint 2). Decisions behind it: `docs/requirements.md` (2026-11-03, "Vouch deploys to
Cloud Run" and "the walking skeleton has no secrets").

> **Easiest: run `scripts/deploy-wizard.sh`** from the repo root. It walks through the steps below
> one at a time: it explains each, shows the command and asks before running it, and skips what
> already exists, so you can stop and run it again. `--dry-run` prints every command, runs none and
> writes nothing, even with no `gcloud` or `docker` installed; `--help` lists the steps. Its plain answers (project id, region, image tag) stay in
> `deploy/cloudrun/wizard.env` (git-ignored). Inside Claude Code, `! scripts/deploy-wizard.sh` puts
> its output in the chat. The steps below are the same commands by hand. From #118 it also puts
> the secrets in Secret Manager from your keyring, grants the service's account its two roles and
> deploys with the secrets and the Cloud SQL instance: see "Secrets and access" below.

> *Unverified:* nothing here has run against a real project yet. The first deploy is #15. The
> wizard's own behaviour is *checked* with fake `gcloud`, `docker`, `curl` and `secret-tool`
> (`tests/test_deploy_wizard.py`). Each claim below says *checked* (and where) or *unverified*.

## What runs where

- **One container on Cloud Run**, region `europe-west1` (Belgium, as Patch): the API under `/api`
  and the built front end on every other path (*checked* in the `Dockerfile` and README, "One
  container").
- **Min 0 instances** (it sleeps when nobody uses it; the first request after a sleep waits for the
  container to start) and **max 1**, set twice: `--max-instances=1` caps each revision and
  `--max=1` caps the whole service, shared by every revision that gets traffic (*checked* in
  `gcloud run deploy --help`, SDK 589, and Cloud Run's "maximum instances" page; the lower limit
  wins). Max 1 caps what the free tier has to cover; that it stays inside is *unverified*.
- **The scripted fake model until the service has `OPENROUTER_API_KEY`** (*checked* in
  `vouch.model_choice`: no key, the demo script), so a deploy without it costs no model calls.
  With the key (M3, through Secret Manager) assessments use OpenRouter, and in clerk mode only a
  signed-in user reaches them.
- **Public** (`--allow-unauthenticated`, *checked* in the `gcloud run deploy` reference): anyone
  with the URL can use it until sign-in lands.

## What it costs

Expected: **nothing**, or a few cents a month for storing the image (*unverified* estimate).

| Service | Free each month | Vouch's use | Source |
|---|---|---|---|
| Cloud Run | 2M requests, 180,000 vCPU-s, 360,000 GiB-s, per billing account, at Tier 1 prices (europe-west1 is Tier 1) | a few demos a day, billed only while serving | *unverified*: from a search summary of cloud.google.com/run/pricing (the page itself didn't render, 2026-11-04) and Patch's DEPLOY.md; the use is an estimate |
| Artifact Registry | 0.5 GB of storage | one image per tag, size not yet measured; delete old tags | *unverified* (from memory: about $0.10 per GB-month above it) |

**The budget alert** comes from #7: a budget of a few euros on the billing account, alerting at 50 %
and 100 %. An alerts-only budget doesn't cap spending (*checked*, docs.cloud.google.com/billing/docs/how-to/budgets).
If #7 didn't set one: Billing, then Budgets & alerts, then Create budget. Or:

```bash
gcloud billing budgets create --billing-account=<ACCOUNT_ID> --display-name="Vouch" \
  --budget-amount=5EUR --threshold-rule=percent=0.5 --threshold-rule=percent=1.0
```

The currency must be the billing account's own. Patch's DEPLOY.md records this as *checked* on its
first deploy (2026-10-19: the owner's account bills in EUR, and `1USD` failed); *unverified* for
Vouch's project.
After a week, check Billing, then Reports, filtered to the project: expected 0.

## Before you start

- The Google Cloud project from #7: linked to billing, with a budget alert.
- `gcloud`, signed in: `gcloud auth login`. On Fedora Atomic, install it in your home folder (as for Patch)
  (`curl https://sdk.cloud.google.com | bash`), then open a new terminal.
- Docker, and a checkout of the code to deploy.

Run every command from the repo root, in one shell:

```bash
export PROJECT_ID=<your project id>      # from #7
export REGION=europe-west1
export IMAGE=$REGION-docker.pkg.dev/$PROJECT_ID/vouch/vouch
```

## 1 to 3. Sign in, the project's APIs, Artifact Registry

```bash
gcloud auth list --filter=status:ACTIVE --format='value(account)'   # empty: gcloud auth login
gcloud projects describe $PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com --project=$PROJECT_ID
gcloud artifacts repositories create vouch --repository-format=docker --location=$REGION --project=$PROJECT_ID
gcloud auth configure-docker $REGION-docker.pkg.dev   # lets docker push there (*checked*, Artifact Registry docs)
```

## 4. Build and push the image

```bash
docker build -t $IMAGE:v1 .
docker push $IMAGE:v1
```

Each deploy gets the next tag: `v2`, `v3`… The wizard counts them in `wizard.env`, and saves a tag only once its push has run. Skip the build and it deploys the last pushed tag, or stops if there is none (*checked*, `tests/test_deploy_wizard.py`).

## 5. Deploy

Before it, the secrets and the roles ("Secrets and access", below) and the database setup
("The database", below). Then:

```bash
gcloud run deploy vouch --image=$IMAGE:v1 --region=$REGION --project=$PROJECT_ID \
  --allow-unauthenticated --min-instances=0 --max-instances=1 --max=1 \
  --set-secrets=CLERK_SECRET_KEY=CLERK_SECRET_KEY:<n>,CLERK_JWT_KEY=CLERK_JWT_KEY:<n>,OPENROUTER_API_KEY=OPENROUTER_API_KEY:<n>,VOUCH_DATABASE_URL=VOUCH_DATABASE_URL:<n> \
  --set-env-vars=CLERK_PUBLISHABLE_KEY=$(secret-tool lookup service clerk-vouch-publishable),CLERK_AUTHORIZED_PARTIES=$URL \
  --add-cloudsql-instances=$SQL_INSTANCE --quiet
```

`$URL` is the service's own address (step 6), or for a first deploy the one Cloud Run will give
it, `https://vouch-<project number>.$REGION.run.app` (*checked*, Cloud Run's "Invoke with an HTTPS
request" page: the deterministic URL, shown first by `status.url`). Sign-in accepts session tokens
only from there. The wizard works it out, and stops after the deploy if Cloud Run gave the service
another address: run it again and it uses that one. `--set-secrets` and `--set-env-vars` replace
the service's existing ones (*checked*, the `gcloud run deploy` reference), so a deploy always
carries all six. Each `<n>` is a version number, pinned ("Secrets and access", below).

`--max=1` is the service-level limit: without it Cloud Run leaves the service at its automatic default (3 on the live service), so a traffic split across revisions could run more than one instance. `--max-instances=1` alone caps only each revision.

The image sets `VOUCH_FRONTEND`, `PORT` and `VOUCH_AUTH_MODE=clerk` (*checked* in the `Dockerfile`). In clerk mode it refuses to start without the three Clerk keys and `CLERK_AUTHORIZED_PARTIES`, naming them (*checked*, `tests/test_settings.py` and CI), so a deploy from M3 on needs the keys from Secret Manager and `CLERK_AUTHORIZED_PARTIES` set to the service's URL as a plain environment variable (not a secret).

## Secrets and access (from #118)

Four settings are secrets; each lives in Secret Manager under its own env var's name, and its
value comes from one keyring entry:

| Secret (and env var) | Keyring entry |
|---|---|
| `CLERK_SECRET_KEY` | `clerk-vouch-secret` |
| `CLERK_JWT_KEY` | `clerk-vouch-jwt-key` (a PEM: its lines go through whole) |
| `OPENROUTER_API_KEY` | `openrouter` |
| `VOUCH_DATABASE_URL` | `cloudsql-vouch-run-url` |

Two settings aren't secret, so they go in as plain env vars: `CLERK_PUBLISHABLE_KEY` (from the
keyring's `clerk-vouch-publishable`; the browser gets it anyway) and `CLERK_AUTHORIZED_PARTIES`
(the service's URL).

**What the wizard does** (its steps 2 and 6 to 9 of 11; *checked* with fakes, `tests/test_deploy_wizard.py`):

1. **Keyring** (before anything changes): each of the five entries must hold something besides
   whitespace; `clerk-vouch-jwt-key` a `-----BEGIN PUBLIC KEY-----` line; `clerk-vouch-publishable`
   one `pk_test_` or `pk_live_` key, so a secret stored under the wrong entry is never shown or
   deployed as a plain env var. The check runs inside the pipe: only a yes or no leaves it. One
   wrong: it stops, naming each, with nothing changed.
2. **Secret Manager:** it creates each secret that isn't there, then adds a version with the
   keyring's value, through a pipe only:
   `secret-tool lookup service clerk-vouch-secret | gcloud secrets versions add CLERK_SECRET_KEY --data-file=-`
   (`-` reads standard input, *checked* in the `gcloud secrets versions add` reference). A value is
   never an argument, a file, or a line of output.
   - A secret whose latest version already holds the keyring's value gets no new version: the
     wizard compares the two values' SHA-256 digests, each through a pipe.
   - Just before an add it checks the entry again (a locked keyring stops it there). If the pipe
     breaks during the add, it disables the version that add made, which may hold part of the value.
   - Skipping a create or an add stops the wizard there, naming the secret.
   - "Not found" means create it; any other error from Secret Manager (no permission, the API still
     turning on) stops it: wait a minute and run it again.
   - Every `gcloud` call runs with `CLOUDSDK_CORE_LOG_HTTP=false`, so gcloud's HTTP log (on screen,
     or in `~/.config/gcloud/logs`) can't hold a payload, even if your config turns it on (an env
     var beats `gcloud config set`, *checked*, cloud.google.com/sdk/docs/properties).
3. **Access:** it grants the service's account **Secret Manager Secret Accessor**
   (`roles/secretmanager.secretAccessor`) on each of the four secrets, not the project, and
   **Cloud SQL Client** (`roles/cloudsql.client`) on the project (both ids *checked*, below). The
   account is the service's own (`spec.template.spec.serviceAccountName`), or, for a service with
   none, Compute Engine's default, `<project number>-compute@developer.gserviceaccount.com`
   (*checked*, Cloud Run's service identity page). Granting a role twice changes nothing.
4. **Database:** it finds the instance's connection name and prints the two commands that run
   `vouch-db-setup` against it ("The database", below). Without `--db-ready` it stops there; once
   setup has run, `scripts/deploy-wizard.sh --db-ready` goes on to the deploy.
5. **Deploy:** step 5's command, each secret pinned to the version it ended with in step 2.

**Pinned versions, not `:latest`.** Env-var secrets are read when an instance starts (*checked*,
Cloud Run's secrets page), and with min 0 that's after every sleep. With `:latest`, a version added
later (or a broken one) would reach the running revision at its next cold start, with no deploy and
no health check; Google recommends pinning for env vars. Pinned, a new value reaches the app only
through a deploy, and the wizard's health check then checks it. The version number comes from the
add's own output (`--format='value(name)'`, ending in `/versions/<n>`), or from
`gcloud secrets versions describe latest` for an unchanged secret.

By hand, steps 2 and 3 are, for each secret and entry (the account as the wizard finds it):

```bash
SA=$(gcloud run services describe vouch --region=$REGION --project=$PROJECT_ID \
  --format='value(spec.template.spec.serviceAccountName)')
SA=${SA:-$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')-compute@developer.gserviceaccount.com}
export CLOUDSDK_CORE_LOG_HTTP=false
gcloud secrets create CLERK_SECRET_KEY --replication-policy=automatic --project=$PROJECT_ID
secret-tool lookup service clerk-vouch-secret | gcloud secrets versions add CLERK_SECRET_KEY \
  --data-file=- --project=$PROJECT_ID --format='value(name)'     # prints .../versions/<n>
gcloud secrets add-iam-policy-binding CLERK_SECRET_KEY --member=serviceAccount:$SA \
  --role=roles/secretmanager.secretAccessor --project=$PROJECT_ID
```

**Rotate a secret:** put the new value in the keyring
(`secret-tool store --label=clerk-vouch-secret service clerk-vouch-secret`, which reads it), then
run `scripts/deploy-wizard.sh --db-ready`. It adds a version only to the secret that changed and
deploys a new revision pinned to it; the old revision keeps the old version until then. Once the
new revision is healthy, revoke the old key where it was issued (Clerk, OpenRouter, Cloud SQL's
user), then disable its version:
`gcloud secrets versions list CLERK_SECRET_KEY --project=$PROJECT_ID`, then
`gcloud secrets versions disable <n> --secret=CLERK_SECRET_KEY --project=$PROJECT_ID`
(`destroy` in place of `disable` removes it for good). A rollback to an older revision needs its
pinned versions still enabled.

Secret Manager bills active versions above a small free tier (*unverified*: the pricing page didn't
render, 2026-11-04); the wizard adds none for an unchanged secret, and destroying old versions
keeps the count down.

## 6 and 7. The URL, and the health check

```bash
URL=$(gcloud run services describe vouch --region=$REGION --project=$PROJECT_ID --format='value(status.url)')
curl -fsS $URL/api/health          # {"status":"ok","version":"...","database":"reachable"}
                                   # "needs-setup": run vouch-db-setup ("The database")
```

The first answer can take 10 to 30 s, a cold start (*unverified*: Patch's figure). If it doesn't answer, the log says why:

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="vouch"' \
  --project=$PROJECT_ID --limit=50 --freshness=1h
```

## The database (Cloud SQL, from #33)

Vouch's Postgres is the Cloud SQL instance `vouch-db` (`europe-west1`, Enterprise, `db-f1-micro`;
created in #29). The app reads one URL, `VOUCH_DATABASE_URL`: there's no pooler, so the same URL
serves the app and the setup command. With it unset the app runs without a database; health then
leaves the database out, and a route that needs it answers 503 (*checked*, `tests/test_database.py`).

**Deploy flag and role** (*checked*, docs.cloud.google.com/sql/docs/postgres/connect-run): the
service connects through the instance's Unix socket, `/cloudsql/<connection name>`. It needs the
Cloud SQL Admin API, the instance added to the service, and the **Cloud SQL Client** role on the
service's account:

```bash
export SQL_INSTANCE=$(gcloud sql instances describe vouch-db --project=$PROJECT_ID --format='value(connectionName)')
gcloud services enable sqladmin.googleapis.com --project=$PROJECT_ID
# $SA as in "Secrets and access": the service's account, else Compute Engine's default
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$SA \
  --role=roles/cloudsql.client --condition=None   # the id *checked*, Cloud SQL's IAM roles page
# step 5's deploy, with the instance added:
gcloud run deploy vouch ... --add-cloudsql-instances=$SQL_INSTANCE
```

On Cloud Run the URL is the keyring's `cloudsql-vouch-run-url`
(`postgresql://vouch:<password>@/vouch?host=/cloudsql/<connection name>`), given to the service
from Secret Manager as `VOUCH_DATABASE_URL` (the wizard does both, "Secrets and access"). Health then says `"database": "reachable"`
(it answers, and `vouch-db-setup`'s tables are there), `"needs-setup"` (it answers, but
`vouch-db-setup` never ran on it: every assessment answers 503 until it does, #123) or
`"unavailable"`; its answer is reused for 5 s (*checked*, `tests/test_database.py`). Health's
`status` stays `ok` in each case: it says the app is alive.

**The deploy check needs the database.** From M3 on the live app always has a database, so the
wizard's health check (step 11) passes only on `"status": "ok"` *and* `"database": "reachable"`
(*checked*, `tests/test_deploy_wizard.py`). A deploy without the database URL or the instance
flag fails there, with the logs command. The wizard's deploy carries both (#118). On
`"needs-setup"` it stops with the two `vouch-db-setup` commands. It says Done only when this run
deployed: answering n to the deploy ends with "Nothing was deployed" and a non-zero exit, since
health would pass on the old revision (#123).

The app holds a small pool (at most 4 connections; db-f1-micro allows 25, *checked* on Cloud SQL's
flags page for ~0.5 GB instances). It checks each connection before use and reconnects after an
instance restart. A wrong password or a missing database: the app answers 503 after a 5 s wait,
and `vouch-db-setup` names the reason at once.

**LangGraph's tables, once per database** (and again after an upgrade of
`langgraph-checkpoint-postgres`; it only migrates). Run from your machine through the Cloud SQL
Auth Proxy, in two terminals from the repo root:

```bash
cloud-sql-proxy $SQL_INSTANCE --port 5432          # terminal 1: leave it running
uv run vouch-db-setup                              # terminal 2: .envrc gives VOUCH_DATABASE_URL
```

It also creates Vouch's settings table, where the developer page keeps the model (#34): until
it has run, changing the model answers 503 and the log says to run `vouch-db-setup`.
It prints `vouch-db-setup: LangGraph's tables are ready (checkpointer and store)`, or why it
couldn't connect, without the password.

**When the trial ends** the instance stops unless the billing account is upgraded, as Cloud Run
does; health then says `unavailable` (docs/requirements.md, 2026-11-04).

## Redeploy, roll back, remove

- **Redeploy after a change:** `scripts/deploy-wizard.sh --db-ready` (once `vouch-db-setup` has
  run). It skips what exists and builds the next tag.
- **Roll back:** `gcloud run revisions list --service=vouch --region=$REGION`, then
  `gcloud run services update-traffic vouch --region=$REGION --to-revisions=<revision>=100`.
- **Delete an old image:** `gcloud artifacts docker images delete $IMAGE:<tag> --delete-tags`.
- **Remove everything** (stops all cost): `gcloud projects delete $PROJECT_ID`.
