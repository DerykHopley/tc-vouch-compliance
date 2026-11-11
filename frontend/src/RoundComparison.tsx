import { type ReactNode, useLayoutEffect, useState } from 'react'
import { VERDICT_WORDS } from './assessment.ts'
import { controlPath, dateWords, openInPlace, type Overview, SEGMENTS } from './overview.ts'
import { areas } from './paper.ts'
import {
  acceptedWord, CHANGE_WORDS, changesFirst, type ControlChange, keepPlace, placeOf, type RoundComparison, useComparison,
} from './roundComparison.ts'
import { COMPANY_PATH } from './rounds.ts'

/** What the view names controls, requirements and questions by: the overview's, when read. */
export type ControlNames = Pick<Overview, 'answer_language'> & {
  controls: Pick<Overview['controls'][number], 'id' | 'question'>[]
  requirements: Record<string, { id: string; text: string }[]>
  questions?: Record<string, { id: number; question: string }[]>
}

type Props = {
  /** The closed round compared with: its number in the order opened. */
  number: number
  names?: ControlNames
  /** Opens a control's working paper, whose Back returns here; with the round's name for its way back. */
  onOpen: (controlId: string, roundName: string) => void
  /** Back to the Company page's Rounds section. */
  onRounds: () => void
}

/**
 * The round comparison (#573; `docs/design.md`, Components: Round comparison view) at `/company/rounds/<number>`: now
 * against one closed review round, a summary on top, then the controls by chapter, changes first and the unchanged ones
 * folded. Choosing a control opens its working paper; coming back, the folds and the scroll are as they were. Both
 * goals; never called progress.
 */
export default function RoundComparisonView({ number, names, onOpen, onRounds }: Props) {
  const { state, tryAgain } = useComparison(number)
  const back = (
    <a className="btn btn-ghost crumb" href={COMPANY_PATH} onClick={(e) => openInPlace(e, onRounds)}>← Rounds</a>
  )
  if (state.status === 'loading') {
    return <main className="compare-view wrap">{back}<p className="lbl" role="status">Loading the round comparison…</p></main>
  }
  if (state.status !== 'ready') {
    return (
      <main className="compare-view wrap">
        {back}
        <section className="sheet notice">
          <p role="alert" className="answer">{state.message}</p>
          {state.status === 'failed'
            ? <button className="btn" onClick={tryAgain}>Try again</button>
            : <a className="compare-back" href={COMPANY_PATH} onClick={(e) => openInPlace(e, onRounds)}>Back to the rounds</a>}
        </section>
      </main>
    )
  }
  return <Loaded comparison={state.comparison} names={names} onOpen={onOpen} back={back} />
}

function Loaded({ comparison, names, onOpen, back }: { comparison: RoundComparison; names?: ControlNames;
  onOpen: Props['onOpen']; back: ReactNode }) {
  const { round } = comparison
  const [unfolded, setUnfolded] = useState(() => placeOf(round.number).unfolded)
  useLayoutEffect(() => {
    const { scrollY } = placeOf(round.number)
    if (scrollY === null) return
    keepPlace(round.number, { scrollY: null })
    window.scrollTo(0, scrollY)
  }, [round.number])
  const fold = (area: string, open: boolean) => {
    const next = open ? [...new Set([...unfolded, area])] : unfolded.filter((a) => a !== area)
    if (next.length === unfolded.length && next.every((a) => unfolded.includes(a))) return
    setUnfolded(next)
    keepPlace(round.number, { unfolded: next })
  }
  const open = (controlId: string) => {
    keepPlace(round.number, { scrollY: window.scrollY })
    onOpen(controlId, round.name)
  }
  const chapters = areas(comparison.controls.map((c) => ({ ...c, id: c.control_id })))
  return (
    <main className="compare-view wrap">
      {back}
      <div className="company-head">
        <span className="pill">Closed {round.closed_at ? dateWords(round.closed_at) : ''}</span>
        <h1>Compared with {round.name}</h1>
      </div>
      <Summary comparison={comparison} />
      {chapters.map(([area, controls]) => {
        const { changed, unchanged } = changesFirst(controls)
        const id = `chapter-${controls[0].chapter}`
        return (
          <section key={area} className="sheet compare-chapter" aria-labelledby={id}>
            <h2 id={id}>{area}</h2>
            {changed.length > 0 && <Rows rows={changed} comparison={comparison} names={names} onOpen={open} />}
            {unchanged.length > 0 && (
              <details open={unfolded.includes(area)} onToggle={(e) => fold(area, e.currentTarget.open)}>
                <summary>{unchanged.length} unchanged</summary>
                <Rows rows={unchanged} comparison={comparison} names={names} onOpen={open} />
              </details>
            )}
          </section>
        )
      })}
    </main>
  )
}

function Summary({ comparison }: { comparison: RoundComparison }) {
  const { round, changes, readiness, controls } = comparison
  const scope = (['new-since', 'out-of-scope-since'] as const)
    .map((k) => [k, controls.filter((c) => c.change === k).length] as const)
  const verdicts = readiness.by_verdict
  const counted = SEGMENTS.filter((v) => v !== 'not-applicable' || verdicts[v].now > 0 || verdicts[v].then > 0)
  const nothing = changes.better + changes.worse + changes.new + scope[0][1] + scope[1][1] === 0
  return (
    <section className="sheet compare-summary" aria-label="Summary">
      <ul className="compare-counts" aria-label="Changes">
        {(['better', 'worse', 'new', 'unchanged'] as const).map((k) => (
          <li key={k}>{CHANGE_WORDS[k]} <b>{changes[k]}</b></li>
        ))}
      </ul>
      {scope.some(([, n]) => n > 0) && (
        <ul className="compare-counts" aria-label="Scope changes">
          {scope.map(([k, n]) => <li key={k}>{CHANGE_WORDS[k]} <b>{n}</b></li>)}
        </ul>
      )}
      <p>Accepted: {readiness.accepted.then} when {round.name} closed → {readiness.accepted.now} now</p>
      <ul className="compare-counts" aria-label="Accepted by verdict">
        {counted.map((v) => <li key={v}>{VERDICT_WORDS[v]}: {verdicts[v].then} → {verdicts[v].now}</li>)}
      </ul>
      {nothing && <p>Nothing has changed since {round.name} closed.</p>}
    </section>
  )
}

function Rows({ rows, comparison, names, onOpen }: { rows: ControlChange[]; comparison: RoundComparison;
  names?: ControlNames; onOpen: (controlId: string) => void }) {
  const language = names?.answer_language
  return (
    <table className="report-table compare-controls">
      <thead>
        <tr><th scope="col">Control</th><th scope="col">Then → now</th><th scope="col">Change</th>
          <th scope="col">Since</th></tr>
      </thead>
      <tbody>
        {rows.map((c) => {
          const card = names?.controls.find((k) => k.id === c.control_id)
          const label = <span><span className="mono">{c.control_id}</span>{card && <> <span lang={language}>{card.question}</span></>}</span>
          const done = comparison.remediation_done.filter((d) => d.control_id === c.control_id)
          const answered = comparison.questions_answered.filter((a) => a.control_id === c.control_id)
          return (
            <tr key={c.control_id}>
              <th scope="row" data-label="Control">
                {card
                  ? <a href={controlPath(c.control_id)} onClick={(e) => openInPlace(e, () => onOpen(c.control_id))}>{label}</a>
                  : label}
              </th>
              <td data-label="Then → now"><span>{acceptedWord(c.before)} → {acceptedWord(c.after)}</span></td>
              <td data-label="Change">
                <span>{CHANGE_WORDS[c.change]}{c.proposed && <span className="pill compare-proposed">Proposed, waiting for review</span>}</span>
              </td>
              <td data-label="Since">
                {(done.length > 0 || answered.length > 0) && (
                  <ul className="compare-since">
                    {done.map((d) => {
                      const text = names?.requirements[d.control_id]?.find((r) => r.id === d.requirement_id)?.text
                      return (
                        <li key={`d-${d.requirement_id}`}>Done since: <span className="mono">{d.requirement_id}</span>
                          {text && <> <span lang={language}>{text}</span></>}</li>
                      )
                    })}
                    {answered.map((a) => {
                      const text = names?.questions?.[a.control_id]?.find((q) => q.id === a.question_id)?.question
                      return <li key={`a-${a.question_id}`}>Answered since: {text ? <span lang={language}>{text}</span> : `question ${a.question_id}`}</li>
                    })}
                  </ul>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
