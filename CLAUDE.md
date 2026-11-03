# Vouch

## Agent skills

### Issue tracker

Issues live in GitHub Issues for DerykHopley/tc-vouch-compliance, through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Working state

Level: **Discovery**, set by `/rig:project-kickoff` (`~/development/rig`, rig 2026-11-03). The project is deciding what to build. Rerun kickoff to move up to Standard or Spec once the work has a shape.

- **The work here:** grilling (`/grill-with-docs`), research (`/research`), throwaway prototypes (`/prototype`) and process trials. There's no product code on main yet, so there are no CI lanes, merge gate or ticket loop.
- **Decisions** go in `docs/requirements.md` under Decisions, each dated and with its reason. Domain words go in `GLOSSARY.md`, and choices about structure become ADRs (the `domain-modeling` skill).
- **A prototype answers one question, then goes.** Record the answer in the decision log. Keep prototype code out of main (a `prototypes/` folder that nothing imports, or a throwaway repo). Code worth keeping is carried over on purpose at the move up, with its tests.
- **Issues:** ideas and open questions are filed as `needs-triage`. Nothing is `ready-for-agent` until the level is chosen.
- Commit docs straight to main; with no code there's nothing for a PR to check.
- **Ready to move up** when you can name the first piece of work and how it lands:
  - **Spec**, if its user stories only make sense together, built over weeks on an integration branch.
  - **Standard**, if each ticket can merge to main on its own.

  Then rerun `/rig:project-kickoff`.
