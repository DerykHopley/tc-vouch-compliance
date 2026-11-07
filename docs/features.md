# Features

The four common tasks, step by step, from a fresh start on your own machine. The words in
quotes are the app's own; the domain words are in the [glossary](../GLOSSARY.md).

**Before you start:** run `scripts/dev.sh` (see [setup](setup.md#run-the-app)) and open the URL it
prints. It runs on the scripted fake model, offline and free, with no sign-in: you are "Local
user", on the Nordlicht Komponenten GmbH profile. The fake has scripted runs for four
controls, **1.1.1**, **1.6.2**, **4.1.2** and **5.1.1**; these walk-throughs use 4.1.2. Work
done here lasts until you stop the script. On the live app any control runs on the real model.

Do the walk-throughs in order: each starts where the one before stopped.

## 1. Assess a control

1. On the Overview (the Dossier, `/`), "Where you stand" reads "0 accepted of 12", and every
   control under "The controls" is "Not assessed".
2. Open **4.1.2** ("To what extent is the user access to IT services and IT systems secured?").
   Its working paper opens at `/controls/4.1.2`: "Not assessed yet. 5 requirements apply in this
   profile.", with the requirements M1, M2, S1, S2 and S3 listed.
3. Select **Assess**. The steps appear one by one under "How Vouch reached this":
   - `load_control`: the control loaded, and that 5 requirements apply under Nordlicht's profile;
   - `apply_overlays`: "No country overlay adds a requirement to this control, so no overlay
     branch is taken.";
   - `gather` and `tools`: the policies searched, and the passages found;
   - `check_answer`: "Checked the answer: it claims no audit result.";
   - `judge`: "Judged 4 of 5 requirements evidenced.";
   - `check_quotes`: each quote checked against its document;
   - `remediate`: "Drafted 1 remediation item (1 partly evidenced).";
   - `review`: "Waiting for the compliance lead's review."
4. At the top, the control's verdict is **Partly evidenced**, marked "Proposed": "Partly
   evidenced: 1 of 2 must requirements is fully evidenced." Below the steps are the draft answer
   for the auditor and the run's usage (tokens and an estimated cost; nothing is charged on the
   fake).
5. The requirements table gives each requirement its verdict, its reason and its exact quotes,
   each with the document, version and section, and "Quote verified":
   - **M1** is **Partly evidenced**: the standard says sign-in methods follow each system's
     exposure, but no record shows that assessment was done. Its remediation item, a draft, says
     what is missing, with an owner and a due date.
   - **M2**, **S1**, **S2** and **S3** are **Evidenced**.
6. At the foot, the proposed verdict waits for your review: a note field ("Your note (kept in the
   audit trail)"), **Accept verdict** and **Reject verdict**.

## 2. Review it with an override

1. On 4.1.2's working paper, in **S2**'s row, select **Change**.
2. The override opens: "Verdict" is set to **Partly evidenced**, and "Reason (required)" is
   empty. **Save** stays disabled until you give a reason. Type one, for example "The supplier
   portal does not check passwords against breach lists yet.", and select **Save**.
3. S2 now reads **Partly evidenced**, "Overridden from Evidenced", with your reason, and
   **Undo** next to **Change**. Above the review buttons: "1 override, recorded with your
   decision." The control's verdict stays Partly evidenced (M1 still is).
4. Select **Accept verdict**. The verdict is marked "Accepted", and the foot of the page reads
   "Accepted by Local user on" the date and time.
5. M1's remediation item now offers **Approve** and **Discard**, with its owner role and due
   date. Select **Approve**: it reads "Approved". The override also drafts an item for S2, which
   reads "not drafted" and needs an owner role before it can be approved: leave it. The Overview
   counts both items, the report only the approved one.
6. Back on the Overview: 4.1.2's card is "Accepted", Partly evidenced, and "Where you stand"
   reads "1 accepted of 12", with 1 partly evidenced.

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
   evidenced, 0 Evidenced, 0 Not evidenced, 0 Not applicable, 11 Open, and 1 remediation item.
4. **Controls**: every control with its status. 4.1.2 is "Accepted", Partly evidenced, reviewed
   by Local user on its date, with the quotes behind it. The others are "Open: not yet accepted,
   so it has no reviewed verdict."
5. **Remediation items**: the approved items of accepted controls, here M1's, with its owner and
   due date.
6. **Not applicable**: what the profile excludes, each with its reason; for Nordlicht, 5.1.2 S3
   (no remote access to the company network).
7. **Audit trail**: one row per action, with when, who, the control, the requirement, the
   verdict before and after, the note, the evidence, the model and the prompt version. Newest
   first: the approval of M1's remediation item, the acceptance of 4.1.2 and the override of S2,
   with your reason as its note.
8. At the foot, the disclaimer: "Drafted by an AI assistant, reviewed by Local user; not an
   audit result."
9. Select **Export**: a Markdown file named `readiness-report-nordlicht-<date>.md` downloads, with
   the report's parts in the page's order and the whole audit trail.
10. Print with the browser's own print (Ctrl P, Cmd P on a Mac): only the report prints, without
    the top bar, the tabs, the chat or the page's buttons; tables print whole, and a row doesn't
    break across pages. Save as PDF from the print dialog for a file to attach.

## Examples

### The German profile

1. Open the profile switcher in the top bar (it shows "Nordlicht Komponenten GmbH") and choose
   **Alpenwerk Präzision GmbH · Austria · German**. The controls' questions are now the German
   ISA edition's; the app's own labels stay English.
2. Open **4.1.2** and select **Assess**. The working paper says "Answer language: German", and
   the verdict, the draft answer and the requirements are in German: "Teilweise nachgewiesen",
   with 6 requirements under Alpenwerk's high protection level.
3. Select **Accept verdict**, then **Report**: the report is in German ("Bereitschaftsbericht",
   "1 von 12 Kontrollen akzeptiert"), with Alpenwerk's country overlay "AT NIS2 (illustrative)"
   in its head and the disclaimer "Erstellt von einem KI-Assistenten, geprüft von Local user;
   kein Auditergebnis."

### The overlay branch

A country overlay adds a requirement, and the assessment graph takes its overlay branch for it.
Alpenwerk's overlay adds O1 to 1.6.2.

1. Choose **Alpenwerk Präzision GmbH** in the profile switcher, open **1.6.2** and select
   **Assess**.
2. Under "How Vouch reached this":
   - `apply_overlays`: "Applied the country overlays: AT NIS2 (illustrative) adds O1.";
   - `overlay_branch`: "Overlay branch: gathering evidence for O1 (AT NIS2 (illustrative))."
3. O1 is in the requirements table, marked "Country overlay" and "AT NIS2 (illustrative)", with
   its own verdict and reason: "Nicht nachgewiesen", since nothing provides for an early warning
   to the national CSIRT.

On the live app the overlay branch is taken wherever a country overlay adds a requirement.
