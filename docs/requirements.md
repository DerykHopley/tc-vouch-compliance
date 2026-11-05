# Sprint 3 project — requirements and ideas

Status: idea 1 is final: Vouch, the ISMS / TISAX audit-readiness agent. Grilled 2026-11-03 (Decisions below); build plan: milestones M0 to M5. No product code yet; a process trial built a thin slice of flow 4 in throwaway repos ([trial-spec.md](trial-spec.md), [trial-results.md](trial-results.md)).
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
- 2026-11-03: **idea 1 is final**: Vouch is the ISMS / TISAX audit-readiness agent. Why: the name, repo and persona were all chosen for it; grilling went ahead on that basis.
- 2026-11-03: **the guided audit-prep chat is the product**, with a small readiness panel (controls by verdict) beside it. Flows 2, 4, 5 and 8 run behind the chat; auditor Q&A (flow 6) is the same chat. Flows 3 (asset ingestion) and 7 (keep current) are out of v1. Why: a chat shows the ReAct loop the grading asks about; separate screens would hide the agent behind buttons. The panel gives the progress view audit prep needs.
- 2026-11-03: **dates**: the official due date is 2026-11-23; the target is ready for review by **2026-11-16**, a week earlier. Why: the owner's call, leaving a week for review and the explain-back.
- 2026-11-03: **bonus targets: 3 medium + 2 hard**, one of margin each. Medium: memory, security guard, token usage and cost. Hard: agentic RAG, evaluation report (DeepEval or Ragas). Easy: personality (formal), help guide (the chat). Patch and observability are optional extras. Why: each target falls out of the design (checkpointer, profile allow-lists, approvals, policy search); the trial's found / partial / gap fixtures are a ready eval set.
- 2026-11-03: **catalogue: real VDA ISA questions as the base framework**, a subset of about 10 to 15 across 3 or 4 areas, shipped unchanged with credit in its own data folder (CC BY-ND 4.0 plus VDA's derivatives clause, read in the licence sheet of the ISA 2027 English Excel, portal.enx.com). Nordlicht Komponenten GmbH stays as the invented company overlay (policies, evidence). ISO 27001 Annex A is referenced by number and short title only. Why: TISAX users know ISA, and it makes the demo credible; NIST is US-centred, invented controls are weak in front of a grader. Cost: no edits to ISA text (our paraphrase lives in our own fields; any edited copy must be marked "derivative not approved by the licensor"). Not legal advice: the ISO reference practice is unverified.
- 2026-11-03: **UI: React, Vite and TypeScript, in front of a FastAPI backend** that streams the agent's steps. Patch's chat and approval-card patterns carry over as patterns, not code. Why: the grading names a front-end library and the agent's steps; the owner has built this stack once and is learning TypeScript. Fallback if time slips: Streamlit.
- 2026-11-03: **Vouch deploys to Cloud Run**, as Patch. Why: the owner's call (a live link for reviewers). Consequences: the first ticket is the walking skeleton on the real host; a deployed app needs access control and a spending guard (next grilling round).
- 2026-11-03: **two compliance profiles**: one full (Nordlicht), and a second company with an invented country overlay that adds a requirement, so the graph's "overlay adds a requirement" branch (R3) is real. The answer language is a profile field. Why: a language swap alone shows no new agent behaviour.
- 2026-11-03: **evidence comes in from a folder per profile**, on the allow-list, indexed by one command. Upload in the UI only if time is left. Policy documents are one evidence source among several: others can be added later (server logs, config exports, Patch). Why: upload is a security surface (type, size, path, injection) that costs days.
- 2026-11-03: **a verdict is reviewed**: the compliance lead accepts or rejects the agent's verdict, with an optional note. Only accepted verdicts count towards readiness; a rejected one goes back for another look or is set to gap by hand. Why: one clear human-in-the-loop step (R4), and readiness counts only what a human signed off.
- 2026-11-03: **remediation items stay in Vouch**: for a gap the agent drafts one (what's missing, owner role, due date), the lead approves it, and it's listed in the readiness report. No external ticket system in v1. Why: an action with effect behind approval, no integration to build.
- 2026-11-03: **Patch is one fixture-backed tool**, `vulnerability_status`, shaped like the new read-only tool Patch would offer. Live Patch only if time is left after 2026-11-12. Why: cheap, and a second kind of evidence source strengthens R1's "why an agent".
- 2026-11-03: **sign-in: Clerk, invite-only**, as Patch. Locally: no sign-in, one default user. Why: known setup from sprint 2, and it adds the medium "authentication and personalisation" task as margin. Cost: under Factory, the sign-in ticket is the owner's to build or review.
- 2026-11-03: **state lives in hosted Postgres** (Neon or similar free tier), through LangGraph's Postgres checkpointer (threads, resumable runs, `interrupt()` for review) and its store (long-term memory). Fallback: Patch's per-user JSON in Cloud Storage. Why: makes the memory bonus genuinely LangGraph memory. Checked 2026-11-03: Neon Free has no card, 1 GB per project, 100 CU-hours a month, sleeps after 5 min idle, has a Frankfurt region (`aws-eu-central-1`, fixed at creation), and deletes Free projects idle 90+ days (neon.com/docs/introduction/plans, /regions). `langgraph-checkpoint-postgres` 3.2.0 has both `AsyncPostgresSaver` and `AsyncPostgresStore`, on psycopg 3; connections need `autocommit=True`, `row_factory=dict_row`, `prepare_threshold=0`; `.setup()` runs once, over the direct (non-pooled) URL, since Neon's pooler is transaction mode (the direct-URL part is inferred). A node with `interrupt()` re-runs from its start on resume, so code before the interrupt must be safe to run twice (docs.langchain.com, interrupts).
- 2026-11-03: **spending guard, as Patch**: a spending cap on the OpenRouter key, a per-user rate limit (for example 30 assessments a day), at most 1 Cloud Run instance, and token usage and cost shown per assessment.
- 2026-11-03: **v1 evidence sources: policy documents (agentic RAG) and Patch (fixture)**, behind one evidence-source interface with a per-profile allow-list. Log excerpts are a stretch source, worth it only if the ISA subset includes logging controls.
- 2026-11-03: **the second profile is an invented Austrian company**, answering in German, with an invented national NIS2 overlay that adds one requirement (for example an early warning to the authority within 24 hours of a serious incident; illustrative, not legal research). Nordlicht stays German-based, answering in English. A **third, Lithuanian profile** is a nice-to-have if time allows: several reviewers are Lithuanian.
- 2026-11-03: **model: OpenRouter, `anthropic/claude-haiku-5.5` to start**, set in developer settings, apart from the user's view; no model picker for users. Evals should make the model choice clearer (next grilling round). Paid runs still wait for the owner's OK with a cost estimate.
- 2026-11-03: **the ISA subset is 12 questions in 4 areas**: 1.1.1, 1.6.1, 1.6.2 (policies and incidents; 1.6.x carries the Austrian overlay); 2.1.3 (awareness); 4.1.2, 4.1.3, 4.2.1 (access); 5.1.1, 5.1.2, 5.2.4, 5.2.5, 5.2.9 (IT security; 5.2.5 is Patch's, 5.2.4 suits log excerpts later). Chapters 8 and 9 are out. The Nordlicht policies are rewritten for a spread of found, partial and gap. Why: each part of the design gets a real case.
- 2026-11-03: **evals: DeepEval**, on about 24 cases (12 controls × 2 profiles) with hand-written expected verdicts, expected cited documents and overlay requirements. Measures: verdict accuracy, citation precision, faithfulness (judge model), cost and time per assessment. Models: Haiku 5.5, Sonnet 5.5, and one cheap model from another provider (named once checked on OpenRouter). Raw results saved; every paid run waits for the owner's OK. Why: pytest-style fits the test suite, and the results settle the model choice.
- 2026-11-03: **two agent layers**: the guided chat is a ReAct agent that picks the next control, answers ad-hoc questions and starts assessments; an assessment is a LangGraph graph (load control → apply overlays, branch when an overlay adds a requirement → gather evidence, ReAct over allow-listed tools → judge → gap drafts a remediation item → review interrupt → save reviewed verdict). Code before the interrupt only reads; saving happens after it, because the node re-runs on resume. Why: a concrete answer to "differences between agent types", with R3's branches and R4's interrupt in one place.
- 2026-11-03: **a session opens where the user left off**: the chat states progress (controls reviewed, rejected verdicts waiting, the next control) and suggests the next step. Why: audit prep spans weeks; this is what memory is for.
- 2026-11-03: **the readiness report is a page in the app**: controls by reviewed verdict, citations and remediation items, in the profile's language, exported as Markdown and printed to PDF from the browser. Filling ISA's own Excel is a later improvement.
- 2026-11-03: **trial-b's code is the starting backend**, copied in at the move up (to Spec level, as it turned out; ticket #8 in M0) with fresh history: package renamed `audit_agent` → `vouch`, its tests kept, the `NK-*` controls replaced by the ISA subset in the first tickets. Then sprint3-trial-b is archived.
- 2026-11-03: **the walking skeleton has no secrets**: the React shell posts one control, FastAPI runs trial-b's graph with the fake model, the streamed answer shows, deployed on Cloud Run. Clerk, Neon and the real model are later tickets. Until Clerk lands, the deployed app runs on the fake model only, so an open URL costs nothing. Why: proves build, container, deploy and streaming in a day, and Factory can build it.
- 2026-11-03: **cut line for 2026-11-16.** Must: skeleton; ISA subset and Nordlicht fixtures; assessment graph (overlays, gather, judge, review interrupt); policy search (agentic RAG) and the Patch fixture tool; guided chat with readiness panel; Neon memory and resume; Clerk; spending guard and token cost; the Austrian profile in German; remediation items; the readiness report page; the DeepEval eval with model comparison; README and docs. Stretch, in order: Lithuanian profile, log excerpts, live Patch, upload, Langfuse. If the must list slips, cut stretch from the bottom first; within the must list, the Austrian profile shrinks to its one overlaid control (1.6.x) before anything else goes.
- 2026-11-03: **the security guard is five layers in middleware**, outside the model: input limits (message length, rate limit); the per-profile allow-list on every tool and evidence source; document text treated as data (a fixture policy carries an injected instruction, and a test proves the agent ignores it); no secrets or personal data in answers or logs; developer settings (model, system prompt) on a developer page the compliance lead can't see.
- 2026-11-03: **build in milestones, one small spec each**, each landing in main as one PR: M0 walking skeleton (Nov 5), M1 assessment core (Nov 8), M2 guided chat and memory (Nov 11), M3 live and guarded (Nov 13), M4 second profile, report and evals (Nov 15), M5 docs and polish (Nov 16). *Dates and order superseded below* ("milestones run in two parallel pairs"), and *the just-in-time rule superseded* by "specs and tickets may be written ahead". `/to-spec` and `/to-tickets` run per milestone, just before it's built, so tickets are current when built and reviewed. Milestones may run in parallel where they don't depend on each other. Why: small spec PRs are the owner's explain-back points; just-in-time tickets use what earlier milestones taught.
- 2026-11-03: **review mode: Factory inside each spec**: agents build, review, verify and merge tickets onto the integration branch; the owner answers parked questions and reads and tests each spec PR (M1 and M2 PRs carry a short "how it works" walkthrough). The owner is happy to review one PR while another milestone builds. Why: about one review every two days, without the owner becoming the bottleneck.
- 2026-11-03: **asset and risk management: designed for now, built as the top stretch.** The profile gets an asset register (a fixture, like an inventory export) and a place for risks to link to the controls that reduce them; nothing uses them until after M4. If M0 to M3 land on time, it's built before the Lithuanian profile, after its own grilling round (asset types, risk scale, how risk reorders the prep). ISA questions it would add: 1.3.1, 1.3.2, 1.4.1, 3.1.1, 3.1.4. Why: real audits start from risk (the owner's TISAX experience: "is the server room locked; what if a laptop is stolen"), and room in the data model is cheap now and a rewrite later.
- 2026-11-03: **milestones run in two parallel pairs**: M0 skeleton (Nov 5) → M1 assessment core (Nov 8) alongside M3 live and guarded (Nov 9) → M2 chat and memory alongside M4 Austrian profile and evals (both Nov 11) → M5 readiness report, docs and polish (Nov 14). Prompt-injection protection (security layer 3) moves into M1, where evidence is gathered; setting up Neon is an owner ticket in M3, ready before M2; the readiness report moves to M5. Why: frees about two days (Nov 14 to 16) for the asset and risk stretch. Risk: merge conflicts in shared API code, limited by M0 setting module boundaries (agent package, API, auth and settings).
- 2026-11-03: **assessment works per requirement**: each "+" bullet of an ISA control (must, should, and the high or very high protection columns) is a requirement with its own verdict; the control's verdict rolls up (all must-requirements found → found; some → partial; none → gap). Which columns apply comes from the profile's protection level (normal, high, very high), a company-overlay field. Each missing requirement can become a remediation item. Why: a partial verdict then says what's missing, and the eval gets sharper.
- 2026-11-03: **evidence has a kind: documented or implemented.** A requirement about doing something needs implemented evidence (a record: access review export, training completion, backup test log, Patch's vulnerability status) to be found; a policy alone gives partial. Nordlicht fixtures get a `records/` folder beside `policies/`. Why: "paper vs practice" is the commonest audit finding, and it gives the agent a reason to call a second tool.
- 2026-11-03: **no maturity score in v1.** Verdicts are about evidence coverage, and the report says so; the model leaves room for a maturity field. Why: maturity needs process judgement documents rarely show, and a guessed "level 3" would claim more than was evidenced. Named in the reflection as the next step.
- 2026-11-03: **evidence documents carry metadata** (title, version, owner, approval date, review cycle, language). Stale evidence caps a requirement at partial, and the answer says why. One fixture policy is stale on purpose, as an eval case. Why: auditors check currency first; flow 7 would need the same data later.
- 2026-11-03: **citations are exact quotes, checked in code**: document, section, version and the quoted passage. Middleware checks the quote appears in that document version; a failing citation is dropped and logged. Quotes stay in the source language; the answer is in the profile's language. Why: a deterministic guard against invented citations, and a hard check for the eval's citation precision.
- 2026-11-03: **an append-only audit trail**: every reviewed verdict and approved remediation item is saved with who, when, the note, the cited document versions, and the model and prompt version that drafted it. The readiness report links to it. Why: "who accepted this, on what basis?" is an auditor's question, and it shows the AI didn't change a signed-off verdict.
- 2026-11-03: **controls or requirements can be not applicable**, marked in the profile with a written reason, left out of readiness and listed in the report. Why: ISA expects justified exclusions; one profile field.
- 2026-11-03: **Vouch never claims "compliant", "certified" or "passed".** It says evidenced, partly evidenced, not evidenced; the readiness report states "Drafted by an AI assistant, reviewed by <user>; not an audit result." Enforced in the system prompt and by an output check for the banned words. Why: a TISAX label or ISO certificate comes only from an accredited audit; fits the formal persona.
- 2026-11-03: **invented data only**: the deployed app holds no real documents. The docs and reflection say what a real deployment needs (provider data-retention terms, an EU-hosted or local model, a classification check before anything leaves the company). **Local models via Ollama join the evals** as a free arm (Ollama is installed on the owner's machine; AMD RX 7900-class GPU, 30 GB RAM, checked 2026-11-03), so the reflection can compare a local model's quality with the hosted ones; the model is named at M4. Why: honest about confidentiality, and a local model is the realistic answer to it.
- 2026-11-03: **screen layout, from the UI prototype** (branch `prototype/ui`, variant E): the **Dossier** page is the landing overview (where you stand, the 12 controls as cards, evidence quotes); opening a control goes to its **Working paper** (the control index, one row per requirement with verdict and exact quotes, review at the foot), where policies and documents are worked through. The chat is **Patch's pattern**: a floating button bottom right that opens a bubble; enlarged, it can be pinned beside the page; Ctrl/Cmd+K opens it, Esc closes the bubble but never a pinned chat; at 700px or less it is a full-screen sheet; it knows the page it's on. Why: the owner's call after trying five variants; chat-first (A) and one-question-at-a-time (C) were dropped. The look (Tailored or Plain) and the prototype's open questions are still to settle.
- 2026-11-03: **the look is Tailored**, from the owner's mood board (glen-check suits, Atlas Accounting, a dark lamp page): warm glen-check greys, navy for links and focus, brass for small details, oxblood (light) or champagne (dark) for sign-off actions; uppercase serif headings (Libre Caslon Display) over Manrope body text and IBM Plex Mono for ids; pill buttons and navigation; sharp corners (2 to 4px on panels and cards, none on full-width bands and decision areas); a thin glen-check strip under the top bar. Light and dark are both first-class. Why: the owner's pick over the plainer working-paper style; it carries the suit-and-tie persona.
- 2026-11-03: **review is per control, with per-requirement override**: the compliance lead reads the requirement rows and accepts or rejects the control once, with a note; before that, any requirement's verdict can be changed, with a reason, and the control's verdict recalculates. The audit trail keeps the agent's verdict, the override and its reason. Why: sign-off stays at the auditor's level (12 per profile, not 60 to 80), and a wrong requirement verdict can still be fixed.
- 2026-11-03: **should-requirements don't change the verdict**: the verdict rolls up from must-requirements (plus high-protection and overlay ones where they apply); a should score is shown beside it, for example "partly evidenced · should 3 of 4". Why: ISA treats should as good practice; auditors note it but don't fail on it.
- 2026-11-03: **a rejected verdict re-runs only when the lead asks**: "Assess again" carries the lead's rejection note into the next run; Vouch never re-runs on its own. With overrides, a rejection mostly means the evidence was wrong or stale.
- 2026-11-03: **one remediation item per missing requirement**, grouped under its control in the readiness report. Why: each has a clear owner and done-condition.
- 2026-11-03: **verdict values match the screen wording**: `evidenced`, `partly-evidenced`, `not-evidenced` (plus `not-applicable`) in code, data and the glossary, shown as Evidenced, Partly evidenced, Not evidenced, Not applicable (German: Nachgewiesen, Teilweise nachgewiesen, Nicht nachgewiesen, Nicht anwendbar). This supersedes found / partial / gap in the decisions above. Why: the owner's call; one vocabulary from data to screen, and it keeps to the "never say compliant" rule.
- 2026-11-03: **the interface stays English; answers, chat and the readiness report follow the profile's language.** Full interface translation is listed as a later improvement. Why: translating menus shows no agent behaviour and costs a day.
- 2026-11-03: **a German-language profile cites the German ISA edition**, if its licence matches the English one (unverified: only the English Excel's licence sheet was read); otherwise English ISA text with German answers. Check at M4.
- 2026-11-03: **the readiness report is written for the company's management and as an auditor's pre-read**: formal and fit to forward, which is why it carries the AI disclaimer, the audit trail and the not-applicable list.
- 2026-11-03: **specs and tickets may be written ahead** (amends the milestone decision above): all six specs are filed (#1 to #6); tickets can be too. What must not happen is work stalling on an owner-only task, so `ready-for-human` tickets are filed first and listed for the owner up front. Why: the owner's clarification; the cost of waiting is the owner's blocked tasks, not early tickets.
- 2026-11-04: **on Cloud Run the app runs in `clerk` auth mode, set explicitly by the container, and refuses to start without the Clerk keys, naming the missing one.** `local` mode (one default user) is for a developer's machine only. Why: M3's tickets as written (#31's `local` default when no Clerk key is set, with #34's real model in local mode when a key is set) would let an anonymous visitor on the public URL use the paid model, breaking M0's no-cost guarantee; a misconfigured deploy should fail loudly, never bill. Found reading ahead from M0. Rejected: real model only for signed-in users (no local real-model runs without Clerk); both guards (most settings to explain).
- 2026-11-04: **the developer role lives in Clerk's private metadata** (`{"role": "developer"}`), read only by the backend through Clerk's API (cached per user), never sent to the browser. Why: the owner's choice when creating the developer user; the role stays invisible in the browser. Cost: an API call or a cache per check, unlike Patch's public metadata. Rejected: public metadata (the tickets' first assumption).
- 2026-11-04: **the database is Cloud SQL for PostgreSQL, not Neon**, in `europe-west1` beside Cloud Run, Enterprise edition, `db-f1-micro` (amends the 2026-11-03 state decision; LangGraph's Postgres checkpointer and store stay). Why: the owner has a Google Cloud free trial with about €264 of credits, so it costs nothing during the course. One vendor fewer, a direct Unix-socket connection from Cloud Run, no idle sleep, no 90-day deletion. `europe-west1` rather than Frankfurt: Frankfurt was chosen only because Neon has no Belgium region, and keeping the database beside the app avoids cross-region latency and traffic; both are in the EU. Cost: about €10 a month from the credits (unverified), and the instance stops when the trial ends unless the account is upgraded, as Cloud Run does. Checked 2026-11-04 (docs.cloud.google.com/sql/docs/postgres/create-instance, /connect-run): from PostgreSQL 16 the default edition is Enterprise Plus and shared-core tiers need Enterprise, so `--edition=ENTERPRISE` is passed explicitly. Cloud Run connects with `--add-cloudsql-instances` and the Cloud SQL Client role, through `/cloudsql/<project:region:instance>`. No pooler, so `prepare_threshold=0` and a separate direct URL aren't needed. Rejected: Neon (a second vendor; free, and would outlive the trial).
- 2026-11-04: **#32 (Clerk sign-in) merges into the integration branch only after the owner reads its PR**; the merge gate isn't started on it alone. Why: auth is a Factory fixed gate, and the owner chose to review the PR rather than park the ticket.
- 2026-11-05: **the owner's calls on M3's spec PR (#87)**, recorded in Fix #151:
  - **Clerk keys set with a blank auth mode refuse to start**, naming `VOUCH_AUTH_MODE`; an explicit `local` with keys set still starts (a developer's choice), and `scripts/dev.sh` sets `local` when nothing is set. Why: keys without a mode is a deploy that lost its mode, and falling back to `local` would serve the paid model to anyone (the 2026-11-04 rule); failing loudly beats a silent default.
  - **`CALL_BUDGET` is 12 model calls per assessment**, up from 8. Why: M1's graph already makes about 6 (gather, judge, remediation drafts, each output check's retry), so 8 left too little room; 12 still bounds the cost of one assessment.
  - **Phone numbers stay out of redaction**; an international-only pattern (`+43 …`) is added only if phone numbers show up in evidence. Why: #36's pattern both missed joined numbers and removed ordinary text such as `01/2024-12/2024`, `doc 0815/2023` and `0800-1700`.
  - **No dedicated `vouch` service account** while the Google Cloud project holds only Vouch; the service keeps the account the deploy wizard grants its two roles to. Why: a separate account matters when other workloads share the project, and none do.

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

Updated 2026-11-03, after grilling. The earlier questions (the product, the catalogue, the UI, Patch) are answered under Decisions.

- Where profiles live. Recommended: YAML files checked by Pydantic; settle in M1's `/to-spec`.
- The third eval model from another provider, and the local Ollama model: named at M4, once checked on OpenRouter and on the owner's GPU.
- Asset and risk management: its own grilling round (asset types, risk scale, how risk reorders the prep), only if M0 to M3 land on time.
- The Lithuanian profile: a nice-to-have, after the risk stretch.
- An avatar for Vouch: parked 2026-11-03. Fourteen options were tried on the prototype branch (`prototype/ui`: tick, clip, seal, button, pocket watch, owl, stamp, monogram seal, lens, tie, pot plant, notebook, coffee cup); none worked. Vouch may not need one, and that's fine: the formal persona carries it.
