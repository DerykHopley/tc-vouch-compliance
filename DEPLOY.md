# Deploy Vouch to Google Cloud Run

How to put Vouch on a public URL at no cost, and redeploy it after a change. Adapted from Patch's
`DEPLOY.md` (sprint 2). Decisions behind it: `docs/requirements.md` (2026-11-03, "Vouch deploys to
Cloud Run" and "the walking skeleton has no secrets").

> **Easiest: run `scripts/deploy-wizard.sh`** from the repo root. It walks through the steps below
> one at a time: it explains each, shows the command and asks before running it, and skips what
> already exists, so you can stop and run it again. `--dry-run` prints every command, runs none and
> writes nothing, even with no `gcloud` or `docker` installed; `--help` lists the steps. Its plain answers (project id, region, image tag) stay in
> `deploy/cloudrun/wizard.env` (git-ignored). Inside Claude Code, `! scripts/deploy-wizard.sh` puts
> its output in the chat. The steps below are the same commands by hand.

> *Unverified:* nothing here has run against a real project yet. The first deploy is #15. The
> wizard's own behaviour is *checked* with fake `gcloud`, `docker` and `curl`
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
- **The scripted fake model only** (*checked* in `vouch.api`: the assessment route always uses
  `fake_model`), so the deploy needs no secret and an open URL costs no model calls.
  Secrets come with sign-in (M3), through Secret Manager.
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

```bash
gcloud run deploy vouch --image=$IMAGE:v1 --region=$REGION --project=$PROJECT_ID \
  --allow-unauthenticated --min-instances=0 --max-instances=1 --max=1 --quiet
```

`--max=1` is the service-level limit: without it Cloud Run leaves the service at its automatic default (3 on the live service), so a traffic split across revisions could run more than one instance. `--max-instances=1` alone caps only each revision.

The image sets `VOUCH_FRONTEND`, `PORT` and `VOUCH_AUTH_MODE=clerk` (*checked* in the `Dockerfile`). In clerk mode it refuses to start without the three Clerk keys, naming them (*checked*, `tests/test_settings.py` and CI), so a deploy from M3 on needs them from Secret Manager (#37).

## 6 and 7. The URL, and the health check

```bash
URL=$(gcloud run services describe vouch --region=$REGION --project=$PROJECT_ID --format='value(status.url)')
curl -fsS $URL/api/health          # {"status":"ok","version":"..."}
```

The first answer can take 10 to 30 s, a cold start (*unverified*: Patch's figure). If it doesn't answer, the log says why:

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="vouch"' \
  --project=$PROJECT_ID --limit=50 --freshness=1h
```

## Redeploy, roll back, remove

- **Redeploy after a change:** run the wizard again. It skips what exists and builds the next tag.
- **Roll back:** `gcloud run revisions list --service=vouch --region=$REGION`, then
  `gcloud run services update-traffic vouch --region=$REGION --to-revisions=<revision>=100`.
- **Delete an old image:** `gcloud artifacts docker images delete $IMAGE:<tag> --delete-tags`.
- **Remove everything** (stops all cost): `gcloud projects delete $PROJECT_ID`.
