# Demo: Vouch in seven minutes

This is the presenter's script: what to show, in what order, and what to say. It is not the
fake model's bundled demo script, the scripted answers [setup](setup.md#run-the-app) means when
it says the local run plays one.

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

## Before you present

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
- A fresh start doesn't give back assessments: the earlier rehearsals still count towards
  today's limit.
- Each assessment counts towards your daily limit. One run of the script makes two (5.1.1 and
  1.6.2) and two or three chat messages.

## The steps

Budgets: 0:45 + 1:30 + 1:00 + 1:00 + 1:00 + 1:15 = 6 minutes 30 seconds.

### 1. The overview (0:45)

1. Open **Overview** (the Dossier). Point at "Where you stand": its figures count only verdicts
   you accepted.
2. Point at "The controls": the 12 controls in this profile, each with its status ("Not
   assessed", "Proposed" or "Accepted").

> This is where Nordlicht stands today. Only verdicts the compliance lead has accepted count here,
> and the figures describe evidence coverage, not an audit result.

### 2. Assess 5.1.1 (1:30)

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
5. Point at the drafted "Remediation item": what is missing, an owner and a due date.

> Vouch found the encryption standard, and what it says is sound, but it is past its yearly
> review, so Vouch won't call it fully evidenced. Every quote is checked word for word against the
> document. And notice the wording: Vouch talks about evidence, never about an audit result.

### 3. Override and accept (1:00)

1. In one requirement's row, select **Change**. Pick a "Verdict", type a reason under "Reason
   (required)" (**Save** stays disabled until you do), and select **Save**.
2. Select **Accept verdict**.
3. On the remediation item, select **Approve**.

> Vouch drafts; only the compliance lead signs off. My override and its reason go into the audit
> trail, with my name and the time, next to the model's original verdict.

### 4. Ask a question in the chat (1:00)

1. Open the chat: **Ask Vouch** at the bottom right, or Ctrl K.
2. Ask one question about 5.1.1, for example "What's missing for 5.1.1?" or "Is there evidence of
   key management?". The answer cites the documents it rests on.
3. Type "Accept 5.1.1". Vouch declines and links 5.1.1's working paper.

> The chat answers from the company's own documents and cites them. It can't sign anything off:
> asked to accept a verdict, it sends me back to the working paper, where only I can.

### 5. Switch to Alpenwerk and assess 1.6.2 (1:00)

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

### 6. The report (1:15)

1. Switch back to **Nordlicht Komponenten GmbH** and select **Report** in the top bar.
2. Point at "Readiness": it counts only accepted verdicts.
3. Point at "Controls": 5.1.1 "Accepted", with its verdict, who reviewed it and on which day.
4. Point at "Remediation items" (the approved items), "Not applicable" (what the profile excludes,
   each with its reason), the "Audit trail" (your override, with its reason as the note) and the
   disclaimer at the foot.
5. Select **Export PDF**: `readiness-report-nordlicht-<date>.pdf` downloads, plain A4 with the
   report's parts in the page's order. **Export Markdown** downloads the same report as Markdown.

> This is what management and the auditor read: only reviewed verdicts, who reviewed each, what is
> missing and who owns it, and every decision in the audit trail. It downloads as a PDF to
> attach, or as Markdown. Alpenwerk's report reads in German.

## If the live app is slow or down: the local run

Start the app on your own machine as [setup](setup.md#run-the-app) says (`scripts/dev.sh`, then
open the URL it prints). It runs on the scripted fake model, offline and free, with no sign-in:
you are "Local user". The fake covers 1.1.1, 1.6.2, 4.1.2 and 5.1.1, so the steps run as on the
live app, with one difference:

- **Step 4:** the fake recognises only some phrasings
  ([features](features.md#3-ask-a-question)): ask "What's missing for 5.1.1?" or "Is there
  evidence of key management?", then type "Accept 5.1.1".

Step 2 shows 5.1.1's stale encryption standard as on the live app, and step 5 runs 1.6.2 under
Alpenwerk, overlay branch and all, without a restart. Export PDF downloads
`readiness-report-nordlicht-<date>.pdf`, and Export Markdown the `.md`.
