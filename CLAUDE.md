# Vouch

## Agent skills

### Issue tracker

Issues live in GitHub Issues for DerykHopley/tc-vouch-compliance, through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Working state

Level: **Build**, review mode: **Factory**. Set by `/rig:project-kickoff` (`~/development/rig`, rig 2026-11-11 @53a3ec0); rerun it to change either, or to bring in the rig's newer rules.

- Work lives in GitHub issues. **Each piece of work is a Fix, a Feature or a Spec (on trial),** chosen when it's triaged, not once per project, so specs come and go without a kickoff:

  | Unit | When | Lands |
  |---|---|---|
  | **Fix** | a bug on main, a process fix, or a small enhancement (the Fixes lane's rule) | one PR into main, through the Fixes lane |
  | **Feature** | one story or change that can land on its own; the default | one PR into main, through the ticket loop |
  | **Spec** | stories that only work together, or share a core change, so landing them one at a time would leave main half-done | an integration branch, through `/rig:spec-build` and `/rig:spec-close` (Specs, below) |

  Bigger work is grilled first (`/grill-with-docs`), then split with `/to-tickets` into Features that each land alone; it becomes a spec (`/to-spec`) only when they can't. A Feature carries the `feature` label. Vouch, 2026-11-11: grouping work into specs made specs wait on each other and clash on shared files, so most work there is now Features.
- **A Feature** goes through the ticket loop below, its verifier before the PR, then the merge gate. With a UI change, it gets an **owner preview** (below) before its merge, with one-line "try this" steps: blocking feedback is built on the same PR, polish becomes a Fix, and it merges after the owner's go. A Fix keeps the Fixes lane's batch check instead.
- **Who builds (on trial).** The session that keeps the owner's desk never builds. Under `/rig:spec-run`, the coordinator starts a thread for each spec and Feature and keeps one Fixes thread; one-off jobs get their own thread. With no coordinator, the main session builds one piece of work at a time, where every step's results stay in view, and spawns a subagent only for an independent check (the `code-review` skill, the `rig:verifier` agent), for parallel implementers, or for work that would flood the context (screenshots, a long log): it returns a verdict, not a summary to act on. Vouch, 2026-11-11: moved from Spec to Standard, its coordinator read "build in the main session" and started building.
- Write each ticket as an agent brief (the shape in Matt's `triage` skill): current behaviour, desired behaviour, key interfaces, testable acceptance criteria, out of scope. Name types, functions and behaviour rather than file paths, so the brief stays true while the code moves. Your notes and product voice go in a comment, apart from the brief.
- Keep `GLOSSARY.md` words in the code, the tickets and the UI.
- **A project with a UI keeps `docs/design.md` (on trial):** identity, tokens, layout and component states, and who approves visual changes (the owner, or a named person at a customer). The tokens live in the CSS; `docs/design.md` explains them and `scripts/design-lint.py` fails on a colour outside them. A ticket with a visual change names the section it follows; a choice the file doesn't settle goes to the approver, and the answer lands in the file. **How the approver sees it (on trial):** a static choice between options (a colour, a layout, wording), as a screenshot of each option side by side; an interactive one (a handle, drag, resize, focus, hover, motion, keyboard), by trying it in the owner preview or the live app, listed as a one-line "try this" step, with no screenshot to approve. Screenshots also stand as proof in a PR. Prototypes answer a question and are thrown away; the decision goes in `docs/design.md`.
- Record each decision where it will be acted on: a ticket comment or the PR description.
- PRs merge with a merge commit (`gh pr merge --merge`), so any merge can be undone with one revert.
- **Undo a bad merge with a revert, then fix.** When a merge into main breaks something, tell the owner and open a revert PR: `git revert -m 1 <merge-sha>` on a branch from main, or GitHub's Revert button. It lands like any PR in this review mode. Build the real fix as a Fix on a fresh branch: merging the reverted branch again brings nothing back, as git counts its commits as already in. Never force-push to undo.
- **A project that deploys (an app, a service): the first ticket is a walking skeleton on the real host,** through every outside service the app depends on (sign-in, database, storage), checked live by the owner. Problems with the host and those services show up in an hour, not after the features are built. A project with nothing to deploy (a CLI, a library) skips this.
- Run `/security-review` before the first deploy, and before each deploy that changes sign-in, secrets or what users can reach.
- **Owner preview:** `scripts/dev.sh --api-port <spare> --web-port <spare> --detach <name>` from the Feature's worktree or the spec's integration branch, so it outlives the agent's turn as its own user unit (`scripts/dev.sh --stop <name>` stops it, #309): local sign-in mode, the scripted fake model, never the owner's own running app or the live database. A few real model calls only with the owner's OK and a cost estimate. Cloud Run previews are out while sign-in accepts only the fixed address (#178).
- **The desk:** every report to the owner (a coordinator's, a spec's or a Feature's) ends with what's waiting on them (Blocking, Hands-on steps, Ready to merge, Decided for you), so the latest message is all the owner needs to read.
- Name `/mattpocock-skills:improve-codebase-architecture` to the owner when the same files keep changing together across tickets.

### Specs (when a piece of work is one)

These rules hold while a spec is open; with none open, they wait.

- A spec holds at most **12 tickets**: about one day of review. A larger plan becomes two specs.
- **Tickets are written just in time (on trial).** The spec carries a `## Build order`: numbered slices, one line each, naming the user stories each delivers. `/rig:spec-build` writes it when it's missing and a `rig:ticket-writer` writes each slice's ticket when a build slot frees up, against the code as it is then. `/to-tickets` isn't needed at Spec level; tickets it wrote count as slices, rewritten in place when their turn comes.
- Parallel implementers are the one other use of subagents: each ticket needs little from the others, so a fresh context per ticket loses nothing.
- **`/rig:spec-build <spec>` builds a spec** (not Matt's `/implement-spec`, which merges locally and reviews once at the end): each ticket goes through the ticket loop below, its own PR into the spec's integration branch (`spec/<spec>-<slug>`) through the merge gate, and a `rig:verifier` on the merge commit. One PR then takes the spec into main and closes its tickets. In Factory mode, this is the queue: a spec's tickets are built by `/rig:spec-build`, never picked up from main on their own. Up to 3 tickets build at once, never two whose `Touches` lines share a file or area.
- **A spec starts when the specs it depends on have landed in main.** A ticket closes only when its spec lands, so a ticket blocked by another spec's ticket waits until that code is in main. Two specs with no link between them may build at the same time, each on its own branch. In Factory mode, Claude starts `/rig:spec-build` on each spec as soon as nothing blocks it; in the other modes it names the startable specs and asks.
- Ticket branches merge into the integration branch with `git merge --no-ff`, so each ticket stays one merge commit. After the spec lands, one bad ticket is undone alone with `git revert -m 1 <its merge-sha>`; a revert that conflicts goes to the owner.
- An implementer's prompt holds the ticket number and the integration branch; the ticket was written against that branch, and the implementer agent carries the rest.
- The integration branch merges main in after each Fix lands, so the spec builds on fixed code.
- Steps only the owner can do are `ready-for-human` tickets, each self-contained: what it waits for, every step, and how to finish it.
- **Close the spec before improving it.** Work joins an open spec only when it is one of these:
  - something its acceptance criteria need;
  - a bug the spec's own code caused;
  - a prerequisite that makes the remaining tickets smaller, approved by the owner.
- Everything else goes to a lane below, with a one-line answer: "Filed #N in `<lane>`. It isn't in the spec because …".
- Bug tickets count toward the 12, so a spec that keeps finding its own bugs reaches the owner instead of never closing.
- When no slice can start and none is running, report why rather than wait.
- When the owner says "put it in the spec", add it. Past 12 tickets, say so once ("Spec is now 13 of 12: split it, or drop something?") and let the owner choose.
- When the spec's last slice is done, `/rig:spec-build` runs `/rig:spec-close`'s Ready part: the stories verified, then a running copy for the owner to test (**Owner preview**, below). Blocking feedback becomes new slices before the merge, polish becomes Fix tickets after; after the owner's go, the agent merges the spec PR (in Learning mode the owner merges it, and the next spec-build run finds it) and Landed closes the spec and starts the next.

### Each ticket

1. Read the ticket, its comments, the spec and the chosen design (the `docs/design.md` section the ticket names); raise conflicts before building.
2. Agree the test seams, then build test-first with the `tdd` skill. **One end-to-end test per user story the ticket touches (on trial):** logic and edge cases go in unit tests below the UI, where they're cheap and steady. **Each other test goes at the lowest level that still shows the behaviour (on trial):** unit, then integration, then browser.
   - **The ticket wins (on trial):** an interface the ticket names (a signature, a type, a field, a flag) or a test level it names in `Done when` is settled. It beats the lowest-level rule here and baseline findings in step 5, so neither the builder nor the reviewer reopens it.
   - **Outside services (on trial):** before code that calls a third-party service, upgrades a dependency, or touches auth or billing, read the vendor's current docs for the installed version. The PR description carries the implementer's `Docs:` lines.
   - **Open choices (on trial):** acceptance criteria, then the codebase's own way, then the smallest revertable change. A choice made on the third step goes in a `--- decision ---` commit trailer and is flagged (below). Only a hard stop goes to the owner.
3. **A bug fix only (on trial):** prove the test that catches the bug: undo the fix (from a copy), see the test go red, restore. A new feature's tests already failed first under test-first.
4. Check it for real: the app in a browser at desktop and phone width, in light and dark, with the fake model (no paid run); an agent-only ticket through the CLI or the API.
5. **Standards review (on trial):** run the `code-review` skill on its Standards axis only (tell it the verifier covers the spec, and list the interfaces and test levels the ticket names, which it doesn't raise, and that a test proving again what a lower-level test already proves is a finding), once. Skip it for a ticket that changes only how the UI looks (styles and markup, no logic). Offer "fix all" when the findings are independent; after the fixes, recheck only those findings, not a new full review.
   - **Over 400 changed lines (on trial):** lockfiles left out, counted with `git diff --numstat <base>...HEAD`. 400 is a target, not a wall: aim under it, and propose a split when one is easy. A bigger ticket still goes on, with a deeper review instead of a stop: also run Claude Code's built-in correctness review, `/code-review high`, once, and fix its findings the same way. Name the PR and its size in the report.
6. **Verify (on trial).** Commit step 5's fixes first: the verifier checks that commit, so what merges is what was verified. A `rig:verifier` agent checks the ticket blind, in its own worktree, and is its only check against the ticket: give it the ticket number and the commit SHA, never the implementer's report. While it runs you may start the next ticket; a failed verify brings you back to this branch. A Feature or a Fix: before the PR. A spec's ticket: after it merges into the integration branch. Compare its verdicts with the implementer's report; a disagreement is a choice like any other: decided and flagged, or the owner's when it's a hard stop. At most two verify rounds per ticket: a criterion that fails twice goes to the owner.

### Decide and flag, or stop (on trial)

Agents take reversible choices themselves (config, UI, wording, internal module shape, test setup), with the recommended option, and flag each one: a comment on the ticket starting `**Decided for you:**` and the `decided` label, so the owner can overrule it later. An overrule becomes new work, not a revert under way. These **hard stops** always wait for the owner, in every mode:

- money: a paid model run (a pay-per-token API call; agents on the Claude subscription aren't one), a new paid service;
- secrets, and the owner's keys or accounts;
- live deploys and live data;
- sign-in, access and security;
- deleting things (data, branches with unmerged work, history: no force push);
- scope: a user story into or out of a spec;
- core pieces that are hard to change: the data model and its migrations, an interface another spec or a landed spec relies on, choosing or replacing an outside service or main library, anything that breaks data already stored;
- two failed tries at the same step.

This project adds: none yet; the retro proposes additions and removals.

Steps marked "on trial" are being tuned to stay out of the way; `CHANGELOG.md` in the rig says what would end each trial.

Implementer subagents run as the `rig:implementer` agent type, checkers as `rig:verifier`; this project's own rules for them live in `docs/agents/agent-rules.md`.


### Triage when filing

Whoever files an issue sorts it into one lane straight away, with a label or milestone:

| Lane | What | Goes |
|---|---|---|
| **Current spec** | meets the "joins an open spec" rule above, while a spec is open | a ticket on the integration branch |
| **Fixes** | a bug already on main, a process fix (tooling, CI, hooks, agent docs), or a small enhancement (below) | its own PR straight into main, through the Fixes lane below |
| **Feature** | one story or change that can land on its own, already planned (acceptance criteria, `Touches`) | its own PR into main, labelled `feature` |
| **Idea pool** | a feature, a new screen, a data or model change, or anything else bigger than a small enhancement, not yet planned; tag ideas that improve an open spec or a landed Feature "follow-up to <n>" | the `Next` milestone |
| **Architecture** | a change to the code's structure | grilled into Features, or a small spec when its steps can't land alone |

- **Who files what (on trial):** a bug on main with a way to reproduce it may be filed straight in as a Fix (`ready-for-agent`, with a `Touches` section). Anything else an agent or a retro files lands as `needs-triage`, and the triage pass sorts it. Vouch, 2026-11-06: 18 issues filed in one day went straight to `ready-for-agent`, and a top-bar redesign ran as a Fix beside the spec redesigning the same screens.
- **Triage when a spec or a Feature lands (on trial):** `/rig:spec-close`'s Landed part, or one triage agent the coordinator starts after a Feature merges while `needs-triage` issues are open, sorts `needs-triage` into lanes, marks duplicates, closes stale or superseded issues with the reason (reopening is the overrule), and groups the idea pool into candidate Features or specs. Each move is flagged; planning one from a group is the owner's, with `/grill-with-docs`.
- **A small enhancement (on trial)** is a Fix when all of these hold: one change you can say in a sentence; one PR of about 300 changed lines at most; and every design choice in it is reversible: wording, placement, sizing, spacing or layout on a screen that already exists. A new screen, a new feature, a data or model change, or a change to a `docs/design.md` decision is the idea pool. Its choices are decided and flagged on the issue (`**Decided for you:**`, the `decided` label); a follow-up Fix is the overrule. Vouch, 2026-11-10: M9 bundled eight separate polish items (hero wording, review bar placement, chat button sizing, report tables on a phone) into a 10-slice spec only because a Fix couldn't carry a layout choice.
- For a backlog that needs a proper sort between specs, suggest `/triage`.
- **A ticket that edits a markdown table** checks the rendered table (every row has the header's cell count); a `|` inside a cell is written `\|`.

### The Fixes lane (on trial)

- **One Fixes thread** works the lane (under `/rig:spec-run`, the coordinator starts it and keeps its queue), up to 3 Fixes at once when their `Touches` lines share no file or area.
- **Order, by what waiting costs:** a bug users hit on main; then anything blocking a spec or a Feature; then tooling and process fixes; then small enhancements, oldest first.
- **Checked against open specs and running Features before it starts:** compare its `Touches` with each open spec's changed files (`git diff --name-only origin/main...origin/spec/<n>-<slug>`) and each running ticket's `Touches`. A bug users hit goes ahead, and that thread is told while the Fix's PR is still open. Anything else that overlaps is offered to that thread (`fix #<n> overlaps your <files>: take it?`); if it takes it, it leaves the queue. It waits only when the thread declines, or is at Ready.
- **A Fix keeps its PR** (CI and a one-merge revert), and its loop scales with its size: **under about 100 changed lines with no UI change**, the implementer's test seen red then green plus CI are the check, with no Standards review and no verifier; anything bigger, or any UI change, takes the full ticket loop. A UI change merges on green after its verifier, and goes on `STATUS.md`'s "ready to check" list with what to try, so the owner checks enhancements in batches rather than one at a time.


### CI

- **Fast lane, on every PR, under 10 minutes:** `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest` with no API key and sockets disabled, and in `frontend/` `npm run typecheck`, `npm run lint` and `npm test`, then `python3 -I -B scripts/design-lint.py frontend/src`, plus the browser tests for the pages the PR touches, in one browser.
- **Full lane, on main, nightly and before a deploy:** every test in every browser.
- Split a browser suite across parallel runners once it passes 10 minutes.
- Rerun a CI job only for a fault in the infrastructure (a runner that died), and say so; a red test gets a diagnosis, not a rerun.
- **A full-lane red on main (on trial):** file it as a Fix straight away, with the failing test id and the run link, and build it after the current ticket rather than stopping it. When the red breaks what users can do on the deployed app, revert first (the revert rule), then the Fix. A test that passed on rerun is still unexplained: the Fix says so.
- The pre-push hook runs the fast lane's quickest checks: `uv run ruff check && uv run ruff format --check && uv run mypy && npm --prefix frontend run lint && python3 -I -B scripts/design-lint.py frontend/src`.


### Review mode: Throughput

- **Merge gate (no branch protection: GitHub's free plan on a private repo):** merge only with `scripts/merge-on-green.sh <pr>`, run as a background task. It prints the PR's changed lines, lockfiles left out, and notes one over the 400 target without refusing it, then waits for every check on the PR's current commit (limit 20 minutes, about twice the fast lane), merges with a merge commit only if all pass, and refuses a PR with no checks. Never merge a PR any other way, unless the owner says so for that PR.
- A ticket or a Fix: when the local checks pass, push, open the PR, start the merge gate, then start the next ticket. Report a red or timed-out result, not a green one.
- A Feature with a UI change: its PR merges after the owner's go on its preview, through the merge gate.
- A spec: its one PR into main merges after the owner has tested a running copy and their blocking points are built (`/rig:spec-close`'s Ready part), with a merge commit through the merge gate. The owner can also merge it themselves, or say "merge it".
- After merges, refresh the owner's running app when it won't break what they're doing; say what changed.
- The owner tests in batches: keep a short "ready to check" list in `STATUS.md`, naming any PR over the 400 target and its size.


### Review mode: Factory (on trial)

The Throughput rules above hold. Factory adds a queue agents work through on their own, and turns each owner stop into a park, so one blocked ticket doesn't stop the rest.

- **The queue:** open issues labelled `ready-for-agent`, oldest first. Skip one that `python3 -I -B ~/development/rig/scripts/ticket-lint.py --json` flags, or that is blocked by an open issue. When a ticket merges, take the next.
- **Park, don't stop.** Where the ticket loop says a thing goes to the owner (a hard stop), park the ticket instead:
  - label it `needs-owner` and remove `ready-for-agent`;
  - comment with the one question, a recommended answer, and what was tried;
  - push work worth keeping as a draft PR; leave nothing half-merged;
  - start the next ticket.
  The owner answers in the comment and puts `ready-for-agent` back; the ticket rejoins the queue and is built on from its draft PR's branch.
- **Size target, not a cap:** aim for a PR under 400 changed lines, lockfiles left out. Over it, don't park: the ticket loop's deeper review runs, the PR merges, and the report names it with its size.
- **Hard stops park.** Factory never does one of the ticket loop's hard stops; a ticket that needs one parks. Any other choice is decided and flagged, and the queue moves on.
- **Report when the queue stops:** when no unparked ticket can start, one message: what merged, each parked ticket with its question, anything unexplained, and last the ready-to-check list (each merged ticket and what to test by hand, with its size when over 400). The same list is `STATUS.md`'s "ready to check", which the Fixes lane keeps current: each UI change goes on it when it merges, with what to try. Then stop.
- **A full-lane red** still becomes a Fix ticket (`ready-for-agent`); a red that breaks the deployed app is reverted first, then the queue goes on.

### This project

- **Decisions** stay dated in `docs/requirements.md` under Decisions, each with its reason. Milestones M0 to M9 were built as specs (label `spec`, closed); new work runs as Fixes, Features or specs, chosen at triage (above). **Owner-only tickets (`ready-for-human`) are filed first and listed for the owner up front**, so nothing waits on them. Domain words are in `GLOSSARY.md`.
- **Carried over at M0:** trial-b's backend (`src/`, `tests/`, `fixtures/`, `pyproject.toml`, `uv.lock`, its CI workflow) from `sprint3-trail-b` at `863ae43`, package renamed `audit_agent` → `vouch`, its tests with it. The trial's evidence stays in `trial/`.
- **The UI prototype** is on branch `prototype/ui` (never merged); its answers are decisions in `docs/requirements.md` and `docs/design.md`. A ticket that builds a prototyped screen links the branch.
- **The course submission repo** (`submission` remote, TuringCollegeSubmissions/dhople-AE.AFA.updated0209.3.6) is updated after each Feature, spec or batch of Fixes the owner has checked lands on main and CI is green: `scripts/push-submission.sh` (local only, git-ignored; `--dry-run` first). It merges origin/main into the submission repo, keeps Turing's first commit, and adds a reviewer note pointing at this repo's issues, PRs and CI. Issues and PRs stay here only.
- **No paid model run without the owner's OK** and a cost and time estimate; tests run on the scripted fake model, offline.
