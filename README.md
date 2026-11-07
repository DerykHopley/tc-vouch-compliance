# Vouch

Vouch is an audit-readiness agent for ISMS and TISAX. For each control it searches the company's
policies and records, judges each requirement as Evidenced, Partly evidenced or Not evidenced
(Not applicable when the company's profile excludes it), and drafts an answer for the auditor
that quotes its sources word for word. The compliance lead reviews every verdict; only accepted verdicts count towards
readiness.

## Who it's for

- **A compliance lead** preparing a TISAX or ISO 27001 (ISMS) audit, working through the VDA ISA
  controls one piece of evidence at a time.
- **The readiness report's readers:** management and the auditor, who read where the company
  stands, what is missing and who reviewed each verdict.

## Run it locally

You need [uv](https://docs.astral.sh/uv/) with Python 3.12, Node 24 and npm, bash 4.4 or newer,
python3 and curl.

```sh
uv sync
(cd frontend && npm ci)
scripts/dev.sh                                   # open the URL it prints; Ctrl-C stops it
scripts/dev.sh --api-port 8010 --web-port 5180   # when 8000 or 5173 is taken
```

It runs on the scripted fake model: offline, free, with no keys and no sign-in. The fake covers
four controls, **1.1.1**, **1.6.2**, **4.1.2** and **5.1.1**, under both bundled profiles; any
other control plays 4.1.2's script. The full setup, and what to
do when something fails, is in [`docs/setup.md`](docs/setup.md); the four common tasks, step by
step, are in [`docs/features.md`](docs/features.md).

## The live app

<https://vouch-828715466396.europe-west1.run.app>: invite-only (Clerk sign-in; members of the
course's Discord server are let in too). It runs on the real model.

## Course tasks

The optional tasks done; the full table, with the code and the app page for each, is in
[`docs/course-tasks.md`](docs/course-tasks.md).

| Level | Task | Details |
|---|---|---|
| Easy | Give the agent a personality | [Easy](docs/course-tasks.md#easy) |
| Easy | An interactive help feature or chatbot guide | [Easy](docs/course-tasks.md#easy) |
| Medium | Calculate and show token usage and cost | [Medium](docs/course-tasks.md#medium) |
| Medium | Long- or short-term memory in LangGraph | [Medium](docs/course-tasks.md#medium) |
| Medium | User authentication and personalisation | [Medium](docs/course-tasks.md#medium) |
| Medium | Multi-model support | [Medium](docs/course-tasks.md#medium) |
| Medium | A security guard; developer settings apart from the user experience | [Medium](docs/course-tasks.md#medium) |
| Hard | Agentic RAG in the LangGraph app | [Hard](docs/course-tasks.md#hard) |
| Hard | An AI evaluation report with DeepEval | [Hard](docs/course-tasks.md#hard) |

## Docs

- [`docs/setup.md`](docs/setup.md): local setup, the CLI, the evals, the API, sign-in, the
  container, the checks, keys, Cloud Run in short, and common failures.
- [`docs/features.md`](docs/features.md): assess a control, review it, ask a question, read the
  report.
- [`docs/demo.md`](docs/demo.md): the demo, Vouch in seven minutes, with the local run as a
  fallback.
- [`docs/technical-decisions.md`](docs/technical-decisions.md),
  [`docs/course-tasks.md`](docs/course-tasks.md),
  [`docs/eval-summary.md`](docs/eval-summary.md) and [`docs/reflection.md`](docs/reflection.md).
- [`docs/eval-report.md`](docs/eval-report.md): the full eval report.
- [`DEPLOY.md`](DEPLOY.md): deploying to Cloud Run.
- [`GLOSSARY.md`](GLOSSARY.md): the domain words. [`docs/requirements.md`](docs/requirements.md):
  requirements and dated decisions.

## Attribution

The controls are the VDA ISA 2027.1, © ENX Association, licensed under CC BY-ND 4.0, and shipped
unchanged (English and German editions). See
[`src/vouch/data/vda-isa-2027.1/NOTICE.md`](src/vouch/data/vda-isa-2027.1/NOTICE.md).
