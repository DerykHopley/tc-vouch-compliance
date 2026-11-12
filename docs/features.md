# Features

What Vouch does, step by step, from a fresh start on your own machine: nine walk-throughs, then
the Company page, the Developer page and two examples. The words in quotes are the app's own; the
domain words are in the [glossary](../GLOSSARY.md).

**Before you start:** run `scripts/dev.sh` (see [setup](setup.md#run-the-app)) and open the URL it
prints. It runs on the scripted fake model, offline and free, with no sign-in: you are "Local
user", a developer, and Nordlicht Komponenten GmbH is the current company, named in the top bar.
The fake has scripted runs for four controls, **1.1.1**, **1.6.2**, **4.1.2** and **5.1.1**;
these walk-throughs use 4.1.2. Work done here lasts until you stop the script. On the live app any control runs on the real model.

Do the walk-throughs in order: each starts where the one before stopped.

## The top bar and the user menu

- The top bar names the current company, with its country and answer language ("DE · EN" for
  Nordlicht), and under "Vouch" what the Goal makes it: "Audit preparation" or "Security
  improvement".
- Its tabs: **Overview** (the Dossier, `/`), **Report** (`/report`) and **Company** (the Company
  page, `/company`).
- The user menu, under your initials (**LU**) at the top right: **Dark theme** (or **Light
  theme**) and "Start Nordlicht Komponenten GmbH afresh…" (a fresh start) for everyone, and for
  developers only **Developer page** (`/developer`) and **Set up a company**
  (`/developer/setup`). A compliance lead's menu has neither.
- **Ask Vouch**, at the bottom right of every page, opens the chat.

## 1. Assess a control

1. On the Overview (the Dossier, `/`), "Where you stand" reads "0 accepted of 12", and every
   control under "The controls" is "Not assessed".
2. Open **4.1.2** ("To what extent is the user access to IT services and IT systems secured?").
   Its working paper opens at `/controls/4.1.2`:
   "Not assessed yet. 5 requirements apply in this profile.",
   with the requirements M1, M2, S1, S2 and S3 listed.
3. Select **Assess**. The steps appear one by one under "How Vouch reached this":
   - `load_control`: the control loaded, and that 5 requirements apply under Nordlicht's
     compliance profile;
   - `apply_overlays`: "No country overlay adds a requirement to this control, so no overlay
     branch is taken.";
   - `gather` and `tools`: the policies searched, and the passages found;
   - `check_answer`: "Checked the answer: it claims no audit result.";
   - `judge`: "Judged 4 of 5 requirements evidenced.";
   - `check_quotes`: each quote checked against its document;
   - `remediate`: "Drafted 1 remediation item (1 partly evidenced).";
   - `review`: "Waiting for the compliance lead's review."
4. At the top, the control's verdict is **Partly evidenced**, marked "Proposed": "Partly
   evidenced: 1 of 2 must requirements is fully evidenced." Below the steps is the "Draft answer"
   for the auditor, in two sections: "What the evidence shows", a short paragraph for each
   document with its quotes, and "What is missing", one line for each gap ("No record shows that
   each system's exposure was assessed to choose its sign-in method."). Under it, the run's usage
   (tokens and an estimated cost; nothing is charged on the fake).
5. The requirements table gives each requirement its verdict, its reason and its exact quotes,
   each with the document, version and section, and "Quote verified":
   - **M1** is **Partly evidenced**: the standard says sign-in methods follow each system's
     exposure, but no record shows that assessment was done. Its remediation item, a draft, says
     what is missing, with an owner and a due date.
   - **M2**, **S1**, **S2** and **S3** are **Evidenced**.
6. At the foot, the proposed verdict waits for the first of the review's three steps: the steps
   "Confirm findings", "Plan fixes" and "Accept", a note field ("Your note (kept in the audit
   trail)"), **Confirm findings** and **Reject verdict**.

## 2. Review it with an override

1. On 4.1.2's working paper, in **S2**'s row, select **Change**.
2. The override opens: "Verdict" is set to **Partly evidenced**, and "Reason (required)" is
   empty. **Save** stays disabled until you give a reason. Type one, for example "The supplier
   portal does not check passwords against breach lists yet.", and select **Save**.
3. S2 now reads **Partly evidenced**, "Overridden from Evidenced", with your reason, and
   **Undo** next to **Change**. Above the review buttons: "1 override, recorded with the
   findings." The control's verdict stays Partly evidenced (M1 still is).
4. **Confirm findings:** select **Confirm findings**. The review moves to Plan fixes: the foot
   reads "0 of 2 remediation items decided.", and **Accept verdict** stays disabled: "Approve or
   discard each remediation item to accept the verdict."
5. **Plan fixes:** M1's remediation item now offers its "Owner role" and "Due date", with
   **Approve** and **Discard**. Select **Approve**: it reads "Approved". The override also drafted
   an item for S2, which reads "not drafted" with an empty "Owner role": "Name the role that owns
   the fix to approve it." Select **Discard** (it reads "Discarded"), or type an owner role and
   select **Approve**. The foot reads "2 of 2 remediation items decided."
6. **Accept:** select **Accept verdict**. The question takes the button's place: "Accept this
   control's verdict? Your decision is recorded in the audit trail, and the verdict counts towards
   readiness." Select **Accept verdict** under it. The verdict is marked "Accepted", and the foot
   of the page reads "Accepted by Local user on" the date and time.
7. Back on the Overview: 4.1.2's card is "Accepted", Partly evidenced, and "Where you stand"
   reads "1 accepted of 12", with 1 partly evidenced and 1 remediation item (M1's; with S2's
   approved too, 2).

## 3. Ask a question

1. Open the chat: **Ask Vouch** at the bottom right, or Ctrl K (Cmd K on a Mac). It greets you
   with your progress and suggests the next step.
2. On the fake, the chat recognises these phrasings rather than any question:
   - **"Where do I stand?"**: your progress, for example "Of the 12 controls, 1 is accepted, and
     11 are not assessed.", and the next step.
   - **"What's missing for 4.1.2?"**: 4.1.2's newest assessment and its review, each
     requirement's verdict and reason, and the quotes they rest on, cited.
   - **"Is there evidence of hardware security keys?"** (any words after "evidence of"): the
     passages found in the policies and the records, cited.
   - **"Which controls have documented evidence only?"**: every control's newest assessment at
     once, then "Documented evidence only: 4.1.2.", the stale evidence, the musts not evidenced
     and the controls not assessed yet.
   - **"What improved since Q2 2026?"** (any round's name or number after "since"): the round
     comparison, in walk-through 6. With no round of that name: "There is no review round “Q2
     2026”. The rounds are 1 “First round”."
   - **"Accept 4.1.2"**: Vouch declines: signing off is yours, and Vouch never writes or changes
     a verdict for you. It links 4.1.2's working paper, where you accept or reject.
   - **"Assess 4.1.2"**: starts a new assessment of 4.1.2 in the chat, with its steps. That run
     waits for your review again, so 4.1.2 no longer counts as accepted until you review it:
     leave it until after walk-through 4.
3. Anything else gets the fake's one reply: "Thank you for your question. Please ask what is
   missing for a control, or whether there is evidence of something in your documents." On the
   live app the chat answers on the real model.

## 4. Read the report

1. Select **Report** in the top bar (`/report`). The readiness report opens.
2. Its head: the company, country, base framework (VDA ISA 2027.1), protection level, country
   overlays, language and the date it was drawn up.
3. **Readiness** counts only accepted verdicts: "1 of 12 controls accepted", then 1 Partly
   evidenced, 0 Evidenced, 0 Not evidenced, 0 Not applicable, 11 Open, and 1 remediation item
   (2 if you approved S2's).
4. **Controls**: every control with its status. 4.1.2 is "Accepted", Partly evidenced, reviewed
   by Local user on its date, with the quotes behind it. The others are "Open", with no verdict.
5. **Remediation items**: the approved items of accepted controls, here M1's (and S2's, if you
   approved it), with its owner and due date.
6. **Not applicable**: what the company's compliance profile excludes, each with its reason; for
   Nordlicht, 5.1.2 S3 (no remote access to the company network).
7. **Audit trail**: one row per action, with when, who, the control, the requirement, the
   verdict before and after, the note, the evidence, the model and the prompt version. Newest
   first: the acceptance of 4.1.2, the decision on S2's remediation item ("Discarded" or
   "Approved"), the approval of M1's, "Findings confirmed", and the override of S2, with your
   reason as its note.
8. At the foot, the disclaimer: "Drafted by an AI assistant, reviewed by Local user; not an
   audit result."
9. Select **Export PDF**: a PDF named `readiness-report-nordlicht-<date>.pdf` downloads, plain
   black on white A4, with the report's parts in the page's order, the whole audit trail on a
   landscape page, and each table's header repeated on every page. **Export Markdown** downloads
   the same report as `readiness-report-nordlicht-<date>.md`.
10. Print with the browser's own print (Ctrl P, Cmd P on a Mac) for a paper copy: only the report
    prints, without the top bar, the tabs, the chat or the page's buttons.

## 5. Change the Goal

Each compliance profile has a Goal: **Audit readiness** (preparing for an external audit, where
every company starts unless setup chose otherwise) or **Internal improvement** (improving the
company's own security, with no audit planned). The Goal changes the report and the plan, not the
assessments.

1. On the Overview, the hero reads "Goal: Audit readiness", with **Change goal** beside it. The
   same line is on the Company page, under "Goal".
2. Select **Change goal**. The question takes the button's place: "Change this profile's goal to
   Internal improvement? The change is recorded in the audit trail." Select **Change goal** under
   it.
3. The hero reads "Goal: Internal improvement", and "Improving security. One piece of evidence at
   a time."; the top bar says "Security improvement".
4. Select **Report**: it is now the "Improvement report", written for the team, from the same
   page and the same exports (`improvement-report-nordlicht-<date>.pdf` and `.md`):
   - "Compared with the last round": "No review round has been closed yet, so there is nothing to
     compare with." (walk-through 6 closes one);
   - "Improvement plan": the approved remediation items by priority, here M1's under "Medium",
     with its owner, due date and status, "Open";
   - "Open questions": "No open question has been logged.";
   - "Audit trail": newest first, "Goal changed from Audit readiness to Internal improvement";
   - at the foot: "Internal self-assessment, drafted by an AI assistant, reviewed by Local user."

## 6. Review rounds, the improvement plan and open questions

A review round is a period the lead names and closes; closing it freezes its results, so later
work can be compared with it. Every company starts in "First round".

1. **Close the round.** On the Overview, the round line reads "Review round: First round, open
   since the start". Select **Close round**: "Close the review round "First round"? Its results
   are frozen, and the overview compares later work with them. The close is recorded in the audit
   trail." Select **Close round** under it. The line reads "No review round open."
2. **Start the next.** Type "Q4 2026" in the field "Name of the next round" and select **Start
   round** ("Name the round first." if the field is empty). The line reads "Review round: Q4
   2026, open since" today. Under "Where you stand", "Compared with First round": "Accepted: 1
   now, 1 when First round closed.", and "Nothing has changed since First round closed."
3. **The improvement plan.** Open **4.1.2**. M1's remediation item reads "Approved", "Priority:
   Medium" (the default from its verdict). Under "Priority", choose **High**: it reads "Priority:
   High". Select **Mark done**, type what was done under "What was done" (for example "Recorded
   the exposure check for every system in the access register."), and select **Mark done**. It
   reads "Done", with "Done on" today and your note. The paper says "Every approved remediation
   item is done. Assess 4.1.2 again: the control is Closed once that assessment is accepted with
   no gaps."
4. **An open question.** Under "Open questions", select **Log a question**. Type the "Question"
   ("Does the supplier portal check passwords against breach lists?"), "For whom" ("IT operations
   lead") and a "Due date", and select **Log question**: it reads "For IT operations lead · Due"
   the date. Select **Record answer**, type the "Answer" ("Yes: since October the portal checks
   every new password against the breach list.") and select **Save answer**: "Answered on" today,
   with the answer. The paper says "An open question was answered. Assess 4.1.2 again so its
   verdict takes the answer into account." On the Overview, 4.1.2's card reads "Fixes done:
   assess again" and "Question answered: assess again".
5. **Assess again.** On 4.1.2, select **Assess again**. The judge reads the answered question as
   the lead's statement, which it may cite, but never above partly evidenced. The fake finds what
   it found before: M1 is **Partly evidenced** again.
6. **Review it.** In M1's row, select **Change**, choose **Evidenced**, type the reason ("The
   access register now records the exposure check for every system.") and select **Save**. Select
   **Confirm findings**: the foot reads "Plan fixes: skipped, nothing to fix". Select **Accept
   verdict**, and **Accept verdict** again under the question. The verdict reads **Evidenced**,
   "Closed": every approved item was done, and the accepted re-assessment leaves nothing not or
   partly evidenced. Done marks alone never close a control.
7. **The round comparison, on the Overview.** "Compared with First round": "Evidenced 1 (+1)",
   "Partly evidenced 0 (-1)", and under "Changed controls" 4.1.2, "Partly evidenced → Evidenced".
   4.1.2's card reads "Closed".
8. **On the Company page.** Select **Company** in the top bar. Under "Review rounds", the
   "Rounds" list: "Q4 2026", "Open since" today, and "First round", "Opened at the start, closed"
   today, marked "Compared by default". Select **First round**: its comparison opens at
   `/company/rounds/1`, "Compared with First round", with "Better 1", "Worse 0", "New 0" and
   "Unchanged 11", "Accepted: 1 when First round closed → 1 now", the verdict counts then and now,
   "Evidence changes" ("No evidence has changed since First round closed."), and the controls by
   topic. Under "Access", 4.1.2 reads "Partly evidenced → Evidenced", "Better", with "Done since:
   M1" and "Answered since:" its question. **← Rounds** goes back.
9. **In the chat.** Open **Ask Vouch** and ask "What improved since First round?". The chat's
   `compare_rounds` tool reads the same comparison: "Now compared with review round 1, “First
   round”, closed on" the date, "Better: 1, worse: 0, new: 0, unchanged: 11.", readiness then and
   now, "4.1.2: better, partly evidenced → evidenced.", "Remediation items done since: M1 on
   4.1.2." and "Questions answered since: one on 4.1.2.", with a link to 4.1.2's working paper.
   With no round named, the tool compares with the last round closed; the fake needs the
   round's name or number.
10. **In the improvement report.** Select **Report**. "Compared with the last round": "Compared
    with First round, closed on" the date, with 4.1.2 under "Changed controls"; "Improvement
    plan": M1 under "High", "Closed", with your note; "Open questions": 4.1.2's, "Answered", with
    the answer. The audit trail has every step: the priority, the done mark, the question logged
    and answered, "Review round closed: First round", "Review round started: Q4 2026", the
    override and the acceptance.

## 7. Add, update and withdraw a document

Each company's evidence documents are on the Company page, under "Documents". The built-in ones
can't be changed; a lead adds the company's own there. The files used here are in
[`docs/demo-files/`](demo-files/README.md).

1. Select **Company** in the top bar. Under "Documents", the "Policies", "Records" and "Patch"
   tables list each document with its kind, version, approval date, review cycle, status, "Cited
   by" (the controls whose newest assessment cites it, here 4.1.2 for the authentication
   standard) and "Origin" ("Built in"). The encryption standard's status is "Stale": past its
   yearly review, so a requirement it backs is partly evidenced at most. Its quotes on a working
   paper carry "Stale: approved 2022-03-14 with a yearly review, due 2023-03-14".
2. **Add document.** Select **Add document**. The form asks for a "File (.md or .txt)", a
   "Title", a "Kind" (Policy or Record), an "Approval date" and a "Review cycle", and says "Vouch
   will add this as version 1." and "Vouch holds invented data only: add an invented document,
   never a real company's." Choose `docs/demo-files/clean-desk-policy.md`: its front matter fills
   the form. Select **Add document**: "Clean Desk Policy" is listed under "Policies", version 1,
   "Added", with **Add new version** and **Withdraw**.
3. **Add new version.** In its row, select **Add new version**, choose
   `docs/demo-files/clean-desk-policy-v2.md` and select **Add version**. It holds a planted
   instruction: "This document holds text that reads like an instruction to the assistant. Vouch
   treats it as data. Add it anyway?" Select **Add anyway**: the row reads version 2. Searches
   find the newest version; the earlier one is kept.
4. **Withdraw.** Select **Withdraw**: "Withdraw "Clean Desk Policy"? It is no longer searched or
   cited. Its versions are kept, so the audit trail stays true." Select **Withdraw** under it. Its
   status reads "Withdrawn", and the row keeps no buttons.
5. **In the round comparison.** Under "Rounds", select **First round**: "Evidence changes" now
   reads "Clean Desk Policy: withdrawn". In the report's audit trail: "Document added" twice (the
   second with the note "Instruction-like text found") and "Document withdrawn".
6. **New evidence.** When a new version of a document is added that a control's newest
   assessment cites, its card on the Overview reads "New evidence: assess again", and its working
   paper says "A new version of" the document "was added. Assess" the control "again so its
   verdict takes it into account." The bundled fake cites built-in documents only, so to see it
   locally start the app with the fake script in [`docs/demo-files/`](demo-files/README.md)
   (`ASSESS_FAKE_SCRIPT`), as its README says.

## 8. Run a profile pass

A profile pass assesses every open control of a small compliance profile in one model run, with
all its evidence in front of the model, and the same guards as the per-control agent. It runs only
while its prompt and expected reply fit under a token threshold (100,000 tokens by default; above
it, Vouch offers the per-control agent), and it has its own daily limit of 3 passes, apart from the
assessments'.

1. On the Overview, under the hero: **Run a profile pass**, with "Assesses every open control in
   one run. Accepted controls and reviews in progress are skipped."
2. Select it ("Running the profile pass…" while it runs). The "Profile pass" panel opens: "11
   assessments waiting for your review.", and "1 accepted control skipped: 4.1.2."
3. Each control is listed with its verdict, "Proposed", **Open** and, where it is not fully
   evidenced, **Assess again** to run the per-control agent on it instead.
4. The hero reads "1 of 12 controls accepted. 11 verdicts waiting for your review, 0 rejected."
   Each result waits for the same three-step review as an assessment: open one and review it as
   in walk-through 2.

## 9. Set up a company

Company setup is for developers only: on the live app the new company's first profile pass runs
the real model. The company here is invented; any name works.

1. In the user menu, choose **Set up a company** (or **Set up a company** above the list on the
   Developer page). Company setup opens at `/developer/setup`, "Developers only", with its seven
   steps along the top, "Step 1 of 7" heading the form.
2. **Company:** type "Hafenblick Logistik GmbH" under "Company name", choose **Germany** under
   "Country", and select **Next**.
3. **Language:** "Answer language", English or German: "Vouch's answers, the chat and the
   readiness report use this language; the interface stays English." Keep **English**; **Next**.
4. **Protection level:** **Normal** ("Must and should requirements: 85 in 12 controls."),
   **High** or **Very high**, each saying what it adds. Choose **High**; **Next**.
5. **Goal:** **Internal improvement** ("Improving the company's own security, with no audit
   planned.") or **Audit readiness** ("Preparing for an external audit."): "You can change the
   Goal later on the overview." Keep Internal improvement; **Next**.
6. **Not applicable:** tick **5.2.9** and type why under "Why 5.2.9 doesn't apply" (for example
   "Backups are run by the parent company's IT, under its own assessment."); **Next**.
7. **Evidence:** the sample library, in "Policies" and "Records", each document with its kind,
   version, approval date, review cycle and status; the backup policy is "Stale". Select **Select
   all** for the policies and for the records. The line under the tables counts what is chosen:
   "Ready to cite: 17 documents (11 documented, 6 implemented, 1 stale)."; **Next**.
8. **Finish:** a summary of every answer and the chosen documents, and "Creating the company adds
   it to the Companies list and makes it the current company, for every user. Then Vouch sets its
   Goal and runs a profile pass: every applicable control drafted, waiting for your review."
   Select **Create company**.
9. The app opens on the Overview of **Hafenblick Logistik GmbH**, now the current company and
   named in the top bar: "Goal: Internal improvement", "Review round: First round, open since the
   start", and the profile pass running. Once it is done: "11 assessments waiting for your
   review.", and 5.2.9's card reads "Not applicable" with your reason.
10. Remove it again, as [the Developer page](#the-developer-page) says: the current company falls
    back to Nordlicht.

## The Company page

The **Company** tab (`/company`) shows the current company:

- Its name, and its "Country", "Answer language", "Base framework" (VDA ISA 2027.1), "Protection
  level" and "Overlays". Nordlicht and Alpenwerk read "This company is defined in files: its
  details can't be changed here, apart from the Goal."; a company made in setup reads "This
  company was made in Company setup: its details can't be changed here, apart from the Goal."
- "Goal", with **Change goal**, as on the Overview ([walk-through 5](#5-change-the-goal)).
- "Review rounds": the round line with **Close round** or **Start round**, and the "Rounds" list,
  each closed round opening its comparison ([walk-through 6](#6-review-rounds-the-improvement-plan-and-open-questions)).
- "Scope": what the protection level brings in ("Normal protection brings in 85 requirements of
  VDA ISA 2027.1, in 12 controls."), by obligation, and the country overlays.
- "Not applicable": each requirement or control the profile excludes, with its reason (Nordlicht:
  5.1.2 S3; the setup-made company: "5.2.9, the whole control").
- "Documents": the evidence documents, with **Add document**, **Add new version** and
  **Withdraw** for the added ones ([walk-through 7](#7-add-update-and-withdraw-a-document)).
  `/documents` leads here.

## The Developer page

The **Developer page** (`/developer`, in the user menu, developers only) reads "Developers only":

- **Companies:** **Set up a company**, then each company with its id. The current one is marked
  "Current"; the others offer **Make current**, which opens the app afresh there under that
  company for every user ("Alpenwerk Präzision GmbH is now the current company."). A company made
  in setup shows "Made" and its date, and **Remove**: "Remove Hafenblick Logistik GmbH? It leaves
  the Companies list for everyone and can't be opened or made current again. Its audit trail is
  kept." When it was current, the current company falls back to Nordlicht: "Removed Hafenblick
  Logistik GmbH. Nordlicht Komponenten GmbH is now the current company." Its id is never used
  again.
- **Reset demo**, in each company's row: "Reset the demo of Nordlicht Komponenten GmbH? Every
  user's progress and history under it is deleted: assessments, rounds, the audit trail, open
  questions, remediation and added documents. Then the demo round is played: Q2 2026 closed, Q3
  2026 open. This can't be undone." On Nordlicht it then reads "Reset Nordlicht Komponenten GmbH
  to its demo state: Q2 2026 closed, Q3 2026 open.", with every control accepted and the
  Overview "Compared with Q2 2026"; any other company starts empty. It is played on the fake
  model and counts against no limit. Not the user menu's fresh start, which deletes nothing.
- With no OpenRouter key: "No OpenRouter key is set: assessments run on the fake model."
- "Model": the model id assessments run on, **Change model**, and each model with its price ("$2.00
  in · $10.00 out per million tokens"), the one in use marked "Current".
- "Prompt version", and "Daily limit": "30 assessments per user per day (UTC)", with **Change
  limit**.
- "Prompts": every prompt Vouch sends, for the current company and a control you choose (Gather
  evidence, Country overlay, Judge, Remediation, Profile pass, Chat), parts filled in by each run
  marked "[from the run: …]".
- "Usage": the assessments' tokens and cost on OpenRouter and on the fake model ("estimated, not
  spent"), and "Recent runs": "The last 10 assessments and profile passes, for every company."
- On the local app: "No database is set, so the model choice, the current company, the daily
  limit and counts, and the usage are kept until the server restarts."

## Examples

### The German company

1. Make Alpenwerk current: in the user menu (**LU**), choose **Developer page**, and under
   "Companies" select **Make current** in Alpenwerk Präzision GmbH's row. The app opens afresh on
   the Developer page, reading "Alpenwerk Präzision GmbH is now the current company.", and the
   top bar names Alpenwerk Präzision GmbH. Select **Overview** in the top bar: the controls'
   questions are now the German ISA edition's; the app's own labels stay English.
2. Open **4.1.2** and select **Assess**. The working paper says "Answer language: German", and
   the verdict, the draft answer and the requirements are in German: "Teilweise nachgewiesen",
   with 6 requirements under Alpenwerk's high protection level.
3. Review it in three steps: select **Confirm findings**; at Plan fixes, select **Approve** on
   its one remediation item (M1's); then select **Accept verdict**, twice. Then select **Report**:
   the report is in German ("Bereitschaftsbericht",
   "1 von 12 Kontrollen akzeptiert"), with Alpenwerk's country overlay "AT NIS2 (illustrativ)"
   in its head and the disclaimer "Erstellt von einem KI-Assistenten, geprüft von Local user;
   kein Auditergebnis."
4. Make Nordlicht current again: on the Developer page (user menu), under "Companies", select
   **Make current** in Nordlicht Komponenten GmbH's row. The top bar names Nordlicht Komponenten
   GmbH.

### The overlay branch

A country overlay adds a requirement, and the assessment graph takes its overlay branch for it.
Alpenwerk's overlay adds O1 to 1.6.2.

1. Make Alpenwerk current as in the German example: on the Developer page (user menu), under
   "Companies", select **Make current** in Alpenwerk Präzision GmbH's row. Select **Overview** in
   the top bar, open **1.6.2** and select **Assess**.
2. Under "How Vouch reached this":
   - `apply_overlays`: "Applied the country overlays: AT NIS2 (illustrativ) adds O1.";
   - `overlay_branch`: "Overlay branch: gathering evidence for O1 (AT NIS2 (illustrativ))."
3. O1 is in the requirements table, marked "Country overlay" and "AT NIS2 (illustrativ)", with
   its own verdict and reason: "Nicht nachgewiesen", since nothing provides for an early warning
   to the national CSIRT.
4. Make Nordlicht current again: on the Developer page, select **Make current** in Nordlicht
   Komponenten GmbH's row. The Overview's top bar names Nordlicht Komponenten GmbH.

On the live app the overlay branch is taken wherever a country overlay adds a requirement.
