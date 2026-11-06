# Full-context comparison (Nordlicht)

A side test, outside Vouch's agent: can a model judge Nordlicht's 84 requirements when all 20 evidence documents are in its prompt? It shows how many points Vouch's search loop loses (follow-up #188).

- `build.py <checkout> <out>` writes the prompt (`bundle.md`): the rules Vouch's agent follows, every requirement, and every evidence document. The expected answers are not in it.
- Each model answered the bundle once, in one pass, as JSON (`control`, `requirement`, `verdict`, `cites`, `why`): `opus.json` (Opus 5.5), `fable.json` (Fable 5.1), `sonnet.json` (Sonnet 5.5) and `haiku.json` (Haiku 5.5). They ran on a Claude subscription on 2026-11-05, so there was no per-token cost.
- `score.py <checkout> <this folder> opus fable sonnet haiku` scores them against `fixtures/nordlicht/expected-answers.yaml`, using the same roll-up as the eval runner, and writes `scores.json`.

There was no search, quote check or output guard, so these scores are not comparable one-for-one with the runs in `evals/kept/`. `scripts/eval-site.py` puts both on the report page, `docs/evals/`.
