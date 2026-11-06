import { plural, REVIEW_WORDS, type Verdict, VERDICT_WORDS } from './assessment.ts'
import { type ControlCard, controlPath, isoLabel, type NextStep, type Overview, type OverviewState, type Readiness }
  from './overview.ts'
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
}

export default function Dossier({ state, onRetry, onOpen, running }: Props) {
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
      <Hero overview={overview} onOpen={onOpen} running={running} />
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
      <Quotes overview={overview} />
    </main>
  )
}

const NEXT_WORDS: Record<NonNullable<NextStep>['action'], (id: string) => string> = {
  review: (id) => `Review ${id}`,
  'assess-again': (id) => `Assess ${id} again`,
  assess: (id) => `Assess ${id}`,
}

function Hero({ overview, onOpen, running }: { overview: Overview; onOpen: Props['onOpen']; running: string | null }) {
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
  // While a run is in progress (one at a time), the next step opens its paper rather than starting another.
  const assess = next !== null && next.action !== 'review' && running === null
  return (
    <section className="hero">
      <span className="pill">{overview.company} · {overview.country}</span>
      <h1>Prepared for audit. One piece of evidence at a time.</h1>
      <p><b>{headline}</b> {detail}</p>
      {next && (
        <div className="next">
          <button className="btn btn-primary" onClick={() => onOpen(next.control_id, assess)}>
            {next.action === 'review' || assess ? NEXT_WORDS[next.action](next.control_id) : `Open ${next.control_id}`}
          </button>
        </div>
      )}
    </section>
  )
}

/** The verdicts in the readiness bar's order; the open rest stays the band's own colour. */
const SEGMENTS: Verdict[] = ['evidenced', 'partly-evidenced', 'not-evidenced', 'not-applicable']

/** A verdict as the band says it, from VERDICT_WORDS: the one place verdict words live (M4's verdictWord later). */
const bandWord = (verdict: Verdict) => VERDICT_WORDS[verdict].toLowerCase()

function Band({ readiness: r }: { readiness: Readiness }) {
  // Not applicable gets a tile only when a control has it.
  const counted = SEGMENTS.filter((v) => v !== 'not-applicable' || r.by_verdict[v] > 0)
  const words = [...counted.map((v) => `${r.by_verdict[v]} ${bandWord(v)}`), `${r.open} open`].join(', ')
  const tiles: [number, string][] = [
    [r.accepted, `accepted of ${r.controls}`],
    ...counted.map((v): [number, string] => [r.by_verdict[v], bandWord(v)]),
    [r.open, 'open'],
    [r.remediation_items, 'remediation items'],
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

/** A control's card; `language`, the profile's answer language, is its verdict's (#200). */
function Card({ card, onOpen, assessing, language }:
  { card: ControlCard; onOpen: Props['onOpen']; assessing: boolean; language: string }) {
  const edge = card.verdict ?? 'none'
  return (
    <article className="card" aria-label={`Control ${card.id}`}>
      <a className={`card-head edge-${edge}`} href={controlPath(card.id)} aria-label={`Open ${card.id}`}
        onClick={(e) => {
          // The link covers the whole card (#231): a Ctrl/Cmd/Shift/Alt or non-primary click is the browser's (a new
          // tab or window), not an open in place.
          if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return
          e.preventDefault()
          onOpen(card.id, false)
        }}>
        <b>{card.id}</b>
        {card.status !== 'not-assessed' && <span className={`tag tag-${card.status}`}>{REVIEW_WORDS[card.status]}</span>}
      </a>
      <div className="control-title">{card.question}</div>
      <div className="card-meta">
        <span className="mono">{isoLabel(card.iso_27001)}</span>
        <VerdictChip verdict={card.verdict} assessing={assessing} language={language} />
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
          {overview.quotes.map((q, i) => (
            // By position: the quotes are a fixed list, and two may read alike once redacted.
            <article className="quote" key={i}>
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
