import { plural, type Verdict, VERDICT_WORDS } from './assessment.ts'
import { type ControlCard, controlPath, type NextStep, type Overview, type OverviewState, type Readiness,
  type ReviewState } from './overview.ts'

/** The Dossier overview (`docs/design.md`, Decisions: Screens): the headline with the next step, the readiness band,
 * the 12 control cards and the evidence quotes, all from `GET /api/overview`. */

type Props = {
  state: OverviewState
  onRetry: () => void
  /** Opens the control's working paper; `assess` starts an assessment there too. */
  onOpen: (controlId: string, assess: boolean) => void
}

const REVIEW_WORDS: Record<Exclude<ReviewState, 'not-assessed'>, string> = {
  'waiting-for-review': 'Proposed',
  accepted: 'Accepted',
  rejected: 'Rejected',
}

export default function Dossier({ state, onRetry, onOpen }: Props) {
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
      <Hero overview={overview} onOpen={onOpen} />
      <Band readiness={overview.readiness} />
      <section className="controls" aria-labelledby="controls-title">
        <div className="head">
          <span className="pill">The controls</span>
          <h2 id="controls-title">Twelve questions</h2>
          <p>Open a control to work through its working paper.</p>
        </div>
        <div className="cards">{overview.controls.map((c) => <Card key={c.id} card={c} onOpen={onOpen} />)}</div>
      </section>
      <Quotes overview={overview} />
    </main>
  )
}

const nothingAssessed = (overview: Overview) => overview.controls.every((c) => c.status === 'not-assessed')

const NEXT_WORDS: Record<NonNullable<NextStep>['action'], (id: string) => string> = {
  review: (id) => `Review ${id}`,
  'assess-again': (id) => `Assess ${id} again`,
  assess: (id) => `Assess ${id}`,
}

function Hero({ overview, onOpen }: { overview: Overview; onOpen: Props['onOpen'] }) {
  const { readiness, next_step: next } = overview
  const waiting = overview.controls.filter((c) => c.status === 'waiting-for-review').length
  const rejected = overview.controls.filter((c) => c.status === 'rejected').length
  let headline: string
  let detail: string
  if (nothingAssessed(overview)) {
    headline = 'Nothing is assessed yet.'
    detail = next ? `Start with ${next.control_id}: assess it, then review what Vouch finds.` : ''
  } else {
    headline = `${readiness.accepted} of ${readiness.controls} controls accepted.`
    detail = `${plural(waiting, 'verdict')} waiting for your review, ${rejected} rejected.`
      + (next ? ` Next: ${NEXT_WORDS[next.action](next.control_id).toLowerCase()}.` : ' Every control is accepted.')
  }
  return (
    <section className="hero">
      <span className="pill">{overview.company} · {overview.country}</span>
      <h1>Prepared for audit. One piece of evidence at a time.</h1>
      <p><b>{headline}</b> {detail}</p>
      {next && (
        <div className="next">
          <button className="btn btn-primary" onClick={() => onOpen(next.control_id, next.action !== 'review')}>
            {NEXT_WORDS[next.action](next.control_id)}
          </button>
        </div>
      )}
    </section>
  )
}

/** Each verdict's count in the readiness figures, in the bar's order; the open rest stays the band's own colour. */
const SEGMENTS = [
  ['evidenced', 'evidenced'],
  ['partly_evidenced', 'partly-evidenced'],
  ['not_evidenced', 'not-evidenced'],
  ['not_applicable', 'not-applicable'],
] as const

/** A verdict as the band says it, from VERDICT_WORDS: the one place verdict words live (M4's verdictWord later). */
const bandWord = (verdict: Verdict) => VERDICT_WORDS[verdict].toLowerCase()

function Band({ readiness: r }: { readiness: Readiness }) {
  // Not applicable gets a tile only when a control has it.
  const counted = SEGMENTS.filter(([key]) => key !== 'not_applicable' || r[key] > 0)
  const words = [...counted.map(([key, verdict]) => `${r[key]} ${bandWord(verdict)}`), `${r.open} open`].join(', ')
  const tiles: [number, string][] = [
    [r.accepted, `accepted of ${r.controls}`],
    ...counted.map(([key, verdict]): [number, string] => [r[key], bandWord(verdict)]),
    [r.open, 'open'],
    [r.remediation_items, 'remediation items'],
  ]
  return (
    <section className="band" aria-labelledby="readiness">
      <div><span className="pill">Where you stand</span><h2 id="readiness">Readiness</h2></div>
      <p>Only verdicts you accepted count; the figures describe evidence coverage, not audit results.</p>
      <div className="readiness" role="img" aria-label={`Accepted verdicts: ${words}, of ${r.controls}`}>
        {SEGMENTS.map(([key, verdict]) => r[key] > 0 && (
          <span key={key} className={`readiness-${verdict}`} style={{ width: `${(r[key] / r.controls) * 100}%` }} />
        ))}
      </div>
      <div className="tiles">
        {tiles.map(([n, label]) => <div className="tile" key={label}><b>{n}</b><span>{label}</span></div>)}
      </div>
    </section>
  )
}

function Card({ card, onOpen }: { card: ControlCard; onOpen: Props['onOpen'] }) {
  const edge = card.verdict ?? 'none'
  return (
    <article className="card" aria-label={`Control ${card.id}`}>
      <a className={`card-head edge-${edge}`} href={controlPath(card.id)} aria-label={`Open ${card.id}`}
        onClick={(e) => { e.preventDefault(); onOpen(card.id, false) }}>
        <b>{card.id}</b>
        {card.status !== 'not-assessed' && <span className={`tag tag-${card.status}`}>{REVIEW_WORDS[card.status]}</span>}
      </a>
      <div className="control-title">{card.question}</div>
      <div className="card-meta">
        <span className="mono">{card.iso_27001.replace(/^ISO 27001:2022:? /, 'ISO ')}</span>
        {card.verdict
          ? <span className={`chip v-${card.verdict}`}>{VERDICT_WORDS[card.verdict]}</span>
          : <span className="chip v-none">Not assessed</span>}
      </div>
    </article>
  )
}

function Quotes({ overview }: { overview: Overview }) {
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
          {overview.quotes.map((q) => (
            <article className="quote" key={`${q.doc_id}|${q.quote}`}>
              <blockquote>{q.quote}</blockquote>
              <footer>
                <span className="mono">{q.doc_id} v{q.version} · {q.section}</span>
                <span>
                  <span className={`kind kind-${q.kind}`}>{q.kind}</span>
                  {q.stale && <span className="flag">stale</span>}
                  {' '}· for <span className="mono">{q.control_id} {q.requirement_id}</span>
                </span>
              </footer>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
