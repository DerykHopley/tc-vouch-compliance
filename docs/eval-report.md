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
| openai/gpt-6-luna | not run | 68% (57/84) |
| openai/gpt-6.1-sol | not run | 69% (58/84) |
| openai/gpt-6.1-terra | not run | 57% (48/84) |
| typesafe/jev-router | not run | 70% (59/84) |

## Control accuracy

Controls whose rolled-up verdict is the expected one.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 58% (7/12) | 42% (5/12) |
| anthropic/claude-sonnet-5.5 | 50% (6/12) | 75% (9/12) |
| ollama/gpt-oss:20b | not run | 0% (0/12) |
| openai/gpt-6-luna | not run | 58% (7/12) |
| openai/gpt-6.1-sol | not run | 58% (7/12) |
| openai/gpt-6.1-terra | not run | 25% (3/12) |
| typesafe/jev-router | not run | 75% (9/12) |

## Citation precision

Cited documents that are expected, of all documents cited after the quote check.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 76% (109/144) | 75% (82/109) |
| anthropic/claude-sonnet-5.5 | 82% (93/113) | 79% (101/128) |
| ollama/gpt-oss:20b | not run | 43% (3/7) |
| openai/gpt-6-luna | not run | 78% (90/116) |
| openai/gpt-6.1-sol | not run | 74% (89/121) |
| openai/gpt-6.1-terra | not run | 71% (89/125) |
| typesafe/jev-router | not run | 78% (81/104) |

## Faithfulness

DeepEval's faithfulness of each answer to its cited passages, by the judge model; the mean over the answers judged (one citing nothing or claiming nothing isn't).

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 1.00 (12 judged) | 1.00 (11 judged) |
| anthropic/claude-sonnet-5.5 | 1.00 (12 judged) | 1.00 (11 judged) |
| ollama/gpt-oss:20b | not run | 1.00 (1 judged) |
| openai/gpt-6-luna | not run | 1.00 (12 judged) |
| openai/gpt-6.1-sol | not run | 1.00 (11 judged) |
| openai/gpt-6.1-terra | not run | 0.99 (11 judged) |
| typesafe/jev-router | not run | 1.00 (11 judged) |

## Tokens per assessment

Input and output tokens, the mean over the cases that ran.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 43217 | 35614 (1 failed, left out) |
| anthropic/claude-sonnet-5.5 | 39432 | 33741 |
| ollama/gpt-oss:20b | not run | 16301 (7 failed, left out) |
| openai/gpt-6-luna | not run | 22805 |
| openai/gpt-6.1-sol | not run | 30215 |
| openai/gpt-6.1-terra | not run | 33098 |
| typesafe/jev-router | not run | 34424 |

## Cost per assessment

US dollars, the mean over the cases that ran: OpenRouter's reported cost, else the model's listed price; a local model costs nothing. The judge's cost is not included.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | $0.0083 | $0.0062 (1 failed, left out) |
| anthropic/claude-sonnet-5.5 | $0.1214 | $0.1011 |
| ollama/gpt-oss:20b | not run | $0.0000 (7 failed, left out) |
| openai/gpt-6-luna | not run | $0.0032 |
| openai/gpt-6.1-sol | not run | $0.0568 |
| openai/gpt-6.1-terra | not run | $0.0345 |
| typesafe/jev-router | not run | $0.0327 |

## Seconds per assessment

Wall-clock seconds, the mean over the cases that ran.

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | 40.8 s | 31.4 s (1 failed, left out) |
| anthropic/claude-sonnet-5.5 | 33.8 s | 30.3 s |
| ollama/gpt-oss:20b | not run | 31.4 s (7 failed, left out) |
| openai/gpt-6-luna | not run | 38.2 s |
| openai/gpt-6.1-sol | not run | 38.9 s |
| openai/gpt-6.1-terra | not run | 32.4 s |
| typesafe/jev-router | not run | 65.9 s |

## Served models

The models OpenRouter named as serving the assessments' calls, with how many each served: a router's picks. "As requested" when the model asked for served them all; "not recorded" for a run saved before served models were (#185).

| Model | alpenwerk | nordlicht |
|---|---|---|
| anthropic/claude-haiku-5.5 | not recorded | not recorded |
| anthropic/claude-sonnet-5.5 | not recorded | not recorded |
| ollama/gpt-oss:20b | not run | not recorded |
| openai/gpt-6-luna | not run | not recorded |
| openai/gpt-6.1-sol | not run | not recorded |
| openai/gpt-6.1-terra | not run | not recorded |
| typesafe/jev-router | not run | not recorded |

## Runs

- `20261105T140418420437Z-nordlicht-ollama_gpt-oss_20b.json`: ollama/gpt-oss:20b on nordlicht, judged by openai/gpt-6.1-terra, run 2026-11-05T14:04:18.420437+00:00, 12 cases, complete
- `20261105T141531804090Z-nordlicht-anthropic_claude-haiku-5.5.json`: anthropic/claude-haiku-5.5 on nordlicht, judged by openai/gpt-6.1-terra, run 2026-11-05T14:15:31.804090+00:00, 12 cases, complete
- `20261105T153410983625Z-nordlicht-anthropic_claude-sonnet-5.5.json`: anthropic/claude-sonnet-5.5 on nordlicht, judged by openai/gpt-6.1-terra, run 2026-11-05T15:34:10.983625+00:00, 12 cases, complete
- `20261105T154650528535Z-alpenwerk-anthropic_claude-haiku-5.5.json`: anthropic/claude-haiku-5.5 on alpenwerk, judged by openai/gpt-6.1-terra, run 2026-11-05T15:46:50.528535+00:00, 12 cases, complete
- `20261105T160256693705Z-alpenwerk-anthropic_claude-sonnet-5.5.json`: anthropic/claude-sonnet-5.5 on alpenwerk, judged by openai/gpt-6.1-terra, run 2026-11-05T16:02:56.693705+00:00, 12 cases, complete
- `20261105T164311153561Z-nordlicht-typesafe_jev-router.json`: typesafe/jev-router on nordlicht, judged by openai/gpt-6.1-terra, run 2026-11-05T16:43:11.153561+00:00, 12 cases, complete
- `20261105T173042244875Z-nordlicht-openai_gpt-6-luna.json`: openai/gpt-6-luna on nordlicht, judged by anthropic/claude-sonnet-5.5, run 2026-11-05T17:30:42.244875+00:00, 12 cases, complete
- `20261105T174116188329Z-nordlicht-openai_gpt-6.1-terra.json`: openai/gpt-6.1-terra on nordlicht, judged by anthropic/claude-sonnet-5.5, run 2026-11-05T17:41:16.188329+00:00, 12 cases, complete
- `20261105T175055701165Z-nordlicht-openai_gpt-6.1-sol.json`: openai/gpt-6.1-sol on nordlicht, judged by anthropic/claude-sonnet-5.5, run 2026-11-05T17:50:55.701165+00:00, 12 cases, complete
<!-- end of eval tables -->

## Reading the numbers

Written by hand around the tables above (#55); `python -m vouch.evals report evals/kept --out docs/eval-report.md` re-renders only the tables block and keeps this. The raw results are in `evals/kept/` (nine runs, 2026-11-05: six from #54, three OpenAI models from #55).

### Before comparing

- **Nordlicht is the stronger signal.** Its expected answers were reviewed by the owner (#111). Alpenwerk's were drafted by an agent; the owner reviewed 5 of the 100 requirements, found nothing to correct, and approved the set as drafted, so the other 95 stand as the agent wrote them (#50). **Alpenwerk's scores measure agreement with that draft**, not checked correctness.
- **The Jev Router (`typesafe/jev-router`) ran on Nordlicht only.** It picks a model for each request. Its kept run predates #185, which records the model that served each call, so the "Served models" table shows "not recorded" and its picks are unknown. It may route to OpenAI, the judge's provider, so **its faithfulness of 1.00 may include answers judged by their own provider**.
- **Two judges.** The six #54 runs were judged by `openai/gpt-6.1-terra`. The three OpenAI models (GPT-6 Luna, GPT-6.1 Sol, GPT-6.1 Terra) were judged by `anthropic/claude-sonnet-5.5` instead, so OpenAI never judges its own answers. With different judges, **faithfulness isn't strictly comparable between the two groups**.
- **Sonnet's judge step left requirements unjudged** (below, "Sonnet's unjudged requirements"): 27 of Alpenwerk's 100 and 2 of Nordlicht's 84 are recorded "not judged", which Vouch shows as not-evidenced. Haiku, the router and the three OpenAI models have none. Fix #184 handles it (the judge asked again for skipped requirements, and a warning when they stay skipped).
- **Small samples, compared control by control.** Each run is 12 controls (84 requirements on Nordlicht, 100 on Alpenwerk). Requirements within one control share their evidence and the same judgement, so they aren't independent: counting requirements overstates the sample. The rule used here: for two models, count the controls where each got more requirements right (ties left out), and call the difference real only when one model leads on clearly more controls than chance gives (a sign test, two-sided p under 0.05). By that rule a lead on 10 of 11 controls is real (p = 0.01); 6 of 10 (p = 0.75) or 4 of 7 (p = 1.0) is noise.
- **What the runs cost:** $6.57 for these nine runs, the judge included: $4.27 for #54's six (Sonnet $1.40 and $1.69, the router $0.59, Haiku $0.25 and $0.34, gpt-oss $0.01 for the judge alone) and $2.29 for the OpenAI three (Sol $1.13, Terra $0.79, Luna $0.37). The cost table above leaves the judge out.

### Verdicts

- **On Nordlicht, Sonnet 5.5 is best:** 76% of verdicts (64/84) and 75% of controls (9/12). Haiku 5.5 gets 51% (43/84) and 42% (5/12). Of the 21-verdict gap, 5 of Haiku's wrong verdicts come from its failed case 5.1.2 (all 5 requirements count wrong when the output check refuses the answer; Sonnet got 4 of them). Without that control the gap is 17 (60/79 against 43/79). **Control by control, Sonnet got more right on 10 controls and Haiku on 1** (1.1.1), with 1 tie: a real difference by the rule above.
- **The Jev Router comes close to Sonnet:** 70% of verdicts (59/84) and the same 75% of controls (9/12). Control by control Sonnet leads on 4, the router on 3, 5 tied: noise.
- **On Alpenwerk, Haiku 58% (58/100) and Sonnet 50% (50/100) is within noise, not a reversal:** an 8-requirement and 1-control gap, and control by control Haiku leads on 4, Sonnet on 6, 2 tied (p = 0.75). Most of Sonnet's shortfall is its unjudged requirements: on the four controls where its judge left requirements out (2.1.3, 4.2.1, 5.2.4, 5.2.9) it got 2 of 31 right and Haiku 24. On the other 8 controls Sonnet leads, 48 of 69 against Haiku's 34, and 5 controls right against 4. Agreement with the agent's draft (above) is a second reason to read Alpenwerk loosely.
- **OpenAI's models on Nordlicht** (below, "OpenAI's models through the pipeline"): GPT-6.1 Sol 69% (58/84), 7 of 12 controls; GPT-6 Luna 68% (57/84), 7 of 12; GPT-6.1 Terra 57% (48/84), 3 of 12. Control by control, only Terra's gap to Sonnet is real (Sonnet leads on 8 controls, Terra on none, p = 0.008). Luna and Sol against Sonnet (2 against 4, 6 tied, p = 0.69 each) and Luna against Haiku (7 against 3, p = 0.34) don't separate on 12 controls.
- **Haiku failed one case** (Nordlicht 5.1.2): the output check refused its answer twice for claiming an audit result ("passed"). That is the guard working, but the user gets no assessment of that control. No other hosted model failed a case.

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

### Outside the pipeline: the models with all the evidence in front of them

**A different setup from everything above, not comparable cell for cell.** Source: [#55's comment of 2026-11-05](https://github.com/DerykHopley/tc-vouch-compliance/issues/55#issuecomment-6664843496). Its raw answers aren't in `evals/kept`, so it lives here in the reading, not in the rendered tables.

- **Setup:** one pass per model, with the rules (the spec's verdict and staleness rules), Nordlicht's 84 requirements and all 20 of Nordlicht's evidence documents in full (47k characters, about 12k tokens) in context. No retrieval, no call budget, no quote check, no output guard. One run each, on the Claude subscription (no API cost).
- **Scored** against Nordlicht's owner-reviewed expected answers (#111), with the spec's roll-up. The models weren't shown those answers.

| Model | Verdicts right | Controls right | Citations expected | Vouch's pipeline, same model (verdicts / controls) |
|---|---|---|---|---|
| Sonnet 5.5 | 90% (76/84) | 83% (10/12) | 88% | 76% / 75% |
| Opus 5.5 | 89% (75/84) | 83% (10/12) | 85% | not run |
| Fable 5.1 | 88% (74/84) | 83% (10/12) | 82% | not run |
| Haiku 5.5 | 80% (67/84) | 75% (9/12) | 77% | 51% / 42% |

**What it says: Vouch's pipeline, not the model, loses most of the points.** The same model scores 14 points of verdicts lower inside Vouch's search-and-judge graph (Sonnet, 90% to 76%), and 29 lower for Haiku (80% to 51%). With the evidence in front of it, Haiku (80%) beats Sonnet inside the pipeline (76%), and Sonnet, Opus and Fable tie near 90%: past Sonnet, a larger model buys nothing here. The likely losses, each from the pipeline:

- **Retrieval:** the graph sees only the passages its searches find, so a requirement whose evidence it never searched for is judged on less.
- **The call budget** of 12 model calls caps how much it can search.
- **Skipped requirements** (Fix #184): a judgement that leaves requirements out records them as not judged (above).

**Caveats:** one run each, so a point or two either way is noise; no quote check, so a citation here isn't checked word for word as it is in the pipeline; no output guard, so an answer claiming an audit result wasn't refused; and whole-profile context works only because Nordlicht's evidence is small (about 12k tokens). A real company's evidence won't fit, which is why the pipeline searches.

### OpenAI's models through the pipeline

GPT-6 Luna, GPT-6.1 Sol and GPT-6.1 Terra ran through the same pipeline on Nordlicht (source: [#55's comment of 2026-11-05](https://github.com/DerykHopley/tc-vouch-compliance/issues/55#issuecomment-6667303085)), and their rows are in the rendered tables above. **They were judged by Sonnet 5.5, not Terra**, so OpenAI never judges its own answers; their faithfulness (Luna 1.00, Sol 1.00, Terra 0.99) isn't strictly comparable with the other runs', which Terra judged.

| Model | Verdicts right | Controls right | Cost per assessment | Against Sonnet, control by control |
|---|---|---|---|---|
| GPT-6.1 Sol | 69% (58/84) | 7/12 | $0.057 | Sonnet leads 4, Sol 2, 6 tied (p = 0.69) |
| GPT-6 Luna | 68% (57/84) | 7/12 | $0.003 | Sonnet leads 4, Luna 2, 6 tied (p = 0.69) |
| GPT-6.1 Terra | 57% (48/84) | 3/12 | $0.035 | Sonnet leads 8, Terra 0 (p = 0.008) |

- **GPT-6 Luna is the find:** Sol's accuracy (68% against 69%; Sol leads on 5 controls, Luna on 2, p = 0.45) at about half of Haiku's cost per assessment ($0.003 against $0.006), with no failed case and no unjudged requirement.
- **Against Sonnet** (76%, 9 of 12): Luna is 8 points and 2 controls lower, at about 1/32 of the cost. On 12 controls that gap doesn't separate them (p = 0.69), which doesn't show Luna as good as Sonnet: it shows they can't be told apart on this sample.
- **Against the router** (70%, 9 of 12): the router leads on 6 controls, Luna on 2 (p = 0.29), at about 10× Luna's cost.
- **Against Haiku** (51%, 5 of 12): Luna leads on 7 controls, Haiku on 3 (p = 0.34), at half the cost and without Haiku's refused answer.
- **Terra**, the judge of the other runs, is the weakest hosted assessor (57%, 3 of 12, behind Sonnet on 8 controls). That says nothing about it as a judge, which is a different task.
- **Not run:** none of the three ran on Alpenwerk, so how they do with German answers and the high-protection requirements isn't known.

### Citations

Every hosted model cites mostly expected documents: 71% to 82%. Sonnet is a little higher on both profiles (79% and 82%) than Haiku (75% and 76%), the router (78%), Luna (78%), Sol (74%) and Terra (71%): a few points, not tested control by control. Citations don't separate the hosted models; verdicts do.

### Faithfulness

**Faithfulness hardly moves: every run's mean is 0.99 or 1.00, so on this eval it doesn't discriminate between models.** Only three of the 92 judged answers scored below 1: one each from Haiku (0.96) and Sonnet (0.97) under Terra's judging, and from Terra (0.92) under Sonnet's. The two groups had different judges (above), so 0.99 against 1.00 isn't a comparison. Two reasons it stays near 1, both from how Vouch and the measure work, not from the models being equally good:

- Vouch already refuses a citation whose quote isn't word for word in the evidence (M1's quote check), and the output check refuses an answer that claims an audit result. What reaches the judge is already tied to real passages.
- DeepEval's faithfulness only lowers the score for a claim the passages contradict; a claim they don't mention counts as backed (`vouch.faithfulness`, the library's default). The answers summarise their own quotes, so there is little to contradict.

Answers citing nothing aren't judged (4 of gpt-oss's 5 answers that ran, and on Nordlicht 5.2.9 one each from Sonnet, the router, Sol and Terra). To make this measure useful, it would need a stricter metric (unsupported claims counted against the answer) or harder cases; until then, verdict accuracy is the measure to choose by.

### Cost and time

- **Per assessment:** GPT-6 Luna $0.003, Haiku $0.006 to $0.008, the router $0.033, GPT-6.1 Terra $0.035, GPT-6.1 Sol $0.057, Sonnet $0.10 to $0.12. Sonnet costs 15 to 16× Haiku and about 32× Luna; the router about 5× Haiku and a third of Sonnet. Luna lists at Haiku's price per token ($0.10 / $0.50, `PRICES`); it costs half as much per assessment because it used fewer tokens (22,805 against Haiku's 35,614 on Nordlicht).
- **Per full assessment of a company (12 controls):** Luna about $0.04, Haiku about $0.07 to $0.10, the router about $0.39, Sol about $0.68, Sonnet about $1.21 to $1.46.
- **Against the daily limit** (30 assessments per user, `DAILY_LIMIT`): at most about $0.10 a user a day on Luna, $0.25 on Haiku, $1 on the router, $1.70 on Sol, $3.64 on Sonnet.
- **Time:** Haiku, Sonnet and the OpenAI models take 30 to 41 s per assessment; the router 66 s, about twice as long.

### The local model: what confidentiality costs

A local model keeps the evidence on the company's own machine, which is the confidentiality case for it. **As the graph stands, gpt-oss 20B can't drive it:**

- **7 of 12 Nordlicht cases failed:** 5 spent the call budget (more than 12 model calls without finishing), and 2 hit Ollama errors parsing the model's tool calls.
- Of the 5 that ran, 4 cited nothing; citation precision was 43% (3 of 7 cited documents expected).
- **Verdicts: 8% (7/84) and 0 of 12 controls,** against Sonnet's 76% and 75% and Haiku's 51% and 42%.

So keeping the evidence local, with this model on this hardware, costs about **68 points of verdict accuracy against Sonnet (43 against Haiku)**, to save $0.006 to $0.12 per assessment. That trade isn't worth it today. What could change it: a larger local model (the 20 GB card rules out the next sizes, `docs/requirements.md`, 2026-11-05), a graph that asks a local model for fewer, simpler tool calls, or an EU-hosted provider as the middle ground (Mistral was the one picked for that, and the owner's OpenRouter guardrail blocked it).

### Recommendation: the default model

**Recommended: Sonnet 5.5 (`anthropic/claude-sonnet-5.5`).** It is the only model with a lead shown control by control: over Haiku on Nordlicht (10 of 11 controls that differ, p = 0.01) and over Terra (8 of 8). It has the best verdicts on the owner-reviewed Nordlicht answers (76%, 9 of 12 controls), is best with all the evidence in context too (90%, level with Opus and Fable), failed no case, and costs $0.10 to $0.12 per assessment, at most about $3.64 a user a day under the daily limit.

- **The cheap alternative is now GPT-6 Luna, not Haiku.** Luna gets 68% and 7 of 12 controls for $0.003 per assessment, about 1/32 of Sonnet's cost, at most about $0.10 a user a day. Against Sonnet it is 8 points and 2 controls lower, a gap 12 controls can't confirm or rule out (p = 0.69). If the owner weighs cost over those 8 points, Luna is the default to choose; before that it should run on Alpenwerk, since a default must serve the German profile too, and no OpenAI model has.
- **The caveat on Sonnet: its judge step left requirements unjudged** in 5 of 24 assessments (27 of Alpenwerk's 100 requirements, 2 of Nordlicht's 84), shown to the user as not-evidenced. Until Fix #184 lands (skipped requirements asked for again, and a warning when they stay skipped), choosing Sonnet means some assessments understate what the evidence shows. No other hosted model did this; the local gpt-oss 20B did once (4 of Nordlicht 4.1.2's 5 requirements not judged), among its other failures.
- **The pipeline matters more than the model.** Outside the pipeline Haiku reaches 80%, above Sonnet inside it. So the bigger gain is in Vouch's graph, not a bigger model: Fix #184 for the skipped requirements, then better retrieval and a call budget that lets the graph find each requirement's evidence. Fix #184 should lift Sonnet most (its Alpenwerk losses are mostly skipped requirements). Retrieval and budget work could lift every model, and could close enough of a cheap model's gap (Luna's or Haiku's) to make it the default. **Rerun the pipeline eval after that work, and revisit this choice then.**
- **Why not Haiku 5.5** (today's default): Luna is at least as good for half the cost (it leads on 7 controls to 3, with no refused answer). Against Sonnet, Haiku is 25 points lower on Nordlicht's verdicts (22 without its failed case) and behind on 10 of 11 controls that differ. Its Alpenwerk lead over Sonnet is within noise and comes from Sonnet's unjudged requirements.
- **Why not GPT-6.1 Sol:** level with Luna (69% against 68%) at about 18× Luna's cost, and not better than Sonnet.
- **Why not Opus 5.5 or Fable 5.1:** outside the pipeline they tie with Sonnet (89% and 88% against 90%), so there is no sign a larger model is worth its price. Neither ran through the pipeline.
- **Why not the Jev Router:** close to Sonnet (70%, the same 9 of 12 controls, no difference control by control) at a third of the price. But it ran on one profile only, takes twice as long, and its kept run predates #185, so which models answered isn't known, and a compliance tool should be able to say where a company's evidence goes. Worth a second run on Alpenwerk, now that served models are recorded, before choosing it.
- **Not decided here.** The owner chooses; `DEFAULT_MODEL` stays Haiku and Haiku's price stays in `PRICES` until then (#55's third criterion). This branch took Mistral Small 4, which didn't run, out of `PRICES`, and added GPT-6 Luna and GPT-6.1 Sol, which did.
