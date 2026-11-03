# Trial spec: one control, assessed by an agent

Status: approved 2026-11-03, ready for the seed setup. Started: 2026-11-03.

## Why this exists

A process trial, not the product. The same three tickets are built twice, in two throwaway repos, to compare two ways of running the build:

| Arm | Repo (local folder) | Driver | Who builds | Who checks |
|---|---|---|---|---|
| B | `sprint3-trial-b` (`sprint3-trail-b`) | Factory mode: the session works the queue, following rig's `factory.md` | the main session, as rig's Standard level says ("Build in the main session") | `working-state:verifier` |
| C | `sprint3-trial-c` (`sprint3-trail-c`) | A dynamic workflow: a script works the queue, with parking and the two-tries cap written in code | a `working-state:implementer` subagent per ticket, each in its own worktree | `working-state:verifier` |

The tickets, the seed, the ticket loop's steps and the checker are the same in both arms.

Decided 2026-11-03: the builder differs as well as the driver. A workflow script can't build anything itself, so arm C needs implementer subagents; arm B stays Factory exactly as rig defines it. Why: this compares the two processes as they would really be chosen, and tests rig as written. The cost: a difference in results can't be pinned on the driver alone or the builder alone. Say which one a lesson is about only when the evidence shows it, for example a park the script caused, or a defect traced to the builder's context.

The owner reviews the code afterwards, so learning comes from reading something that's already built.

Decided 2026-11-03: each driver runs tickets in parallel where it can. T2 and T3 depend only on T1, so arm C builds them at once. Arm B follows `factory.md` as written, which takes the next ticket only after one merges. Why: parallelism is part of what a driver offers, so limiting it would hide a real difference. When reading the results, the wall-clock gap includes this; compare owner minutes and defects per ticket too, not only total time.

The slice is a thin part of flow 4 (control assessment) from [requirements.md](requirements.md). Its code may seed the real project. The process lessons go into rig.

## Ground rules for both arms

- **No paid model runs.** Every test runs against a scripted fake model and fixture documents. Factory parks any ticket that needs a real API call. The suite passes with no API key set and the network blocked.
- **No secrets, no auth, no deploy.** These are Factory's fixed gates, and nothing here needs them.
- **Fixtures are invented.** One fictional company, Nordlicht Komponenten GmbH. Its policies and controls are written in our own words, not ISO or VDA ISA text (those are copyrighted). Control ids read `NK-<area>-<nn>`: `NK` for the company, then the area (`ACC` access, `ENC` encryption, `VUL` vulnerabilities). The ids are the company's own, so they don't claim to be a real catalogue's numbering; mapping to real catalogues is the profile and crosswalk work, later.
- **Stack:** Python 3.12, `uv`, LangGraph, LangChain core, PyYAML, pytest, ruff. There's no UI in the trial: it's a CLI only.
- **Seed** (set up before either arm starts, identical in both repos):
  - `pyproject.toml` for a package named `audit_agent` with an empty `__init__.py`, and one smoke test.
  - A CI fast lane: `ruff check`, then `pytest`.
  - The working state from `/working-state:project-kickoff` (Standard level, Factory mode), the `needs-owner` label, and `scripts/merge-on-green.sh`.
  - The fixtures, copied unchanged from `trial/seed/fixtures/` in this repo:
    - `fixtures/nordlicht/controls.yaml`: three controls. Each has an `id`, a `title`, a `statement`, an `owner_role` and its `evidence_points` (each with an `id` and `text`).
    - `fixtures/nordlicht/policies/*.md`: four short policies. They cover `NK-ENC-01` fully, `NK-ACC-01` partly, and `NK-VUL-01` not at all, so each verdict has a real case.

## Measures

Each arm is recorded in `docs/trial-results.md` in this repo (sprint3-project), and raw notes are kept so the comparison re-renders.

- Owner minutes, logged by the owner: answering parks and reviewing.
- Wall-clock time from start to queue stopped.
- Subscription usage: agents started, and the share of the usage window used.
- Hidden acceptance suite: failures per ticket. It's kept in `trial/acceptance/` here, outside both repos, and run after each arm. It tests only behaviour these tickets state, through the interfaces they name. Afterwards, each arm's transcripts are checked for any read of that folder.
- Parks and reworks, and why each happened.
- Explain-back: five cold questions about the code, answered by the owner without looking.

## Tickets

Each ticket below is the body of a GitHub issue labelled `ready-for-agent` and `enhancement`. They are filed in the same order in both repos.

---

### T1: Assess one control through a LangGraph graph

**What happens now**

The seed repo has fixtures and a smoke test, but no agent. Nothing reads a control or produces an assessment.

**What should happen**

`uv run assess NK-ENC-01` runs a LangGraph graph for that control and prints one JSON `Assessment` to stdout, with these fields:
- `control_id`
- `verdict`: `found`, `partial` or `gap`
- `answer`: a short draft answer for an auditor
- `sources`: a list of `{doc_id, heading}`, empty for now

Requirements:
- The graph's state holds the control, the evidence gathered so far, and the verdict. Findings live in state, not in the chat history.
- The model is passed into the graph, never created inside it. The CLI builds the real model from environment settings.
- **The fake model:** when `ASSESS_FAKE_SCRIPT` names a JSON file, the CLI uses a scripted fake model instead and makes no network call. The file looks like this:

  ```json
  {
    "gather": [
      {"tool": "search_policies", "args": {"query": "encrypt"}},
      {"answer": "Laptops use full-disk encryption ...", "verdict": "found", "cited": ["encryption-policy"]}
    ],
    "judge": {"covered_points": ["ENC-1", "ENC-2", "ENC-3"]}
  }
  ```

  - The `gather` turns are used in order: each turn is either a tool call or the final answer, and the last turn is always the answer.
  - Each later ticket says which part of the file it reads: T2 handles tool calls and `cited`, T3 uses `judge`. Until then, they're ignored.
  - Tests in the repo use the same fake.
- An unknown control id exits with code 2 and a one-line error on stderr naming the id. It prints no traceback.
- In this ticket the verdict is the answer turn's `verdict`. T3 replaces that with routing.

**Done when**

- [ ] For `NK-VUL-01`, with a fake script whose answer turn says `gap`, the graph returns an `Assessment` with `verdict == "gap"` and the script's answer text. **Check:** a pytest test, no network.
- [ ] `ASSESS_FAKE_SCRIPT=<file> uv run assess NK-ENC-01` prints JSON with all four `Assessment` fields, and exits 0. **Check:** a pytest test running the CLI as a subprocess.
- [ ] `uv run assess NK-XXX-99` exits 2, and its stderr is one line containing `NK-XXX-99`, with no traceback. **Check:** a pytest test running the CLI as a subprocess.
- [ ] The whole suite passes with `OPENROUTER_API_KEY` unset and sockets disabled (`pytest-socket`, `--disable-socket`). **Check:** CI's fast lane runs pytest that way.

**Needs**

None.

**Out of scope**

Searching documents (T2). Branching on the verdict (T3). Any real model call. Any UI.

---

### T2: The agent searches the company's policies, within an allow-list

**What happens now**

After T1, the agent answers without looking at any document, and `sources` is always empty.

**What should happen**

A tool, `search_policies(query: str, limit: int = 5)`, importable as `from audit_agent import search_policies`, searches the policy folder and returns a list of passages. Each passage has:
- `doc_id`: the file name without `.md`
- `heading`: the nearest Markdown heading above the passage
- `text`

Keyword matching is enough; no embeddings yet.

Where it searches:
- The policy folder is `fixtures/nordlicht/policies/`, or the path in `ASSESS_POLICY_DIR` when that's set. Only `.md` files directly in that folder are ever opened.
- A query can't name a path.

The graph offers the tool to the model. An `Assessment`'s `sources` holds the passages the answer turn cites in `cited`, but only ones a search in that run actually returned. A cited `doc_id` no search returned is dropped.

Errors are returned, never raised:
- **Bad input.** `query` must be 1 to 200 characters after trimming, and `limit` must be 1 to 10. These are checked before anything else, so bad input returns `{"error": "invalid_input", "detail": ...}` even when the folder is missing. The error goes to the model; nothing raises through the graph.
- **Folder missing or unreadable.** The tool returns `{"error": "source_unavailable"}`. The graph still finishes: the `Assessment` gets a `warnings` list containing `source_unavailable`, and `verdict == "gap"`. `warnings` is an empty list in any other run.

**Done when**

- [ ] `search_policies("encrypt")` returns at least one passage with `doc_id == "encryption-policy"` and a non-empty `heading`. **Check:** a pytest unit test.
- [ ] `search_policies("")`, a 201-character query, `limit=0` and `limit=11` each return `invalid_input`, also when `ASSESS_POLICY_DIR` points at a missing folder. **Check:** pytest unit tests.
- [ ] `search_policies("../../pyproject.toml")` returns only passages whose `doc_id` is a file in the policy folder. **Check:** a pytest unit test.
- [ ] With a fake script that searches `encrypt` and then cites `encryption-policy`, `Assessment.sources` holds a passage with that `doc_id`. With a script that cites a `doc_id` no search returned, `sources` doesn't hold it. **Check:** pytest graph tests, no network.
- [ ] With `ASSESS_POLICY_DIR` at a missing path, the graph finishes with `verdict == "gap"` and `"source_unavailable"` in `warnings`. **Check:** a pytest graph test.

**Needs**

None.

**Out of scope**

Other tools (a ticket system, Patch). Embeddings and vector stores. The found/partial/gap routing (T3).

**Blocked by**

#N, the T1 issue's number in that repo (the lint and Factory read only `#N`).

---

### T3: The graph routes each control to found, partial or gap

**What happens now**

After T1, the answer turn's verdict is returned as is. Nothing checks it against the control's `evidence_points`, and a gap leads to nothing.

**What should happen**

After evidence gathering, a `judge` step decides which of the control's evidence points the evidence covers. That decision comes from the model as structured output: the fake script's `judge.covered_points`. The `Assessment` gains three fields:
- `covered_points`: the ids the judge named that belong to this control, in the control's order. Ids that don't belong are ignored.
- `missing_points`: the control's other evidence point ids, in its order.
- `gap_note`: `null` unless the verdict is `gap`.

The graph then branches on fixed edges, not a model choice. The answer turn's `verdict` is ignored from now on.
- **All points covered** → `found`.
- **Some points covered** → `partial`.
- **None covered** → `gap`. An `escalate` node fills `gap_note` with `{control_id, missing_points, owner_role}`, taking `owner_role` from the control's fixture entry.

**Done when**

- [ ] Three scripted runs end in the expected verdict, `covered_points` and `missing_points`: `NK-ENC-01` with all its points covered gives `found`; `NK-ACC-01` with only `ACC-1` gives `partial`; `NK-VUL-01` with none gives `gap`. **Check:** pytest graph tests.
- [ ] The `gap` run's `gap_note` holds `NK-VUL-01`, all three missing ids and the owner role from `controls.yaml`. The `found` and `partial` runs have `gap_note == null`. **Check:** pytest graph tests.
- [ ] A judge naming `ENC-1` and `ENC-9` for `NK-ENC-01` gives `covered_points == ["ENC-1"]` and `verdict == "partial"`. **Check:** a pytest graph test.
- [ ] An answer turn saying `found`, with a judge that covers nothing, still gives `gap`. **Check:** a pytest graph test.

**Needs**

None.

**Out of scope**

Human approval before "found" counts (flow 4's approval gate comes later, with the UI). Remediation tickets (flow 5). Profiles and overlays.

**Blocked by**

#N, the T1 issue's number in that repo (the lint and Factory read only `#N`).

---

## Decided

- 2026-11-03: `NK-<area>-<nn>` control ids stay.
- 2026-11-03: T2 stays whole, though it's the ticket most likely to go over Factory's 400-line cap (tests included). Why: whether the size-cap park works, or gets in the way, is part of what the trial measures. Record in the results whether either arm went over the cap, and what each driver did about it.
