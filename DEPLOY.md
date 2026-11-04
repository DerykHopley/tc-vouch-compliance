# Deploy Vouch to Google Cloud Run

How to put Vouch on a public URL at no cost, and redeploy it after a change. Adapted from Patch's
`DEPLOY.md` (sprint 2). Decisions behind it: `docs/requirements.md` (2026-11-03, "Vouch deploys to
Cloud Run" and "the walking skeleton has no secrets").

> **Easiest: run `scripts/deploy-wizard.sh`** from the repo root. It walks through the steps below
> one at a time: it explains each, shows the command and asks before running it, and skips what
> already exists, so you can stop and run it again. `--dry-run` prints every command and runs none;
> `--help` lists the steps. Its plain answers (project id, region, image tag) stay in
> `deploy/cloudrun/wizard.env` (git-ignored). Inside Claude Code, `! scripts/deploy-wizard.sh` puts
> its output in the chat. The steps below are the same commands by hand.

> *Unverified:* nothing here has run against a real project yet. The first deploy is #15. The
> wizard's own behaviour is tested with fake `gcloud`, `docker` and `curl`
> (`tests/test_deploy_wizard.py`).

## What runs where

- **One container on Cloud Run**, region `europe-west1` (Belgium, as Patch): the API under `/api`
  and the built front end on every other path (the `Dockerfile`; README, "One container").
- **Min 0 instances** (it sleeps when nobody uses it; the first request after a sleep waits for the
  container to start) and **max 1**, so the free tier can't be outrun.
- **The scripted fake model only.** The API never builds the real model in M0, so the deploy needs
  no secret and an open URL costs nothing. Secrets come with sign-in (M3), through Secret Manager.
- **Public** (`--allow-unauthenticated`): anyone with the URL can use it until sign-in lands.

## What it costs

Expected: **nothing**, or a few cents a month for storing the image.

| Service | Free each month | Vouch's use | Source |
|---|---|---|---|
| Cloud Run | 2M requests, 180,000 vCPU-s, 360,000 GiB-s, per billing account, at Tier 1 prices (europe-west1 is Tier 1) | a few demos a day, billed only while serving | the free amounts *checked* (cloud.google.com/run/pricing, 2026-11-04); the use is an *unverified* estimate |
| Artifact Registry | 0.5 GB of storage | one image per tag, a few hundred MB each; delete old tags | *unverified* (from memory: about $0.10 per GB-month above it) |

**The budget alert** comes from #7: a budget of a few euros on the billing account, alerting at 50 %
and 100 %. It only emails; it never stops anything (*checked*, docs.cloud.google.com/billing/docs/how-to/budgets).
If #7 didn't set one: Billing, then Budgets & alerts, then Create budget. Or:

```bash
gcloud billing budgets create --billing-account=<ACCOUNT_ID> --display-name="Vouch" \
  --budget-amount=5EUR --threshold-rule=percent=0.5 --threshold-rule=percent=1.0
```

The currency must be the billing account's own (Patch's account bills in EUR: `5USD` fails there).
After a week, check Billing, then Reports, filtered to the project: expected 0.

## Before you start

- The Google Cloud project from #7: linked to billing, with a budget alert.
- `gcloud`, signed in: `gcloud auth login`. On Fedora Atomic, install it in your home folder
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
gcloud auth configure-docker $REGION-docker.pkg.dev   # lets docker push there (~/.docker/config.json)
```

## 4. Build and push the image

```bash
docker build -t $IMAGE:v1 .
docker push $IMAGE:v1
```

Each deploy gets the next tag: `v2`, `v3`… The wizard counts them in `wizard.env`.

## 5. Deploy

```bash
gcloud run deploy vouch --image=$IMAGE:v1 --region=$REGION --project=$PROJECT_ID \
  --allow-unauthenticated --min-instances=0 --max-instances=1 --quiet
```

No environment variables and no secrets: the image sets `VOUCH_FRONTEND` and `PORT`.

## 6 and 7. The URL, and the health check

```bash
URL=$(gcloud run services describe vouch --region=$REGION --project=$PROJECT_ID --format='value(status.url)')
curl -fsS $URL/api/health          # {"status":"ok","version":"..."}
```

The first answer can take 10 to 30 s (a cold start). If it doesn't answer, the log says why:

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
