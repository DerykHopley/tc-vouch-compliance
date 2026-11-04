# Vouch

Vouch is an audit-readiness agent for ISMS and TISAX. For each control it searches the company's
policies, judges whether the evidence covers the control, and drafts an answer for the auditor,
citing its sources.

## Run it locally

You need [uv](https://docs.astral.sh/uv/) and Python 3.12.

```sh
uv sync
ASSESS_FAKE_SCRIPT=tests/scripts/enc_found.json uv run assess NK-ENC-01
```

This assesses control `NK-ENC-01` on the scripted fake model, offline and free, and prints the
Assessment as JSON. The controls and policies it reads are the demo company's, in
`fixtures/nordlicht/`.

To use a real model instead, leave `ASSESS_FAKE_SCRIPT` unset and set `OPENROUTER_API_KEY` and
`ASSESS_MODEL`. That is a paid call: load the key from the system keyring, never from a file in
the repo.

## Checks

```sh
uv run ruff check
uv run pytest            # sockets are disabled; no test needs an API key
```

## Docs

- [`docs/`](docs/): requirements and decisions, design, and the project brief.
- [`GLOSSARY.md`](GLOSSARY.md): the domain words.
- Work is tracked in this repo's GitHub issues.
