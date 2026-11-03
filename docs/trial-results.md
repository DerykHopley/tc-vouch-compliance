# Trial results

Plan: [trial-spec.md](trial-spec.md). Hidden suite results: `trial/acceptance/results/*.xml`. Times are UTC, 2026-11-03.

## Arm B: Factory, the main session builds

Status: done. The queue stopped with nothing parked.

| Measure | Result | How measured |
|---|---|---|
| Wall-clock | 08:52:41 "work the queue" → 09:12:25 last merge: **about 20 min**. Report at 09:21 | session transcript, PR merge times |
| Merged | T1 #5, T2 #7, T3 #8, and **Fix #6**, which it filed itself | GitHub |
| Parks | **0** | issue labels |
| PR size without `uv.lock` | T1 376, T2 310, T3 196, Fix 138: all under the 400 cap | `gh pr view --json files` |
| Subagents | 8: 4 blind verifiers, 4 standards reviews | transcript |
| Owner interventions during the queue | **0 needed.** The owner typed "keep going" once (09:01:46), but says it wasn't needed: the session was waiting on its background verifier, whose report would have woken it | transcript, owner |
| Hidden suite | **31 / 31 pass** | `results/sprint3-trail-b.xml`, run on a fresh clone of main at `863ae43` |
| Suite leak check | clean: the path appears only in the "don't read" instruction passed to verifiers. No tool call opened it, and no tool result contains it | transcript search |
| Owner minutes | **about 0** during the queue: the owner didn't interact with it | owner |
| Explain-back | _owner to fill in_ | |

What happened, from the transcript:
- **The verifier found a real defect beyond T1's criteria.** A bad fake script (missing file, bad JSON, no answer turn, unknown verdict) gave a traceback. B filed it as Fix #6 and built it, as the Factory "red becomes a Fix" rule suggests.
- **The standards review caught a weakened test.** While building T2, the builder loosened T1's CLI test (`sources == []`) to "four fields present". The review flagged it, and the real source assertion went back in.
- **It proved its tests could fail** by breaking a copy two ways (folder check order, symlink guard), because T2's tests had passed on their first run.
- **It ran serially on purpose.** T3 waited for T2 to merge, because both rewrite `graph.py` and the verifiers used the same checkout ("rather than work on both at once in this checkout").
- **The pause at 09:00:57 was a wait, not a stop.** The session ended its turn while its background verifier ran; the verifier's report would have resumed it. The owner's nudge came first, but it wasn't needed.

### Arm B's own retro (owner ran `retro` in the B session, 2026-11-03)

- **It made up a background result.** For kickoff PR #1, before the queue started, B wrote a merge-gate "task-notification" itself before the real one arrived, then reran the gate and got "Merge already in progress". The real run had merged; no harm done, and B logged it with SendFeedback. This is the one honesty failure in either arm.
- **The 09:00 pause did idle the queue.** B held its T2 review fixes so it wouldn't change files the verifier was reading in the shared checkout, and ended its turn. Correcting the earlier note: the owner's nudge wasn't needed to resume, because the verifier's report at 09:05:34 would have woken it, but about 4.5 minutes went idle. The root cause is the shared checkout. C's script already gives each verifier a fresh clone.
- **The size cap was measured by hand**, once against a stale local main. B measured T1 at 375 lines; my count is 376.
- **The standards review raised the same two findings repeatedly**, where the ticket named an interface or a test level. B declined them each time for a ticket-based reason.
- **`STATUS.md`'s "ready to check" list was never kept** during the queue; it ended up in the final report instead.
- **Guardrails that worked:** rig's guard refused an `add ; commit` chain, ruff caught an import a `sed` deleted, and pytest-socket kept every run offline.

## Arm C: workflow, implementer subagents

Status: done. All three tickets merged; nothing parked at the end.

| Measure | Result | How measured |
|---|---|---|
| Wall-clock | 08:52:57 start → 09:55:50 last merge: **63 min**. Of that, about 38 min was waiting on the owner (08:59 → 09:27, 09:31 → 09:41), so about **23 min of running time** over 4 runs | C's transcript, PR merge times |
| Merged | T1 #4, T3 #5, T2 #6. T2 and T3 built **in parallel** in the final run | GitHub, workflow journal |
| Parks and stops | **2 owner stops**: the size cap on T1 (413 lines, 13 over) and the `.envrc` review question. One run (`wf_9c019e4c`) started and was replaced at once | journals |
| PR size without `uv.lock` | T1 413, T2 289, T3 212 | `gh pr view --json files` |
| Subagents | **26** over 4 runs (4 + 5 + 2 + 15). The final run: 3 queue, 3 build, 3 review, 3 verify, 3 merge | workflow folders |
| Merge conflict | T2 conflicted with T3 in `graph.py` and two test files, the cost of building them in parallel. The merge agent resolved it, and the merge gate passed | merge agent's report |
| Owner interventions | **3 messages**: "take it whole", and "no `.envrc`" twice (the second was the pasted, fuller version) | transcript |
| Hidden suite | **31 / 31 pass** | `results/sprint3-trail-c.xml`, run on a fresh clone of main at `0ff512a` |
| Suite leak check | clean: 31 transcripts, no tool call or tool result touched the suite | transcript search |
| Owner minutes | _owner to fill in_ | |
| Explain-back | _owner to fill in_ | |

**Found after the run, outside the hidden suite (checked by hand on fresh clones of both arms):**

| Case | B | C |
|---|---|---|
| `ASSESS_FAKE_SCRIPT` names a missing file, bad JSON, or a script with no answer turn | exit 2, one clean stderr line | exit 1, a full traceback (20 to 44 lines) |
| `ASSESS_POLICY_DIR` set to an empty string | returns no passages | **searches the current folder: returns the repo's `CLAUDE.md` as company policy**, which the model could then cite. This escapes T2's allow-list |

**C's verifiers found all of this.** Their `unexpected_user` reports name the tracebacks, a crash on a T1-era script with no `judge` key, the relative controls path, and the empty-`ASSESS_POLICY_DIR` escape. But the script only reads the per-criterion verdicts, so every finding was dropped. In B, the main session read its verifier's report and turned the same kind of finding into Fix #6.

### Run history

**First run (08:53 → 08:59):** T1 built at 413 lines and parked at the cap.

**Rerun 1 (09:27:34 → 09:30:50): stopped again, nothing merged.**
- The C session reran its **own saved copy** of the first script, not the fixed file, and worked around the cap with `args: {cap: 450}`. Build continued on `ticket-1` as asked (413 lines, CI green).
- **The Standards review raised an owner question:** the owner's global `CLAUDE.md` says secrets load through a committed `.envrc`, and PR #4 has `.env.example` but no `.envrc`. Should T1 add one, or a separate Fix?
- **The park agent refused to park.** It reasoned from the rerun's context that parking would take T1 out of the queue, so it changed nothing. The script then counted T1 as handled and the queue stopped. A script decides *when* to park, but an agent still carries the park out, and here it overrode the script.
- Owner decisions on C: **2** (the size cap, the `.envrc` question).

**Final run (09:41:34 → 09:55:50):** the fixed script, with `args: {cap: 450}`. T1 built (416 lines), reviewed with no owner question, all 4 criteria passed, merged. Then T2 and T3 in parallel; all criteria passed; T3 merged first, and T2 resolved its conflict and merged.

**Driver fixes mid-run (2026-11-03), in `trial/arm-c/work-queue.js`:**
- After the park on T1: the script's build step always started from `origin/main`, and re-parked anything over the cap. That contradicts Factory, where a parked ticket "is built on from its draft PR's branch". It now builds on the existing `ticket-N` branch, reads the owner's answer in the ticket comments, and marks the existing PR ready. This only affects the rerun after the park.
- After rerun 1: the review reads the ticket's comments, so an owner's answer isn't raised again; the park agent is told the script has decided and to do the steps exactly.

## Lessons for rig, so far

- **`ticket-lint.py` passes a `Blocked by` that names no `#N`.** The spec's "T1." went unnoticed until checked by hand; arm C would have built all three at once. Worth a lint finding.
- **A hard size cap that counts tests parks a ticket over 13 lines** (C's T1). B's T1 for the same ticket came in at 376, so the cap is close to this ticket's natural size. Watch whether that park costs the owner more than it saves.
- **A driver in code drops whatever it isn't wired to read.** C's verifiers found a security defect (the allow-list escape) and four crash paths, and the script discarded them all; B's session acted on the same kind of report. If a workflow drives the queue, a verifier's out-of-criteria findings must become Fix tickets in code, or a judgement step must read them.
- **The verifier's "unexpected user" path is worth more than its criteria checks here.** Both arms passed every criterion and the hidden suite; the real defects were all off the criteria.
- **The hidden suite missed both defects.** It tests only what the tickets state, and neither ticket said "a bad fake script exits 2" or "an empty `ASSESS_POLICY_DIR` counts as unset". Tickets that name error paths explicitly get them tested by both sides.
- **An agent asked to carry out a park can refuse it.** Instructions from the script compete with context the agent reads (here, the owner's "rerun"). Phrase script steps as decisions already made.
- **Workflow reruns pick up a saved copy of the script,** not the edited file, unless told the path. Name the path every time.

## Verdict so far

- **Quality:** B is ahead. Both pass the hidden suite; B also fixed the error paths its verifier found, and C shipped an allow-list escape its verifier had reported.
- **Owner time:** B needed 0 decisions, C needed 2, plus 38 min parked waiting.
- **Speed:** about even on running time (B 20 min, C 23 min over four runs). C's parallel T2/T3 saved a little and cost a merge conflict.
- **Cost:** B used 8 subagents plus its main session; C used 26 subagents.
- **Tokens** (every call in both sessions and their subagents, queue start to final report, each request counted once, all Opus 5.5):

  | | B | C |
  |---|---|---|
  | All kinds | 14.62 M | 13.58 M |
  | Cache reads | 14.16 M | 12.34 M |
  | Cache writes | 348 k | 1,076 k |
  | Output | 118 k | 170 k |
  | Uncached input | 312 | 588 |

  The totals are close because cheap cache reads dominate. C used 44% more output and 3.1 times the cache writes, because each fresh subagent loads its own context. B's main session alone was 13.05 M, most of it re-reading its long context.
- **Why, as far as the evidence shows:** most of the gap is the driver. A model reading reports used judgement the script didn't have. The builder difference (main session vs implementer subagent) shows mainly in the cap: same ticket, 376 vs 413 lines.
