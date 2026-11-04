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
ASSESS_FAKE_SCRIPT=tests/scripts/isa_4_1_2.json uv run assess 4.1.2 --profile nordlicht
```

This assesses VDA ISA control `4.1.2` for the `nordlicht` compliance profile (the default) on
the scripted fake model, offline and free, and prints the Assessment as JSON: a verdict per
requirement, the control's rolled-up verdict and its should score. The profile and policies it
reads are the demo company's, in `fixtures/nordlicht/`.

To use a real model instead, leave `ASSESS_FAKE_SCRIPT` unset and set `OPENROUTER_API_KEY` and
`ASSESS_MODEL`. That is a paid call: load the key from the system keyring, never from a file in
the repo.

### The offline eval

```sh
uv run python -m vouch.evals
```

This assesses each of Nordlicht's 12 controls on the scripted fake model (a script per control in
`evals/scripts/nordlicht/`) and scores the verdicts and citations against
`fixtures/nordlicht/expected-answers.yaml`, read when it runs: verdict accuracy per requirement
and per control, and citation precision. It prints a summary and saves it, with the raw results,
to `evals/results/` (git-ignored). On the fake model it checks the harness, not a model. The
scripts are drafted from the expected answers: after those change, regenerate them with
`uv run python evals/scripts/generate.py` and read the diff.

### The API

```sh
uv run uvicorn vouch.api:app --port 8000
curl localhost:8000/api/health
curl -N -X POST localhost:8000/api/assessments \
  -H 'content-type: application/json' -d '{"control_id": "4.1.2"}'
```

`POST /api/assessments` streams server-sent events: a `step` per agent step, a `requirement` per
judged requirement, one `result`, then the stream closes (or one `error`). The API runs only the scripted fake model: the bundled demo
script, or the one `ASSESS_FAKE_SCRIPT` names. It never builds the real model until sign-in
exists.

### One container, as on Cloud Run

```sh
docker build -t vouch:dev .
docker run --rm -p 127.0.0.1:8081:8080 vouch:dev     # open http://localhost:8081
```

FastAPI serves the API under `/api` and the built front end (`VOUCH_FRONTEND`, `frontend/dist`)
on every other path, so a link such as `/controls/4.1.2` survives a refresh. It listens on
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
