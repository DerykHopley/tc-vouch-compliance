# Vouch

Vouch helps a compliance lead prepare for an information security audit (TISAX, ISO 27001) by tracing each control back to the company's own evidence.

## Language

### Catalogue and profile

**Control**:
One requirement the company must show it meets, such as one VDA ISA question.
_Avoid_: Requirement, check, question (except when quoting ISA)

**Requirement**:
One "+" bullet of a control (must, should, or for high or very high protection); the unit the agent judges.
_Avoid_: Evidence point, criterion, sub-control

**Protection level**:
The company's protection need (normal, high or very high), which decides which of a control's requirements apply.
_Avoid_: Assessment level, risk level, tier

**Not applicable**:
A control or requirement the profile excludes, with a written reason.
_Avoid_: Out of scope, skipped, N/A (in prose)

**Base framework**:
The published catalogue of controls a profile starts from; for v1, VDA ISA.
_Avoid_: Standard, catalogue (alone)

**Company overlay**:
The company's own layer on top of the base framework: its scope, its policies and its evidence.
_Avoid_: Company config, tenant

**Country overlay**:
A layer of national rules (for example a country's NIS2 law) that can add requirements to the base framework's controls.
_Avoid_: Regional rules, local law

**Compliance profile**:
The stack of layers (base framework, overlays, language) that defines what one company must show and how answers are written.
_Avoid_: Config, settings, project

### Assessment

**Assessment**:
One run of the agent over one control in one compliance profile, ending in a verdict with its cited evidence.
_Avoid_: Check, evaluation, audit

**User**:
One person using Vouch: signed in with Clerk on the live app, or the one default user on a developer's machine. Their id scopes everything they store. A user's role is compliance lead (the default) or developer.
_Avoid_: Account, tenant

**Developer page**:
The page, and its API, where a developer sees and changes the model assessments run on and the daily limit, and sees the prompt version and every assessment's usage totalled. Only a user with the developer role reaches it; a compliance lead never sees it.
_Avoid_: Admin page, settings page

**Prompt version**:
The version of the assessment's prompts (`PROMPT_VERSION`), raised with any change to their words, so a run can say which prompts drafted it.
_Avoid_: Prompt id, template version

**Daily limit**:
How many assessments one user may start in a UTC day (30 unless a developer changes it); past it, an assessment is refused with the time the count resets.
_Avoid_: Quota, rate limit (that is the per-minute request limit)

**Usage**:
One assessment's input and output tokens and its cost: OpenRouter's reported cost, or one computed from a price table and marked estimated.
_Avoid_: Spend, billing

**Audit prep**:
The weeks-long work of a compliance lead getting a compliance profile's controls evidenced before an audit; Vouch's guided chat walks them through it.
_Avoid_: Onboarding, project, campaign

**Evidence**:
A company document, or a passage in one, that shows a control is met.
_Avoid_: Proof, artefact, attachment

**Evidence source**:
A place the agent may search for evidence, allowed by the compliance profile, such as the policy folder or Patch.
_Avoid_: Connector, integration, data source

**Documented evidence**:
Evidence that says what the company intends to do, such as a policy or procedure.
_Avoid_: Paper evidence, policy evidence

**Implemented evidence**:
Evidence that shows something is actually done, such as a record, an export or a log.
_Avoid_: Operational evidence, proof of practice

**Stale evidence**:
Evidence past its review cycle; it can make a requirement partly evidenced at best.
_Avoid_: Expired, outdated, old

**Citation**:
An exact quoted passage from one version of an evidence document, with its section, backing a verdict.
_Avoid_: Source, reference, link

**Verdict**:
The agent's proposed judgement of a requirement's or control's evidence: evidenced, partly evidenced or not evidenced.
_Avoid_: Status, result, score, found / partial / gap

**Guard step**:
A step in an assessment (node `guard`) saying a guard, in code outside the model, refused, flagged or removed something: a tool call refused (not on the compliance profile's allow-list, or its folder now outside where the profile allowed it), instruction-like text flagged in a passage and treated as data, a citation the quote check dropped, or a secret or personal data removed from what is shown.
_Avoid_: Alert, block, filter step (a warning is something else: a source that could not be read)

**Reviewed verdict**:
A verdict the compliance lead has accepted or rejected, with an optional note; only accepted ones count towards readiness.
_Avoid_: Approval, sign-off, final verdict

**Audit trail**:
The append-only record of every reviewed verdict and approved remediation item: who, when, the note, and the evidence versions behind it.
_Avoid_: History, log, changelog

**Override**:
A requirement's verdict changed by the compliance lead before signing off a control, with a reason; the agent's original verdict is kept.
_Avoid_: Correction, manual verdict, edit

**Remediation item**:
A drafted fix for a requirement that is not evidenced (what's missing, an owner role, a due date), kept once the compliance lead approves it.
_Avoid_: Ticket, task, action item

**Not evidenced**:
A verdict meaning no evidence was found for a requirement or control.
_Avoid_: Gap, fail, non-compliance, finding

**Readiness**:
How much of a compliance profile's controls are backed by accepted verdicts, shown as controls by verdict.
_Avoid_: Compliance score, maturity

**Progress**:
Where a user stands under a compliance profile: each assessed control's latest assessment, accepted, rejected or waiting for review, and when they last worked. Unlike readiness, it counts every status, not only accepted verdicts; it's what Vouch greets the lead with. On screen, an assessment waiting for review is **Proposed** (its review state tag, `docs/design.md`); `waiting-for-review` is the same state in code and the API.
_Avoid_: Status, history

**Long-term memory**:
What Vouch keeps for a user under a compliance profile from one visit to the next (which assessments they started, when they last worked), in LangGraph's store. The assessments themselves wait in the checkpointer, keyed by the same user and profile.
_Avoid_: Session, cache, history

**Readiness report**:
The summary of a compliance profile's reviewed verdicts, citations and remediation items, written in the profile's language.
_Avoid_: Audit report, self-assessment, export

**Eval**:
A measured run of assessments against cases with known expected verdicts and citations, used to compare models.
_Avoid_: Test (for model-graded runs), benchmark

**Eval case**:
One control of a compliance profile in an eval, with the expected verdict and expected cited documents for each of its applicable requirements.
_Avoid_: Test case, sample

**Bad case**:
An eval case that doesn't fit the compliance profile (an unknown control, a requirement that doesn't apply, one left out or named twice), so it is reported and not run.
_Avoid_: Invalid test

**Failed case**:
An eval case whose assessment raised an error; every requirement and its control count as wrong.
_Avoid_: Flaky case, crash
