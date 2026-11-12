import { type CSSProperties, type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { type Assessment, dateTimeLabel, plural, REVIEW_WORDS, type ReviewStatus, type Verdict, VERDICT_WORDS } from './assessment.ts'
import { confirmFindings, JUDGED_VERDICTS, type JudgedVerdict, type Override, type Refusal, REVIEW_STEP_WORDS,
  REVIEW_STEPS, type Review, type ReviewDecision, type ReviewStep, submitReview } from './review.ts'
import InlineConfirm from './InlineConfirm.tsx'
import { useHeightVar } from './useHeightVar.ts'

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
  /** Whether the lead has changed the verdict or the reason from what the editor opened with (#356); false again
   * when the editor closes. */
  onChanged: (requirementId: string, changed: boolean) => void
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
  useEffect(() => {
    onChanged(requirementId, changed)
    return () => onChanged(requirementId, false) // Save, Cancel or the paper going: nothing typed is left open
  }, [requirementId, changed, onChanged])
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

/**
 * What a final decision's ask says before it is recorded (#634): what goes in the audit trail, and what the decision
 * leads to. A reject's note is the only guidance the next assessment gets (`_note_text` in `vouch.graph`), so a reject
 * with none says so. The ask follows the note as it is typed, without closing.
 */
const decisionAsk = (decision: ReviewDecision['decision'], note: string) => {
  const noted = note.trim() !== ''
  const recorded = noted ? 'Your decision and note are recorded in the audit trail'
    : 'Your decision is recorded in the audit trail'
  if (decision === 'accept') return `Accept this control's verdict? ${recorded}, and the verdict counts towards readiness.`
  return noted ? `Reject this control's verdict? ${recorded}, and the next assessment of this control reads your note.`
    : `Reject this control's verdict with no note? ${recorded}. A note is the only guidance the next assessment of this `
      + 'control gets: write one in Your note first.'
}

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
  const at = REVIEW_STEPS.indexOf(step)
  return (
    <ol className="review-steps" aria-label="Review steps">
      {REVIEW_STEPS.map((s, i) => {
        const words = REVIEW_STEP_WORDS[s]
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

/** The room kept between a focused field and the docked buttons, or the top bar. */
const GAP = 8
/** Rounding the browser's own focus scroll may leave, under which nothing scrolls again: one scroll a focus. */
const SLACK = 2

/** A length the page holds in px, read from the root's computed style; `fallback` where there is none (a test page). */
const rootPx = (read: (style: CSSStyleDeclaration) => string, fallback: number) =>
  parseFloat(read(getComputedStyle(document.documentElement))) || fallback

/** The window's foot the buttons dock above: the page's bottom scroll padding (app.css: the Ask Vouch button's 88px
 * clearance and the safe area), the same length the docked row's `bottom` is, so the two never disagree. */
const foot = () => rootPx((style) => style.scrollPaddingBottom, 88)

/** The sticky top bar's height (`--top-bar`, TopBar). */
const topBar = () => rootPx((style) => style.getPropertyValue('--top-bar'), 0)

/** Whether the buttons' place in the bar ends below the window's foot, so they dock. */
const docks = (slot: HTMLElement) => slot.getBoundingClientRect().bottom > window.innerHeight - foot() + 1

/**
 * The review's buttons in reach (#467, `docs/design.md`, Components: Review bar): while the review is in progress and
 * the buttons' place in the bar (`slot`) ends below the window's foot (above the Ask Vouch button's clearance), the
 * buttons (`dock`) are `stuck` there, over the bar's width (`place`, given to app.css as `--dock-left` and
 * `--dock-width`, which print leaves out), and the slot keeps their room; once the slot comes into view they are back
 * in it. Whether they dock is read before the bar's first paint, so a bar shown afresh (each time its assessment is
 * read again) never blinks back into its place. The docked row's height inside its padding (`--review-actions`) is the
 * slot's room and, in app.css, the scroll margin that keeps a field brought into view above the row; a field focused
 * under the row anyway, or under it as it grows, is scrolled clear of it once (Firefox doesn't scroll a field it
 * counts as in view), never so far that its top goes under the top bar; a field too tall for the room between them
 * keeps its top in view. A field focused by a pointer press isn't scrolled: the press is on what the lead can see, and
 * a scroll before the release would move it from under the pointer, so the click lands elsewhere (#698).
 */
function useDock(reviewing: boolean) {
  const bar = useRef<HTMLElement>(null)
  const slot = useRef<HTMLDivElement>(null)
  const dock = useRef<HTMLDivElement>(null)
  const [stuck, setStuck] = useState(false)
  const [place, setPlace] = useState<{ left: number; width: number } | null>(null)
  useHeightVar(dock, '--review-actions', reviewing, 'content')

  useLayoutEffect(() => {
    const el = slot.current
    if (!reviewing || !el) return
    const check = () => setStuck(docks(el))
    check()
    if (typeof IntersectionObserver === 'undefined') return
    // The observer only says when to look again; the slot is read as it is then, not from the entries.
    const observer = new IntersectionObserver(check, { rootMargin: `0px 0px -${foot()}px 0px`, threshold: [0, 1] })
    observer.observe(el)
    return () => {
      observer.disconnect()
      setStuck(false)
    }
  }, [reviewing])

  useLayoutEffect(() => {
    const el = bar.current
    if (!stuck || !el) return
    const measure = () => {
      const { left, width } = el.getBoundingClientRect()
      setPlace({ left, width })
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(el)
    window.addEventListener('resize', measure)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [stuck])

  useEffect(() => {
    const el = dock.current
    if (!reviewing || !el) return
    const pending = { frame: 0 } // one check a frame, however many focus or resize events come
    const clear = () => {
      cancelAnimationFrame(pending.frame)
      pending.frame = requestAnimationFrame(() => {
        const field = document.activeElement
        const paper = el.closest('.paper')
        if (!el.classList.contains('stuck') || !(field instanceof HTMLElement) || el.contains(field)
          || !paper?.contains(field)) return
        const f = field.getBoundingClientRect()
        const below = topBar() + GAP
        if (f.top >= window.innerHeight || f.height > window.innerHeight - below) return // a whole scrolling table
        const over = below - f.top // its top under the top bar: a field taller than the room, lined up by its foot
        const under = f.bottom + GAP - el.getBoundingClientRect().top
        if (over > SLACK) window.scrollBy(0, -over)
        else if (under > SLACK) window.scrollBy(0, Math.min(under, -over))
      })
    }
    const pointer = { down: false } // a press under way: the focus it gives stays where it is (#698)
    const press = () => { pointer.down = true }
    const release = () => { pointer.down = false }
    const focused = () => { if (!pointer.down) clear() }
    document.addEventListener('pointerdown', press, true)
    document.addEventListener('pointerup', release, true)
    document.addEventListener('pointercancel', release, true)
    document.addEventListener('focusin', focused)
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(clear)
    observer?.observe(el)
    return () => {
      cancelAnimationFrame(pending.frame)
      document.removeEventListener('pointerdown', press, true)
      document.removeEventListener('pointerup', release, true)
      document.removeEventListener('pointercancel', release, true)
      document.removeEventListener('focusin', focused)
      observer?.disconnect()
    }
  }, [reviewing])

  return { bar, slot, dock, stuck, place }
}

/**
 * The review bar at the paper's foot (`docs/design.md`, Components: Review bar). On a proposed control it names the
 * review's three steps and marks the current one (#372). Confirm findings: the overrides made on the rows, recorded
 * with Confirm findings through the findings route. Plan fixes: the remediation items are decided on the rows; the
 * bar counts them, and Accept verdict waits, saying why. Accept: Accept verdict. The note goes with Reject (at any
 * step) or Accept; both ask first in the bar (InlineConfirm, `decisionAsk`, #634), a Retry doesn't. A failure
 * shows the step as not recorded, with Retry; a refused one (422) says why, with nothing to retry; a 409 shows the
 * review moved on, with Refresh. On a reviewed control: who decided and when, the note, and for
 * a rejected one Assess again; an accepted one reviewed before the three steps is marked "before the three-step review"
 * (#373). Shown afresh (a new key) whenever its assessment is read again, so a message about an
 * older read never lingers. While the review is in progress its buttons stay in reach (#467, `useDock`).
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
  const reviewing = status === 'waiting-for-review' && step !== null
  const { bar, slot, dock, stuck, place } = useDock(reviewing)

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

  if (!reviewing) {
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
    <section ref={bar} className="review-bar" aria-label="Review">
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
      <div ref={slot} className={stuck ? 'review-slot holding' : 'review-slot'}>
        <div ref={dock} className={stuck ? 'review-dock stuck' : 'review-dock'}
          style={stuck && place
            ? { '--dock-left': `${place.left}px`, '--dock-width': `${place.width}px` } as CSSProperties : undefined}>
          <div className="review-actions">
            {step === 'confirm-findings' ? (
              <button className="btn btn-primary" disabled={held} aria-busy={sending === 'confirm'}
                onClick={() => void act('confirm')}>Confirm findings</button>
            ) : (
              <InlineConfirm label="Accept verdict" subject="accept" question={decisionAsk('accept', note)}
                confirmDisabled={held} onConfirm={() => void act('accept')}
                trigger={{ className: 'btn btn-sign', disabled: held || planning, 'aria-busy': sending === 'accept',
                  'aria-describedby': planning ? whyId : undefined }} />
            )}
            <InlineConfirm label="Reject verdict" subject="reject" question={decisionAsk('reject', note)}
              confirmDisabled={held} onConfirm={() => void act('reject')}
              trigger={{ className: 'btn', disabled: held, 'aria-busy': sending === 'reject' }} />
          </div>
          {/* What a press answers stays with the buttons, where it was pressed. */}
          {refused && (
            <div className="review-actions">
              <p className="field-note notice" role="alert">{refused.message}</p>
              {refused.refusal === 'conflict' && <button className="btn" onClick={() => void refresh()}>Refresh</button>}
              {refused.refusal === 'failed' && (
                <button className="btn" onClick={() => void act(refused.action)}>Retry</button>
              )}
            </div>
          )}
        </div>
      </div>
      {planning && <p id={whyId} className="field-note">{DECIDE_EACH_ITEM}</p>}
      {editing !== null && <p className="field-note">Save or cancel the override of {editing} first.</p>}
      {notReadAgain && <p className="field-note notice" role="status">{NOT_READ_AGAIN}</p>}
    </section>
  )
}
