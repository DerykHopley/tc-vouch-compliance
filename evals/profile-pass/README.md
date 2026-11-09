# Profile pass sample run (Sonnet 5.5, #430)

One paid run of the profile pass (#371) on each demo profile, with the owner's OK on #430, at merge 4b74e60 of `spec/350-keep-improving` (`PROFILE_PASS_PROMPT_VERSION` m7-profile-pass-2). It checks the built feature once on the real model; the tests run on the scripted fake.

- How it ran: a throwaway local app (local sign-in, a throwaway Postgres for the daily limit) on `anthropic/claude-sonnet-5.5` through OpenRouter, then `POST /api/profile-pass` once per profile, on 2026-11-09. No retry.
- `<profile>-pass.json`: the route's answer, with the pass's usage as OpenRouter reported it.
- `<profile>-assessments.json`: each assessment the pass made, as `GET /api/assessments/<thread>` gave it (requirements, citations, steps, remediation drafts).
- `score.py`: scores them against `fixtures/<profile>/expected-answers.yaml` and writes `scores.json`, without calling a model: `uv run python -I evals/profile-pass/score.py .`

| Profile | Verdicts right | Stricter / more generous | Citations to an expected document | Time | Tokens in / out | Cost |
|---|---|---|---|---|---|---|
| Nordlicht | 75/84 (89%) | 3 / 6 | 100/111 | 150 s | 16,839 / 26,097 | $0.295 |
| Alpenwerk | 87/100 (87%) | 9 / 4 | 147/156 | 219 s | 30,640 / 41,531 | $0.477 |

For comparison on Nordlicht: Sonnet 5.5 with all evidence in context but no guards (`evals/full-context/`) 90%; Vouch's per-control agent 76% (`docs/eval-report.md`). The run cost $0.77 in all, over the $0.60 the owner approved; why, and what it suggests, is on #430 and #432.
