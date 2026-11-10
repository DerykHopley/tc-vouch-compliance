# Vouch

Vouch is an audit-readiness agent for ISMS and TISAX. For each control it searches the company's
policies and records, judges each requirement as Evidenced, Partly evidenced or Not evidenced
(Not applicable when the company's profile excludes it), and drafts an answer for the auditor
that quotes its sources word for word. The compliance lead reviews every verdict in three steps:
**Confirm findings** (each requirement's verdict confirmed or overridden), **Plan fixes** (each
remediation item approved or discarded) and **Accept**. Only accepted verdicts count towards
readiness.

Each compliance profile has a goal: **Audit readiness**, preparing for an external audit, or
**Internal improvement**, improving the company's own security with no audit planned, through
review rounds, an improvement plan by priority, open questions and an improvement report. The
goals and the review steps are a demo/prototype process: a real SaaS product might do it
differently.

For a small profile, Vouch can also run a **profile pass**: one model run over every applicable
control with all the profile's evidence in front of it, each result waiting for the lead's review.
The same guards hold as for the per-control agent (the quote check, the staleness rule, the output
guard). It runs only while its prompt and expected reply fit under a token threshold (100,000
tokens by default; above it, Vouch offers the per-control agent) and has its own daily limit of 3
passes, apart from the assessments'. On Sonnet 5.5, a pass over 12 controls costs about
$0.30–0.50.

## Who it's for

- **A compliance lead** preparing a TISAX or ISO 27001 (ISMS) audit, working through the VDA ISA
  controls one piece of evidence at a time.
- **A compliance lead improving the company's own security** with no audit planned, round by
  round, under the Internal improvement goal.
- **The readiness report's readers:** management and the auditor, who read where the company
  stands, what is missing and who reviewed each verdict.
- **The improvement report's readers:** the company's own team, who read what changed since the
  last review round, the plan by priority with each item's status, and the open questions.

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
- [`docs/demo.md`](docs/demo.md): the demo in three parts: Part 1, Audit readiness (on the live
  app, with the local run as a fallback), Part 2, Keep improving (the Internal improvement goal
  and the profile pass, on the local app), and Part 3, Company setup (a developer sets up a new
  company and removes it again, on the local app).
- [`docs/technical-decisions.md`](docs/technical-decisions.md),
  [`docs/course-tasks.md`](docs/course-tasks.md),
  [`docs/eval-summary.md`](docs/eval-summary.md) and [`docs/reflection.md`](docs/reflection.md).
- [`docs/eval-report.md`](docs/eval-report.md): the full eval report.
- [How Vouch runs](https://derykhopley.github.io/tc-vouch-compliance/flow/) ([`docs/flow/index.html`](docs/flow/index.html)):
  the runtime process flow, from the lead's click through FastAPI, the LangGraph graphs and the
  model calls to what is written to Postgres, the three-step review, the chat and the profile
  pass.
- [`DEPLOY.md`](DEPLOY.md): deploying to Cloud Run.
- [`GLOSSARY.md`](GLOSSARY.md): the domain words. [`docs/requirements.md`](docs/requirements.md):
  requirements and dated decisions.

## Attribution

The controls are the VDA ISA 2027.1, © ENX Association, licensed under CC BY-ND 4.0, and shipped
unchanged (English and German editions). See
[`src/vouch/data/vda-isa-2027.1/NOTICE.md`](src/vouch/data/vda-isa-2027.1/NOTICE.md).
