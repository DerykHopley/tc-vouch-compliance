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

**ISA edition**:
One language's version of the VDA ISA catalogue (English or German), published by the ENX Association and quoted unchanged; never a translation by Vouch. A compliance profile reads the edition in its answer language, English when there is none.
_Avoid_: Translation, localisation, language pack

**Company overlay**:
The company's own layer on top of the base framework: its scope, its policies and its evidence.
_Avoid_: Company config, tenant

**Country overlay**:
A layer of national rules (for example a country's NIS2 law) that can add requirements to the base framework's controls.
_Avoid_: Regional rules, local law

**Compliance profile**:
The stack of layers (base framework, overlays, language) that defines what one company must show and how answers are written.
_Avoid_: Config, settings, project

**Company**:
The organisation Vouch works for, with one compliance profile. The UI says "company" where a lead would; "compliance profile" stays for its layers.
_Avoid_: Profile (alone), tenant, client

**Current company**:
The one company every user sees, developers included. A developer sets it on the Developer page. It starts as Nordlicht, and falls back to Nordlicht when the current company is removed. A demo/prototype choice: a real product gives each lead their own companies (#163).
_Avoid_: Selected profile, active profile

**Company page**:
The page, reached by the top bar's **Company** tab, with the current company's details and settings. Only the Goal can be changed there.
_Avoid_: Settings page, profile page

**Goal**:
What a compliance profile is used for: **Audit readiness** (preparing for an external audit; the flow before M7) or **Internal improvement** (improving the company's own security with no audit planned). The lead can change it; each change is an audit trail entry.
_Avoid_: Purpose, mode, use case

**Company setup**:
Creating a new compliance profile in Vouch for a company (in the demo, an invented one), in seven steps: its name and country, its answer language, its protection level, its Goal, the controls that are not applicable (each with a reason), its evidence chosen from the sample library, then a summary to finish. Setup ends with the new company opened, its Goal set and a first profile pass drafting every applicable control. Only a developer does it. A demo/prototype process; a real SaaS product would do it differently.
_Avoid_: Onboarding (that word is on Audit prep's avoid list), sign-up, registration

**Sample library**:
A set of invented evidence documents (policies and records, with a few deliberate gaps) that ships with Vouch, from which a company made in company setup picks its evidence. Its documents name no company.
_Avoid_: Document pool, template documents

**Reference profile**:
The compliance profile over the whole sample library that the eval scores; it is no company, so no user sees it.
_Avoid_: Sample company, test profile

**Documents tab**:
The read-only list of a compliance profile's evidence documents, each with its kind, version, approval date, review cycle and whether it is stale, and the controls whose newest assessment cites it. Every company has one, for every user. It is the Documents panel of the Company page (`/company`; `/documents` leads there; #495, #505).
_Avoid_: Document library, uploads, files

**Remove company**:
A developer hiding a company made in company setup everywhere: it leaves every list and can't be opened again, but its audit trail is kept and its id is never used again. When it was the current company, everyone falls back to Nordlicht. File-defined profiles can't be removed.
_Avoid_: Delete company, archive

### Assessment

**Assessment**:
One run of the agent over one control in one compliance profile, ending in a verdict with its cited evidence.
_Avoid_: Check, evaluation, audit

**User**:
One person using Vouch: signed in with Clerk on the live app, or the one default user on a developer's machine. Their id scopes everything they store. A user's role is compliance lead (the default) or developer.
_Avoid_: Account, tenant

**Course's Discord server**:
The Discord server of the course Vouch is built for; its members may sign in without an invitation (`DISCORD_SERVER_ID`).
_Avoid_: Guild, community, channel

**Access**:
Whether a signed-in user may use Vouch: a member of the course's Discord server, a developer, or an invited user. Anyone else is not a member.
_Avoid_: Permission, entitlement, allow-list (that is the tools')

**Developer page**:
The page, and its API, where a developer sees and changes the model assessments run on and the daily limit, and sees the prompt version and every assessment's usage totalled. It is also where a developer makes a company current, sets one up and removes one. It is reached from the user menu; only a user with the developer role reaches it, and a compliance lead never sees it.
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
A place the agent may search for evidence, allowed by the compliance profile, such as the policy folder or Patch. A source may be limited to chosen documents of its folder (a company set up in the app chooses from the sample library); then no other document of that folder exists for the profile.
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
An exact quoted passage from one version of an evidence document, or from the lead's statement (an answered open question), with its section, backing a verdict.
_Avoid_: Source, reference, link

**Verdict**:
The agent's proposed judgement of a requirement's or control's evidence: evidenced, partly evidenced or not evidenced.
_Avoid_: Status, result, score, found / partial / gap

**Guard step**:
A step in an assessment (node `guard`) saying a guard, in code outside the model, refused, flagged or removed something: a tool call refused (not on the compliance profile's allow-list, or its folder now outside where the profile allowed it), instruction-like text flagged in a passage and treated as data, a citation the quote check dropped, an answer asked for again (it claimed an audit result, or was written in another language than the profile's) or kept in another language after its one retry, or a secret or personal data removed from what is shown.
_Avoid_: Alert, block, filter step (a warning is something else: a source that could not be read)

**Confirm findings**:
The first step of reviewing a control: the lead confirms or overrides each requirement's verdict.
_Avoid_: Approve requirements, check findings

**Plan fixes**:
The second step of reviewing a control: the lead approves or discards each remediation item. Skipped when nothing is missing.
_Avoid_: Remediation review, action plan approval

**Accept**:
The third and last step of reviewing a control, after Confirm findings and Plan fixes: the lead accepts the control's verdict, which makes it a reviewed verdict. Only accepted controls count towards readiness and the report. An assessment accepted before the three-step review counts as accepted and is marked "before the three-step review".
_Avoid_: Sign off, approve, finalise

**Closed**:
In the Internal improvement goal, a control whose approved remediation items are all done (each with a note) and whose re-assessment after them was accepted with no requirement left not evidenced or partly evidenced. Done fixes alone don't close a control.
_Avoid_: Resolved, fixed, complete

**Review round**:
A period the lead names and opens (such as "Q4 2026"; the first opens on its own) and later closes, which freezes its results. The overview compares the current round with the last closed one. Used in both goals.
_Avoid_: Snapshot, cycle, sprint, audit period

**Open question**:
A question the lead logs on a control for someone else to answer: what is asked, who it is for, a due date, and the answer. Once answered or overdue, Vouch suggests re-assessing the control.
_Avoid_: Task, query, follow-up, ticket

**Lead-supplied evidence**:
An open question's answer used as citable evidence, labelled as the lead's statement. Like stale evidence, it can make a requirement partly evidenced at best.
_Avoid_: Testimony, self-declaration, manual evidence

**Profile pass**:
One model run over every applicable control of a compliance profile with all its evidence in context, producing one assessment per control, each waiting for review. Controls already accepted are skipped, and so are reviews in progress (findings confirmed, at Plan fixes or Accept). Used only when its prompt and expected reply fit under a token threshold, and counted against its own daily limit, apart from the assessments'.
_Avoid_: Batch run, bulk assessment, full scan

**Reviewed verdict**:
A verdict the compliance lead has accepted (the last step of the review, after Confirm findings and Plan fixes) or rejected, with an optional note; only accepted ones count towards readiness.
_Avoid_: Approval, sign-off, final verdict

**Audit trail**:
The append-only record of every action of the compliance lead, one entry each: findings confirmed, a control accepted or rejected, a requirement overridden, a remediation item approved or discarded, its priority changed or its done mark, a review round started or closed, an open question logged or answered, the goal changed. Each entry says who (the user id, and their display name as it was then), when, the note, the verdict before and after, the evidence versions behind it, and the model and prompt version of the run.
_Avoid_: History, log, changelog

**Override**:
A requirement's verdict changed by the compliance lead while confirming findings, before accepting a control, with a reason; the agent's original verdict is kept.
_Avoid_: Correction, manual verdict, edit

**Remediation item**:
A drafted fix for a requirement that is not evidenced or only partly evidenced (what's missing, an owner role, a due date), kept once the compliance lead approves it.
_Avoid_: Ticket, task, action item

**Not evidenced**:
A verdict meaning no evidence was found for a requirement or control.
_Avoid_: Gap, fail, non-compliance, finding

**Readiness**:
How much of a compliance profile's controls are backed by accepted verdicts, shown as controls by verdict.
_Avoid_: Compliance score, maturity

**Progress**:
Where a user stands now under a compliance profile: each assessed control's latest assessment, accepted, rejected or waiting for review, and when they last worked. Unlike readiness, it counts every status, not only accepted verdicts; it's what Vouch greets the lead with. Change between review rounds is a comparison, not progress. On screen, an assessment waiting for review is **Proposed** (its review state tag, `docs/design.md`); `waiting-for-review` is the same state in code and the API.
_Avoid_: Status, history

**Fresh start**:
The lead's progress under a compliance profile listed from a time on (#285): what they started before it is no longer listed or counted, so every control is not assessed again, and the chat opens a new conversation. Nothing is deleted: the assessments, the remediation decisions and the audit trail stay whole, and the audit trail still shows every entry. Started from the user menu ("Start <company> afresh…"), for a repeat demo.
_Avoid_: Reset, clear

**Long-term memory**:
What Vouch keeps for a user under a compliance profile from one visit to the next (which assessments they started, when they last worked), in LangGraph's store. The assessments themselves wait in the checkpointer, keyed by the same user and profile.
_Avoid_: Session, cache, history

**Conversation**:
One chat thread with Vouch: the lead's messages and Vouch's answers, kept on the server, one current per user and compliance profile, and what the chat opens on (#251). "New conversation" starts another; the old one is kept. In code, a chat thread.
_Avoid_: Session, history, chat log

**Dossier**:
The overview of one compliance profile: its readiness, next steps and the controls as cards; the app's landing page, reached by the top bar's **Overview** tab and the working paper's "Overview" breadcrumb (#222).
_Avoid_: Dashboard, home page

**Readiness report**:
The summary of a compliance profile's reviewed verdicts, citations and remediation items, written in the profile's language.
_Avoid_: Audit report, self-assessment, export

**Improvement report**:
The readiness report's shape in the Internal improvement goal, written for the team: where it stands compared with the last closed review round, the improvement plan by priority with each item's status, and the open questions; an internal self-assessment, from the same report page and exports.
_Avoid_: Team report, internal audit report

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

**Dry run**:
The first eval case of a paid or local eval, run alone so the runner can print a cost and time estimate for the rest; the rest run only after the owner types yes.
_Avoid_: Trial run, preview, test run

**Judge model**:
The model that scores an assessment's faithfulness in an eval; named apart from the model being assessed, and never the scripted fake.
_Avoid_: Grader, evaluator, critic

**Faithfulness**:
In an eval, the share of an assessment answer's claims that the passages it cites back, scored by the judge model; an answer citing nothing or claiming nothing is not scored.
_Avoid_: Accuracy (that is the verdicts'), groundedness, hallucination rate

**Kept results**:
The raw results of an eval run that the owner chose to keep, copied to `evals/kept/` and committed; the eval report renders from them.
_Avoid_: Golden results, baseline, snapshot

**Eval report**:
The page of eval measures, one table per measure by model and compliance profile, rendered from saved raw results without calling a model; about the models, not a company's controls.
_Avoid_: Readiness report (that is a company's), benchmark, scorecard
