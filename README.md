# Vouch

Vouch is an audit-readiness agent for ISMS and TISAX. For each control it searches the company's
policies, judges whether the evidence covers the control, and drafts an answer for the auditor,
citing its sources.

## Run it locally

You need [uv](https://docs.astral.sh/uv/) and Python 3.12, and Node 24 for the front end.

```sh
uv sync && (cd frontend && npm ci)
scripts/dev.sh                                   # open the URL it prints; Ctrl-C stops both
scripts/dev.sh --api-port 8010 --web-port 5180   # when 8000 or 5173 is taken
```

`scripts/dev.sh` starts the API (reloading when `src/` changes, on the scripted fake model) and
the Vite dev server, which passes `/api` to it. A port already in use stops it before anything
starts.

The agent on its own, from the command line:

```sh
ASSESS_FAKE_SCRIPT=tests/scripts/enc_found.json uv run assess NK-ENC-01
```

This assesses control `NK-ENC-01` on the scripted fake model, offline and free, and prints the
Assessment as JSON. The controls and policies it reads are the demo company's, in
`fixtures/nordlicht/`.

To use a real model instead, leave `ASSESS_FAKE_SCRIPT` unset and set `OPENROUTER_API_KEY` and
`ASSESS_MODEL`. That is a paid call: load the key from the system keyring, never from a file in
the repo.

### The API

```sh
uv run uvicorn vouch.api:app --port 8000
curl localhost:8000/api/health
curl -N -X POST localhost:8000/api/assessments \
  -H 'content-type: application/json' -d '{"control_id": "NK-ENC-01"}'
```

`POST /api/assessments` streams server-sent events: a `step` per agent step, one `result`, then
the stream closes (or one `error`). The API runs only the scripted fake model: the bundled demo
script, or the one `ASSESS_FAKE_SCRIPT` names. It never builds the real model until sign-in
exists.

### Sign-in (clerk mode)

Locally there is no sign-in: one default user. In `clerk` mode, as on Cloud Run, every `/api`
route but `/api/health` needs a Clerk session token (`Authorization: Bearer ...`); without a
valid one the API answers 401. The token is checked offline, and only from the origins in
`CLERK_AUTHORIZED_PARTIES` (comma-separated, not a secret, so set in the shell rather than the
keyring). The front end shows the "access is by invitation" page with Clerk's sign-in until a
user signs in. A user is a developer when their **private** metadata in Clerk is
`{"role": "developer"}`; anyone else is a compliance lead. Only routes that need the role ask
Clerk's API for it, and answer 503 when Clerk can't be reached. To try it locally on the
development instance, with the Clerk keys loaded from the keyring by `.envrc`:

```sh
npm --prefix frontend run build
VOUCH_AUTH_MODE=clerk CLERK_AUTHORIZED_PARTIES=http://localhost:8000 \
  VOUCH_FRONTEND=frontend/dist uv run uvicorn vouch.api:app --port 8000
```

### One container, as on Cloud Run

```sh
docker build -t vouch:dev .
docker run --rm -p 127.0.0.1:8081:8080 -e VOUCH_AUTH_MODE=local vouch:dev   # open http://localhost:8081
```

FastAPI serves the API under `/api` and the built front end (`VOUCH_FRONTEND`, `frontend/dist`)
on every other path, so a link such as `/controls/NK-ENC-01` survives a refresh. It listens on
`$PORT` (8080 by default). Without a front-end build it stops at once and says so.

## Checks

```sh
uv run ruff check
uv run pytest            # sockets are disabled; no test needs an API key
frontend/e2e/run.sh      # the browser check, on the built front end
VOUCH_IMAGE=vouch:dev frontend/e2e/run.sh --browser firefox   # on the container
```

## Docs

- [`docs/`](docs/): requirements and decisions, design, and the project brief.
- [`GLOSSARY.md`](GLOSSARY.md): the domain words.
- Work is tracked in this repo's GitHub issues.
