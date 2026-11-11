import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { type Assessment, dateTimeLabel, plural, REVIEW_WORDS, type ReviewStatus, type Verdict, VERDICT_WORDS } from './assessment.ts'
import { confirmFindings, JUDGED_VERDICTS, type JudgedVerdict, type Override, type Refusal, REVIEW_STEPS,
  type Review, type ReviewDecision, type ReviewStep, submitReview } from './review.ts'

/** The lead's review on the working paper (#42, in three steps since #372): the override editor and the review bar at
 * the paper's foot. */

type EditorProps = {
  requirementId: string
  /** The agent's verdict: an override must give another. */
  agentVerdict: Verdict
  /** The override saved before, when it is opened again. */
  saved?: Override
  onSave: (override: Override) => void
  onCancel: () => void
  /** Whether the lead has changed the verdict or the reason from what the editor opened with (#356). */
  onChanged?: (changed: boolean) => void
}

/**
 * Overrides one requirement's verdict: a verdict other than the agent's, and a reason. Save stays disabled, saying
 * why, until both are given.
 */
export function OverrideEditor({ requirementId, agentVerdict, saved, onSave, onCancel, onChanged }: EditorProps) {
  const [opened] = useState(() => ({
    verdict: saved?.verdict ?? JUDGED_VERDICTS.find((v) => v !== agentVerdict) ?? 'evidenced',
    reason: saved?.reason ?? '',
  }))
  const [verdict, setVerdict] = useState<JudgedVerdict>(opened.verdict)
  const [reason, setReason] = useState(opened.reason)
  const changed = verdict !== opened.verdict || reason !== opened.reason
  useEffect(() => onChanged?.(changed), [changed, onChanged])
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

export const NOT_READ_AGAIN = 'The assessment could not be read again just now. Please try again.'
export const DECIDE_EACH_ITEM = 'Approve or discard each remediation item to accept the verdict.'

/** What the bar sends: the findings (Confirm findings), or a decision. */
type Action = 'confirm' | ReviewDecision['decision']

type ReviewBarProps = {
  status: ReviewStatus
  threadId: string
  /** The recorded decision, on a reviewed control. */
  review: Review | null | undefined
  /** Reviewed before the three-step review (#373): an accepted one is marked so. */
  beforeThreeStep?: boolean
  /** The step a proposed control's review waits at (`review.ts`'s `reviewStep`). */
  step: ReviewStep | null
  /** The overrides saved on the rows and not yet recorded: they go with Confirm findings. */
  overrides: Override[]
  /** The requirement whose override is open, unsaved: Confirm findings and Reject wait for it. */
  editing: string | null
  /** The control's remediation items: how many the lead has decided, of all. */
  items: { decided: number; total: number }
  /** A step is recorded: the assessment as it now stands (at its next step, or reviewed). */
  onReviewed: (assessment: Assessment) => void
  /** Reads the assessment again; false when it couldn't be. */
  onRefresh: () => Promise<boolean>
  /** Assess again for a rejected control, with any note on why it is held. */
  assessAgain: ReactNode
}

/** The three steps, in order, the current one marked (`aria-current="step"`); Plan fixes skipped when nothing is. */
function Steps({ step, items }: { step: ReviewStep; items: ReviewBarProps['items'] }) {
  const at = REVIEW_STEPS.findIndex(([s]) => s === step)
  return (
    <ol className="review-steps" aria-label="Review steps">
      {REVIEW_STEPS.map(([s, words], i) => {
        const skipped = s === 'plan-fixes' && step === 'accept' && items.total === 0
        return (
          <li key={s} aria-current={i === at ? 'step' : undefined} className={i < at ? 'done' : undefined}>
            {skipped ? `${words}: skipped, nothing to fix` : words}
          </li>
        )
      })}
    </ol>
  )
}

/**
 * The review bar at the paper's foot (`docs/design.md`, Components: Review bar). On a proposed control it names the
 * review's three steps and marks the current one (#372). Confirm findings: the overrides made on the rows, recorded
 * with Confirm findings through the findings route. Plan fixes: the remediation items are decided on the rows; the
 * bar counts them, and Accept verdict waits, saying why. Accept: Accept verdict. The note goes with Reject (at any
 * step) or Accept. A failure shows the step as not recorded, with Retry; a refused one (422) says why, with nothing to
 * retry; a 409 shows the review moved on, with Refresh. On a reviewed control: who decided and when, the note, and for
 * a rejected one Assess again; an accepted one reviewed before the three steps is marked "before the three-step review"
 * (#373). Shown afresh (a new key) whenever its assessment is read again, so a message about an
 * older read never lingers.
 */
export default function ReviewBar(props: ReviewBarProps) {
  const { status, threadId, review, beforeThreeStep, step, overrides, editing, items, onReviewed, onRefresh,
    assessAgain } = props
  const [note, setNote] = useState('')
  const [sending, setSending] = useState<Action | null>(null)
  const [refused, setRefused] = useState<{ action: Action; refusal: Refusal; message: string } | null>(null)
  const [notReadAgain, setNotReadAgain] = useState(false)
  const noteId = useId()
  const whyId = useId()

  const act = async (action: Action) => {
    setSending(action)
    setRefused(null)
    const answer = action === 'confirm' ? await confirmFindings(threadId, overrides)
      : await submitReview(threadId, { decision: action, note })
    setSending(null)
    if (answer.ok) onReviewed(answer.assessment)
    else setRefused({ action, refusal: answer.refusal, message: answer.message })
  }
  const refresh = async () => {
    setNotReadAgain(false)
    if (!(await onRefresh())) setNotReadAgain(true)
  }

  if (status !== 'waiting-for-review' || step === null) {
    return (
      <section className="review-bar" aria-label="Review">
        <p className="decided">
          <span className={`tag tag-${status}`}>{REVIEW_WORDS[status]}</span>
          {review?.reviewer_name && review.reviewed_at && (
            <span>by {review.reviewer_name} on {dateTimeLabel(review.reviewed_at)}</span>
          )}
          {status === 'accepted' && beforeThreeStep && <span className="field-note">before the three-step review</span>}
        </p>
        {review?.note && <blockquote className="note">{review.note}</blockquote>}
        {status === 'rejected' && <div className="review-actions">{assessAgain}</div>}
      </section>
    )
  }
  const held = sending !== null || editing !== null
  const planning = step === 'plan-fixes'
  return (
    <section className="review-bar" aria-label="Review">
      <Steps step={step} items={items} />
      <label className="lbl" htmlFor={noteId}>Your note (kept in the audit trail)</label>
      <textarea id={noteId} className="field" rows={2} value={note} onChange={(e) => setNote(e.target.value)}
        placeholder={step === 'confirm-findings' ? 'Optional: why you reject' : 'Optional: why you accept or reject'} />
      {step === 'confirm-findings' && overrides.length > 0 && (
        <p className="field-note">{plural(overrides.length, 'override')}, recorded with the findings.</p>
      )}
      {items.total > 0 && step !== 'confirm-findings' && (
        <p className="field-note">{items.decided} of {plural(items.total, 'remediation item')} decided.</p>
      )}
      <div className="review-actions">
        {step === 'confirm-findings' ? (
          <button className="btn btn-primary" disabled={held} aria-busy={sending === 'confirm'}
            onClick={() => void act('confirm')}>Confirm findings</button>
        ) : (
          <button className="btn btn-sign" disabled={held || planning} aria-busy={sending === 'accept'}
            aria-describedby={planning ? whyId : undefined} onClick={() => void act('accept')}>Accept verdict</button>
        )}
        <button className="btn" disabled={held} aria-busy={sending === 'reject'}
          onClick={() => void act('reject')}>Reject verdict</button>
        {planning && <p id={whyId} className="field-note">{DECIDE_EACH_ITEM}</p>}
        {editing !== null && <p className="field-note">Save or cancel the override of {editing} first.</p>}
      </div>
      {refused && (
        <div className="review-actions">
          <p className="field-note notice" role="alert">{refused.message}</p>
          {refused.refusal === 'conflict' && <button className="btn" onClick={() => void refresh()}>Refresh</button>}
          {refused.refusal === 'failed' && (
            <button className="btn" onClick={() => void act(refused.action)}>Retry</button>
          )}
        </div>
      )}
      {notReadAgain && <p className="field-note notice" role="status">{NOT_READ_AGAIN}</p>}
    </section>
  )
}
