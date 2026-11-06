# Course tasks

Which of the course's optional tasks Vouch does, and where to find each one. The task list is the course brief's ([`project-brief.md`](project-brief.md), "Optional tasks"); the target was 3 medium and 2 hard, one of margin each ([decision of 2026-11-03, "bonus targets"](requirements.md#decisions)).

**App pages** a reviewer opens: `/` is the Dossier (where the lead stands, the 12 controls, and the chat button bottom right, also opened with Ctrl K or Cmd K); `/controls/<id>` is a control's working paper (for example `/controls/5.1.2`); `/developer` is the developer page, for a user with the developer role only. Locally, `scripts/dev.sh` runs the app with no sign-in and the free scripted model.

**Summary:** 2 easy, 5 medium and 2 hard tasks done.

## Optional tasks

### Easy

| Task | Status | Where |
|---|---|---|
| Ask an LLM to critique the solution (usability, security, prompt engineering) | Not done | No critique is written up for the project. |
| Give the agent a personality | Done | Formal, like an experienced auditor: the chat's system prompt in [`vouch.chat_agent`](../src/vouch/chat_agent.py) and the assessment's in [`vouch.graph`](../src/vouch/graph.py). App: the chat on `/`. |
| Let the user choose from a list of LLMs | Not done | On purpose: users get no model picker ([decision of 2026-11-03, "model"](requirements.md#decisions)). A developer chooses the model instead (multi-model support, below). |
| Expose model settings (temperature, max tokens) as sliders or fields | Not done | Not planned: the developer page sets the model and the daily limit only. |
| An interactive help feature or chatbot guide | Done | The guided chat: it greets the lead with their progress, suggests the next step and answers questions with cited evidence. [`vouch.chat_agent`](../src/vouch/chat_agent.py), [`AskVouch.tsx`](../frontend/src/AskVouch.tsx). App: the chat on `/` or any page. |

### Medium

| Task | Status | Where |
|---|---|---|
| Calculate and show token usage and cost | Done | [`vouch.spending`](../src/vouch/spending.py): each assessment's tokens and cost (OpenRouter's reported cost, else the price table). App: the Usage line on `/controls/<id>` after an assessment; every assessment's usage totalled on `/developer`. |
| Long- or short-term memory in LangChain/LangGraph | Done | LangGraph's Postgres checkpointer and store: [`vouch.memory`](../src/vouch/memory.py), [`vouch.database`](../src/vouch/database.py). App: reload `/`, and the chat greets the lead where they left off. |
| One more function tool that calls an external API | Not done | Patch's `vulnerability_status` tool ([`vouch.patch`](../src/vouch/patch.py)) reads a recorded response, not the live Patch API; live Patch is a stretch item. |
| User authentication and personalisation | Done | Clerk sign-in, invite-only (plus members of the course's Discord server): [`vouch.auth`](../src/vouch/auth.py), [`ClerkApp.tsx`](../frontend/src/ClerkApp.tsx). Personalisation: each user's own progress, memory and audit trail per profile, and the profile switcher ([`ProfileSwitcher.tsx`](../frontend/src/ProfileSwitcher.tsx)). App: the live app's sign-in, and the top bar on `/`. |
| A feedback loop: users rate responses, and the ratings improve the agent | Not done | The lead accepts or rejects each verdict with a note, and "Assess again" carries a rejection note into that control's next run, but nothing improves the agent from it. |
| 2 extra function tools (5 in total), with a UI to enable or disable them | Not done | The chat has five tools and an assessment three, but they're enabled per profile in its YAML allow-list, with no UI. |
| Multi-model support (OpenAI, Anthropic, etc.) | Done | Any OpenRouter model id (Anthropic's, OpenAI's, TypeSafe's router), and local models through Ollama in the evals: [`vouch.model_choice`](../src/vouch/model_choice.py), [`vouch.eval_models`](../src/vouch/eval_models.py). App: the model field on `/developer`. |
| At least one security guard; developer settings apart from the user experience | Done | Five layers in code outside the model ([technical decisions](technical-decisions.md#five-security-layers-and-the-spending-guard)): [`vouch.limits`](../src/vouch/limits.py), [`vouch.guards`](../src/vouch/guards.py), [`vouch.allow_list`](../src/vouch/allow_list.py), [`vouch.redaction`](../src/vouch/redaction.py), and the developer page ([`DeveloperPage.tsx`](../frontend/src/DeveloperPage.tsx)). App: guard steps in an assessment's trace ("How Vouch reached this") on `/controls/<id>`; `/developer` for a developer only. |

### Hard

| Task | Status | Where |
|---|---|---|
| Agentic RAG in the LangChain/LangGraph app | Done | The model searches the policies and records through tools, inside the graph's gather loop, and each quote is checked in code: [`vouch.search`](../src/vouch/search.py), [`vouch.citations`](../src/vouch/citations.py), [`vouch.graph`](../src/vouch/graph.py). App: run an assessment on `/controls/<id>` and read its quotes. |
| An LLM observability tool (LangSmith, Langfuse or others) | Not done | Langfuse is a stretch item, after the must list ([decision of 2026-11-03, "cut line"](requirements.md#decisions)). |
| An AI evaluation report with Ragas or DeepEval | Done | DeepEval's faithfulness with a judge model, plus verdict accuracy, citation precision, cost and time: [`vouch.evals`](../src/vouch/evals.py), [`vouch.faithfulness`](../src/vouch/faithfulness.py), [`eval-report.md`](eval-report.md), [eval summary](eval-summary.md), and the [published report](https://derykhopley.github.io/tc-vouch-compliance/evals/). |
| An agent that learns from user feedback and adjusts its capabilities | Not done | Reviews are kept in the audit trail but don't change the agent. |
| Integration with external data sources (APIs or websites) | Not done | The one outside source, Patch, is a recorded response ([`vouch.patch`](../src/vouch/patch.py)); live Patch is a stretch item. |

## Core requirements

The course's core requirements, R1 to R5, as written in [`requirements.md`](requirements.md#requirements-from-the-course-brief).

| Requirement | Where |
|---|---|
| [R1](requirements.md#r1--agent-fundamentals-and-react-part-1): an agent that chooses tools and acts on their results (ReAct) | The chat's ReAct agent, [`vouch.chat_agent`](../src/vouch/chat_agent.py), and the graph's gather loop, [`vouch.graph`](../src/vouch/graph.py). Why an agent: [technical decisions](technical-decisions.md#why-an-agent-and-when-prompt-engineering-rag-or-an-agent-fits). App: the chat on `/`. |
| [R2](requirements.md#r2--tools-state-and-middleware-part-2): state, tool input checks, failures handled, security | Graph state in [`vouch.graph`](../src/vouch/graph.py); tool input checks in [`vouch.search`](../src/vouch/search.py) and [`vouch.guards`](../src/vouch/guards.py); Patch's errors as results, never exceptions, in [`vouch.patch`](../src/vouch/patch.py); the five security layers. App: guard steps on `/controls/<id>`. |
| [R3](requirements.md#r3--langgraph-part-3): nodes, edges and state, with branching | The assessment graph and its overlay branch, [`vouch.graph`](../src/vouch/graph.py); [technical decisions](technical-decisions.md#the-graphs-branches-and-the-review-interrupt). App: locally, the [overlay example](features.md#the-overlay-branch) (1.6.2 under Alpenwerk, with `scripts/dev.sh` started on its eval script); on the live app, assess 1.6.2 under Alpenwerk on `/controls/1.6.2`. |
| [R4](requirements.md#r4--memory-and-human-in-the-loop-part-4): memory and human review | The review interrupt in [`vouch.graph`](../src/vouch/graph.py), the audit trail in [`vouch.audit`](../src/vouch/audit.py), memory in [`vouch.memory`](../src/vouch/memory.py). App: the review at the foot of `/controls/<id>`. |
| [R5](requirements.md#r5--bring-it-together): a usable UI, documented, choices explained | The React front end, [`frontend/src/`](../frontend/src/App.tsx); [technical decisions](technical-decisions.md), [eval summary](eval-summary.md), [reflection](reflection.md). |
| The readiness report | [`Report.tsx`](../frontend/src/Report.tsx) and its API, `GET /api/report` (#56, #234). App: the Report tab, `/report`; the [walk-through](features.md#4-read-the-report). |
