<!-- eval tables: rendered by `python -m vouch.evals report`, edits here are lost -->
# Eval report

Rendered from the saved raw results by `python -m vouch.evals report`, without calling a model. Each cell is the newest complete run of that model on that profile; a partial run or a dry run stands in, marked, only when there is no complete one.

## Verdict accuracy

Right verdicts of all requirements scored (a failed case's count as wrong).

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 58% (58/100) | 51% (43/84) |
| anthropic/claude-sonnet-5.5 | 50% (50/100) | 76% (64/84) |
| ollama/gpt-oss:20b | not run | 8% (7/84) |
| typesafe/jev-router | not run | 70% (59/84) |

## Control accuracy

Controls whose rolled-up verdict is the expected one.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 58% (7/12) | 42% (5/12) |
| anthropic/claude-sonnet-5.5 | 50% (6/12) | 75% (9/12) |
| ollama/gpt-oss:20b | not run | 0% (0/12) |
| typesafe/jev-router | not run | 75% (9/12) |

## Citation precision

Cited documents that are expected, of all documents cited after the quote check.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 76% (109/144) | 75% (82/109) |
| anthropic/claude-sonnet-5.5 | 82% (93/113) | 79% (101/128) |
| ollama/gpt-oss:20b | not run | 43% (3/7) |
| typesafe/jev-router | not run | 78% (81/104) |

## Faithfulness

DeepEval's faithfulness of each answer to its cited passages, by the judge model; the mean over the answers judged (one citing nothing or claiming nothing isn't).

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 1.00 (12 judged) | 1.00 (11 judged) |
| anthropic/claude-sonnet-5.5 | 1.00 (12 judged) | 1.00 (11 judged) |
| ollama/gpt-oss:20b | not run | 1.00 (1 judged) |
| typesafe/jev-router | not run | 1.00 (11 judged) |

## Tokens per assessment

Input and output tokens, the mean over the cases that ran.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 43217 | 35614 (1 failed, left out) |
| anthropic/claude-sonnet-5.5 | 39432 | 33741 |
| ollama/gpt-oss:20b | not run | 16301 (7 failed, left out) |
| typesafe/jev-router | not run | 34424 |

## Cost per assessment

US dollars, the mean over the cases that ran: OpenRouter's reported cost, else the model's listed price; a local model costs nothing. The judge's cost is not included.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | $0.0083 | $0.0062 (1 failed, left out) |
| anthropic/claude-sonnet-5.5 | $0.1214 | $0.1011 |
| ollama/gpt-oss:20b | not run | $0.0000 (7 failed, left out) |
| typesafe/jev-router | not run | $0.0327 |

## Seconds per assessment

Wall-clock seconds, the mean over the cases that ran.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 40.8 s | 31.4 s (1 failed, left out) |
| anthropic/claude-sonnet-5.5 | 33.8 s | 30.3 s |
| ollama/gpt-oss:20b | not run | 31.4 s (7 failed, left out) |
| typesafe/jev-router | not run | 65.9 s |

## Runs

- `20261105T140418420437Z-nordlicht-ollama_gpt-oss_20b.json`: ollama/gpt-oss:20b on nordlicht, judged by openai/gpt-6.1-terra, run 2026-11-05T14:04:18.420437+00:00, 12 cases, complete
- `20261105T141531804090Z-nordlicht-anthropic_claude-haiku-5.5.json`: anthropic/claude-haiku-5.5 on nordlicht, judged by openai/gpt-6.1-terra, run 2026-11-05T14:15:31.804090+00:00, 12 cases, complete
- `20261105T153410983625Z-nordlicht-anthropic_claude-sonnet-5.5.json`: anthropic/claude-sonnet-5.5 on nordlicht, judged by openai/gpt-6.1-terra, run 2026-11-05T15:34:10.983625+00:00, 12 cases, complete
- `20261105T154650528535Z-alpenwerk-anthropic_claude-haiku-5.5.json`: anthropic/claude-haiku-5.5 on alpenwerk, judged by openai/gpt-6.1-terra, run 2026-11-05T15:46:50.528535+00:00, 12 cases, complete
- `20261105T160256693705Z-alpenwerk-anthropic_claude-sonnet-5.5.json`: anthropic/claude-sonnet-5.5 on alpenwerk, judged by openai/gpt-6.1-terra, run 2026-11-05T16:02:56.693705+00:00, 12 cases, complete
- `20261105T164311153561Z-nordlicht-typesafe_jev-router.json`: typesafe/jev-router on nordlicht, judged by openai/gpt-6.1-terra, run 2026-11-05T16:43:11.153561+00:00, 12 cases, complete
<!-- end of eval tables -->

## Reading the numbers

Written by hand around the tables above (#55); `python -m vouch.evals report evals/kept --out docs/eval-report.md` re-renders only the tables block and keeps this. The raw results are in `evals/kept/` (six runs, 2026-11-05, #54).

### Before comparing

- **Nordlicht is the stronger signal.** Its expected answers were reviewed by the owner (#111). Alpenwerk's were drafted by an agent; the owner reviewed 5 of the 100 requirements, found nothing to correct, and approved the set as drafted, so the other 95 stand as the agent wrote them (#50). **Alpenwerk's scores measure agreement with that draft**, not checked correctness.
- **The Jev Router (`typesafe/jev-router`) ran on Nordlicht only.** It picks a model for each request, and the results record only the router, not which models it picked. It may route to OpenAI, the judge's provider, so **its faithfulness of 1.00 may include answers judged by their own provider**.
- **Sonnet's judge step left requirements unjudged** (below, "Sonnet's unjudged requirements"): 27 of Alpenwerk's 100 and 2 of Nordlicht's 84 are recorded "not judged", which Vouch shows as not-evidenced. Haiku and the router have none.
- **Small samples, compared control by control.** Each run is 12 controls (84 requirements on Nordlicht, 100 on Alpenwerk). Requirements within one control share their evidence and the same judgement, so they aren't independent: counting requirements overstates the sample. The rule used here: for two models, count the controls where each got more requirements right (ties left out), and call the difference real only when one model leads on clearly more controls than chance gives (a sign test, two-sided p under 0.05). By that rule a lead on 10 of 11 controls is real (p = 0.01); 6 of 10 (p = 0.75) or 4 of 7 (p = 1.0) is noise.
- **What the runs cost:** $4.27 for these six runs, the judge included (Sonnet $1.40 and $1.69, the router $0.59, Haiku $0.25 and $0.34, gpt-oss $0.01 for the judge alone). The cost table above leaves the judge out.

### Verdicts

- **On Nordlicht, Sonnet 5.5 is best:** 76% of verdicts (64/84) and 75% of controls (9/12). Haiku 5.5 gets 51% (43/84) and 42% (5/12). Of the 21-verdict gap, 5 of Haiku's wrong verdicts come from its failed case 5.1.2 (all 5 requirements count wrong when the output check refuses the answer; Sonnet got 4 of them). Without that control the gap is 17 (60/79 against 43/79). **Control by control, Sonnet got more right on 10 controls and Haiku on 1** (1.1.1), with 1 tie: a real difference by the rule above.
- **The Jev Router comes close to Sonnet:** 70% of verdicts (59/84) and the same 75% of controls (9/12). Control by control Sonnet leads on 4, the router on 3, 5 tied: noise.
- **On Alpenwerk, Haiku 58% (58/100) and Sonnet 50% (50/100) is within noise, not a reversal:** an 8-requirement and 1-control gap, and control by control Haiku leads on 4, Sonnet on 6, 2 tied (p = 0.75). Most of Sonnet's shortfall is its unjudged requirements: on the four controls where its judge left requirements out (2.1.3, 4.2.1, 5.2.4, 5.2.9) it got 2 of 31 right and Haiku 24. On the other 8 controls Sonnet leads, 48 of 69 against Haiku's 34, and 5 controls right against 4. Agreement with the agent's draft (above) is a second reason to read Alpenwerk loosely.
- **Haiku failed one case** (Nordlicht 5.1.2): the output check refused its answer twice for claiming an audit result ("passed"). That is the guard working, but the user gets no assessment of that control. Sonnet and the router failed none.

### Sonnet's unjudged requirements

In five of Sonnet's 24 assessments the judge step kept a verdict for the first requirement only (M1) and recorded every other one as "not judged" (`graph.NOT_JUDGED`), which Vouch shows as not-evidenced:

| Profile | Control | Requirements | Not judged |
|---|---|---|---|
| alpenwerk | 2.1.3 | 7 | S1 to S6 (6) |
| alpenwerk | 4.2.1 | 8 | M2, S1 to S5, H1 (7) |
| alpenwerk | 5.2.4 | 10 | M2 to M5, S1 to S3, H1, H2 (9) |
| alpenwerk | 5.2.9 | 6 | M2, S1, H1 to H3 (5) |
| nordlicht | 5.2.9 | 3 | M2, S1 (2) |

- **The answers weren't the problem.** Sonnet's own answer for Alpenwerk 4.2.1 says M1, S1 to S5 and H1 are evidenced and M2 partly, yet the judge step recorded only M1.
- **Not German, not size.** It happened on English Nordlicht too (5.2.9), and not on Alpenwerk's largest control (4.1.3, 17 requirements, all judged). Haiku and the router, given the same prompts, have no unjudged requirement.
- **What the saved results can't say:** whether Sonnet's structured judgement listed only M1, named the others differently, or gave them a verdict Vouch doesn't accept. Vouch keeps only the checked verdicts, not the judge's raw reply, and records no warning when requirements go unjudged, so the cause can't be read back without running the model again. Vouch sets no output-token limit, so a cut-off reply isn't from Vouch's side.
- **What it means for the scores:** Sonnet's Alpenwerk verdicts and controls are understated (where the expected verdict was not-evidenced, a "not judged" happens to score right, as on Nordlicht 5.2.9, so it can cut both ways). For a user it is worse than a score: requirements the answer calls evidenced show as not-evidenced, with "not judged" as the reason.

### Citations

Every hosted model cites mostly expected documents: 75% to 82%. Sonnet is a little higher on both profiles (79% and 82%) than Haiku (75% and 76%) and the router (78%): a few points, not tested control by control. Citations don't separate the hosted models; verdicts do.

### Faithfulness

**Faithfulness came out 1.00 on every judged run, so on this eval it doesn't discriminate.** Two reasons, both from how Vouch and the measure work, not from the models being equally good:

- Vouch already refuses a citation whose quote isn't word for word in the evidence (M1's quote check), and the output check refuses an answer that claims an audit result. What reaches the judge is already tied to real passages.
- DeepEval's faithfulness only lowers the score for a claim the passages contradict; a claim they don't mention counts as backed (`vouch.faithfulness`, the library's default). The answers summarise their own quotes, so there is little to contradict.

Answers citing nothing aren't judged (4 of gpt-oss's 5 answers that ran, 1 of Sonnet's and 1 of the router's on Nordlicht). To make this measure useful, it would need a stricter metric (unsupported claims counted against the answer) or harder cases; until then, verdict accuracy is the measure to choose by.

### Cost and time

- **Per assessment:** Haiku $0.006 to $0.008, the router $0.033, Sonnet $0.10 to $0.12. Sonnet costs 15 to 16× Haiku; the router about 5× Haiku and a third of Sonnet.
- **Per full assessment of a company (12 controls):** Haiku about $0.07 to $0.10, the router about $0.39, Sonnet about $1.21 to $1.46.
- **Against the daily limit** (30 assessments per user, `DAILY_LIMIT`): at most about $0.25 a user a day on Haiku, $1 on the router, $3.64 on Sonnet.
- **Time:** Haiku and Sonnet take 30 to 41 s per assessment; the router 66 s, about twice as long.

### The local model: what confidentiality costs

A local model keeps the evidence on the company's own machine, which is the confidentiality case for it. **As the graph stands, gpt-oss 20B can't drive it:**

- **7 of 12 Nordlicht cases failed:** 5 spent the call budget (more than 12 model calls without finishing), and 2 hit Ollama errors parsing the model's tool calls.
- Of the 5 that ran, 4 cited nothing; citation precision was 43% (3 of 7 cited documents expected).
- **Verdicts: 8% (7/84) and 0 of 12 controls,** against Sonnet's 76% and 75% and Haiku's 51% and 42%.

So keeping the evidence local, with this model on this hardware, costs about **68 points of verdict accuracy against Sonnet (43 against Haiku)**, to save $0.006 to $0.12 per assessment. That trade isn't worth it today. What could change it: a larger local model (the 20 GB card rules out the next sizes, `docs/requirements.md`, 2026-11-05), a graph that asks a local model for fewer, simpler tool calls, or an EU-hosted provider as the middle ground (Mistral was the one picked for that, and the owner's OpenRouter guardrail blocked it).

### Recommendation: the default model

**Recommended: Sonnet 5.5 (`anthropic/claude-sonnet-5.5`).** It is best on the owner-reviewed Nordlicht answers (76% of verdicts against Haiku's 51%, 75% of controls against 42%, ahead on 10 of 11 controls that differ), failed no case, and costs $0.10 to $0.12 per assessment, at most about $3.64 a user a day under the daily limit.

- **The caveat: Sonnet's judge step left requirements unjudged** in 5 of 24 assessments (27 of Alpenwerk's 100 requirements, 2 of Nordlicht's 84), shown to the user as not-evidenced. Until the cause is found and handled (the raw judge reply kept, missing requirements asked for again or flagged), choosing Sonnet means some assessments understate what the evidence shows. Haiku and the router didn't do this.
- **Why not Haiku 5.5** (today's default): 15 to 16× cheaper, but 25 points lower on Nordlicht's verdicts (22 without its failed case), behind on 10 of 11 controls that differ, and one refused answer. Its Alpenwerk lead over Sonnet is within noise and comes from Sonnet's unjudged requirements.
- **Why not the Jev Router:** close to Sonnet (70%, the same 9 of 12 controls, no difference control by control) at a third of the price, which makes it the one to watch. But it ran on one profile only, takes twice as long, and its results don't say which models answered, so where a company's evidence goes isn't known, which a compliance tool should be able to say. Worth a second run on Alpenwerk, with routing recorded, before choosing it.
- **Not decided here.** The owner chooses; `DEFAULT_MODEL` stays Haiku and Haiku's price stays in `PRICES` until then (#55's third criterion). This branch only took Mistral Small 4, which didn't run, out of `PRICES`.
