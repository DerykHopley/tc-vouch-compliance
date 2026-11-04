# Rules for Vouch's implementer and reviewer agents

Project rules on top of the rig's ticket loop (`CLAUDE.md`). Each line says where it came from, so it can be removed when it stops earning its place.

## Tests

- **Keyboard, focus and `inert` get a browser test** (`frontend/e2e/`, pytest-playwright). jsdom doesn't model focus movement or `inert`, so a unit test there passes on code that fails in a browser. A unit test may still check the attribute or the handler; the browser test proves the behaviour. From #66, whose first fix passed its unit tests and failed verify round 1.

## Colour and contrast

- **Compute every contrast ratio** with `scripts/contrast.py <fg> <bg> [--min 4.5]`, and quote the number it prints. Never estimate one. From the M0 design calls, where an estimated "~3.9:1" was really 3.0:1.
- **Verdict tokens are checked by `scripts/token-roles.py`** (in CI and the pre-push hook): `--evidenced` and the rest only on verdict chips (`.v-*`), card edges (`.card-head`) and the readiness bar (`.readiness*`), as `docs/design.md` says. A new role for them is a `docs/design.md` change first, then a selector in the script's `ALLOWED`. From #11 and #12, where reviews caught two decorative uses that design-lint passed.

## Filing tickets

- **Start a ticket from `.github/ISSUE_TEMPLATE/ticket.md`** (`gh issue create --template ticket.md`): its headings are the ones `ticket-lint.py` checks. From #66, first filed without `Needs`.
