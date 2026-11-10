import { useEffect, useId, useRef, useState } from 'react'
import InlineConfirm from './InlineConfirm.tsx'
import { changeGoal, closeRound, dateWords, GOAL_WORDS, type Overview, ROUND_NAME_LENGTH, type RoundChange, startRound }
  from './overview.ts'
import type { Goal } from './report.ts'

/** The company's Goal and review round controls (`docs/design.md`, Components: Goal and Review round), shared by the
 * Dossier's hero and the Company page (#441, #505). Each asks first in the app (`InlineConfirm`), never with the
 * browser's own confirm. */

const GOAL_FAILED = 'The goal could not be changed just now. Please try again.'
const GOAL_STALE = 'The goal was changed, but the overview could not be read again just now.'

/**
 * The profile's goal (`docs/design.md`, Components: Goal, #369): "Goal: <goal>" and a ghost Change goal, which asks
 * first in the app (#441) and changes it to the other goal. The change starts no run, so it stays usable while one goes
 * on. A failure says so, and the line keeps the old goal. On a company just made (#447), it waits while the arrival
 * sequence changes the Goal (`arriving`); `notSet`: the Goal chosen in setup couldn't be set then, said until a later
 * change goes through. `onChanged` reads the overview again after a change; whether it was read.
 */
export function GoalControl({ overview, onChanged, arriving, notSet }: { overview: Pick<Overview, 'goal'>;
  onChanged: (goal: Goal) => Promise<boolean>; arriving: boolean; notSet?: Goal }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  // The change was kept but the overview wasn't read again: the line shows the new goal; Retry reads it again.
  const [stale, setStale] = useState<Goal | null>(null)
  const reread = async (goal: Goal) => setStale(await onChanged(goal) ? null : goal)
  const other = overview.goal === 'audit-readiness' ? 'internal-improvement' : 'audit-readiness'
  const change = async () => {
    if (busy || arriving) return
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
        {/* While the change runs the button is aria-disabled, not disabled, so it keeps the focus the ask gave it. */}
        <InlineConfirm label="Change goal" onConfirm={() => void change()} confirmDisabled={busy || arriving}
          trigger={{ disabled: arriving, 'aria-disabled': busy || undefined, 'aria-busy': busy || arriving || undefined }}
          question={`Change this profile's goal to ${GOAL_WORDS[other]}? The change is recorded in the audit trail.`} />
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
 * The review round (`docs/design.md`, Components: Review round, #402): the open round with a ghost Close round, which
 * asks first in the app (#441, as Change goal does); with none open, a name field and a ghost Start round. Either
 * starts no run, so it stays usable while one goes on. Afterwards the overview is read again (`onChanged`: whether it
 * was read); a refusal (409) says the server's sentence, any other failure says so.
 */
export function RoundControl({ overview, onChanged }: { overview: Pick<Overview, 'rounds'>;
  onChanged: () => Promise<boolean> }) {
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  // The change was kept but the overview wasn't read again: Retry reads it again.
  const [stale, setStale] = useState(false)
  // A blank name refused in the browser marks the field, described by the message (as DeveloperPage's fields).
  const messageId = useId()
  const unnamed = message === NAME_FIRST
  // A round closed here: once the line swaps to the next round's form, focus goes to its name field, not the body.
  const closing = useRef(false)
  const nameField = useRef<HTMLInputElement>(null)
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
      } else if (answer.result === 'failed') {
        closing.current = false
        setMessage(ROUND_FAILED)
      }
    } finally {
      setBusy(false)
    }
  }
  const current = overview.rounds.current
  useEffect(() => {
    if (current !== null || !closing.current) return
    closing.current = false
    nameField.current?.focus()
  }, [current])
  const close = () => {
    if (busy || current === null) return
    closing.current = true
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
          <InlineConfirm label="Close round" onConfirm={close} confirmDisabled={busy}
            trigger={{ 'aria-disabled': busy || undefined, 'aria-busy': busy || undefined }}
            subject={`${current.name} ${current.opened_at}`}
            question={`Close the review round "${current.name}"? Its results are frozen, and the overview compares `
              + 'later work with them. The close is recorded in the audit trail.'} />
        </div>
      ) : (
        <form className="round" onSubmit={(e) => { e.preventDefault(); start() }}>
          <span>No review round open.</span>
          <input ref={nameField} className="field" type="text" aria-label="Name of the next round" placeholder="Name of the next round"
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
