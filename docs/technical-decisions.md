# Technical decisions

Why Vouch is built the way it is. Each section gives the choice, the reason, the alternative not taken, and where the decision was made. The full decision log is [`requirements.md`, Decisions](requirements.md#decisions): dated bullets, newest at the bottom. The words used here are [`GLOSSARY.md`](../GLOSSARY.md)'s.

Vouch helps a compliance lead prepare for a TISAX or ISO 27001 audit. For each control of a compliance profile it searches the company's evidence, quotes it, proposes a verdict (Evidenced, Partly evidenced, Not evidenced), and waits for the lead to review it.

## Why an agent, and when prompt engineering, RAG or an agent fits

**Choice:** an agent only where each step depends on what the last one found; plain code everywhere else.

**Reason:** each control needs different evidence. One needs a policy, another a record that something was done, a third Patch's vulnerability status. A fixed script can't know in advance which source to search, what to search for next, or when it has enough. The agent decides that, and the code around it checks what it brings back.

How Vouch splits the work, which is also its answer to the brief's question:

- **Prompt engineering alone** fits a fixed task with all its input in hand: the wording rule, the formal persona, and the answer language are prompt instructions (backed by checks in code, below).
- **RAG** fits when the answer lives in documents too large to send whole: the policy and record searches (`search_policies`, `search_records`) are retrieval, and the eval shows why it's needed (a real company's evidence won't fit in one prompt).
- **An agent** fits when the steps aren't known in advance: gathering evidence for a control, and the guided chat picking the next step for the lead.
- **Plain code** does the rest: loading profiles and controls, the quote check, the roll-up of requirement verdicts into a control verdict, the readiness counts, the audit trail.

**Alternative not taken:** a fixed pipeline per control (search the policies, then judge). It would miss implemented evidence and Patch, and couldn't search again when the first search finds nothing.

**Decision:** [2026-11-03, "the guided audit-prep chat is the product"](requirements.md#decisions) and the flow split in [Fit with the project brief](requirements.md#fit-with-the-project-brief-added-2026-11-02).

## Two agent layers: a ReAct chat and an assessment graph

**Choice:** two kinds of agent, kept apart.

- **The guided chat is a ReAct agent** ([`vouch.chat_agent`](../src/vouch/chat_agent.py)). It picks one of five tools each turn: `progress` (where the lead stands), `start_assessment`, `explain_control`, `search_evidence` and `assessed_controls`. It is open-ended: the lead asks anything, and the model chooses the tool.
- **An assessment is a LangGraph graph** ([`vouch.graph`](../src/vouch/graph.py)): fixed nodes and edges, with the model called inside some of them. One control in, one assessment out, with a review in the middle.

**Reason:** an assessment must always do the same things in the same order (apply overlays, gather, judge, check quotes, draft remediation, wait for review), and must pause safely for a human. A graph makes that order explicit and checkpointed. The chat has no fixed order, so a ReAct loop fits it. The chat starts an assessment through a tool (`start_assessment`), and never judges a control itself. It is also a concrete answer to "differences between agent types".

**Alternative not taken:** one ReAct agent doing everything. Its order would depend on the model, and the review pause would have nowhere fixed to live.

**Decision:** [2026-11-03, "two agent layers"](requirements.md#decisions); built in #45 and #46.

## The graph's branches and the review interrupt

**Choice:** the assessment graph's nodes are `apply_overlays`, `overlay_branch`, `gather`, `tools`, `check_answer`, `judge`, `check_quotes`, `remediate` and `review`, with three branches:

- **An overlay adds a requirement:** after `apply_overlays`, a control that a country overlay extends (Alpenwerk's illustrative Austrian NIS2 rule on 1.6.2: an early warning to the national CSIRT within 24 hours of a serious incident) takes `overlay_branch`; others go straight to `gather`.
- **Gather loops:** `gather` calls `tools` until the model submits an answer (a ReAct loop inside the graph, within the call budget).
- **The answer check:** `check_answer` sends a refused answer back to `gather` for another try.

The `review` node calls LangGraph's `interrupt()`. The run stops there, kept in the checkpointer, until the compliance lead accepts or rejects it. **Code before the interrupt only reads;** the review's result is applied after resume.

**Reason:** a node with `interrupt()` runs again from its start when it resumes, so anything it wrote before the interrupt would be written twice. Keeping writes after it makes a resumed review safe. The branch makes R3's "explicit branching" a real case rather than a diagram, which is why the second profile exists.

**Alternative not taken:** a review as a status flag set by a separate API call, with no pause in the graph. The run's state and its review could then disagree.

**Decision:** [2026-11-03, "two agent layers"](requirements.md#decisions) (the node list and "code before the interrupt only reads"), and [2026-11-03, "two compliance profiles"](requirements.md#decisions) (why the overlay branch).

## Agentic RAG, with exact quotes checked in code

**Choice:** the model searches the evidence itself, through tools ([`vouch.search`](../src/vouch/search.py): a keyword search over one evidence folder), and every quote it cites is checked in code ([`vouch.citations`](../src/vouch/citations.py)). A quote must appear word for word in the document version it names; one that doesn't is dropped and shown as a guard step. The answer can't cite what isn't there.

Two more rules sit on top:

- **Stale evidence:** each document carries its approval date and review cycle in its front matter ([`vouch.documents`](../src/vouch/documents.py)). A document past its review cycle can make a requirement partly evidenced at best, and the answer says why.
- **Documented versus implemented evidence:** a policy says what the company intends (documented); a record shows it was done (implemented). A requirement about doing something needs implemented evidence to be evidenced; a policy alone gives partly evidenced. That's why each profile has a `policies/` and a `records/` folder, and why the agent has a reason to call a second tool.

**Reason:** an audit-prep tool that invents a citation is worse than none. A check in code is deterministic, and it also gives the eval a hard citation-precision measure. Staleness and paper-versus-practice are the commonest audit findings.

**Alternative not taken:** trusting the model's own citations, or checking them with a second model. Both can be wrong in the same way the first model is.

**Decision:** [2026-11-03, "citations are exact quotes, checked in code"](requirements.md#decisions), ["evidence has a kind: documented or implemented"](requirements.md#decisions), ["evidence documents carry metadata"](requirements.md#decisions) and ["v1 evidence sources"](requirements.md#decisions).

## Compliance profiles as data

**Choice:** everything that differs between companies is a YAML file per company, validated on load ([`vouch.profile`](../src/vouch/profile.py); [`fixtures/nordlicht/profile.yaml`](../fixtures/nordlicht/profile.yaml), [`fixtures/alpenwerk/profile.yaml`](../fixtures/alpenwerk/profile.yaml)): the base framework, the protection level, country overlays, not-applicable controls and requirements with their reasons, the answer language, and the evidence sources the agent may use. One agent serves every profile.

The base framework is 12 real VDA ISA 2027.1 controls, quoted unchanged with credit ([`src/vouch/data/vda-isa-2027.1/`](../src/vouch/data/vda-isa-2027.1/NOTICE.md)). **A German-language profile cites the German ISA edition** (Alpenwerk, the invented Austrian company): its licence matches the English one, so both editions ship unchanged, and a profile picks the edition by its answer language.

**Reason:** making things configurable touches every tool, the state and the prompts, so it was designed in from the start rather than added later. The auditor of a German-speaking company reads the German ISA, and quoting it unchanged stays within its CC BY-ND licence.

**Alternative not taken:** prompts and code per company (a rewrite for each new customer); for German, Vouch's own translation of ISA (a derivative the licence restricts).

**Decision:** [2026-11-02, "compliance profiles are core"](requirements.md#decisions), [2026-11-03, "catalogue: real VDA ISA questions"](requirements.md#decisions), [2026-11-05, "the German ISA edition's licence matches"](requirements.md#decisions) (#48).

## Five security layers, and the spending guard

**Choice:** security is enforced in code outside the model, in five layers:

1. **Input limits** ([`vouch.limits`](../src/vouch/limits.py)): a request body over 8,192 bytes is refused (413), and a user over 60 requests a minute too (429).
2. **The per-profile allow-list** ([`vouch.guards`](../src/vouch/guards.py), [`vouch.allow_list`](../src/vouch/allow_list.py)): every tool call is checked against the profile's evidence sources before it runs, once in the graph and again at the API.
3. **Document text is data** ([`vouch.guards`](../src/vouch/guards.py)): evidence reaches the model as escaped JSON inside tags, with instruction-like text flagged. A fixture policy carries an injected instruction, and a test proves the agent ignores it.
4. **No secrets or personal data out** ([`vouch.redaction`](../src/vouch/redaction.py)): keys, tokens, passwords in URLs and e-mail addresses (other than the profile's own listed ones) are removed from every streamed event and every log line.
5. **Developer settings apart** ([developer page](../frontend/src/DeveloperPage.tsx), `/developer`): the model and the daily limit are changed on a page only a developer reaches; the role lives in Clerk's private metadata, never sent to the browser.

**The spending guard** ([`vouch.spending`](../src/vouch/spending.py)) bounds what a deployed app can cost: a daily limit of 30 assessments per user, a call budget of 12 model calls per assessment (`CALL_BUDGET`, [`vouch.model_choice`](../src/vouch/model_choice.py)), at most one Cloud Run instance, and each assessment's tokens and cost shown with it. On Cloud Run only signed-in users reach the paid model; without a key, assessments run on a free scripted model.

**Reason:** a model can be talked out of a rule in its prompt; code can't. A public URL in front of a paid model needs a ceiling that holds even when everything else goes wrong.

**Alternative not taken:** rules in the system prompt only.

**Decision:** [2026-11-03, "the security guard is five layers in middleware"](requirements.md#decisions), ["spending guard, as Patch"](requirements.md#decisions), [2026-11-04, "on Cloud Run the app runs in clerk auth mode"](requirements.md#decisions), [2026-11-05, "`CALL_BUDGET` is 12 model calls"](requirements.md#decisions) (#151).

## Memory: LangGraph's Postgres checkpointer and store on Cloud SQL

**Choice:** LangGraph's Postgres checkpointer keeps each assessment as its own thread (so a run waiting for review survives a restart), and its store keeps each user's long-term memory per compliance profile: which assessments they started, when they last worked ([`vouch.memory`](../src/vouch/memory.py)). Both live in Cloud SQL for PostgreSQL, beside Cloud Run in `europe-west1`, through one connection pool ([`vouch.database`](../src/vouch/database.py)).

**A session opens where the user left off:** the chat greets the lead with their progress (controls reviewed, rejected verdicts waiting, the next control) and suggests the next step (the chat's `progress` tool).

**Reason:** audit prep spans weeks, so remembering where the lead stands is what memory is for. Using LangGraph's own checkpointer and store makes it real LangGraph memory, and the checkpointer is also what makes the review interrupt resumable. Cloud SQL runs on the owner's Google Cloud free trial credits, one vendor with the app, with no idle sleep.

**Alternative not taken:** a second vendor's free hosted Postgres (the first plan), or per-user JSON files in Cloud Storage (the fallback, not LangGraph memory).

**Decision:** [2026-11-03, "a session opens where the user left off"](requirements.md#decisions), [2026-11-04, "the database is Cloud SQL for PostgreSQL"](requirements.md#decisions); built in #33 and #38.

## The wording rule: never "compliant", "certified" or "passed"

**Choice:** Vouch never claims an audit result. Verdicts are Evidenced, Partly evidenced, Not evidenced and Not applicable (in German: Nachgewiesen, Teilweise nachgewiesen, Nicht nachgewiesen, Nicht anwendbar).

Where it is enforced:

- **In the prompts:** the assessment's and the chat's system prompts forbid the words.
- **In code, on every assessment:** the graph's `check_answer` step refuses an answer that claims an audit result and asks again; `remediate` does the same for each drafted remediation item ([`vouch.graph`](../src/vouch/graph.py), `claims_audit_result`). The words are listed per language in [`vouch.language`](../src/vouch/language.py), and a negation ("not compliant") or a quote from the evidence doesn't count.

In the chat the rule is the prompt's; the chat's code check is on quotes (every quote checked against its document), not on these words.

**Reason:** a TISAX label or ISO certificate comes only from an accredited audit. A tool that says "compliant" overstates, and could mislead a manager reading its output.

**Alternative not taken:** the prompt alone. In the eval the check refused Haiku's answer for Nordlicht 5.1.2 twice for saying "passed", so the prompt alone wasn't enough.

**Decision:** [2026-11-03, "Vouch never claims 'compliant', 'certified' or 'passed'"](requirements.md#decisions), and ["verdict values match the screen wording"](requirements.md#decisions).

## The default model: Sonnet 5.5

**Choice:** assessments run on `anthropic/claude-sonnet-5.5` (`DEFAULT_MODEL` in [`vouch.model_choice`](../src/vouch/model_choice.py)), through OpenRouter, unless a developer picks another on the developer page.

**Reason, from the evals:** on Nordlicht, the profile whose expected answers the owner reviewed, Sonnet got 76% of verdicts and 9 of 12 controls right, and it is the only model whose lead holds control by control (over Haiku on 10 of 11 controls that differ). It failed no case. It costs about $0.10 to $0.12 an assessment. See the [eval summary](eval-summary.md).

**Alternative not taken:** GPT-6 Luna (68% at about 1/32 of the cost, but not run on the German profile, so not shown to serve it), and Haiku 5.5, the earlier default (Luna is as good for half the price).

**Known cost of the choice:** Sonnet's judge step sometimes left requirements unjudged in the eval (shown as not-evidenced); the eval report names Fix #184 for it.

**Decision:** [2026-11-06, "the default model is Sonnet 5.5"](requirements.md#decisions) (the owner on #55), from [the eval report's recommendation](eval-report.md#recommendation-the-default-model-chosen-sonnet-55).
