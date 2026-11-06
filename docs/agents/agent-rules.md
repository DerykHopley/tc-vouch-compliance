# Rules for Vouch's implementer and reviewer agents

Project rules on top of the rig's ticket loop (`CLAUDE.md`). Each line says where it came from, so it can be removed when it stops earning its place.

## Tests

- **Keyboard, focus and `inert` get a browser test** (`frontend/e2e/`, pytest-playwright). jsdom doesn't model focus movement or `inert`, so a unit test there passes on code that fails in a browser. A unit test may still check the attribute or the handler; the browser test proves the behaviour. From #66, whose first fix passed its unit tests and failed verify round 1.
- **A browser check that depends on an API answer waits for it** (`with page.expect_response(...)`) before asserting on the page. Asserting first races the answer and goes red only sometimes. From #122, where the compliance-lead test on the Developer page failed the merge gate of #121.
- **A new page or control state gets a touch-target case** in `frontend/e2e/test_touch_targets.py` (`small_targets(page) == []` at 400px). design.md's 44px floor is measured, not read. From #45, whose chat links failed verify twice on that floor.

## Commands

- **Take an exit code before any pipe.** In `cmd | tail; echo $?`, `$?` is `tail`'s code, which is 0 even when `cmd` failed. Run the command on its own with its output in a scratch file (`cmd > "$SCRATCH/out.txt" 2>&1; echo rc=$?`), or read `${PIPESTATUS[0]}` straight after the pipe. From #213, where #53's verifier reported exit 0 for an eval run that exited 3.

## Colour and contrast

- **Compute every contrast ratio** with `scripts/contrast.py <fg> <bg> [--min 4.5]`, and quote the number it prints. Never estimate one. From the M0 design calls, where an estimated "~3.9:1" was really 3.0:1.
- **Verdict tokens are checked by `scripts/token-roles.py`** (in CI and the pre-push hook): `--evidenced` and the rest only on verdict chips (`.v-*`), card edges (`.card-head`) and the readiness bar (`.readiness*`), as `docs/design.md` says. A new role for them is a `docs/design.md` change first, then a selector in the script's `ALLOWED`. From #11 and #12, where reviews caught two decorative uses that design-lint passed.

## Language

- **Mark text in the profile's answer language with `lang`** (the profile's `answer_language`): model output (chat replies, draft answers, reasons, remediation drafts), the ISA edition's text (questions, requirements, the edition's words a step quotes), verdict words and the roll-up note. Leave interface words unmarked. `frontend/e2e/test_profile_language.py` walks Alpenwerk and names any German text without `lang="de"`. From #244, after #200, #207, #224 and PR #227 each fixed a place one at a time.

## Pull requests

- **A Done-when criterion the PR doesn't meet is listed in its description** as `Dropped: <criterion>, because …`, and the reviewer checks that list against the ticket. From #36, where phone-number redaction was dropped without a note and later described as never asked for.
- **A PR or comment asking the owner to approve screenshots lists each one by its full absolute path, one per line** (never a shortened `…/scratchpad/` path), so the owner's client and the coordinator open them directly. From #228, whose shortened paths sent the coordinator searching (#238).

## Filing tickets

- **Start a ticket from `.github/ISSUE_TEMPLATE/ticket.md`** (`gh issue create --template ticket.md`): its headings are the ones `ticket-lint.py` checks. From #66, first filed without `Needs`.
