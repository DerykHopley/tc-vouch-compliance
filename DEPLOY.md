# Deploy Vouch to Google Cloud Run

How to put Vouch on a public URL at no cost, and redeploy it after a change. Adapted from Patch's
`DEPLOY.md` (sprint 2). Decisions behind it: `docs/requirements.md` (2026-11-03, "Vouch deploys to
Cloud Run" and "the walking skeleton has no secrets").

> **Easiest: run `scripts/deploy-wizard.sh`** from the repo root. It walks through the steps below
> one at a time: it explains each, shows the command and asks before running it, and skips what
> already exists, so you can stop and run it again. `--dry-run` prints every command and writes
> nothing; it runs only the registry's tag list (to name the real next tag), and works with no
> `gcloud` or `docker` installed; `--help` lists the steps. Its plain answers (project id, region)
> stay in `deploy/cloudrun/wizard.env` (git-ignored); the image tag comes from the registry.
> Inside Claude Code, `! scripts/deploy-wizard.sh` puts its output in the chat. The steps below are the same commands by hand. From #118 it also puts
> the secrets in Secret Manager from your keyring, grants the service's account its two roles and
> deploys with the secrets and the Cloud SQL instance: see "Secrets and access" below.

> *Unverified:* nothing here has run against a real project yet. The first deploy is #15. The
> wizard's own behaviour is *checked* with fake `gcloud`, `docker`, `curl`, `secret-tool`,
> `cloud-sql-proxy` and `uv` (`tests/test_deploy_wizard.py`). Each claim below says *checked* (and where) or *unverified*.

## What runs where

- **One container on Cloud Run**, region `europe-west1` (Belgium, as Patch): the API under `/api`
  and the built front end on every other path (*checked* in the `Dockerfile` and `docs/setup.md`, "One
  container, as on Cloud Run").
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
- `cloud-sql-proxy` (v2; `gcloud components install cloud-sql-proxy`) and `uv`: the wizard runs
  `vouch-db-setup` through the proxy before each deploy ("The database"). The proxy signs in with
  Application Default Credentials (`gcloud auth application-default login`); the wizard checks
  they are there at its Sign-in step, before anything is built.

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

Each deploy gets the next tag: one past the highest `vN` already in the registry (`gcloud artifacts docker tags list $IMAGE`), or `v1` when there is none, so a tag that exists is never moved, whichever checkout runs it. Skip the build and it deploys the registry's highest tag, or stops if there is none (*checked*, `tests/test_deploy_wizard.py`; #135).

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

**One address to share (#178).** `$URL` is Cloud Run's fixed address,
`https://vouch-<project number>.$REGION.run.app`, built from the project number and region, so it
is known before the first deploy (*checked*, Cloud Run's "Invoke with an HTTPS request" page: the
deterministic URL; live, *checked* 2026-11-05: `https://vouch-828715466396.europe-west1.run.app`
answers `/api/health`). Cloud Run also
serves the service at a hashed address, `https://vouch-<hash>-ew.a.run.app`, and that is what
`status.url` gives on the live service (*checked* 2026-11-05: `gcloud run services describe` lists
both in its `run.googleapis.com/urls` annotation). Sign-in accepts session tokens only from
`CLERK_AUTHORIZED_PARTIES`, the fixed address alone (the owner, 2026-11-05): signing in at the
hashed address is refused (401 on `/api/access`), so share only the fixed one. The wizard builds
it, health-checks it and names it ("Vouch is at"). `--set-secrets` and `--set-env-vars` replace
the service's existing ones (*checked*, the `gcloud run deploy` reference), so a deploy always
carries all six. Each `<n>` is a version number, pinned ("Secrets and access", below).

`--max=1` is the service-level limit: without it Cloud Run leaves the service at its automatic default (3 on the live service), so a traffic split across revisions could run more than one instance. `--max-instances=1` alone caps only each revision.

The image sets `VOUCH_FRONTEND`, `PORT` and `VOUCH_AUTH_MODE=clerk` (*checked* in the `Dockerfile`). In clerk mode it refuses to start without the three Clerk keys and `CLERK_AUTHORIZED_PARTIES`, naming them (*checked*, `tests/test_settings.py` and CI), so a deploy from M3 on needs the keys from Secret Manager and `CLERK_AUTHORIZED_PARTIES` set to the service's fixed address as a plain environment variable (not a secret).

**Who may sign in (#152).** With `DISCORD_SERVER_ID` set (the course's Discord server id, not a secret: Discord's Developer Mode, **Copy Server ID**), a signed-in user gets in as a member of that server, or as a developer, a user with an accepted Clerk invitation, or one whose private metadata says `{"access": "invited"}`; anyone else sees the members-only page. Vouch reads the user's servers with the Discord token Clerk keeps, so Clerk's Discord connection needs custom credentials and the `guilds` scope (#153). Unset, Clerk's own restrictions decide. The wizard passes it on when `wizard.env` has a `DISCORD_SERVER_ID=` line (*checked*, `tests/test_deploy_wizard.py`); by hand, add `,DISCORD_SERVER_ID=<id>` to `--set-env-vars`.

## Secrets and access (from #118)

Four settings are secrets; each lives in Secret Manager under its own env var's name, and its
value comes from one keyring entry:

| Secret (and env var) | Keyring entry |
|---|---|
| `CLERK_SECRET_KEY` | `clerk-vouch-secret` |
| `CLERK_JWT_KEY` | `clerk-vouch-jwt-key` (a PEM: its lines go through whole) |
| `OPENROUTER_API_KEY` | `openrouter` |
| `VOUCH_DATABASE_URL` | `cloudsql-vouch-run-url` |

Put each value in its entry with `scripts/store-secret.sh <entry> < <file>` (or pipe it in): it
stores the value whole, a PEM's lines too, and says only its size. `secret-tool store` typed at its
prompt keeps the first line only (#130).

Two settings aren't secret, so they go in as plain env vars: `CLERK_PUBLISHABLE_KEY` (from the
keyring's `clerk-vouch-publishable`; the browser gets it anyway) and `CLERK_AUTHORIZED_PARTIES`
(the service's fixed address).

**What the wizard does** (its steps 2 and 6 to 9 of 11; *checked* with fakes, `tests/test_deploy_wizard.py`):

1. **Keyring** (before anything changes): each of the five entries must hold something besides
   whitespace; `clerk-vouch-jwt-key` a PEM public key, on several lines or on one (a line starting
   `-----BEGIN PUBLIC KEY-----`: the app drops line breaks before reading it, *checked* in
   `vouch.auth`, and the wizard passes the value on unchanged, #125); `clerk-vouch-publishable` one
   `pk_test_` or `pk_live_` key, so a secret stored under the wrong entry is never shown or deployed
   as a plain env var. The check runs inside the pipe: only a yes or no leaves it. One wrong: it
   stops, naming each, with nothing changed.
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
4. **Database:** it finds the instance's connection name and runs `vouch-db-setup` against it
   itself, from the image it deploys, through a Cloud SQL Auth Proxy on a private Unix socket ("The
   database", below). If setup fails it
   stops there, with nothing deployed. `--db-ready` is still accepted and does nothing (#594).
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
(`scripts/store-secret.sh clerk-vouch-secret < new-value`, which stores it whole, multi-line too,
and says only its size: `secret-tool store` typed at its prompt keeps one line only, #130), then
run `scripts/deploy-wizard.sh`. It adds a version only to the secret that changed and
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

### Tracing (Langfuse, #460)

The live app traces each assessment, chat turn and profile pass in Langfuse once two more secrets
are in Secret Manager: `LANGFUSE_PUBLIC_KEY` (keyring `langfuse-cloud-public-key`) and
`LANGFUSE_SECRET_KEY` (keyring `langfuse-cloud-secret-key`). The wizard never creates or writes them:
when both have an enabled version it pins each one's newest enabled version and grants the service's account Secret Accessor
on them; when either is missing it says "Tracing stays off" and deploys without them, so a deploy
before they exist runs as before (*checked*, `tests/test_deploy_wizard.py`). Put them there once,
by hand, each through a pipe from the keyring, where the Langfuse Cloud project's (EU) keys already
are:

```bash
export CLOUDSDK_CORE_LOG_HTTP=false
secret-tool lookup service langfuse-cloud-public-key | gcloud secrets create LANGFUSE_PUBLIC_KEY \
  --replication-policy=automatic --data-file=- --project=$PROJECT_ID
secret-tool lookup service langfuse-cloud-secret-key | gcloud secrets create LANGFUSE_SECRET_KEY \
  --replication-policy=automatic --data-file=- --project=$PROJECT_ID
```

then `scripts/deploy-wizard.sh`. A project in Langfuse's US region also needs a
`LANGFUSE_BASE_URL=https://us.cloud.langfuse.com` line in `wizard.env` (not a secret); without
one, traces go to the EU region, `https://cloud.langfuse.com`. To turn tracing off again, disable
every version of either secret (or delete it) and deploy. `scripts/dev.sh` drops both keys, so a
local preview never traces to the live project; a local check of tracing runs the API itself.

## 6 and 7. The URL, and the health check

```bash
URL=https://vouch-$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)').$REGION.run.app
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
`"needs-setup"` it stops, saying setup ran on `VOUCH_DATABASE_URL`'s pinned version just before
the deploy, so check that `cloudsql-vouch-run-url` names the database the service uses (*checked*,
`tests/test_deploy_wizard.py`). It says Done only when this run deployed: answering n to the
deploy ends with "Nothing was deployed" and a non-zero exit, since health would pass on the old
revision (#123; *checked*, `tests/test_deploy_wizard.py`).

The app holds a small pool (at most 4 connections; db-f1-micro allows 25, *checked* on Cloud SQL's
flags page for ~0.5 GB instances). It checks each connection before use and reconnects after an
instance restart. A wrong password or a missing database: the app answers 503 after a 5 s wait,
and `vouch-db-setup` names the reason at once.

**The wizard runs `vouch-db-setup` before every deploy (#594),** so a deploy whose code needs a
new table or column never goes live before the database has it. In its Database step it:

- starts `cloud-sql-proxy $SQL_INSTANCE --unix-socket <folder> --quiet` on a private folder
  (`mktemp -d`, mode 700), so no port is involved: 5432, where your own proxy and servers listen,
  is never touched, and no other program can take the proxy's place;
- waits until the proxy's socket, `<folder>/<connection name>/.s.PGSQL.5432`, is there while the
  proxy still runs (up to 30 s), and stops if the proxy exits first;
- pipes `VOUCH_DATABASE_URL`, the version the deploy pins, from Secret Manager to
  `scripts/proxy-database-url.py`. That helper reads it as the app does (psycopg's
  `conninfo_to_dict`: `postgresql://`, `postgres://` or key=value), sets its host to the socket's
  folder, drops any other address, port and every TLS setting (the socket is plain; the proxy
  encrypts), and keeps the user, password and database as stored. It then runs setup with the
  result in its environment: the value is never printed, an argument, a file or a shell variable;
- runs setup from **the image being deployed**, not this checkout:
  `docker run --rm --network none --user <you> -v <folder>:/cloudsql-proxy:z -e VOUCH_DATABASE_URL <image>:<tag> vouch-db-setup`.
  When you skip the build, that is the registry's tag the deploy uses. `--user` lets the container
  reach your private folder (the image runs as `nobody`); this assumes Docker runs containers as
  root-mapped users (rootful Docker), as here;
- stops the proxy and removes its folder on every exit: done, a failed step, Ctrl-C or a kill (a trap);
- stops with nothing deployed if the proxy or setup fails, or you answer n.

(*checked* with fakes, `tests/test_deploy_wizard.py`.)

**Setup only adds what's missing.** `vouch-db-setup` creates the tables, columns and constraints
the code needs that the database lacks, and runs LangGraph's own migrations; a second run does
nothing. It deletes nothing, and fills a column only as part of adding it (the audit trail's
`reviewer_name`, #154; *checked* in `vouch.database`). A change that rewrites data already stored
(a backfill, a column whose meaning changes, a split table) still needs its own reviewed step,
run and checked before the deploy that relies on it.

By hand, as before (once per database, and again after an upgrade of
`langgraph-checkpoint-postgres`; it only migrates), from the repo root in two terminals:

```bash
cloud-sql-proxy $SQL_INSTANCE --port 5432          # terminal 1: leave it running
uv run vouch-db-setup                              # terminal 2: .envrc gives VOUCH_DATABASE_URL
```

It also creates Vouch's settings table, where the developer page keeps the model (#34): until
it has run, changing the model answers 503 and the log says to run `vouch-db-setup`.
It prints `vouch-db-setup: LangGraph's tables are ready (checkpointer and store)`, or why it
couldn't connect, without the password.

**A deploy that adds a table** (as #43 adds the remediation items' table) needs `vouch-db-setup`
again: the wizard does that before the deploy. It is safe to rerun. Until it has run, what reads the new table carries on as if
it were empty, and what writes to it answers 503, the log saying to run `vouch-db-setup`.

**When the trial ends** the instance stops unless the billing account is upgraded, as Cloud Run
does; health then says `unavailable` (docs/requirements.md, 2026-11-04).

## The demo round (#579)

**Reset demo** (#605), on the Developer page, puts a company back to its demo state: a
developer presses Reset demo on the company's row (current or not) and OKs the confirm naming it.
Every user's progress and history under that company is deleted (assessments, rounds, the audit
trail, open questions, remediation, the Goal, added documents with their versions), the reset's own
audit entry is written, and on Nordlicht the demo round is played for the developer through the
app's own code, on the fake model (no key is read, so no paid call): "Q2 2026" closed, "Q3 2026"
open, three controls better since. Other companies are left empty. It counts against no daily
limit and uses no profile pass; assessments already used today aren't given back.

**The delete exception.** The audit trail, remediation decisions and added documents refuse every
delete in Postgres. Reset demo's own transaction sets `vouch.reset_demo = 'on'` (`SET LOCAL`), and
only under it do those tables let a row be deleted; every other delete, any update and any truncate
stay refused. `vouch-db-setup` applies the changed guards (`CREATE OR REPLACE`, safe to rerun on a
database holding data). **A real deployment would drop this exception**, so its audit trail stays
append-only whoever asks.

`vouch-seed-demo-round` still plays the same steps from a terminal: to seed a user without signing
in as them, or a preview from a script. It stops, writing nothing, when "Q2 2026" is closed already
since the fresh start, or when the user has assessments since it; Reset demo first, then seed. Its
profile pass counts against the day's three, as a lead's would.

```bash
uv run vouch-seed-demo-round --user user_XXXXXXXX   # .envrc gives VOUCH_DATABASE_URL; the Cloud SQL proxy running
env -u VOUCH_DATABASE_URL uv run vouch-seed-demo-round --api http://127.0.0.1:8010   # a preview
```

Find your user id in the Clerk dashboard: Users, your user, the id starting `user_` (*unverified*:
from Clerk's docs, not checked in this dashboard).

## Redeploy, roll back, remove

- **Redeploy after a change:** `scripts/deploy-wizard.sh`. It skips what exists, builds the next
  tag and runs `vouch-db-setup` before the deploy.
- **Roll back:** `gcloud run revisions list --service=vouch --region=$REGION`, then
  `gcloud run services update-traffic vouch --region=$REGION --to-revisions=<revision>=100`.
- **Delete an old image:** `gcloud artifacts docker images delete $IMAGE:<tag> --delete-tags`.
- **Remove everything** (stops all cost): `gcloud projects delete $PROJECT_ID`.
