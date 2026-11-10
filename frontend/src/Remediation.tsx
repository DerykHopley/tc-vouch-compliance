import { useId, useRef, useState } from 'react'
import { dateWords } from './overview.ts'
import {
  decideItem, DONE_NOTE_LENGTH, type ItemDecision, ITEM_WORDS, markDone, NO_OWNER, type Priority, PRIORITY_WORDS, type RemediationItem,
  setPriority,
} from './remediation.ts'

/** The improvement plan's part of an item (#410): shown in the Internal improvement goal only. */
export type Plan = {
  /** The control is accepted: its approved items can be marked done. */
  accepted: boolean
}

type Props = {
  item: RemediationItem
  threadId: string
  /** The control's findings are confirmed (Plan fixes, #372), or it was accepted: its drafts can be approved or
   * discarded. */
  decidable: boolean
  /** The decision, a priority change or a done mark is recorded: the item as kept. */
  onDecided: (item: RemediationItem) => void
  /** The profile's answer language, the draft's: its text is marked with it (#244). */
  language?: string
  /** In the Internal improvement goal, the plan (#410); undefined under Audit readiness, which shows none of it. */
  plan?: Plan
}

const PRIORITIES: Priority[] = ['high', 'medium', 'low']
const SAY_WHAT = 'Say what was done first.'

/**
 * The remediation item of a requirement not or partly evidenced (#212) (`docs/design.md`, Components: Remediation item): draft (approve,
 * discard), approved, discarded. Once the findings are confirmed a draft's owner role and due date can be changed before
 * Approve; a refusal (a past due date, an item decided in another tab) shows the server's reason. One decision is
 * sent at a time, so a double click approves once. Where Vouch suggested no owner, the field starts empty and Approve
 * waits for one. Its fields start from the item: the paper gives it a new key whenever the item changes.
 *
 * In the Internal improvement goal (#410) the head names its priority; an approved item not done has a Priority select
 * that sends the change at once, and, once its control is accepted, Mark done with what was done (final). A done item
 * reads Done, with the date and the lead's note.
 */
export default function Remediation({ item, threadId, decidable, onDecided, language, plan }: Props) {
  const [owner, setOwner] = useState(item.owner_role === NO_OWNER ? '' : item.owner_role)
  const [due, setDue] = useState(item.due_date)
  const [sending, setSending] = useState<ItemDecision['decision'] | null>(null)
  const [refused, setRefused] = useState<{ message: string; invalid: boolean } | null>(null)
  const busy = useRef(false) // a ref, so two clicks in one tick still send one decision
  const id = useId()
  const req = item.requirement_id

  const decide = async (decision: ItemDecision['decision']) => {
    if (busy.current) return
    busy.current = true
    setSending(decision)
    setRefused(null)
    const sent: ItemDecision = decision === 'approve' ? { decision, owner_role: owner.trim(), due_date: due }
      : { decision }
    const answer = await decideItem(threadId, req, sent)
    if (answer.ok) { // held until the paper shows the kept item, which has nothing left to decide
      onDecided(answer.item)
      return
    }
    busy.current = false
    setSending(null)
    setRefused({ message: answer.message, invalid: answer.invalid })
  }

  const open = item.status === 'draft' && decidable
  const noOwner = owner.trim() === '' || owner.trim() === NO_OWNER
  const done = plan && item.status === 'approved' ? item.done ?? null : null
  const tag = done ? 'Done' : ITEM_WORDS[item.status]
  return (
    <section className="remediation" aria-label={`Remediation item ${req}`}>
      <p className="remediation-head">
        <span className="lbl">Remediation item</span>
        <span className={`tag${item.status === 'approved' ? ' tag-accepted' : ''}`}>{tag}</span>
        {plan && item.priority && <span className="why">Priority: {PRIORITY_WORDS[item.priority]}</span>}
      </p>
      <p className="remediation-missing" lang={language}>{item.missing}</p>
      {open ? (
        <form className="override" aria-label={`Decide the remediation item of ${req}`}
          onSubmit={(e) => { e.preventDefault(); void decide('approve') }}>
          <label htmlFor={`${id}-owner`}>Owner role</label>
          <input id={`${id}-owner`} className="field" value={owner} placeholder="A role, such as CISO"
            aria-describedby={noOwner ? `${id}-owner-why` : undefined} onChange={(e) => setOwner(e.target.value)} />
          <label htmlFor={`${id}-due`}>Due date</label>
          <input id={`${id}-due`} className="field" type="date" value={due} aria-invalid={refused?.invalid ?? false}
            aria-describedby={refused?.invalid ? `${id}-why` : undefined} onChange={(e) => setDue(e.target.value)} />
          <div className="review-actions">
            <button className="btn btn-sign" type="submit" disabled={sending !== null || noOwner}
              aria-busy={sending === 'approve'} aria-label={`Approve ${req}`}>Approve</button>
            <button className="btn" type="button" disabled={sending !== null} aria-busy={sending === 'discard'}
              aria-label={`Discard ${req}`} onClick={() => void decide('discard')}>Discard</button>
          </div>
          {noOwner && <p id={`${id}-owner-why`} className="field-note">Name the role that owns the fix to approve it.</p>}
          {refused && <p id={`${id}-why`} className="field-note notice" role="alert">{refused.message}</p>}
        </form>
      ) : (
        <>
          <p className="why">Owner: {item.owner_role} · Due {item.due_date}</p>
          {item.status === 'draft' && <p className="why">Approve or discard it once the findings are confirmed.</p>}
          {done && <p className="why done-note">Done on {dateWords(done.at)}: {done.note}</p>}
          {plan && item.status === 'approved' && !done && (
            <ItemPlan item={item} threadId={threadId} accepted={plan.accepted} onMarked={onDecided} />
          )}
        </>
      )}
    </section>
  )
}

/** An approved item's plan, not yet done (#410): its Priority select, and Mark done once its control is accepted. */
function ItemPlan({ item, threadId, accepted, onMarked }:
  { item: RemediationItem; threadId: string; accepted: boolean; onMarked: (item: RemediationItem) => void }) {
  const [changing, setChanging] = useState<Priority | null>(null)
  const [marking, setMarking] = useState(false) // the done form is open
  const [note, setNote] = useState('')
  const [sendingDone, setSendingDone] = useState(false)
  const [refused, setRefused] = useState<string | null>(null)
  const busy = useRef(false) // one request at a time, whatever the clicks
  const id = useId()
  const req = item.requirement_id
  const kept = item.priority ?? 'high'

  const change = async (priority: Priority) => {
    if (busy.current) return
    busy.current = true
    setChanging(priority)
    setRefused(null)
    const answer = await setPriority(threadId, req, priority)
    busy.current = false
    setChanging(null)
    if (answer.ok) onMarked(answer.item)
    else setRefused(answer.message) // the select shows the kept value again
  }

  const send = async () => {
    if (busy.current) return
    if (note.trim() === '') {
      setRefused(SAY_WHAT)
      return
    }
    busy.current = true
    setSendingDone(true)
    setRefused(null)
    const answer = await markDone(threadId, req, note.trim())
    busy.current = false
    setSendingDone(false)
    if (answer.ok) onMarked(answer.item)
    else setRefused(answer.message)
  }

  const why = refused && <p id={`${id}-why`} className="field-note notice" role="alert">{refused}</p>
  return (
    <div className="item-plan">
      <label htmlFor={`${id}-priority`}>Priority</label>
      <select id={`${id}-priority`} className="field" value={changing ?? kept} disabled={changing !== null || sendingDone}
        aria-busy={changing !== null} onChange={(e) => void change(e.target.value as Priority)}>
        {PRIORITIES.map((p) => <option key={p} value={p}>{PRIORITY_WORDS[p]}</option>)}
      </select>
      {accepted && !marking && (
        <div className="review-actions">
          <button className="btn btn-ghost" type="button" aria-label={`Mark ${req} done`}
            disabled={changing !== null} onClick={() => { setMarking(true); setRefused(null) }}>Mark done</button>
        </div>
      )}
      {accepted && marking ? (
        <form className="override" aria-label={`Mark the remediation item of ${req} done`} noValidate
          onSubmit={(e) => { e.preventDefault(); void send() }}>
          <label htmlFor={`${id}-note`}>What was done</label>
          <textarea id={`${id}-note`} className="field" required value={note} maxLength={DONE_NOTE_LENGTH}
            aria-invalid={refused === SAY_WHAT} aria-describedby={refused ? `${id}-why` : undefined}
            onChange={(e) => setNote(e.target.value)} />
          <div className="review-actions">
            <button className="btn btn-primary" type="submit" disabled={sendingDone} aria-busy={sendingDone}>Mark done</button>
            <button className="btn" type="button" disabled={sendingDone}
              onClick={() => { setMarking(false); setNote(''); setRefused(null) }}>Cancel</button>
          </div>
          {why}
        </form>
      ) : why}
    </div>
  )
}
