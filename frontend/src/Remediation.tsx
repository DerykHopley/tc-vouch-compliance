import { useId, useRef, useState } from 'react'
import { decideItem, type ItemDecision, ITEM_WORDS, type RemediationItem } from './remediation.ts'

type Props = {
  item: RemediationItem
  threadId: string
  /** The control's verdict is accepted: its drafts can be approved or discarded. */
  decidable: boolean
  /** The decision is recorded: the item as kept. */
  onDecided: (item: RemediationItem) => void
}

/**
 * A not-evidenced requirement's remediation item (`docs/design.md`, Components: Remediation item): draft (approve,
 * discard), approved, discarded. On an accepted control a draft's owner role and due date can be changed before
 * Approve; a refusal (a past due date, an item decided in another tab) shows the server's reason. One decision is
 * sent at a time, so a double click approves once.
 */
export default function Remediation({ item, threadId, decidable, onDecided }: Props) {
  const [owner, setOwner] = useState(item.owner_role)
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
  return (
    <section className="remedy" aria-label={`Remediation item ${req}`}>
      <p className="remedy-head">
        <span className="lbl">Remediation item</span>
        <span className={`tag${item.status === 'approved' ? ' tag-accepted' : ''}`}>{ITEM_WORDS[item.status]}</span>
      </p>
      <p className="remedy-missing">{item.missing}</p>
      {open ? (
        <form className="override" aria-label={`Decide the remediation item of ${req}`}
          onSubmit={(e) => { e.preventDefault(); void decide('approve') }}>
          <label htmlFor={`${id}-owner`}>Owner role</label>
          <input id={`${id}-owner`} className="field" value={owner} onChange={(e) => setOwner(e.target.value)} />
          <label htmlFor={`${id}-due`}>Due date</label>
          <input id={`${id}-due`} className="field" type="date" value={due} aria-invalid={refused?.invalid ?? false}
            aria-describedby={refused?.invalid ? `${id}-why` : undefined} onChange={(e) => setDue(e.target.value)} />
          <div className="review-actions">
            <button className="btn btn-sign" type="submit" disabled={sending !== null || owner.trim() === ''}
              aria-busy={sending === 'approve'} aria-label={`Approve ${req}`}>Approve</button>
            <button className="btn" type="button" disabled={sending !== null} aria-busy={sending === 'discard'}
              aria-label={`Discard ${req}`} onClick={() => void decide('discard')}>Discard</button>
          </div>
          {refused && <p id={`${id}-why`} className="field-note notice" role="alert">{refused.message}</p>}
        </form>
      ) : (
        <>
          <p className="why">Owner: {item.owner_role} · Due {item.due_date}</p>
          {item.status === 'draft' && <p className="why">Approve or discard it once the verdict is accepted.</p>}
        </>
      )}
    </section>
  )
}
