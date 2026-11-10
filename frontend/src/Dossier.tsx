import { type MouseEvent, useEffect, useId, useRef, useState } from 'react'
import { CLOSED_WORD, plural, REVIEW_WORDS, type Verdict, VERDICT_WORDS } from './assessment.ts'
import { changeGoal, closeRound, type ControlCard, controlPath, dateWords, GOAL_WORDS, isoLabel, isReviewed, type NextStep, type Overview,
  type OverviewState, type Readiness, type ReassessReason, ROUND_NAME_LENGTH, type RoundChange, type RoundComparison, SEGMENTS, startRound }
  from './overview.ts'
import type { PassState } from './profilePass.ts'
import type { Goal } from './report.ts'
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
  /** The app opened on a company just made (#409): the headline takes focus once it shows. */
  focusHeading?: boolean
  /** The page's profile pass (#390): held above the views, so its result waits here. */
  pass: PassState
  /** Starts a profile pass. */
  onPass: () => void
  /** The goal was changed to `goal` (#369): reads the overview again, so the hero shows the new one; whether it was
   * read. */
  onGoalChanged: (goal: Goal) => Promise<boolean>
  /** A review round was started or closed (#402): reads the overview again; whether it was read. */
  onRoundChanged: () => Promise<boolean>
  /** The arrival sequence on a company just made is under way (#447): the Goal change or the pass is still to come, so
   * Change goal and Run a profile pass wait. */
  arriving?: boolean
  /** The Goal chosen in company setup that couldn't be set on arrival (#447): said under the Goal line until a later
   * Goal change goes through. */
  goalNotSet?: Goal
}

export default function Dossier({ state, onRetry, onOpen, running, focusHeading = false, pass, onPass, onGoalChanged,
  onRoundChanged, arriving = false, goalNotSet }: Props) {
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
      <Hero overview={overview} onOpen={onOpen} running={running} focus={focusHeading} pass={pass} onPass={onPass}
        onGoalChanged={onGoalChanged} onRoundChanged={onRoundChanged} arriving={arriving} goalNotSet={goalNotSet} />
      <PassPanel overview={overview} pass={pass} onOpen={onOpen} onPass={onPass} running={running} />
      <Band readiness={overview.readiness} />
      <Comparison overview={overview} onOpen={onOpen} />
      <section className="controls" aria-labelledby="controls-title">
        <div className="head">
          <span className="pill">The controls</span>
          <h2 id="controls-title">{overview.controls.length} controls</h2>
          <p>Open a control to work through its working paper.</p>
        </div>
        <div className="cards">
          {overview.controls.map((c) => (
            <Card key={c.id} card={c} onOpen={onOpen} assessing={c.id === running} language={overview.answer_language}
              improving={overview.goal === 'internal-improvement'} />
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

type HeroProps = { overview: Overview; onOpen: Props['onOpen']; running: string | null; focus: boolean;
  pass: PassState; onPass: () => void; onGoalChanged: Props['onGoalChanged']; onRoundChanged: Props['onRoundChanged'];
  arriving: boolean; goalNotSet?: Goal }

function Hero({ overview, onOpen, running, focus, pass, onPass, onGoalChanged, onRoundChanged, arriving,
  goalNotSet }: HeroProps) {
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    if (focus) heading.current?.focus()
  }, [focus])
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
      <GoalRow overview={overview} onChanged={onGoalChanged} arriving={arriving} notSet={goalNotSet} />
      <RoundRow overview={overview} onChanged={onRoundChanged} />
      <h1 ref={heading} tabIndex={-1}>Prepared for audit. One piece of evidence at a time.</h1>
      <p><b>{headline}</b> {detail}</p>
      {(next || readiness.open > 0) && (
        <div className="next">
          {next && (
            <button className="btn btn-primary" onClick={() => onOpen(next.control_id, assess)}>
              {next.action === 'review' || assess ? NEXT_WORDS[next.action](next.control_id) : `Open ${next.control_id}`}
            </button>
          )}
          {readiness.open > 0 && (
            <button className="btn btn-ghost" onClick={onPass} disabled={passing || arriving || running !== null}
              aria-busy={passing || arriving || undefined} aria-describedby="pass-note">
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
            : 'Assesses every open control in one run. Accepted controls and reviews in progress are skipped. On Sonnet 5.5, a pass over 12 controls costs about $0.30–0.50.'}
        </p>)}
    </section>
  )
}

const GOAL_FAILED = 'The goal could not be changed just now. Please try again.'
const GOAL_STALE = 'The goal was changed, but the overview could not be read again just now.'

/**
 * The profile's goal on the hero (`docs/design.md`, Components: Goal, #369): "Goal: <goal>" and a ghost Change goal,
 * which asks first (the browser's own confirm, as the fresh start does) and changes it to the other goal. The change
 * starts no run, so it stays usable while one goes on. A failure says so, and the line keeps the old goal. On a company
 * just made (#447), it waits while the arrival sequence changes the Goal (`arriving`); `notSet`: the Goal chosen in
 * setup couldn't be set then, said until a later change goes through.
 */
function GoalRow({ overview, onChanged, arriving, notSet }: { overview: Overview; onChanged: Props['onGoalChanged'];
  arriving: boolean; notSet?: Goal }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  // The change was kept but the overview wasn't read again: the line shows the new goal; Retry reads it again.
  const [stale, setStale] = useState<Goal | null>(null)
  const reread = async (goal: Goal) => setStale(await onChanged(goal) ? null : goal)
  const other = overview.goal === 'audit-readiness' ? 'internal-improvement' : 'audit-readiness'
  const change = async () => {
    if (busy) return
    const question = `Change this profile's goal to ${GOAL_WORDS[other]}? The change is recorded in the audit trail.`
    if (!window.confirm(question)) return
    setBusy(true)
    setFailed(false)
    setStale(null)
    try {
      const result = await changeGoal(other)
      if (result === 'changed') await reread(other)
      else if (result === 'failed') setFailed(true)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <div className="goal">
        <span>Goal: {GOAL_WORDS[overview.goal]}</span>
        <button className="btn btn-ghost" onClick={() => void change()} disabled={busy || arriving}
          aria-busy={busy || arriving || undefined}>
          Change goal
        </button>
      </div>
      {notSet !== undefined && (
        <p role="alert" className="field-note notice">
          The company was created, but its Goal could not be set to {GOAL_WORDS[notSet]}. Use Change goal to set it.
        </p>
      )}
      {failed && <p role="alert" className="field-note notice">{GOAL_FAILED}</p>}
      {stale !== null && (
        <div className="next">
          <p role="alert" className="field-note notice">{GOAL_STALE}</p>
          <button className="btn" onClick={() => void reread(stale)}>Retry</button>
        </div>
      )}
    </>
  )
}

const ROUND_FAILED = 'The review round could not be changed just now. Please try again.'
const ROUND_STALE = 'The review round was changed, but the overview could not be read again just now.'
const NAME_FIRST = 'Name the round first.'

/**
 * The review round on the hero (`docs/design.md`, Components: Review round, #402), under the goal: the open round
 * with a ghost Close round, which asks first (the browser's own confirm, as Change goal does); with none open, a name
 * field and a ghost Start round. Either starts no run, so it stays usable while one goes on. Afterwards the overview
 * is read again; a refusal (409) says the server's sentence, any other failure says so.
 */
function RoundRow({ overview, onChanged }: { overview: Overview; onChanged: Props['onRoundChanged'] }) {
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  // The change was kept but the overview wasn't read again: Retry reads it again.
  const [stale, setStale] = useState(false)
  // A blank name refused in the browser marks the field, described by the message (as DeveloperPage's fields).
  const messageId = useId()
  const unnamed = message === NAME_FIRST
  const reread = async () => setStale(!await onChanged())
  const send = async (request: () => Promise<RoundChange>) => {
    setBusy(true)
    setMessage(null)
    setStale(false)
    try {
      const answer = await request()
      if (answer.result === 'changed') {
        setName('')
        await reread()
      } else if (answer.result === 'refused') {
        setMessage(answer.message)
        await reread()
      } else if (answer.result === 'failed') setMessage(ROUND_FAILED)
    } finally {
      setBusy(false)
    }
  }
  const current = overview.rounds.current
  const close = () => {
    if (busy || current === null) return
    const question = `Close the review round "${current.name}"? Its results are frozen, and the overview compares `
      + 'later work with them. The close is recorded in the audit trail.'
    if (!window.confirm(question)) return
    void send(closeRound)
  }
  const start = () => {
    if (busy) return
    const trimmed = name.trim()
    if (trimmed === '') {
      setStale(false)
      setMessage(NAME_FIRST)
      return
    }
    void send(() => startRound(trimmed))
  }
  return (
    <>
      {current !== null ? (
        <div className="round">
          <span>
            Review round: {current.name}, open since{' '}
            {current.opened_at === null ? 'the start' : dateWords(current.opened_at)}
          </span>
          <button className="btn btn-ghost" onClick={close} disabled={busy} aria-busy={busy || undefined}>
            Close round
          </button>
        </div>
      ) : (
        <form className="round" onSubmit={(e) => { e.preventDefault(); start() }}>
          <span>No review round open.</span>
          <input className="field" type="text" aria-label="Name of the next round" placeholder="Name of the next round"
            value={name} onChange={(e) => setName(e.target.value)} maxLength={ROUND_NAME_LENGTH}
            aria-invalid={unnamed || undefined} aria-describedby={unnamed ? messageId : undefined} />
          <button className="btn btn-ghost" type="submit" disabled={busy} aria-busy={busy || undefined}>
            Start round
          </button>
        </form>
      )}
      {message !== null && <p id={messageId} role="alert" className="field-note notice">{message}</p>}
      {stale && (
        <div className="next">
          <p role="alert" className="field-note notice">{ROUND_STALE}</p>
          <button className="btn" onClick={() => void reread()}>Retry</button>
        </div>
      )}
    </>
  )
}

/** A count's change since the compared round, in words: "(+2)", "(-1)", "(no change)". */
const change = (now: number, then: number) =>
  now === then ? '(no change)' : `(${now > then ? '+' : '-'}${Math.abs(now - then)})`

/** An accepted verdict, or none, as the comparison names it. */
const acceptedWord = (verdict: Verdict | null) => verdict === null ? 'Not accepted' : VERDICT_WORDS[verdict]

/**
 * Now against the last closed review round (`docs/design.md`, Components: Round comparison, #402): accepted verdicts
 * only, as readiness counts them, then each control whose accepted verdict changed, its link opening its working
 * paper in place (as a Control card's). Never called progress.
 */
function Comparison({ overview, onOpen }: { overview: Overview; onOpen: Props['onOpen'] }) {
  const comparison: RoundComparison | null = overview.rounds.comparison
  if (comparison === null) {
    return (
      <section className="controls compare" aria-labelledby="compare-title">
        <div className="head">
          <span className="pill">Review rounds</span>
          <h2 id="compare-title">Compared with the last round</h2>
        </div>
        <p className="sheet empty">
          {overview.rounds.current === null
            // None open and none closed since the fresh start: the last one closed before it.
            ? 'No review round has been closed since the fresh start. Start the next round, and once it is closed the '
              + 'overview compares later work with it.'
            : 'No closed review round yet. Close this one to compare the next with it.'}
        </p>
      </section>
    )
  }
  const { against, accepted, by_verdict: verdicts, changed } = comparison
  const counted = SEGMENTS.filter((v) => v !== 'not-applicable' || verdicts[v].now > 0 || verdicts[v].then > 0)
  return (
    <section className="controls compare" aria-labelledby="compare-title">
      <div className="head">
        <span className="pill">Since {dateWords(against.closed_at)}</span>
        <h2 id="compare-title">Compared with {against.name}</h2>
        <p>Accepted: {accepted.now} now, {accepted.then} when {against.name} closed.</p>
      </div>
      <div className="sheet">
        <ul className="compare-counts">
          {counted.map((v) => (
            <li key={v}>{VERDICT_WORDS[v]} {verdicts[v].now} {change(verdicts[v].now, verdicts[v].then)}</li>
          ))}
        </ul>
        {changed.length === 0 ? <p>Nothing has changed since {against.name} closed.</p> : (
          <>
            <h3 className="prose-h">Changed controls</h3>
            <ul className="compare-rows">
              {changed.map((c) => (
                <li key={c.control_id}>
                  <a href={controlPath(c.control_id)} onClick={(e) => openInPlace(e, () => onOpen(c.control_id, false))}>
                    <span className="mono">{c.control_id}</span>: {acceptedWord(c.before)} → {acceptedWord(c.after)}
                  </a>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  )
}

/** Whether a verdict leaves something to evidence, so the control may be worth assessing again on its own. */
const shortOf = (verdict: Verdict | null) => verdict === 'not-evidenced' || verdict === 'partly-evidenced'

/**
 * The profile pass's result (`docs/design.md`, Components: Profile pass): what waits for review, with a row per new
 * assessment read from its card as it stands now (a review since shows), or why the pass didn't run and the way on.
 * Nothing while the pass is idle or running. While a per-control assessment runs (`running`), it starts nothing: it
 * offers to open a control rather than assess it, holds Try again, and says why.
 */
function PassPanel({ overview, pass, onOpen, onPass, running }:
  { overview: Overview; pass: PassState; onOpen: Props['onOpen']; onPass: () => void; running: string | null }) {
  if (pass.kind === 'idle' || pass.kind === 'running') return null
  const cards = new Map(overview.controls.map((c) => [c.id, c]))
  const held = running !== null && <p className="field-note">Vouch is assessing {running}. One run at a time.</p>
  let body
  if (pass.kind === 'done') {
    // The controls skipped, accepted ones and reviews in progress each on a line of their own (#437).
    const accepted = pass.skipped.filter((s) => s.reason === 'accepted').map((s) => s.control_id)
    const inReview = pass.skipped.filter((s) => s.reason === 'in-review').map((s) => s.control_id)
    const skipped = <>
      {accepted.length > 0 && (
        <p className="field-note">{plural(accepted.length, 'accepted control')} skipped: {accepted.join(', ')}.</p>
      )}
      {inReview.length > 0 && (
        <p className="field-note">
          {plural(inReview.length, 'control')} in review skipped: {inReview.join(', ')}.
          {' '}Finish their review, or assess one again from its paper.
        </p>
      )}
    </>
    body = pass.assessments.length === 0 ? (
      <>
        <p>
          {inReview.length > 0 ? 'Every applicable control is accepted or in review. Nothing was assessed.'
            : 'Every applicable control is accepted already. Nothing was assessed.'}
        </p>
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
                {isReviewed(status) && <span className={`tag tag-${status}`}>{REVIEW_WORDS[status]}</span>}
                <span className="pass-actions">
                  <button className="btn" onClick={() => onOpen(a.control_id, false)}>Open {a.control_id}</button>
                  {shortOf(verdict) && running === null && (
                    // On a control waiting for review it opens the paper and starts nothing (#437): the card may not
                    // know the review moved on (another tab), so the paper's Assess again decides, warning first.
                    <button className="btn btn-ghost" onClick={() => onOpen(a.control_id, status !== 'waiting-for-review')}>
                      Assess {a.control_id} again
                    </button>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
        {held}
      </>
    )
  } else if (pass.kind === 'refused' || pass.kind === 'limit') {
    // The per-control agent is the way on, with its own limit: from the first control not assessed or rejected.
    const first = overview.controls.find((c) => c.status === 'not-assessed' || c.status === 'rejected')
    body = (
      <>
        <p role="alert">{pass.message}</p>
        {first && (running === null
          ? <button className="btn btn-primary" onClick={() => onOpen(first.id, true)}>Assess {first.id} on its own</button>
          : <button className="btn" onClick={() => onOpen(first.id, false)}>Open {first.id}</button>)}
        {held}
      </>
    )
  } else {
    body = (
      <>
        <p role="alert">{pass.message}</p>
        <button className="btn" onClick={onPass} disabled={running !== null}>Try again</button>
        {held}
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

/** A card's line for each reason a re-assessment is suggested (#410, #414). */
const REASON_WORDS: Record<ReassessReason, string> = {
  'fixes-done': 'Fixes done: assess again',
  'question-answered': 'Question answered: assess again',
  'question-overdue': 'Question overdue: assess again',
}

/** A control's card; `language`, the profile's answer language, is its verdict's (#200). */
function Card({ card, onOpen, assessing, language, improving }:
  { card: ControlCard; onOpen: Props['onOpen']; assessing: boolean; language: string; improving: boolean }) {
  const edge = card.verdict ?? 'none'
  // The improvement plan (#410): a Closed card reads Closed in the accepted tag's style (the word is the cue), and a
  // card whose fixes are all done suggests assessing it again; so does one whose open question was answered or is
  // overdue (#414), in both goals.
  const closed = improving && card.status === 'accepted' && card.closed === true
  const reasons = (card.reassess_reasons ?? (card.reassess_suggested ? ['fixes-done' as const] : []))
    .filter((r) => improving || r !== 'fixes-done')
  return (
    <article className="card" aria-label={`Control ${card.id}`}>
      <a className={`card-head edge-${edge}`} href={controlPath(card.id)} aria-label={`Open ${card.id}`}
        onClick={(e) => openInPlace(e, () => onOpen(card.id, false))}>
        <b>{card.id}</b>
        {isReviewed(card.status) && (
          <span className={`tag tag-${card.status}`}>{closed ? CLOSED_WORD : REVIEW_WORDS[card.status]}</span>
        )}
      </a>
      <div className="control-title" lang={language}>{card.question}</div>
      {card.status === 'not-applicable' && card.not_applicable && (
        <p className="field-note" lang={language}>{card.not_applicable}</p>
      )}
      {reasons.map((r) => <p key={r} className="field-note">{REASON_WORDS[r]}</p>)}
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
