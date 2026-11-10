# Demo: Vouch in three parts

This is the presenter's script: what to show, in what order, and what to say. It has three parts,
which run alone or one after the other:

- **[Part 1, Audit readiness](#part-1-audit-readiness)** (7 minutes 15 seconds): a company
  preparing its TISAX audit, on the live app.
- **[Part 2, Keep improving](#part-2-keep-improving)** (6 minutes 40 seconds): the same company
  improving its own security with no audit planned, on the local app.
- **[Part 3, Company setup](#part-3-company-setup)** (6 minutes 10 seconds): a developer sets up
  a new company, which opens with every applicable control drafted, on the local app.

All three take 20 minutes 5 seconds. Vouch works for one **current company** at a time, the same
for every user, and a developer makes a company current on the Developer page. The current
company, the goals, the three-step review and company setup shown here are a demo/prototype
process: a real SaaS product might do it differently, for example giving each lead their own
companies ([#163](https://github.com/DerykHopley/tc-vouch-compliance/issues/163)).

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

- Sign in to the live app, <https://vouch-828715466396.europe-west1.run.app>, as a developer
  (your user menu offers **Developer page**), and open it once a minute before you start, so
  Cloud Run is awake. Steps 5 and 6 change the current company on the Developer page.
- Making a company current changes it for every user of the live app, not only for you: anyone
  else using it during the demo sees the switch too.
- The top bar must name **Nordlicht Komponenten GmbH**, the current company. If it names another,
  make Nordlicht current: in the user menu (your initials, top right), choose **Developer page**,
  and under "Companies" select **Make current** in Nordlicht Komponenten GmbH's row.
- Start Alpenwerk afresh, so step 5's 1.6.2 shows **Assess**, not **Assess again**: on the
  Developer page, under "Companies", select **Make current** in Alpenwerk Präzision GmbH's row.
  Then in the user menu choose **Start Alpenwerk Präzision GmbH afresh…** and OK the question.
  Back on the Developer page, select **Make current** in Nordlicht Komponenten GmbH's row.
- Start Nordlicht afresh before each run: in the user menu, choose **Start Nordlicht Komponenten
  GmbH afresh…** and OK the question. Every control is "Not
  assessed" again and the chat opens a new conversation, so each run starts from the same place.
  Nothing is deleted: the report's audit trail still lists every earlier rehearsal's entries. Wait
  until no assessment is running, or Vouch asks you to.
- The Overview must read "Goal: Audit readiness". A fresh start keeps the goal: if Part 2 left it
  on Internal improvement, select **Change goal**, then the **Change goal** that appears with the
  question in its place.
- A fresh start doesn't give back assessments: the earlier rehearsals still count towards
  today's limit.
- Each assessment counts towards your daily limit. One run of the script makes two (5.1.1 and
  1.6.2) and two or three chat messages.

### The steps

Budgets: 0:45 + 1:30 + 1:15 + 1:00 + 1:15 + 1:30 = 7 minutes 15 seconds.

#### 1. The overview (0:45)

1. Open **Overview** (the Dossier). Point at "Where you stand": its figures count only verdicts
   you accepted.
2. Point at "The controls": Nordlicht's 12 controls, each with its status ("Not
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

1. **Confirm findings.** In M1's row, select **Change**. Pick **Not evidenced** as the "Verdict",
   type a reason under "Reason (required)" (**Save** stays disabled until you do), and select
   **Save**: "1 override, recorded with the findings." Then select **Confirm findings**.
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

#### 5. Switch to Alpenwerk and assess 1.6.2 (1:15)

1. Developer page → Alpenwerk → Make current: in the user menu, choose **Developer page**, and
   under "Companies" select **Make current** in Alpenwerk Präzision GmbH's row. The app opens
   afresh on the Developer page, reading "Alpenwerk Präzision GmbH is now the current company.",
   and the top bar names Alpenwerk Präzision GmbH.
2. Select **Overview** in the top bar, open **1.6.2** from its card and select **Assess**.
3. Point at the steps: `apply_overlays` names the country overlay "AT NIS2 (illustrativ)", which
   adds O1, and `overlay_branch` gathers evidence for it. O1 is in the requirements table, marked
   "Country overlay".
4. Point at "Answer language: German": the verdict, the draft answer and the requirements are in
   German; the app's own labels stay English.

> A second client, in Austria. The country overlay adds a requirement, and the assessment takes an
> extra branch to find evidence for it. Alpenwerk works in German, so Vouch answers in German.

#### 6. The report (1:30)

1. Developer page → Nordlicht Komponenten GmbH → Make current: in the user menu, choose
   **Developer page**, and under "Companies" select **Make current** in Nordlicht Komponenten
   GmbH's row. Then select **Report** in the top bar.
2. Point at "Readiness": it counts only accepted verdicts.
3. Point at "Controls": 5.1.1 "Accepted", with its verdict, who reviewed it and on which day.
4. Point at "Remediation items" (the approved items), "Not applicable" (what the company's
   compliance profile excludes, each with its reason), the "Audit trail" (your override, with its
   reason as the note, the findings confirmed, each item approved and the acceptance) and the
   disclaimer at the foot.
5. Select **Export PDF**: `readiness-report-nordlicht-<date>.pdf` downloads, plain A4 with the
   report's parts in the page's order. **Export Markdown** downloads the same report as Markdown.

> This is what management and the auditor read: only reviewed verdicts, who reviewed each, what is
> missing and who owns it, and every decision in the audit trail. It downloads as a PDF to
> attach, or as Markdown. Alpenwerk's report reads in German.

### If the live app is slow or down: the local run

Start the app on your own machine as [setup](setup.md#run-the-app) says (`scripts/dev.sh`, then
open the URL it prints). It runs on the scripted fake model, offline and free, with no sign-in:
you are the local user (the user menu shows **LU**), a developer, so the user menu offers
**Developer page** and Make current works as on the live app. The fake covers 1.1.1, 1.6.2, 4.1.2
and 5.1.1, so the steps run as on the live app, with one difference:

- **Step 4:** the fake recognises only some phrasings
  ([features](features.md#3-ask-a-question)): ask "What's missing for 5.1.1?" or "Is there
  evidence of key management?", then type "Accept 5.1.1".

Step 2 shows 5.1.1's stale encryption standard as on the live app, step 3 (with M1 set to Not
evidenced) drafts two remediation items (M1 and S1), and step 5 runs 1.6.2 under Alpenwerk, overlay
branch and all, without a restart. Export PDF downloads `readiness-report-nordlicht-<date>.pdf`, and
Export Markdown the `.md`.

## Part 2, Keep improving

Nordlicht switches its goal to Internal improvement: no audit is planned, and the lead works
through review rounds with an improvement plan for the team. Part 2 runs on the **local app with
the scripted fake model** only. The profile pass on the live app runs the real model and costs
money (on Sonnet 5.5, about $0.30–0.50 a pass over 12 controls), so the demo shows it locally.

### Before you present

- Start the local app afresh: stop `scripts/dev.sh` (Ctrl-C) and start it again
  ([setup](setup.md#run-the-app)), then open the URL it prints. You are the local user (the user
  menu shows **LU**), and **Nordlicht Komponenten GmbH** is the current company, named in the top
  bar. On the local app everything lives in the running process, the current company and the
  profile pass's daily limit of 3 included, so a restart starts every count and every control
  from nothing, on Nordlicht.
- A rehearsal that runs more than 3 profile passes in one run of the app is refused until you
  restart it.
- The Overview reads "Goal: Audit readiness" and "Review round: First round, open since the
  start", and every control is "Not assessed".

### The steps

Budgets: 0:30 + 1:00 + 1:00 + 0:30 + 0:50 + 1:20 + 0:30 + 1:00 = 6 minutes 40 seconds.

#### 1. Goal (0:30)

1. On the Overview (the Dossier), select **Change goal**. The question takes the button's place,
   "Change this profile's goal to Internal improvement? The change is recorded in the audit
   trail.", with **Change goal** and **Cancel** under it: select **Change goal**.
2. Point at "Goal: Internal improvement" and the round line, "Review round: First round, open
   since the start".

> No audit this year, but the company wants to get better. The goal says what the company uses
> Vouch for; the change itself is in the audit trail. The assessments stay the same: what changes
> is the plan and the report.

#### 2. Profile pass (1:00)

1. Select **Run a profile pass** ("Assesses every open control in one run. Accepted controls and
   reviews in progress are skipped. On Sonnet 5.5, a pass over 12 controls costs about
   $0.30–0.50.").
2. Point at the result panel, "Profile pass": "12 assessments waiting for your review.", each
   control with its verdict and "Proposed". The cards under "The controls" read "Proposed" too.

> One model run assessed every open control, with all of Nordlicht's evidence in front of it. The
> same guards as the per-control agent hold: every quote checked word for word, stale evidence
> capped, the output guard on every answer. Vouch uses the pass only while its prompt and expected
> reply fit under a token threshold, and it has its own daily limit; above the threshold, Vouch
> offers the per-control agent instead.

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

1. Back on the Overview, select **Close round**. The question takes the button's place, "Close
   the review round "First round"? Its results are frozen, …", with **Close round** and
   **Cancel** under it: select **Close round**. The round line reads "No review round open."
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

## Part 3, Company setup

A developer sets up a new company in Vouch: its name and country, its language, its protection
level, its Goal, the controls that don't apply, and its evidence, picked from the sample library.
Vouch then opens the new company, now the current company, with every applicable control drafted
from the documents chosen, and nothing else. Part 3 runs on the **local app with the scripted fake
model** only, as Part 2 does: company setup is for developers only, and on the live app the new
company's first profile pass runs the real model and costs money. On the local app, "Local user"
is a developer.

The company and its answers below are invented for the demo; any name works.

### Before you present

- Start the local app afresh: stop `scripts/dev.sh` (Ctrl-C) and start it again
  ([setup](setup.md#run-the-app)), then open the URL it prints. You are the local user (the user
  menu shows **LU**), a developer: the user menu offers **Developer page** and **Set up a
  company**. The top bar names **Nordlicht Komponenten GmbH**, the current company.
- Creating a company runs one profile pass, which counts towards the local app's daily limit of
  3 passes (Part 2 runs one too). After three rehearsals in one run of the app, restart it.

### The steps

Budgets: 0:20 + 1:00 + 0:20 + 0:40 + 1:00 + 0:30 + 1:00 + 0:50 + 0:30 = 6 minutes 10 seconds.

#### 1. The Developer page (0:20)

1. In the user menu, choose **Developer page**. Under "Companies", point at the list: Alpenwerk
   Präzision GmbH with **Make current**, and Nordlicht Komponenten GmbH marked "Current". Select
   **Set up a company** above the list.
2. Point at the seven steps along the top: Company, Language, Protection level, Goal, Not
   applicable, Evidence, Finish. The current one is marked, and "Step 1 of 7" heads the form.

> Setting up a company is a developer's job, so it lives on the Developer page, beside the
> companies Vouch already has. Seven steps, and I can go back to any of them without losing an
> answer.

#### 2. Company, Language and Protection level (1:00)

1. **Company:** type "Hafenblick Logistik GmbH" under "Company name", choose **Germany** under
   "Country", and select **Next**.
2. **Language:** leave **English** chosen under "Answer language" and point at the note: "Vouch's
   answers, the chat and the readiness report use this language; the interface stays English."
   Select **Next**.
3. **Protection level:** point at what each level brings into scope. **Normal**: "Must and should
   requirements: 85 in 12 controls." **High**: "Adds 14 high-protection requirements, in 7
   controls (1.6.2, 4.1.2, 4.2.1, 5.1.1, 5.1.2, 5.2.4, 5.2.9): 99 in all." Choose **High** and
   select **Next**.

> A logistics company in Germany, answering in English. The protection level decides which VDA
> ISA requirements are in scope: High adds 14 requirements on top of the must and should ones.

#### 3. Goal (0:20)

1. Point at **Internal improvement**, chosen already ("Improving the company's own security, with
   no audit planned."), and **Audit readiness** ("Preparing for an external audit."), and at "You
   can change the Goal later on the overview."
2. Keep Internal improvement and select **Next**.

> The Goal from Part 2 is chosen here, at the start. It isn't fixed: the lead can change it on
> the overview.

#### 4. Not applicable (0:40)

1. Tick **5.2.9** ("To what extent is the backup and recovery of data and IT services
   ensured?"). Select **Next** with its reason blank: "Say why 5.2.9 doesn't apply."
2. Under "Why 5.2.9 doesn't apply", type "Backups are run by the parent company's IT, under its
   own assessment." and select **Next**.

> A control can be left out only with a reason, and the reason goes in the record. At least one
> control must stay applicable.

#### 5. Evidence (1:00)

1. Point at the two tables, "Policies" and "Records": the sample library, each document with its
   kind (documented or implemented), version, approval date, review cycle and status. The backup
   policy reads "Stale: approved 2024-02-12 with a yearly review, due 2025-02-12".
2. Tick every document except the **Logging standard** (a policy) and **Weekly log checks, third
   quarter of 2026** (a record).
3. Point at the line under the tables: "Ready to cite: 15 documents (10 documented, 5
   implemented, 1 stale)." Select **Next**.

> The sample library ships with Vouch: invented policies and records with a few deliberate gaps.
> There's no upload in this prototype. I leave out the two logging documents, so we can see that
> Vouch cites only what I chose.

#### 6. Finish (0:30)

1. Point at the summary: Company, Country, Answer language, Protection level, Goal, "Not
   applicable" (5.2.9 with its reason), "Ready to cite", and the chosen documents.
2. Point at the line above the buttons: "Creating the company adds it to the Companies list and
   makes it the current company, for every user. Then Vouch sets its Goal and runs a profile pass:
   every applicable control drafted, waiting for your review."
3. Select **Create company** ("Creating…" while it runs).

> One last look at every answer, then one press makes the company.

#### 7. The new company (1:00)

1. The app opens on the overview of **Hafenblick Logistik GmbH**, now the current company and
   named in the top bar: "Goal: Internal improvement", and "Review round: First round, open since
   the start". Within a few seconds the profile pass has run: "Profile pass", "11 assessments
   waiting for your review.", each control with its verdict and "Proposed".
2. Point at the mix: 2.1.3, 5.1.2 and 5.2.5 **Evidenced**, most others **Partly evidenced**, and
   1.6.1 and 5.2.4 **Not evidenced**. 5.2.4 ("To what extent are event logs recorded and
   analysed?") has nothing: its logging documents were left out.
3. Point at 5.2.9's card under "The controls": it reads **Not applicable**, with the reason you
   gave in step 4. The pass left it out, it has no draft, and readiness counts 11 controls.

> The new company starts where Nordlicht did in Part 2: one pass drafted every applicable
> control, from the chosen documents only, with the same guards. A draft never cites a document
> that wasn't chosen, so leaving out the logging documents shows straight away as a gap. 1.6.1 is
> one of the library's planted gaps: there's no procedure for reporting a security event.

#### 8. The Company page (0:50)

1. Select **Company** in the top bar: the Company page names Hafenblick Logistik GmbH, with its
   country, answer language, base framework, protection level "High" and overlays "None", and
   "This company was made in Company setup: its details can't be changed here, apart from the
   Goal."
2. Point at "Not applicable": "5.2.9, the whole control", with the reason given in setup.
3. Under "Documents", the "Policies" and "Records" tables: the 15 chosen documents only, each with
   its kind, version, approval date, review cycle, status and "Cited by".
4. Point at the backup policy, "Stale", cited by 1.1.1, and at "Sign-in and account settings,
   checked October 2026", cited by 4.1.2, 4.1.3 and 4.2.1.

> Every company has this page, Nordlicht and Alpenwerk too: what setup decided, and the evidence
> documents, read-only, with the controls that cite each. Upload would later live here.

#### 9. Remove company (0:30)

1. In the user menu, choose **Developer page**. Under "Companies", point at the company's row:
   "Hafenblick Logistik GmbH", its id `hafenblick-logistik-gmbh`, "Made" today, "Current", and
   **Remove**.
2. Select **Remove** and OK the question ("Remove Hafenblick Logistik GmbH? It leaves the
   Companies list for everyone and can't be opened or made current again. Its audit trail is
   kept."). The row is gone, Nordlicht Komponenten GmbH is marked "Current", and the section reads
   "Removed Hafenblick Logistik GmbH. Nordlicht Komponenten GmbH is now the current company." The
   top bar names Nordlicht Komponenten GmbH.

> Removing a company hides it everywhere, but nothing is deleted: its audit trail stays, and its
> id is never used again. It was the current company, so every user is back on Nordlicht.

### Running Part 3 again

- **Remove company** (step 9) clears the way: the next run can use the same name, and gets a new
  id (`hafenblick-logistik-gmbh-2`), since a removed company's id is never used again.
- Or restart the local app: without a database, the companies made in setup live in the running
  process, so a restart drops them, and the profile pass count with them.
- **Fresh start** in the user menu (**Start Nordlicht Komponenten GmbH afresh…**) still serves
  Parts 1 and 2: it doesn't touch the companies made in setup.
