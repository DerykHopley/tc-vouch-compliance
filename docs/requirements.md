# Sprint 3 project — requirements and ideas

Status: idea 1 (ISMS compliance tool) is the top choice, not final. No product code yet; a process trial built a thin slice of flow 4 in throwaway repos ([trial-spec.md](trial-spec.md), [trial-results.md](trial-results.md)).
Started: 2026-10-28

Flow map (visual, private artifact): https://claude.ai/artifact/CXZ7LB2yySvQPsaRz3xRDK

## Requirements (from the course brief)

### R1 — Agent fundamentals and ReAct (Part 1)
- Build an agent that chooses tools and acts on their results (ReAct loop).
- It solves a clear user problem.
- Explain why an agent fits this problem better than a plain chain or script.

### R2 — Tools, state, and middleware (Part 2)
- Manage task data (state) across steps.
- Validate tool inputs.
- Handle tool and model failures gracefully.
- Apply security suited to the tools (for example input limits, allow-lists, no secrets in output).
- Note: the brief calls this the most directly useful implementation content.

### R3 — LangGraph (Part 3)
- Organise the workflow as nodes, edges and state flow.
- Use it where the agent needs explicit branching or multiple steps.

### R4 — Memory and human-in-the-loop (Part 4)
- Add persistence (remembered context) where it helps. **Optional enhancement.**
- Add human review or approval steps where they help.

### R5 — Bring it together
- Wrap the agent in a usable UI.
- Document setup and usage examples.
- Be ready to explain technical choices and limitations.

## Decisions

- 2026-10-30: a new problem, not an extension of Patch (sprint 2). Why: Patch already covers most of R1, R2, R4 and R5, and its loop doesn't need the branching R3 asks for. Its patterns (security layers, tool input checks, approval cards) can be reused, but not its code.
- 2026-11-02 (if idea 1 is chosen): compliance profiles are core and designed in from the start. Why: making things configurable touches every tool, the state and the prompts, so adding it later would mean a rewrite. Country rules stay illustrative, not researched.
- 2026-11-03: the app is called **Vouch**; the GitHub repo is `tc-vouch-compliance`, and the local folder stays `sprint3-project`, as sprint 2 (`sprint2-project` → `tc-patch-security`). Why: "vouching" is the audit procedure of tracing a claim back to its source document, which is what the agent does for every answer. It's its own product rather than a Patch name, matching the 2026-10-30 decision; Patch stays one evidence source. Other candidates: Attest, Tickmark, Annex.
- 2026-11-03: Vouch's persona is **serious and formal: suit and tie.** It speaks like an experienced auditor, with precise wording, no jokes, and conclusions that say what was and wasn't evidenced. Why: the owner's call, and it suits the users (compliance leads preparing for an audit) and the stakes. It also settles the brief's optional "personality" task and gives the UI a direction.

## Ideas

### 1. ISMS / TISAX audit-readiness agent — current top choice (2026-11-02)

Status: top choice for now, not final. Still open to other ideas before grilling.

Background: three TISAX audits supported at University4Industry.

The agent takes a control or an auditor's question, chooses between tools (search policy docs, check a ticket system, look up evidence), and drafts an answer with sources.

- R1: open-ended, because each control needs different evidence, so a fixed script wouldn't cover it.
- R2: input limits, allow-listed document sources, no sensitive data in output, graceful handling when evidence is missing.
- R3: natural branches: evidence found, partial evidence, gap escalated.
- R4: human approval before anything is marked "compliant".

Open: where the documents come from (real ones are confidential), which control catalogue is licensed for use, and how far it sits from Patch's security domain.

#### Core: country and company profiles (added 2026-11-02)

Decided 2026-11-02: compliance profiles are a core, day-one architecture choice, not a nice-to-have. Country rules can be illustrative (a learning project), so no deep legal research. Multiple languages are in scope.

Requirements differ by country, company and language. Idea: keep one agent, and put the differences in a **compliance profile** (config as data).

- A profile stacks layers: base framework (ISO 27001 / TISAX) → country overlay (national NIS2 law, regulator guidance) → company overlay (scope, assessment level, own policies) → language (of evidence and of answers).
- A control maps across layers (a crosswalk), so one question can pull extra requirements from the overlay.
- Rubric gain: R1 more open-ended (evidence depends on the profile); R2 allow-lists per profile; R3 a branch for "overlay adds a requirement"; R4 approval per profile.
- Risk: scope creep. Suggested cut: build one full profile, plus a thin second one to show the switch works.

## Candidate flows for idea 1 (added 2026-11-02, not yet chosen)

Lifecycle: set up → ingest → assess → fix → report → keep current.

| # | Flow | Agent or pipeline | Rubric |
|---|------|-------------------|--------|
| 1 | Profile setup: frameworks, country overlay, scope, language | Config | R2 |
| 2 | Document ingestion: policies, procedures, evidence; tag to controls, detect language | Pipeline (+ agent for unclear tagging) | R2 |
| 3 | Asset ingestion: laptops and systems (e.g. MDM/CSV export), check against controls | Pipeline + rules | R2 |
| 4 | Control assessment: gather evidence, judge found / partial / gap, draft answer with sources | **Agent** (the core) | R1, R3, R4 |
| 5 | Gap to remediation: draft a ticket with owner and due date | Agent action, approval gate | R4 |
| 6 | Auditor Q&A: ad-hoc question, cited answer in the auditor's language | Agent | R1, R5 |
| 7 | Keep current: expired evidence, changed policy → re-check | Scheduled pipeline | R4 (memory) |
| 8 | Reporting: readiness report / self-assessment export per profile and language | Pipeline | R5 |

Evidence reuse across frameworks (via the crosswalk) runs through flows 4 and 8.

### Proposed front door: guided audit prep (added 2026-11-02)

The user's view: "I'm about to be audited. Walk me through it." A chat assistant that:

- works through the controls in the active profile, one area at a time;
- says which documents each control needs and whether they're already ingested (uses flows 2 and 4);
- suggests where to find missing ones, from a **source map** in the company profile (e.g. policies in Confluence, tickets in Jira, HR records in the HR system), else from typical owners;
- remembers progress across sessions (R4 memory, a real need: audit prep takes weeks);
- ends with the readiness report (flow 8).

It turns flows 2, 4, 5 and 8 into one user journey, instead of separate tools.

### Integration idea: Patch as an evidence source (added 2026-11-02)

The agent queries Patch (sprint 2) for vulnerability-management evidence, e.g. for ISO 27001 A.8.8 "Management of technical vulnerabilities": open CVEs, due dates, overdue items, recorded decisions.

- Fits the 2026-10-30 decision: Patch stays a separate system, reached through an interface, not extended.
- Checked in `sprint2-project/backend/app/mcp_server.py`: Patch's MCP server has four read-only tools, but runs them on `NoStack` on purpose (B23). It gives CVE facts, not the owner's watchlist, statuses or decisions. So process evidence needs a **new read-only Patch tool** (e.g. a vulnerability status summary), with thought about who may read it.
- Reliability demo for free: Patch on Cloud Run can take ~90 s to wake. Timeout, retry, then "evidence pending" instead of failing.
- Fallback: a recorded Patch response (fixture) for tests and the demo; the live connection as stretch.

### Fit with the project brief (added 2026-11-02)

Full brief: [project-brief.md](project-brief.md).

- **Purpose and users:** a compliance or ISMS lead at a European company (e.g. automotive supplier) preparing for a TISAX or ISO 27001 audit. Problem: audit prep means weeks of chasing documents across systems and people, with no clear view of readiness.
- **"When to use prompt engineering, RAG or agents":** answered by the flow split. Pipelines for ingestion and reports, RAG for policy search, an agent only where each control needs different steps.
- **Knowledge base:** control catalogue + policies + evidence. Open: ISO and VDA ISA texts are copyrighted, so either paraphrase controls in our own words or use a free catalogue (e.g. BSI IT-Grundschutz, NIST CSF). *Unverified*: licence terms not checked.
- **Optional tasks the idea covers naturally** (bonus needs 2 medium + 1 hard):
  - Medium: memory (prep spans weeks), external API tool (Patch), security guard (allow-lists, approvals), token cost, multi-model.
  - Hard: agentic RAG (policy and evidence search), external data source (Patch), observability (Langfuse), evaluation report (DeepEval/Ragas).
  - Easy: help guide (the guided chat itself), personality per profile (formal for auditor answers).

## Quality goals (added 2026-11-02)

These apply whichever idea is chosen.

- **Memory and reliability:** recover from context overflow, API failures and tool errors.
  - Context: one control per run keeps context small. Keep findings in graph state, not chat history. Trim or summarise long tool output.
  - API failures: retries with backoff, a fallback model, and a LangGraph checkpointer so a run resumes instead of restarting.
  - Tool errors: tools return structured errors, so the agent can try another tool or mark "evidence missing".
- **Integration:** reach users where they work. Candidates: chat UI (R5), exported audit report, email *drafts*. Slack/Teams later.
- **Safety without crippling:** policies enforced in middleware, outside the model, and driven by the profile.
  - Read-only tools (search, look up) run freely inside the profile's allow-list.
  - Actions with effect (mark compliant, send email) need human approval.

## Open questions

Updated 2026-11-03. The first five questions from 2026-10-28 are mostly answered for idea 1 (problem, tools, memory, human review: see above).

- Is the guided audit-prep chat *the* product, with the other flows behind it? Recommended: yes.
- Where do profiles live? Recommended: YAML files checked by Pydantic.
- Control catalogue: the trial uses invented controls in our own words (`NK-<area>-<nn>`, Nordlicht Komponenten GmbH). Keep that for the product, or map to a free catalogue (BSI IT-Grundschutz, NIST CSF)?
- Which UI (Streamlit, Gradio, web app)? The trial is CLI only.
- Patch integration: add a new read-only Patch tool for vulnerability status, or stay on a recorded fixture?
