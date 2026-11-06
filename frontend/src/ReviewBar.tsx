import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { type Assessment, plural, REVIEW_WORDS, type ReviewStatus, type Verdict, VERDICT_WORDS } from './assessment.ts'
import { JUDGED_VERDICTS, type JudgedVerdict, type Override, type Refusal, type Review, type ReviewDecision,
  submitReview } from './review.ts'

/** The lead's review on the working paper (#42): the override editor and the review bar at the paper's foot. */

type EditorProps = {
  requirementId: string
  /** The agent's verdict: an override must give another. */
  agentVerdict: Verdict
  /** The override saved before, when it is opened again. */
  saved?: Override
  onSave: (override: Override) => void
  onCancel: () => void
}

/**
 * Overrides one requirement's verdict: a verdict other than the agent's, and a reason. Save stays disabled, saying
 * why, until both are given.
 */
export function OverrideEditor({ requirementId, agentVerdict, saved, onSave, onCancel }: EditorProps) {
  const [verdict, setVerdict] = useState<JudgedVerdict>(
    saved?.verdict ?? JUDGED_VERDICTS.find((v) => v !== agentVerdict) ?? 'evidenced')
  const [reason, setReason] = useState(saved?.reason ?? '')
  const id = useId()
  // The form takes focus on its first field when it opens (#226).
  const first = useRef<HTMLSelectElement>(null)
  useEffect(() => first.current?.focus(), [])
  const why = verdict === agentVerdict ? "Choose a verdict other than Vouch's to override it."
    : reason.trim() === '' ? 'Give a reason to save the override.' : null
  return (
    <form className="override" aria-label={`Override ${requirementId}`} onSubmit={(e) => {
      e.preventDefault()
      if (!why) onSave({ requirement_id: requirementId, verdict, reason: reason.trim() })
    }}>
      <label htmlFor={`${id}-verdict`}>Verdict</label>
      <select ref={first} id={`${id}-verdict`} className="field" value={verdict}
        onChange={(e) => setVerdict(e.target.value as JudgedVerdict)}>
        {JUDGED_VERDICTS.map((v) => <option key={v} value={v}>{VERDICT_WORDS[v]}</option>)}
      </select>
      <label htmlFor={`${id}-reason`}>Reason (required)</label>
      <textarea id={`${id}-reason`} className="field" rows={2} value={reason} required
        onChange={(e) => setReason(e.target.value)} />
      <div className="review-actions">
        <button className="btn btn-primary" type="submit" disabled={why !== null}
          aria-describedby={why ? `${id}-why` : undefined}>Save</button>
        <button className="btn" type="button" onClick={onCancel}>Cancel</button>
        {why && <p id={`${id}-why`} className="field-note">{why}</p>}
      </div>
    </form>
  )
}

/** "4 Nov 2026, 08:30": when a decision was made, in the reader's time zone. */
const decidedAtLabel = (at: string) =>
  new Date(at).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit',
    minute: '2-digit' })

export const NOT_READ_AGAIN = 'The assessment could not be read again just now. Please try again.'

type Decision = ReviewDecision['decision']

type ReviewBarProps = {
  status: ReviewStatus
  threadId: string
  /** The recorded decision, on a reviewed control. */
  review: Review | null | undefined
  overrides: Override[]
  /** The requirement whose override is open, unsaved: Accept and Reject wait for it. */
  editing: string | null
  onReviewed: (assessment: Assessment) => void
  /** Reads the assessment again; false when it couldn't be. */
  onRefresh: () => Promise<boolean>
  /** Assess again for a rejected control, with any note on why it is held. */
  assessAgain: ReactNode
}

/**
 * The review bar at the paper's foot (`docs/design.md`, Components: Review bar). On a proposed control: the lead's
 * note and Accept verdict / Reject verdict, recorded with the overrides through the review route; a failure shows the
 * decision as not made, with Retry; a refused one (422) says why, with nothing to retry; a 409 shows it reviewed
 * already, with Refresh. On a reviewed control: who decided and when, the note, and for a rejected one Assess again.
 * Shown afresh (a new key) whenever its assessment is read again, so a message about an older read never lingers.
 */
export default function ReviewBar(props: ReviewBarProps) {
  const { status, threadId, review, overrides, editing, onReviewed, onRefresh, assessAgain } = props
  const [note, setNote] = useState('')
  const [sending, setSending] = useState<Decision | null>(null)
  const [refused, setRefused] = useState<{ decision: Decision; refusal: Refusal; message: string } | null>(null)
  const [notReadAgain, setNotReadAgain] = useState(false)
  const noteId = useId()

  const decide = async (decision: Decision) => {
    setSending(decision)
    setRefused(null)
    const answer = await submitReview(threadId, { decision, note, overrides })
    setSending(null)
    if (answer.ok) onReviewed(answer.assessment)
    else setRefused({ decision, refusal: answer.refusal, message: answer.message })
  }
  const refresh = async () => {
    setNotReadAgain(false)
    if (!(await onRefresh())) setNotReadAgain(true)
  }

  if (status !== 'waiting-for-review') {
    return (
      <section className="review-bar" aria-label="Review">
        <p className="decided">
          <span className={`tag tag-${status}`}>{REVIEW_WORDS[status]}</span>
          {review?.reviewer_name && review.reviewed_at && (
            <span>by {review.reviewer_name} on {decidedAtLabel(review.reviewed_at)}</span>
          )}
        </p>
        {review?.note && <blockquote className="note">{review.note}</blockquote>}
        {status === 'rejected' && <div className="review-actions">{assessAgain}</div>}
      </section>
    )
  }
  const held = sending !== null || editing !== null
  return (
    <section className="review-bar" aria-label="Review">
      <label className="lbl" htmlFor={noteId}>Your note (kept in the audit trail)</label>
      <textarea id={noteId} className="field" rows={2} value={note} placeholder="Optional: why you accept or reject"
        onChange={(e) => setNote(e.target.value)} />
      {overrides.length > 0 && (
        <p className="field-note">{plural(overrides.length, 'override')}, recorded with your decision.</p>
      )}
      <div className="review-actions">
        <button className="btn btn-sign" disabled={held} aria-busy={sending === 'accept'}
          onClick={() => void decide('accept')}>Accept verdict</button>
        <button className="btn" disabled={held} aria-busy={sending === 'reject'}
          onClick={() => void decide('reject')}>Reject verdict</button>
        {editing !== null && <p className="field-note">Save or cancel the override of {editing} first.</p>}
      </div>
      {refused && (
        <div className="review-actions">
          <p className="field-note notice" role="alert">{refused.message}</p>
          {refused.refusal === 'conflict' && <button className="btn" onClick={() => void refresh()}>Refresh</button>}
          {refused.refusal === 'failed' && (
            <button className="btn" onClick={() => void decide(refused.decision)}>Retry</button>
          )}
        </div>
      )}
      {notReadAgain && <p className="field-note notice" role="status">{NOT_READ_AGAIN}</p>}
    </section>
  )
}
