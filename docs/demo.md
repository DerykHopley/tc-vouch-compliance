# Demo: Vouch in two parts

This is the presenter's script: what to show, in what order, and what to say. It has two parts,
which run alone or one after the other:

- **[Part 1, Audit readiness](#part-1-audit-readiness)** (6 minutes 45 seconds): a company
  preparing its TISAX audit, on the live app.
- **[Part 2, Keep improving](#part-2-keep-improving)** (6 minutes 40 seconds): the same company
  improving its own security with no audit planned, on the local app.

Both together take 13 minutes 25 seconds. The goals and the three-step review shown here are a
demo/prototype process: a real SaaS product might do it differently.

It is not the fake model's bundled demo script, the scripted answers
[setup](setup.md#run-the-app) means when it says the local run plays one.

The words in quotes or bold are the app's own; the domain words are in the
[glossary](../GLOSSARY.md). Each step gives a time budget, what to do, and what to say (the quote
block). The live app runs the real model, so its sentences vary from run to run: point at what
the steps name rather than reading out the model's words.

## The story

Nordlicht Komponenten GmbH, a German supplier to carmakers, has its TISAX audit in three weeks.
Its compliance lead has to show management and the auditor where the company stands: which VDA
ISA controls have evidence, what is missing, and who checked. Vouch reads the company's policies
and records, drafts a verdict for each requirement with the exact quotes behind it, and drafts
what to fix. Vouch drafts; the lead decides.

After the audit, the same lead keeps going: no audit is planned, but the company wants its own
security to improve, round by round, with a plan its team can follow.

## Part 1, Audit readiness

### Before you present

- Sign in to the live app, <https://vouch-828715466396.europe-west1.run.app>, and open it once a
  minute before you start, so Cloud Run is awake.
- Choose **Nordlicht Komponenten GmbH** in the profile switcher in the top bar.
- Start Nordlicht afresh before each run: in the user menu (your initials, top right), choose
  **Start Nordlicht Komponenten GmbH afresh…** and OK the question. Do the same under Alpenwerk
  (switch to it, **Start Alpenwerk Präzision GmbH afresh…**, then switch back), so step 5's 1.6.2
  shows **Assess**, not **Assess again**. Every control is "Not
  assessed" again and the chat opens a new conversation, so each run starts from the same place.
  Nothing is deleted: the report's audit trail still lists every earlier rehearsal's entries. Wait
  until no assessment is running, or Vouch asks you to.
- The Overview must read "Goal: Audit readiness". A fresh start keeps the goal: if Part 2 left it
  on Internal improvement, select **Change goal** and OK the question.
- A fresh start doesn't give back assessments: the earlier rehearsals still count towards
  today's limit.
- Each assessment counts towards your daily limit. One run of the script makes two (5.1.1 and
  1.6.2) and two or three chat messages.

### The steps

Budgets: 0:45 + 1:30 + 1:15 + 1:00 + 1:00 + 1:15 = 6 minutes 45 seconds.

#### 1. The overview (0:45)

1. Open **Overview** (the Dossier). Point at "Where you stand": its figures count only verdicts
   you accepted.
2. Point at "The controls": the 12 controls in this profile, each with its status ("Not
   assessed", "Proposed" or "Accepted").

> This is where Nordlicht stands today. Only verdicts the compliance lead has accepted count here,
> and the figures describe evidence coverage, not an audit result.

#### 2. Assess 5.1.1 (1:30)

1. Open **5.1.1** ("To what extent is the use of cryptographic procedures managed?") and select
   **Assess**.
2. Point at the steps as they appear under "How Vouch reached this": the control loaded, the
   policies searched, the quotes checked, the remediation items drafted.
3. Point at the verdict: **Not evidenced**, because its one must requirement is only partly
   evidenced. The evidence is the encryption standard (`encryption-standard`, approved 2022-03-14,
   reviewed yearly), which is past its review, so the requirements it backs are capped at
   **Partly evidenced**. Its quotes carry the flag "Stale:
   approved 2022-03-14 with a yearly review, due 2023-03-14", and the requirement's reason says
   why it is capped.
4. Point at the exact quotes, each with its document, version and section, and "Quote verified".
5. Point at the drafted "Remediation item" under each requirement: what is missing, an owner and
   a due date.

> Vouch found the encryption standard, and what it says is sound, but it is past its yearly
> review, so Vouch won't call it fully evidenced. Every quote is checked word for word against the
> document. And notice the wording: Vouch talks about evidence, never about an audit result.

#### 3. Review in three steps (1:15)

1. **Confirm findings.** In M1's row, select **Change**. Pick a "Verdict", type a reason under
   "Reason (required)" (**Save** stays disabled until you do), and select **Save**: "1 override,
   recorded with the findings." Then select **Confirm findings**.
2. **Plan fixes.** The foot counts the remediation items decided (on the local run, "0 of 2
   remediation items decided."), and **Accept verdict** waits: "Approve or discard each
   remediation item to accept the verdict." On each remediation item, select **Approve** (or
   **Discard**). An item with an empty "Owner role" needs one before **Approve**.
3. **Accept.** With every item decided, select **Accept verdict**.

> Findings first, then the fixes, then the conclusion: Vouch drafts, and only the compliance lead
> decides. Accepting comes last, so the accepted verdict covers the plan too. My override and its
> reason go into the audit trail, with my name and the time, next to the model's original verdict.

#### 4. Ask a question in the chat (1:00)

1. Open the chat: **Ask Vouch** at the bottom right, or Ctrl K.
2. Ask one question about 5.1.1, for example "What's missing for 5.1.1?" or "Is there evidence of
   key management?". The answer cites the documents it rests on.
3. Type "Accept 5.1.1". Vouch declines and links 5.1.1's working paper.

> The chat answers from the company's own documents and cites them. It can't accept anything:
> asked to accept a verdict, it sends me back to the working paper, where only I can.

#### 5. Switch to Alpenwerk and assess 1.6.2 (1:00)

1. In the profile switcher, choose **Alpenwerk Präzision GmbH · Austria · German**.
2. Open **1.6.2** from the control index (the switch keeps you on 5.1.1's address) and select
   **Assess**.
3. Point at the steps: `apply_overlays` names the country overlay "AT NIS2 (illustrativ)", which
   adds O1, and `overlay_branch` gathers evidence for it. O1 is in the requirements table, marked
   "Country overlay".
4. Point at "Answer language: German": the verdict, the draft answer and the requirements are in
   German; the app's own labels stay English.

> A second client, in Austria. The country overlay adds a requirement, and the assessment takes an
> extra branch to find evidence for it. Alpenwerk works in German, so Vouch answers in German.

#### 6. The report (1:15)

1. Switch back to **Nordlicht Komponenten GmbH** and select **Report** in the top bar.
2. Point at "Readiness": it counts only accepted verdicts.
3. Point at "Controls": 5.1.1 "Accepted", with its verdict, who reviewed it and on which day.
4. Point at "Remediation items" (the approved items), "Not applicable" (what the profile excludes,
   each with its reason), the "Audit trail" (your override, with its reason as the note, the
   findings confirmed, each item approved and the acceptance) and the disclaimer at the foot.
5. Select **Export PDF**: `readiness-report-nordlicht-<date>.pdf` downloads, plain A4 with the
   report's parts in the page's order. **Export Markdown** downloads the same report as Markdown.

> This is what management and the auditor read: only reviewed verdicts, who reviewed each, what is
> missing and who owns it, and every decision in the audit trail. It downloads as a PDF to
> attach, or as Markdown. Alpenwerk's report reads in German.

### If the live app is slow or down: the local run

Start the app on your own machine as [setup](setup.md#run-the-app) says (`scripts/dev.sh`, then
open the URL it prints). It runs on the scripted fake model, offline and free, with no sign-in:
you are "Local user". The fake covers 1.1.1, 1.6.2, 4.1.2 and 5.1.1, so the steps run as on the
live app, with one difference:

- **Step 4:** the fake recognises only some phrasings
  ([features](features.md#3-ask-a-question)): ask "What's missing for 5.1.1?" or "Is there
  evidence of key management?", then type "Accept 5.1.1".

Step 2 shows 5.1.1's stale encryption standard as on the live app, step 3 drafts two remediation
items (M1 and S1), and step 5 runs 1.6.2 under Alpenwerk, overlay branch and all, without a
restart. Export PDF downloads `readiness-report-nordlicht-<date>.pdf`, and Export Markdown the
`.md`.

## Part 2, Keep improving

Nordlicht switches its goal to Internal improvement: no audit is planned, and the lead works
through review rounds with an improvement plan for the team. Part 2 runs on the **local app with
the scripted fake model** only. The profile pass on the live app runs the real model and costs
money (on Sonnet 5.5, about $0.30–0.50 a pass over 12 controls), so the demo shows it locally.

### Before you present

- Start the local app afresh: stop `scripts/dev.sh` (Ctrl-C) and start it again
  ([setup](setup.md#run-the-app)), then open the URL it prints. You are "Local user" on
  **Nordlicht Komponenten GmbH**. On the local app everything lives in the running process, the
  profile pass's daily limit of 3 included, so a restart starts every count and every control
  from nothing.
- A rehearsal that runs more than 3 profile passes in one run of the app is refused until you
  restart it.
- The Overview reads "Goal: Audit readiness" and "Review round: First round, open since the
  start", and every control is "Not assessed".

### The steps

Budgets: 0:30 + 1:00 + 1:00 + 0:30 + 0:50 + 1:20 + 0:30 + 1:00 = 6 minutes 40 seconds.

#### 1. Goal (0:30)

1. On the Overview (the Dossier), select **Change goal** and OK the question ("Change this
   profile's goal to Internal improvement? The change is recorded in the audit trail.").
2. Point at "Goal: Internal improvement" and the round line, "Review round: First round, open
   since the start".

> No audit this year, but the company wants to get better. The goal says what this profile is
> for; the change itself is in the audit trail. The assessments stay the same: what changes is
> the plan and the report.

#### 2. Profile pass (1:00)

1. Select **Run a profile pass** ("Assesses every open control in one run. Accepted controls are
   skipped. On Sonnet 5.5, a pass over 12 controls costs about $0.30–0.50.").
2. Point at the result panel, "Profile pass": "12 assessments waiting for your review.", each
   control with its verdict and "Proposed". The cards under "The controls" read "Proposed" too.

> One model run assessed every open control, with all of Nordlicht's evidence in front of it. The
> same guards as the per-control agent hold: every quote checked word for word, stale evidence
> capped, the output guard on every answer. Vouch uses the pass only while its prompt and
> expected reply fit under a token threshold, and it has its own daily limit; above the threshold, Vouch offers the
> per-control agent instead.

#### 3. Three-step review of 4.1.2 (1:00)

1. Open **4.1.2** from the panel or its card: **Partly evidenced**, "Proposed", from the pass.
2. **Confirm findings:** select **Confirm findings** at the foot.
3. **Plan fixes:** point at M1's remediation item, "Priority: Medium", the default for partly
   evidenced. Select **Approve**.
4. **Accept:** select **Accept verdict**. The verdict reads "Accepted".

> The same three steps as before the audit: findings, fixes, then the conclusion. In this goal
> each approved item gets a priority, here Medium because M1 is partly evidenced; the lead can
> change it.

#### 4. Review round (0:30)

1. Back on the Overview, select **Close round** and OK the question ("Close the review round
   "First round"? Its results are frozen, …"). The round line reads "No review round open."
2. Type "Q4 2026" as the name of the next round and select **Start round**: "Review round: Q4
   2026, open since" today.

> Closing a round freezes its results, so the next one has a set point to compare with. The
> lead chooses when; it is not an arbitrary date.

#### 5. Open question (0:50)

1. Open **4.1.2** again. Under "Open questions", select **Log a question**: type the question
   (for example "Does the exposure record cover the supplier portal?"), who it is for ("IT
   lead") and a due date, then select **Log question**. It reads "For IT lead · Due" the date.
2. Select **Record answer**, type the answer ("Yes, since this week.") and select **Save
   answer**: "Answered on" today.
3. Point at the sentence near the top: "An open question was answered. Assess 4.1.2 again so its
   verdict takes the answer into account." (The card on the Overview says "Question answered:
   assess again".)

> Some facts live only in people's heads. An answered question can be cited as the lead's
> statement, but never above partly evidenced: a statement doesn't stand in for a document or a
> record. The fake model here doesn't cite one; on the real model the judge may.

#### 6. Fix and Closed (1:20)

1. On M1's remediation item, select **Mark done**, type what was done ("The exposure record now
   covers every system.") under "What was done", and select **Mark done**. The item reads
   "Done", and the paper says "Every approved remediation item is done. Assess 4.1.2 again: the
   control is Closed once that assessment is accepted with no gaps."
2. Select **Assess again** (the per-control agent). Its steps appear under "How Vouch reached
   this", and M1 is again **Partly evidenced**: the fake model finds the same thing it found
   before.
3. In M1's row, select **Change**, pick **Evidenced**, type the reason "the exposure record now
   covers every system", and select **Save**. Select **Confirm findings**: the foot reads "Plan
   fixes: skipped, nothing to fix". Select **Accept verdict**.
4. The verdict reads **Evidenced**, "Closed".

> The scripted model can't see the new record, so I override M1 with my reason, and that override
> is in the audit trail. Closed needs an accepted re-assessment with nothing left not or partly
> evidenced; "we did the fix" alone never closes a control.

#### 7. Comparison (0:30)

1. Back on the Overview, point at "Compared with First round": "Accepted: 1 now, 1 when First
   round closed.", the verdict counts with their change, and under "Changed controls" 4.1.2,
   "Partly evidenced → Evidenced". 4.1.2's card reads "Closed".

> This is progress over time: what changed since the round we closed, control by control.

#### 8. Improvement report (1:00)

1. Select **Report** in the top bar: the "Improvement report".
2. Point at "Compared with the last round" (4.1.2 under "Changed controls"), the "Improvement
   plan" by priority (Medium: 4.1.2 M1, "Closed", with the note), "Open questions" (4.1.2's,
   "Answered") and the disclaimer at the foot: "Internal self-assessment, drafted by an AI
   assistant, reviewed by Local user."
3. Select **Export PDF**: `improvement-report-nordlicht-<date>.pdf` downloads.

> The same report page, written for the team instead of an auditor: where we stand against the
> last round, the plan in priority order with each item's status, and the questions still open.
> It is an internal self-assessment, and it says so.
