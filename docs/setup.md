# Setup

Everything needed to run, check and deploy Vouch. The short version is in the
[README](../README.md); the four common tasks, step by step, are in [features](features.md).

Commands that need a key, a paid model, `gcloud` or Docker say so next to them. Everything else
runs offline, free, from a fresh clone.

## Local

### What to install

- [uv](https://docs.astral.sh/uv/), with Python 3.12 (uv installs it when it is missing).
- Node 24 and npm, for the front end.
- bash 4.4 or newer, python3 and curl, which `scripts/dev.sh` uses. macOS ships bash 3.2:
  `brew install bash`.

```sh
uv sync
(cd frontend && npm ci)
```

### Run the app

```sh
scripts/dev.sh                                   # open the URL it prints; Ctrl-C stops both
scripts/dev.sh --api-port 8010 --web-port 5180   # when 8000 or 5173 is taken
```

`scripts/dev.sh` starts the API (reloading when `src/` changes) and the Vite dev server, which
passes `/api` to it. When both answer it prints `Vouch is running: http://127.0.0.1:<web port>/`.

- **The scripted fake model.** The API runs only on scripted answers: no network, no cost. It
  drops `OPENROUTER_API_KEY` even when your shell has it, so it never makes a paid call.
- **No keys and no sign-in.** It runs in local auth mode: one default user, shown as "Local
  user". Nothing local needs a key.
- **What the fake covers.** The bundled demo script answers for **4.1.2**: in English under
  Nordlicht (the default profile), in German under Alpenwerk. The Dossier suggests 1.1.1 as the
  next step, but on the fake another control replays 4.1.2's answers (about sign-in), which
  don't fit it. The other controls are for the live app, or for an eval script (below).
- **Kept in memory.** Without `VOUCH_DATABASE_URL`, assessments and reviews last until the API
  stops.

### `ASSESS_FAKE_SCRIPT`: another scripted run

`ASSESS_FAKE_SCRIPT` names a script for the fake model to play instead of the bundled demo
script. `scripts/dev.sh` passes it on to the API, and the `assess` command needs it (or a key).
The eval scripts in `evals/scripts/<profile>/<control>.json` are such scripts, one per control.

The overlay branch is the example: only 1.6.2 under Alpenwerk takes it (the country overlay "AT
NIS2 (illustrative)" adds requirement O1).

```sh
ASSESS_FAKE_SCRIPT=evals/scripts/alpenwerk/1.6.2.json scripts/dev.sh
```

While it runs this way every assessment plays that script, so assess only 1.6.2 under
Alpenwerk; stop it and start `scripts/dev.sh` plainly for anything else. The walk-through is in
[features](features.md#the-overlay-branch).

## The `assess` command

The agent on its own, from the command line:

```sh
ASSESS_FAKE_SCRIPT=tests/scripts/isa_4_1_2.json uv run assess 4.1.2 --profile nordlicht
ASSESS_FAKE_SCRIPT=evals/scripts/alpenwerk/1.6.2.json uv run assess 1.6.2 --profile alpenwerk
```

The first assesses VDA ISA control `4.1.2` for the `nordlicht` compliance profile (the default)
on the scripted fake model, offline and free, and prints the Assessment as JSON: a verdict per
requirement, the control's rolled-up verdict and its should score. The profile and policies it
reads are the demo company's, in `fixtures/nordlicht/`. The second takes the overlay branch for
Alpenwerk (`fixtures/alpenwerk/`), with requirement O1 in its list.

To use a real model instead, leave `ASSESS_FAKE_SCRIPT` unset and set `OPENROUTER_API_KEY`
(**needs a key; a paid call**). `ASSESS_MODEL` names the model; without it,
`anthropic/claude-sonnet-5.5`, the owner's choice from the eval report (#55).

## The offline eval

```sh
uv run python -m vouch.evals
```

This assesses each of Nordlicht's 12 controls on the scripted fake model (a script per control in
`evals/scripts/nordlicht/`) and scores the verdicts and citations against
`fixtures/nordlicht/expected-answers.yaml`, read when it runs: verdict accuracy per requirement
and per control, and citation precision. It prints a summary and saves it, with the raw results,
to `evals/results/` (git-ignored). On the fake model it checks the harness, not a model.
`--profile alpenwerk` runs Alpenwerk's 12 controls the same way (`evals/scripts/alpenwerk/`,
German draft answers):

```sh
uv run python -m vouch.evals --profile alpenwerk
```

The scripts are drafted from the expected answers: after those change, regenerate them and read
the diff:

```sh
uv run python evals/scripts/generate.py
uv run python evals/scripts/generate.py --profile alpenwerk
```

`--models` lists the runner's models and prices:

```sh
uv run python -m vouch.evals --models
```

The report renders from saved results without calling a model (the newest complete run of each
model on each profile; a partial run or a dry run only when there is nothing else, marked):

```sh
uv run python -m vouch.evals report evals/kept --out docs/eval-report.md
```

## The paid eval

**Needs a key, or a local Ollama model; an OpenRouter run is paid.** On a real model
(`--model <OpenRouter id>`, or `--model ollama/<name>` for a local model through Ollama) each case
also records its tokens, cost and seconds, and with `--judge <model>` the faithfulness of its
answer to the passages it cites (DeepEval, judged by that model). A real run is paid, so it runs
one case first (the dry run), prints a cost and time estimate, and runs the rest only when you
type `yes`; anything else, or Ctrl-C, stops with nothing more sent. A judge is never added to a
fake run unless you name it. OpenRouter needs `OPENROUTER_API_KEY` (see [Keys](#keys)); without
it a real model is refused and the fake still runs. Ollama must be running (`ollama serve`) with
the model pulled (`ollama pull <name>`); `--ollama-timeout` sets a local model's per-request limit
(600 s by default). `--profile alpenwerk` runs the second profile.

Raw results go to `evals/results/` (git-ignored), saved after every case, so Ctrl-C or a crash
keeps the cases already run; `--keep` also copies them to `evals/kept/`, to commit.

## The API

```sh
uv run uvicorn vouch.api:app --port 8000
curl localhost:8000/api/health
curl -N -X POST localhost:8000/api/assessments \
  -H 'content-type: application/json' -d '{"control_id": "4.1.2"}'
```

`POST /api/assessments` streams server-sent events: a `step` per agent step, a `requirement` per
judged requirement, one `result`, then the stream closes (or one `error`). The model
(`vouch.model_choice`): the script `ASSESS_FAKE_SCRIPT` names, when set; else OpenRouter, when
`OPENROUTER_API_KEY` is set (a paid call; on Cloud Run only a signed-in user reaches it); else the
bundled demo script. OpenRouter runs the model a developer chose on the developer page
(`/developer`, `GET /api/developer/settings` and `PUT /api/developer/model`, developers only),
else `ASSESS_MODEL`, else `anthropic/claude-sonnet-5.5`. The choice is kept in the database (its
table made by `vouch-db-setup`), or in memory without one. One assessment makes at most 12 model
calls, each retried at most once. `scripts/dev.sh` drops the key, so it always runs the fake.

A proposed assessment waits for the lead's review (`POST /api/assessments/{thread_id}/review`).
`GET /api/assessments` lists the user's assessments, newest first, and
`GET /api/assessments/{thread_id}` opens one; another user's thread id is a 404. With
`VOUCH_DATABASE_URL` set they're kept in Postgres (LangGraph's checkpointer and store, their
tables made by `vouch-db-setup`), so a run waiting for review outlasts a restart; without it, in
memory until the server restarts. `GET /api/report` is the readiness report's data.

## Sign-in (clerk mode)

Locally there is no sign-in: one default user. With any Clerk key set (`.envrc` loads them),
`VOUCH_AUTH_MODE` must be set too, `local` or `clerk`, or the API refuses to start, naming it;
`scripts/dev.sh` sets `local` (#151). In `clerk` mode, as on Cloud Run, every `/api` route but
`/api/health` needs a Clerk session token (`Authorization: Bearer ...`); without a valid one the
API answers 401. The token is checked offline, and only from the origins in
`CLERK_AUTHORIZED_PARTIES` (comma-separated, not a secret, so set in the shell rather than the
keyring). The front end shows the "access is by invitation" page with Clerk's sign-in until a
user signs in. A user is a developer when their **private** metadata in Clerk is
`{"role": "developer"}`; anyone else is a compliance lead. Only routes that need the role ask
Clerk's API for it, and answer 503 when Clerk can't be reached.

With `DISCORD_SERVER_ID` set (#152), every route but health also needs access: a member of that
Discord server (read with the user's Discord token, which Clerk keeps from the sign-in), a
developer, a user with an accepted Clerk invitation, or one whose private metadata says
`{"access": "invited"}`. The answer is cached per user for 5 minutes. Anyone else gets a 403
with `reason: "not-a-member"`, and the page says Vouch is open to the course's server; a Discord
token Vouch can't use gets `reason: "sign-in-again"`; Clerk or Discord failing, a 503. The page
asks `GET /api/access` before it opens.

To try it locally on the development instance (**needs the Clerk keys**, loaded from the keyring
by `.envrc`):

```sh
npm --prefix frontend run build
VOUCH_AUTH_MODE=clerk CLERK_AUTHORIZED_PARTIES=http://localhost:8000 \
  VOUCH_FRONTEND=frontend/dist uv run uvicorn vouch.api:app --port 8000
```

## One container, as on Cloud Run

**Needs Docker.**

```sh
docker build -t vouch:dev .
docker run --rm -p 127.0.0.1:8081:8080 -e VOUCH_AUTH_MODE=local vouch:dev   # open http://localhost:8081
```

FastAPI serves the API under `/api` and the built front end (`VOUCH_FRONTEND`, `frontend/dist`)
on every other path, so a link such as `/controls/4.1.2` survives a refresh. It listens on
`$PORT` (8080 by default). Without a front-end build it stops at once and says so.

## Checks

```sh
uv run ruff check
uv run pytest            # sockets are disabled; no test needs an API key
(cd frontend && npm run typecheck && npm run lint && npm test)
python3 -I -B scripts/design-lint.py frontend/src
```

The browser check runs on the built front end, with throwaway servers on ports the OS picks. It
needs Playwright's Chromium, downloaded once:

```sh
uv run --project frontend/e2e playwright install chromium
frontend/e2e/run.sh
```

On the container (**needs Docker**, and Playwright's Firefox):

```sh
VOUCH_IMAGE=vouch:dev frontend/e2e/run.sh --browser firefox
```

## Keys

- Keys live in the system keyring, never in a file in the repo. The committed `.envrc` reads
  them from the keyring (`secret-tool`) and exports them, through
  [direnv](https://direnv.net/): install direnv, then run `direnv allow` once in the repo.
- To store a key, pipe it into `scripts/store-secret.sh <service>` (for example
  `pbpaste | scripts/store-secret.sh openrouter`). It keeps multi-line keys whole, PEM blocks
  included, checks the read-back and never prints the value; `secret-tool store` typed at its
  prompt keeps only the first line.
- `.env.example` lists the names of every setting Vouch reads, keys included, with no values.
- Don't create a `.env` file: keys stay in the keyring, where no commit can pick them up.
- **Nothing local needs a key.** The app, the `assess` command with a script, the offline eval
  and the checks all run without one. Only a paid model run, clerk mode and a deploy do.

## Cloud Run

The live app is one container on Cloud Run (region `europe-west1`), in clerk mode, with its keys
in Secret Manager and its memory in Cloud SQL. **Needs `gcloud`, Docker and the owner's
project.** The steps are in [`DEPLOY.md`](../DEPLOY.md), and
[`scripts/deploy-wizard.sh`](../scripts/deploy-wizard.sh) walks through them; this page doesn't
repeat them.

## When something fails

**A port is in use.** `scripts/dev.sh` stops before starting anything:

```text
Port 8000 is already in use (another server?). Choose another: --api-port <another port>
```

Choose free ports: `scripts/dev.sh --api-port 8010 --web-port 5180`.

**`uv run assess` with neither a script nor a key.** It stops with exit code 2:

```text
assess: set OPENROUTER_API_KEY, or ASSESS_FAKE_SCRIPT
```

Name a script, for free: `ASSESS_FAKE_SCRIPT=tests/scripts/isa_4_1_2.json uv run assess 4.1.2`.
A key runs the real model, which is paid.

**direnv is not installed, or `.envrc` is not allowed.** Entering the repo loads nothing (or
direnv says the `.envrc` is blocked until you run `direnv allow`), so no key is set. Local runs
still work, since they need no key. Install direnv and run `direnv allow` only for a paid run or
clerk mode.

**A Clerk key is set without `VOUCH_AUTH_MODE`.** The API refuses to start, naming the setting:

```text
Vouch cannot start: VOUCH_AUTH_MODE is not set, but Clerk keys are (CLERK_SECRET_KEY): set VOUCH_AUTH_MODE=clerk, or VOUCH_AUTH_MODE=local to run without sign-in.
```

The brackets name whichever Clerk keys are set.

Set `VOUCH_AUTH_MODE=local` to run without sign-in, or `clerk` with every Clerk setting for
sign-in. `scripts/dev.sh` sets `local` itself, so this is met when starting `uvicorn` by hand.
