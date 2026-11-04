# Vouch

## Agent skills

### Issue tracker

Issues live in GitHub Issues for DerykHopley/tc-vouch-compliance, through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Working state

Level: **Spec**, review mode: **Factory**. Set by `/rig:project-kickoff` (`~/development/rig`, rig 2026-11-04 @9a2a7bf); rerun it to change either, or to bring in the rig's newer rules.

- Work lives in GitHub issues; one ticket is one PR into main.
- Build in the main session, where every step's results stay in view. Spawn a subagent only for an independent check (the `code-review` skill, the `rig:verifier` agent) or for work that would flood the context (screenshots, a long log): it returns a verdict, not a summary to act on.
- Write each ticket as an agent brief (the shape in Matt's `triage` skill): current behaviour, desired behaviour, key interfaces, testable acceptance criteria, out of scope. Name types, functions and behaviour rather than file paths, so the brief stays true while the code moves. Your notes and product voice go in a comment, apart from the brief.
- Keep `GLOSSARY.md` words in the code, the tickets and the UI.
- **A project with a UI keeps `docs/design.md` (on trial):** identity, tokens, layout and component states, and who approves visual changes (the owner, or a named person at a customer). The tokens live in the CSS; `docs/design.md` explains them and `scripts/design-lint.py` fails on a colour outside them. A ticket with a visual change names the section it follows; a choice the file doesn't settle goes to the approver with a screenshot of each option, and the answer lands in the file. Prototypes answer a question and are thrown away; the decision goes in `docs/design.md`.
- Record each decision where it will be acted on: a ticket comment or the PR description.
- PRs merge with a merge commit (`gh pr merge --merge`), so any merge can be undone with one revert.
- **Undo a bad merge with a revert, then fix.** When a merge into main breaks something, tell the owner and open a revert PR: `git revert -m 1 <merge-sha>` on a branch from main, or GitHub's Revert button. It lands like any PR in this review mode. Build the real fix as a Fix on a fresh branch: merging the reverted branch again brings nothing back, as git counts its commits as already in. Never force-push to undo.
- **A project that deploys (an app, a service): the first ticket is a walking skeleton on the real host,** through every outside service the app depends on (sign-in, database, storage), checked live by the owner. Problems with the host and those services show up in an hour, not after the features are built. A project with nothing to deploy (a CLI, a library) skips this.
- Run `/security-review` before the first deploy, and before each deploy that changes sign-in, secrets or what users can reach.
- Name `/mattpocock-skills:improve-codebase-architecture` to the owner when the same files keep changing together across tickets.


### Specs

- A spec holds at most **12 tickets**: about one day of review. A larger plan becomes two specs.
- Parallel implementers are the one other use of subagents: each ticket needs little from the others, so a fresh context per ticket loses nothing.
- **`/rig:spec-build <spec>` builds a spec** (not Matt's `/implement-spec`, which merges locally and reviews once at the end): each ticket goes through the ticket loop below, its own PR into the spec's integration branch (`spec/<spec>-<slug>`) through the merge gate, and a `rig:verifier` on the merge commit. One PR then takes the spec into main and closes its tickets. In Factory mode, this is the queue: a spec's tickets are built by `/rig:spec-build`, never picked up from main on their own.
- **A spec starts when the specs it depends on have landed in main.** A ticket closes only when its spec lands, so a ticket blocked by another spec's ticket waits until that code is in main. Two specs with no link between them may build at the same time, each on its own branch. In Factory mode, Claude starts `/rig:spec-build` on each spec as soon as nothing blocks it; in the other modes it names the startable specs and asks.
- Ticket branches merge into the integration branch with `git merge --no-ff`, so each ticket stays one merge commit. After the spec lands, one bad ticket is undone alone with `git revert -m 1 <its merge-sha>`; a revert that conflicts goes to the owner.
- An implementer's prompt holds the ticket number and what changed since the ticket was written (tickets merged since, renamed interfaces); the ticket and the implementer agent carry the rest.
- The integration branch merges main in after each Fix lands, so the spec builds on fixed code.
- Steps only the owner can do are `ready-for-human` tickets, each self-contained: what it waits for, every step, and how to finish it.
- **Close the spec before improving it.** Work joins an open spec only when it is one of these:
  - something its acceptance criteria need;
  - a bug the spec's own code caused;
  - a prerequisite that makes the remaining tickets smaller, approved by the owner.
- Everything else goes to a lane below, with a one-line answer: "Filed #N in `<lane>`. It isn't in the spec because …".
- Bug tickets count toward the 12, so a spec that keeps finding its own bugs reaches the owner instead of never closing.
- After `/to-tickets`, check the blocking links have no cycle and at least one ticket can start. When no ticket can start and none is running, report the deadlock rather than wait.
- When the owner says "put it in the spec", add it. Past 12 tickets, say so once ("Spec is now 13 of 12: split it, or drop something?") and let the owner choose.
- When the spec's last ticket is done, `/rig:spec-build` runs `/rig:spec-close`'s Ready part: the stories verified, the spec PR handed to the owner, and a watch on it. The owner's merge is the review; Landed then closes the spec and starts the next.


### Each ticket

1. Read the ticket, its comments, the spec and the chosen design (the `docs/design.md` section the ticket names); raise conflicts before building.
2. Agree the test seams, then build test-first with the `tdd` skill. **One end-to-end test per user story the ticket touches (on trial):** logic and edge cases go in unit tests below the UI, where they're cheap and steady. **Each other test goes at the lowest level that still shows the behaviour (on trial):** unit, then integration, then browser.
   - **The ticket wins (on trial):** an interface the ticket names (a signature, a type, a field, a flag) or a test level it names in `Done when` is settled. It beats the lowest-level rule here and baseline findings in step 5, so neither the builder nor the reviewer reopens it.
   - **Outside services (on trial):** before code that calls a third-party service, upgrades a dependency, or touches auth or billing, read the vendor's current docs for the installed version. The PR description carries the implementer's `Docs:` lines.
   - **Open choices (on trial):** acceptance criteria, then the codebase's own way, then the smallest revertable change, then the owner. A choice made on the third step goes in a `--- decision ---` commit trailer.
3. **A bug fix only (on trial):** prove the test that catches the bug: undo the fix (from a copy), see the test go red, restore. A new feature's tests already failed first under test-first.
4. Check it for real: the app in a browser at desktop and phone width, in light and dark, with the fake model (no paid run); an agent-only ticket through the CLI or the API.
5. **Standards review (on trial):** run the `code-review` skill on its Standards axis only (tell it the verifier covers the spec, and list the interfaces and test levels the ticket names, which it doesn't raise), once. Skip it for a ticket that changes only how the UI looks (styles and markup, no logic). Offer "fix all" when the findings are independent; after the fixes, recheck only those findings, not a new full review.
   - **Over 400 changed lines (on trial):** lockfiles left out, counted with `git diff --numstat <base>...HEAD`. 400 is a target, not a wall: aim under it, and propose a split when one is easy. A bigger ticket still goes on, with a deeper review instead of a stop: also run Claude Code's built-in correctness review, `/code-review high`, once, and fix its findings the same way. Name the PR and its size in the report.
6. **Verify (on trial).** Commit step 5's fixes first: the verifier checks that commit, so what merges is what was verified. A `rig:verifier` agent checks the ticket blind, in its own worktree, and is its only check against the ticket: give it the ticket number and the commit SHA, never the implementer's report. While it runs you may start the next ticket; a failed verify brings you back to this branch. Standard: before the PR. Spec: after the ticket merges into the integration branch. Compare its verdicts with the implementer's report; each disagreement goes to the owner. At most two verify rounds per ticket: a criterion that fails twice goes to the owner.

Steps marked "on trial" are being tuned to stay out of the way; `CHANGELOG.md` in the rig says what would end each trial.

Implementer subagents run as the `rig:implementer` agent type, checkers as `rig:verifier`; this project's own rules for them live in `none yet`.


### Triage when filing

Whoever files an issue sorts it into one lane straight away, with a label or milestone:

| Lane | What | Goes |
|---|---|---|
| **Current spec** | meets the "joins an open spec" rule above (Spec level) | a ticket on the integration branch |
| **Fixes** | a bug already on main, a process fix (tooling, CI, hooks, agent docs), or a small change: one PR, no design questions | built one at a time, its own PR straight into main |
| **Idea pool** | a feature or a bigger change; tag ideas that improve the open spec "follow-up to <spec>" | the `Next` milestone |
| **Architecture** | a change to the code's structure | grouped into its own small spec, after the open one |

- When the idea pool holds five related items, or a spec closes, suggest the owner run `/grill-with-docs` over them to make the next spec, follow-ups first.
- For a backlog that needs a proper sort, suggest `/triage`.


### CI

- **Fast lane, on every PR, under 10 minutes:** `uv run ruff check`, `uv run pytest` with no API key and sockets disabled, and in `frontend/` `npm run typecheck`, `npm run lint` and `npm test`, then `python3 -I -B scripts/design-lint.py frontend/src`, plus the browser tests for the pages the PR touches, in one browser.
- **Full lane, on main, nightly and before a deploy:** every test in every browser.
- Split a browser suite across parallel runners once it passes 10 minutes.
- Rerun a CI job only for a fault in the infrastructure (a runner that died), and say so; a red test gets a diagnosis, not a rerun.
- **A full-lane red on main (on trial):** file it as a Fix straight away, with the failing test id and the run link, and build it after the current ticket rather than stopping it. When the red breaks what users can do on the deployed app, revert first (the revert rule), then the Fix. A test that passed on rerun is still unexplained: the Fix says so.
- The pre-push hook runs the fast lane's quickest checks: `uv run ruff check && npm --prefix frontend run lint && python3 -I -B scripts/design-lint.py frontend/src`.


### Review mode: Throughput

- **Merge gate (no branch protection: GitHub's free plan on a private repo):** merge only with `scripts/merge-on-green.sh <pr>`, run as a background task. It prints the PR's changed lines, lockfiles left out, and notes one over the 400 target without refusing it, then waits for every check on the PR's current commit (limit 20 minutes, about twice the fast lane), merges with a merge commit only if all pass, and refuses a PR with no checks. Never merge a PR any other way, unless the owner says so for that PR.
- A ticket or a Fix: when the local checks pass, push, open the PR, start the merge gate, then start the next ticket. Report a red or timed-out result, not a green one.
- A spec: its one PR into main is the owner's to merge, in every review mode. `/rig:spec-close` hands it over when every ticket is done; the owner merges with GitHub's button (merge commit) or says "merge it", which runs the merge gate.
- After merges, refresh the owner's running app when it won't break what they're doing; say what changed.
- The owner tests in batches: keep a short "ready to check" list in `STATUS.md`, naming any PR over the 400 target and its size.


### Review mode: Factory (on trial)

The Throughput rules above hold. Factory adds a queue agents work through on their own, and turns each owner stop into a park, so one blocked ticket doesn't stop the rest.

- **The queue:** open issues labelled `ready-for-agent`, oldest first. Skip one that `python3 -I -B ~/development/rig/scripts/ticket-lint.py --json` flags, or that is blocked by an open issue. When a ticket merges, take the next.
- **Park, don't stop.** Where the ticket loop says a thing goes to the owner (a conflict, a step-4 choice, a verifier disagreement, two failed tries), park the ticket instead:
  - label it `needs-owner` and remove `ready-for-agent`;
  - comment with the one question, a recommended answer, and what was tried;
  - push work worth keeping as a draft PR; leave nothing half-merged;
  - start the next ticket.
  The owner answers in the comment and puts `ready-for-agent` back; the ticket rejoins the queue and is built on from its draft PR's branch.
- **Size target, not a cap:** aim for a PR under 400 changed lines, lockfiles left out. Over it, don't park: the ticket loop's deeper review runs, the PR merges, and the report names it with its size.
- **Fixed gates.** Factory never does these; a ticket that needs one parks: a production deploy, secrets, auth or permission changes, breaking a public contract, a destructive database change, a paid model run (a pay-per-token API call; the queue's own agents on the Claude subscription aren't one), a force push.
- **Report when the queue stops:** when no unparked ticket can start, one message: what merged, each parked ticket with its question, anything unexplained, and last the ready-to-check list (each merged ticket and what to test by hand, with its size when over 400). That list replaces Throughput's `STATUS.md`, which Factory doesn't keep. Then stop.
- **A full-lane red** still becomes a Fix ticket (`ready-for-agent`); a red that breaks the deployed app is reverted first, then the queue goes on.

### This project

- **Decisions** stay dated in `docs/requirements.md` under Decisions, each with its reason; the build plan is milestones M0 to M5 there, one spec each (issues #1 to #6, label `spec`). Specs and tickets may be written ahead; **owner-only tickets (`ready-for-human`) are filed first and listed for the owner up front**, so nothing waits on them. Issues labelled `spec` aren't queue tickets. **The 12-ticket cap is a guide here:** what counts is that the owner can review the spec's one PR in about a day; 14 to 16 small tickets, or 5 large ones, are fine (the owner, 2026-11-03). Domain words are in `GLOSSARY.md`.
- **Carried over at M0:** trial-b's backend (`src/`, `tests/`, `fixtures/`, `pyproject.toml`, `uv.lock`, its CI workflow) from `sprint3-trail-b` at `863ae43`, package renamed `audit_agent` → `vouch`, its tests with it. The trial's evidence stays in `trial/`.
- **The UI prototype** is on branch `prototype/ui` (never merged); its answers are decisions in `docs/requirements.md` and `docs/design.md`. A ticket that builds a prototyped screen links the branch.
- **The course submission repo** (`submission` remote, TuringCollegeSubmissions/dhople-AE.AFA.updated0209.3.6) is updated once per milestone, after its spec PR lands on main and CI is green: `scripts/push-submission.sh` (local only, git-ignored; `--dry-run` first). It merges origin/main into the submission repo, keeps Turing's first commit, and adds a reviewer note pointing at this repo's issues, PRs and CI. Issues and PRs stay here only.
- **No paid model run without the owner's OK** and a cost and time estimate; tests run on the scripted fake model, offline.
