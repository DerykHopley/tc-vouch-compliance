# Design

How Vouch looks and feels, and why. The tokens live in `frontend/src/styles/tokens.css` under `:root` (created by M0's walking skeleton); this file names and explains them, and never holds a value the CSS doesn't. `scripts/design-lint.py` fails on a colour written outside a token.

**Design is part of every ticket, not a polish phase.** A ticket that changes the UI isn't done until it matches this file in light and dark, at phone width, and with reduced motion. Copy the checklist of each section it touches into the ticket's `Done when`.

Keep this file short enough to read before each UI ticket: a component gets a row and a checklist line here; its full spec lives in the ticket that builds it.

## Source and approval

- **Identity from:** the owner's mood board (glen-check suits with a navy tie and brass details; the Atlas Accounting site; a near-black product page with a champagne accent), tried in the UI prototype on branch `prototype/ui` (variant E, style Tailored). The mood images are not in the repo.
- **Visual changes approved by:** the owner. A ticket that needs a visual choice this file doesn't settle goes to them, with a screenshot of each option.

## Identity

- **For:** a compliance or ISMS lead at a European company, at a desk, over weeks of audit preparation; and the auditor or manager who reads the readiness report.
- **Feels:** formal, precise, calm. Suit and tie: an experienced auditor's working paper, not an app.
- **Not:** playful, salesy, cluttered. No mascot or avatar (parked 2026-11-03), no emoji, no "compliant" or "certified" anywhere.

## Decisions

| Area | Decision | Why | Rejected |
|---|---|---|---|
| Screens | **Dossier** overview is the landing page (headline and next steps, a dark readiness band, the 12 controls as cards, evidence quotes); opening a control goes to its **Working paper** (control index on the left, one row per requirement with verdict and exact quotes, review at the foot) | overview to come back to; the working paper is where documents are worked through | chat-first (Counsel), one question at a time (Docket) |
| Chat | Patch's pattern: an "Ask Vouch" button bottom right opens a bubble; enlarged it can be pinned beside the page; Ctrl/Cmd+K opens, Esc closes the bubble but never a pinned chat; at 700px or less a full-screen sheet; it shows the page it's on | the chat stays on top of what you're looking at; pinning covers side by side | chat as its own page; a fixed side rail |
| Look | **Tailored**: warm glen-check greys, navy for links and focus, brass for small details, oxblood (light) or champagne (dark) for sign-off actions | carries the suit-and-tie persona | Plain (cool grey, IBM Plex Serif headings) |
| Sign-off colour (2026-11-04) | `--sign` is oxblood `#7A2232` in light (champagne in dark, unchanged); the code had copied the prototype's ink | signing off reads as a weightier act than Assess, as this file's Look row says; sheet text on it 9.3:1 | ink, the same as the primary button |
| Verdict chip contrast (2026-11-04) | In light the chip text tokens darken one shade, same hue, backgrounds unchanged: `--evidenced` `#3B7350`, `--partly-evidenced` `#8C601D`, `--not-applicable` `#626467` | the prototype's values were 4.24, 3.70 and 3.26:1 on their backgrounds, under the 4.5:1 floor; now 4.6:1 or more. Dark already passed (6.4 to 7.9:1) | lighter backgrounds (Partly evidenced still fails on `--sheet`) |
| Not assessed (2026-11-04) | Hollow, no colour of its own: an outline chip (1px `--rule` ring, hollow dot, `--muted` text) and a dashed `--rule` bottom edge on the card | not assessed means nothing has happened yet; not applicable is a verdict, so they mustn't share a grey; muted text on sheet 5.5:1 | borrowing the Not applicable grey (as in M0) |
| Ask Vouch button (2026-11-04) | Light: a pale tailored pill (`--sheet`, 1px `--ink` hairline, `--ink` text). Dark: the light pill as before, with a 2px `--brass` ring | in light the ink button vanished over the ink readiness band (1.0:1); the pale pill is 15.7:1 over the band, its hairline 13.6:1 over the page. The owner liked the brass ring on the dark theme's white button (9.4:1 over the band) but not in light | ink with a brass ring in light (only a 2px line holds it apart), a 3px sheet halo (heavier than the theme's 1px rules), flipping colour over the band (changes as you scroll), navy (1.5:1, and navy means running) |
| Type | Uppercase **Libre Caslon Display** for page and section headings; **Manrope** for body and UI; **IBM Plex Mono** for control and document ids and the agent's steps; **IBM Plex Serif** italic for quoted evidence | headings from the Atlas look; quotes read as documents, ids as references | Inter, Space Grotesk |
| Shape | Pill buttons, pill navigation and verdict chips; panels and cards 2 to 4px; full-width bands and decision areas square, with a ruled top edge (ink for the working paper's head, sign-off colour for decision bars) | tailored, not bubbly | 14 to 16px rounded panels |
| Texture | A thin glen-check strip under the top bar; nothing else patterned | one detail from the cloth, kept quiet | |
| Verdict wording | Evidenced, Partly evidenced, Not evidenced, Not applicable (German: Nachgewiesen, Teilweise nachgewiesen, Nicht nachgewiesen, Nicht anwendbar); each with a colour **and** a dot and the word | the "never say compliant" rule; colour is never the only cue | found / partial / gap |
| Language | The interface is English; answers, chat and the readiness report follow the profile's language | translating menus shows no agent behaviour | full interface translation (later improvement) |
| Avatar | None | 14 options tried in the prototype; none fit the formal persona | tick, clip, seals, button, watch, owl, stamp, lens, tie, pot plant, notebook, cup |

## Tokens

Names and uses come from the prototype's Tailored block; M0 copies the values into `tokens.css`, and only that file holds them.

| Token | Value lives in | Use for | Not for |
|---|---|---|---|
| `--paper` | `tokens.css` `:root` | page background (glen-check grey) | panels |
| `--sheet` | `tokens.css` `:root` | panels, cards, inputs | page background |
| `--ink` | `tokens.css` `:root` | body text; primary buttons; the working paper's head rule | large fills other than buttons and bands |
| `--muted` | `tokens.css` `:root` | secondary text, labels | anything that must be read to act |
| `--rule` | `tokens.css` `:root` | borders and table rules | text |
| `--navy` | `tokens.css` `:root` | links, focus ring, the running state | sign-off actions |
| `--sign` | `tokens.css` `:root` | sign-off actions (Accept verdict, approve), decision-bar rule | links, decoration |
| `--brass` | `tokens.css` `:root` | small details: active tab edge, quote rule, the brand mark, the top strip | text, buttons |
| `--band`, `--band-fg` | `tokens.css` `:root` | the dark full-width bands (readiness, Ask Vouch) | panels |
| `--evidenced`, `--partly-evidenced`, `--not-evidenced`, `--not-applicable` (each with a `-bg`) | `tokens.css` `:root` | verdict chips, the readiness bar, card edges | anything else (they are semantic, not accents) |
| `--check-a`, `--check-b` | `tokens.css` `:root` | the glen-check strip | anything else |
| `--f-display`, `--f-body`, `--f-mono`, `--f-quote` | `tokens.css` `:root` | the four type roles above | |

Type, spacing, radius, shadow and motion get rows here once the CSS names them as tokens. Dark mode redefines the same tokens; it never adds new ones. Dark is near-black with warm off-white text, champagne for `--sign` and `--brass`.

**Checklist:** every colour is a token (`scripts/design-lint.py` passes); each token used in light is defined in dark.

## Layout

| Width | What changes |
|---|---|
| under 700px | one column; the chat (bubble, enlarged or pinned) is a full-screen sheet; pin and make-larger hide |
| 700–1000px | the working paper's control index stacks above the paper; Dossier's hero, band and ask panel go to one column |
| over 1000px | working paper: index (about 250px) beside the paper; a pinned chat takes a column of about 400px beside the page |

**Checklist:** each changed screen checked at 400px, 900px and 1400px; nothing scrolls sideways (wide tables scroll inside their own container).

## Motion

| What moves | How | With reduced motion |
|---|---|---|
| The agent's steps during an assessment | each step appears as it streams in | appear without animation |
| Chat bubble opening | fades and rises a few pixels, under 200ms | appears |
| Scrolling to the chosen control | smooth scroll | jumps |

**Checklist:** the motion listed for a component ships with it; `prefers-reduced-motion` is honoured.

## Components

| Component | States |
|---|---|
| Button (pill) | default, primary (ink), sign-off (`--sign`), ghost; hover, focus, disabled, busy |
| Verdict chip | evidenced, partly evidenced, not evidenced, not applicable, not assessed (hollow outline), assessing |
| Review state tag | proposed, accepted, rejected |
| Evidence kind tag | documented (dashed), implemented (solid navy); stale flag |
| Citation | quote (serif italic, brass rule), document id and version, section, kind, stale flag, "quote verified" |
| Agent steps (trace) | streaming, done; a guard step and an overlay branch step marked apart |
| Requirement row | must, should, high protection, country overlay; each verdict; override with reason; not applicable with reason |
| Review bar | proposed (note and Accept / Reject), accepted, rejected (with "Assess again") |
| Remediation item | draft (approve, discard), approved, discarded |
| Control card | not assessed (dashed `--rule` edge), assessing, proposed, accepted, rejected; the verdict as a coloured bottom edge |
| Readiness bar | segments by accepted verdict, with the counts in words beside it |
| Chat | closed (button), bubble, enlarged, pinned; the page it's on |

**Checklist:** every state in the row is reachable and looks as described, in light and dark.

## Accessibility floor

- Text contrast at least 4.5:1 (3:1 for large text and icons), in light and dark.
- A visible focus ring on everything that takes focus.
- Touch targets at least 44 by 44px.
- Colour is never the only cue: a label, icon or shape goes with it.

**Checklist:** contrast checked for each new colour pair, in both themes; keyboard reaches and leaves every new control.

## Approved screens

Screenshots of screens the approver signed off, one line each: what it shows and the date. Added only after approval.
