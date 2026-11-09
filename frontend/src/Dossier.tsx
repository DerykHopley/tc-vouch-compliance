import type { MouseEvent } from 'react'
import { plural, REVIEW_WORDS, type Verdict, VERDICT_WORDS } from './assessment.ts'
import { type ControlCard, controlPath, isoLabel, type NextStep, type Overview, type OverviewState, type Readiness,
  SEGMENTS } from './overview.ts'
import type { PassState } from './profilePass.ts'
import VerdictChip from './VerdictChip.tsx'

/** The Dossier overview (`docs/design.md`, Decisions: Screens): the headline with the next step, the readiness band,
 * the control cards and the evidence quotes, all from `GET /api/overview`. */

type Props = {
  state: OverviewState
  onRetry: () => void
  /** Opens the control's working paper; `assess` starts an assessment there too. */
  onOpen: (controlId: string, assess: boolean) => void
  /** The control being assessed just now, if any: its card says so, and the hero offers no second run. */
  running: string | null
  /** The page's profile pass (#390): held above the views, so its result waits here. */
  pass?: PassState
  /** Starts a profile pass. */
  onPass?: () => void
}

const IDLE: PassState = { kind: 'idle' }

export default function Dossier({ state, onRetry, onOpen, running, pass = IDLE, onPass = () => {} }: Props) {
  if (state.status === 'loading') {
    return <main className="dossier wrap"><p className="lbl" role="status">Opening the dossier…</p></main>
  }
  if (state.status === 'failed') {
    return (
      <main className="dossier wrap">
        <section className="sheet notice">
          <p role="alert" className="answer">{state.message}</p>
          <button className="btn" onClick={onRetry}>Retry</button>
        </section>
      </main>
    )
  }
  const { overview } = state
  return (
    <main className="dossier wrap">
      <Hero overview={overview} onOpen={onOpen} running={running} pass={pass} onPass={onPass} />
      <PassPanel overview={overview} pass={pass} onOpen={onOpen} onPass={onPass} />
      <Band readiness={overview.readiness} />
      <section className="controls" aria-labelledby="controls-title">
        <div className="head">
          <span className="pill">The controls</span>
          <h2 id="controls-title">{overview.readiness.controls} controls</h2>
          <p>Open a control to work through its working paper.</p>
        </div>
        <div className="cards">
          {overview.controls.map((c) => (
            <Card key={c.id} card={c} onOpen={onOpen} assessing={c.id === running} language={overview.answer_language} />
          ))}
        </div>
      </section>
      <Quotes overview={overview} onOpen={onOpen} />
    </main>
  )
}

const NEXT_WORDS: Record<NonNullable<NextStep>['action'], (id: string) => string> = {
  review: (id) => `Review ${id}`,
  'assess-again': (id) => `Assess ${id} again`,
  assess: (id) => `Assess ${id}`,
}

type HeroProps = { overview: Overview; onOpen: Props['onOpen']; running: string | null; pass: PassState;
  onPass: () => void }

function Hero({ overview, onOpen, running, pass, onPass }: HeroProps) {
  const { readiness, by_status: count, next_step: next } = overview
  let headline: string
  let detail: string
  if (count['not-assessed'] === readiness.controls) {
    headline = 'Nothing is assessed yet.'
    detail = next ? `Start with ${next.control_id}: assess it, then review what Vouch finds.` : ''
  } else {
    headline = `${readiness.accepted} of ${readiness.controls} controls accepted.`
    detail = `${plural(count['waiting-for-review'], 'verdict')} waiting for your review, ${count.rejected} rejected.`
      + (next ? ` Next: ${NEXT_WORDS[next.action](next.control_id).toLowerCase()}.` : ' Every control is accepted.')
  }
  // While a run is in progress (one at a time, a profile pass included), the next step opens its paper rather than
  // starting another.
  const passing = pass.kind === 'running'
  const assess = next !== null && next.action !== 'review' && running === null && !passing
  return (
    <section className="hero">
      <span className="pill">{overview.company} · {overview.country}</span>
      <h1>Prepared for audit. One piece of evidence at a time.</h1>
      <p><b>{headline}</b> {detail}</p>
      {(next || readiness.open > 0) && (
        <div className="next">
          {next && (
            <button className="btn btn-primary" onClick={() => onOpen(next.control_id, assess)}>
              {next.action === 'review' || assess ? NEXT_WORDS[next.action](next.control_id) : `Open ${next.control_id}`}
            </button>
          )}
          {readiness.open > 0 && (
            <button className="btn btn-ghost" onClick={onPass} disabled={passing || running !== null}
              aria-busy={passing || undefined} aria-describedby="pass-note">
              {passing ? 'Running the profile pass…' : 'Run a profile pass'}
            </button>
          )}
        </div>
      )}
      {readiness.open > 0 && (passing
        ? <p id="pass-note" className="field-note" role="status">
          Vouch is assessing every open control in one run. This can take a few minutes.
        </p>
        : <p id="pass-note" className="field-note">
          {running !== null ? `Vouch is assessing ${running}. One run at a time.`
            : 'Assesses every open control in one run. Accepted controls are skipped.'}
        </p>)}
    </section>
  )
}

/** Whether a verdict leaves something to evidence, so the control may be worth assessing again on its own. */
const shortOf = (verdict: Verdict | null) => verdict === 'not-evidenced' || verdict === 'partly-evidenced'

/**
 * The profile pass's result (`docs/design.md`, Components: Profile pass): what waits for review, with a row per new
 * assessment read from its card as it stands now (a review since shows), or why the pass didn't run and the way on.
 * Nothing while the pass is idle or running.
 */
function PassPanel({ overview, pass, onOpen, onPass }:
  { overview: Overview; pass: PassState; onOpen: Props['onOpen']; onPass: () => void }) {
  if (pass.kind === 'idle' || pass.kind === 'running') return null
  const cards = new Map(overview.controls.map((c) => [c.id, c]))
  let body
  if (pass.kind === 'done') {
    const skipped = pass.skipped.length > 0 && (
      <p className="field-note">
        {plural(pass.skipped.length, 'accepted control')} skipped: {pass.skipped.join(', ')}.
      </p>)
    body = pass.assessments.length === 0 ? (
      <>
        <p>Every applicable control is accepted already. Nothing was assessed.</p>
        {skipped}
      </>
    ) : (
      <>
        <p>{plural(pass.assessments.length, 'assessment')} waiting for your review.</p>
        {skipped}
        <ul className="pass-rows">
          {pass.assessments.map((a) => {
            const card = cards.get(a.control_id)
            const status = card?.status ?? a.status
            const verdict = card ? card.verdict : a.verdict
            return (
              <li key={a.thread_id} className="pass-row" aria-label={`Control ${a.control_id}`}>
                <b className="mono">{a.control_id}</b>
                <VerdictChip verdict={verdict} language={overview.answer_language} />
                {status !== 'not-assessed' && <span className={`tag tag-${status}`}>{REVIEW_WORDS[status]}</span>}
                <span className="pass-actions">
                  <button className="btn" onClick={() => onOpen(a.control_id, false)}>Open {a.control_id}</button>
                  {shortOf(verdict) && (
                    <button className="btn btn-ghost" onClick={() => onOpen(a.control_id, true)}>
                      Assess {a.control_id} again
                    </button>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
      </>
    )
  } else if (pass.kind === 'refused' || pass.kind === 'limit') {
    // The per-control agent is the way on, with its own limit: from the first control not assessed or rejected.
    const first = overview.controls.find((c) => c.status === 'not-assessed' || c.status === 'rejected')
    body = (
      <>
        <p role="alert">{pass.message}</p>
        {first && (
          <button className="btn btn-primary" onClick={() => onOpen(first.id, true)}>Assess {first.id} on its own</button>
        )}
      </>
    )
  } else {
    body = (
      <>
        <p role="alert">{pass.message}</p>
        <button className="btn" onClick={onPass}>Try again</button>
      </>
    )
  }
  return (
    <section className="sheet pass" aria-labelledby="pass-title">
      <h2 id="pass-title">Profile pass</h2>
      {body}
    </section>
  )
}

/** A verdict as the band says it, from VERDICT_WORDS: the one place verdict words live (M4's verdictWord later). */
const bandWord = (verdict: Verdict) => VERDICT_WORDS[verdict].toLowerCase()

/** The remediation tile's label: the approved part named when some, not all, are approved, as the readiness report
 * counts only those (#310). */
const remediationLabel = ({ remediation_items: all, remediation_approved: approved }: Readiness) =>
  approved > 0 && approved < all ? `remediation items (${approved} approved)` : 'remediation items'

function Band({ readiness: r }: { readiness: Readiness }) {
  // Not applicable gets a tile only when a control has it.
  const counted = SEGMENTS.filter((v) => v !== 'not-applicable' || r.by_verdict[v] > 0)
  const words = [...counted.map((v) => `${r.by_verdict[v]} ${bandWord(v)}`), `${r.open} open`].join(', ')
  const tiles: [number, string][] = [
    [r.accepted, `accepted of ${r.controls}`],
    ...counted.map((v): [number, string] => [r.by_verdict[v], bandWord(v)]),
    [r.open, 'open'],
    [r.remediation_items, remediationLabel(r)],
  ]
  return (
    <section className="band" aria-labelledby="readiness">
      <div><span className="pill">Where you stand</span><h2 id="readiness">Readiness</h2></div>
      <p>Only verdicts you accepted count; the figures describe evidence coverage, not audit results.</p>
      <div className="readiness" role="img" aria-label={`Accepted verdicts: ${words}, of ${r.controls}`}>
        {SEGMENTS.map((v) => r.by_verdict[v] > 0 && (
          <span key={v} className={`readiness-${v}`} style={{ width: `${(r.by_verdict[v] / r.controls) * 100}%` }} />
        ))}
      </div>
      <div className="tiles">
        {tiles.map(([n, label]) => <div className="tile" key={label}><b>{n}</b><span>{label}</span></div>)}
      </div>
    </section>
  )
}

/** A click on a link stretched over a card (#231, #311): a plain primary click opens the working paper in place; a
 * Ctrl/Cmd/Shift/Alt or non-primary click is the browser's (a new tab or window). */
function openInPlace(e: MouseEvent, open: () => void) {
  if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return
  e.preventDefault()
  open()
}

/** A control's card; `language`, the profile's answer language, is its verdict's (#200). */
function Card({ card, onOpen, assessing, language }:
  { card: ControlCard; onOpen: Props['onOpen']; assessing: boolean; language: string }) {
  const edge = card.verdict ?? 'none'
  return (
    <article className="card" aria-label={`Control ${card.id}`}>
      <a className={`card-head edge-${edge}`} href={controlPath(card.id)} aria-label={`Open ${card.id}`}
        onClick={(e) => openInPlace(e, () => onOpen(card.id, false))}>
        <b>{card.id}</b>
        {card.status !== 'not-assessed' && <span className={`tag tag-${card.status}`}>{REVIEW_WORDS[card.status]}</span>}
      </a>
      <div className="control-title" lang={language}>{card.question}</div>
      <div className="card-meta">
        <span className="mono">{isoLabel(card.iso_27001)}</span>
        <VerdictChip verdict={card.verdict} assessing={assessing} language={language} />
      </div>
    </article>
  )
}

/** The evidence quotes; each card's one link, its "for <control> <requirement>", opens that control's paper (#311). */
function Quotes({ overview, onOpen }: { overview: Overview; onOpen: Props['onOpen'] }) {
  return (
    <section className="controls" aria-labelledby="quotes-title">
      <div className="head">
        <span className="pill">Evidence on file</span>
        <h2 id="quotes-title">What the evidence says</h2>
        <p>Every quote below was found word for word in the cited document version.</p>
      </div>
      {overview.quotes.length === 0 ? (
        <p className="sheet empty">
          No evidence on file yet. Quotes appear here once a control is assessed
          {overview.next_step ? `; start with ${overview.next_step.control_id}.` : '.'}
        </p>
      ) : (
        <div className="quotes">
          {overview.quotes.map((q, i) => (
            // By position: the quotes are a fixed list, and two may read alike once redacted.
            <article className="quote" key={i}>
              <blockquote>{q.quote}</blockquote>
              <footer>
                <span className="mono">{q.doc_id} v{q.version} · {q.section}</span>
                <span>
                  <span className={`kind kind-${q.kind}`}>{q.kind}</span>
                  {q.stale && <span className="flag">stale</span>}
                  {' '}·{' '}
                  <a className="quote-link" href={controlPath(q.control_id)}
                    onClick={(e) => openInPlace(e, () => onOpen(q.control_id, false))}>
                    for <span className="mono">{q.control_id} {q.requirement_id}</span>
                  </a>
                </span>
              </footer>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
