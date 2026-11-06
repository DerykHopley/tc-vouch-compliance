# Eval summary

A one-minute read of how Vouch's assessments were measured. The full tables and their reading are in [`eval-report.md`](eval-report.md); the same results are published at <https://derykhopley.github.io/tc-vouch-compliance/evals/>. The words are [`GLOSSARY.md`](../GLOSSARY.md)'s: Eval, Eval case, Judge model, Faithfulness, Dry run, Kept results, Eval report.

## What it measures

Each eval case is one control of a compliance profile, with a hand-written expected verdict for each applicable requirement and the evidence documents a good answer cites. The runner ([`vouch.evals`](../src/vouch/evals.py)) runs a real assessment on each case and scores:

- **Verdict accuracy:** right verdicts of all requirements scored.
- **Control accuracy:** controls whose rolled-up verdict is the expected one.
- **Citation precision:** cited documents that are expected, of all documents cited after the quote check.
- **Faithfulness:** DeepEval's faithfulness of each answer to the passages it cites, scored by a judge model ([`vouch.faithfulness`](../src/vouch/faithfulness.py)).
- **Cost and time:** tokens, US dollars and seconds per assessment.

## On which cases

24 eval cases: the 12 VDA ISA controls under each of two profiles.

- **Nordlicht** (English, 84 requirements). Its expected answers were reviewed by the owner, so it is the stronger signal.
- **Alpenwerk** (German, high protection, 100 requirements). Its expected answers were drafted by an agent and approved as drafted, so its scores measure agreement with that draft.

The expected answers live beside each profile: [`fixtures/nordlicht/expected-answers.yaml`](../fixtures/nordlicht/expected-answers.yaml), [`fixtures/alpenwerk/expected-answers.yaml`](../fixtures/alpenwerk/expected-answers.yaml).

## Which models, and which judge

- **On both profiles:** Anthropic's Haiku 5.5 and Sonnet 5.5.
- **On Nordlicht only:** TypeSafe's Jev Router (picks a model per request), OpenAI's GPT-6 Luna, GPT-6.1 Sol and GPT-6.1 Terra, and gpt-oss 20B run locally through Ollama.
- **Judge model:** GPT-6.1 Terra, from another provider than the Anthropic models, so they never judge their own answers. OpenAI's three models were judged by Sonnet 5.5 instead, for the same reason. With two judges, faithfulness isn't strictly comparable between the two groups.

The list is the runner's (`vouch.eval_models.MODELS` and `JUDGE`); `uv run python -m vouch.evals --models` prints it with prices.

## Headline figures

Copied from [`eval-report.md`](eval-report.md) (nine kept runs, 2026-11-05). Cost leaves out the judge.

| Model | Verdicts, Nordlicht | Controls, Nordlicht | Verdicts, Alpenwerk | Controls, Alpenwerk | Cost per assessment, Nordlicht |
|---|---|---|---|---|---|
| Sonnet 5.5 | 76% (64/84) | 75% (9/12) | 50% (50/100) | 50% (6/12) | $0.1011 |
| Jev Router | 70% (59/84) | 75% (9/12) | not run | not run | $0.0327 |
| GPT-6.1 Sol | 69% (58/84) | 58% (7/12) | not run | not run | $0.0568 |
| GPT-6 Luna | 68% (57/84) | 58% (7/12) | not run | not run | $0.0032 |
| GPT-6.1 Terra | 57% (48/84) | 25% (3/12) | not run | not run | $0.0345 |
| Haiku 5.5 | 51% (43/84) | 42% (5/12) | 58% (58/100) | 58% (7/12) | $0.0062 |
| gpt-oss 20B (local) | 8% (7/84) | 0% (0/12) | not run | not run | $0.0000 |

- **Citation precision** is 71% to 82% for every hosted model, so it doesn't separate them.
- **Faithfulness** is 0.99 or 1.00 for every run, so it doesn't separate them either: Vouch already drops any quote not found in the evidence, and DeepEval's measure only counts claims the passages contradict.
- **Time:** 30 to 41 seconds per assessment for most hosted models; the Jev Router 66 seconds on Nordlicht.
- **Failed cases:** Haiku one (Nordlicht 5.1.2: the output check refused its answer twice for saying "passed"); gpt-oss 20B seven of twelve (five over the call budget, two Ollama tool-call errors).
- **Small samples:** 12 controls per run. The report compares models control by control and calls a lead real only when a sign test gives p under 0.05.

## What the default-model choice took from them

The default is **Sonnet 5.5** (the owner on #55, 2026-11-06; [`requirements.md`, Decisions](requirements.md#decisions)):

- It is the only model with a lead that holds control by control: over Haiku on Nordlicht on 10 of 11 controls that differ (p = 0.01), and over Terra on 8 of 8.
- It has the best verdicts on the owner-reviewed Nordlicht answers (76%) and failed no case.
- GPT-6 Luna is the cheap alternative (68% at about 1/32 of Sonnet's cost), but 12 controls can't tell it apart from Sonnet, and it hasn't run on the German profile.
- Sonnet's Alpenwerk score is low mostly because its judge step left 27 requirements unjudged, shown as not-evidenced (the report's ["Sonnet's unjudged requirements"](eval-report.md#sonnets-unjudged-requirements); Fix #184).
- **The pipeline matters more than the model:** with all of Nordlicht's evidence in its prompt, outside Vouch's search-and-judge graph, Sonnet scores 90% and Haiku 80% ([outside the pipeline](eval-report.md#outside-the-pipeline-the-models-with-all-the-evidence-in-front-of-them)). Better retrieval and the call budget are the bigger gain.
- **The local model can't drive the graph yet** (8% of verdicts), so keeping evidence on the company's own machine costs too much accuracy today ([the local model](eval-report.md#the-local-model-what-confidentiality-costs)).

## Rerun it

**Offline and free**, on the scripted fake model (no key, no network), as the test suite does:

```sh
uv run python -m vouch.evals                      # Nordlicht
uv run python -m vouch.evals --profile alpenwerk
```

**Re-render the report** from the kept results, again without calling a model:

```sh
uv run python -m vouch.evals report evals/kept --out docs/eval-report.md
```

**A real run** costs money and waits for the owner's OK. With `OPENROUTER_API_KEY` set (from the keyring, through `.envrc`), name a model; the runner picks the judge:

```sh
uv run python -m vouch.evals --model anthropic/claude-sonnet-5.5 --keep
```

It starts with a **dry run**: the first case alone, then a cost and time estimate for the rest, which run only after a typed "yes". `--keep` copies the raw results to `evals/kept/`, the **kept results** the report renders from. A local model (`--model ollama/gpt-oss:20b`) costs nothing but needs Ollama running with the model pulled.

**What a real run costs:** the nine kept runs cost $6.57 together, the judge included. Per assessment, without the judge: GPT-6 Luna about $0.003, Haiku $0.006 to $0.008, Sonnet $0.10 to $0.12; so all 12 controls of one profile on Sonnet cost about $1.21 to $1.46 before judging.
