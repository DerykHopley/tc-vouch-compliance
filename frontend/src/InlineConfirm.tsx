import { type ButtonHTMLAttributes, type KeyboardEvent, type MouseEvent as ReactMouseEvent, useEffect, useRef, useState } from 'react'
import { swallowPointerClickBurst } from './doubleClickGuard.ts'

/** The trigger's own attributes: its class (a ghost pill unless given), `disabled` (can't be pressed or focused),
 * `aria-disabled` (can't be pressed, keeps focus: busy), `aria-busy`, `aria-label`. */
type Trigger = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick' | 'children' | 'type'>

type Props = {
  /** The trigger's words, and the confirm button's unless `confirmLabel` says otherwise. */
  label: string
  confirmLabel?: string
  cancelLabel?: string
  /** What the ask says, in a `field-note` alert. */
  question: string
  onConfirm: () => void
  /** The confirm button can't be pressed just now (one run at a time, a change already under way). */
  confirmDisabled?: boolean
  /** False: the trigger confirms in one press, with no ask. */
  ask?: boolean
  /** What the ask is about (by default its question): when it changes while open, the ask closes, so nobody confirms a
   * question that changed under them. */
  subject?: string
  trigger?: Trigger
  /** Given the trigger button as it mounts (and null as it goes), for a caller that moves focus to it. */
  triggerRef?: (el: HTMLButtonElement | null) => void
}

/**
 * The in-app ask (#441; `docs/design.md`, Components: Goal, and Review bar's Assess again, #437): a button which,
 * pressed, gives its place to the question, the confirm button (primary) and Cancel (ghost). Never the browser's
 * confirm. Focus moves to Cancel as it opens; Cancel, or Esc inside it (which goes no further, so an open chat or the
 * phone bar stays open), closes it with focus back on the button, as does confirming. While the trigger is
 * `aria-disabled` it opens nothing but keeps focus, so a busy control holds the keyboard user's place. After a pointer
 * press on the confirm, the second click of a double-click presses nothing (#527).
 */
export default function InlineConfirm({ label, confirmLabel = label, cancelLabel = 'Cancel', question, onConfirm,
  confirmDisabled = false, ask = true, subject = question, trigger = {}, triggerRef }: Props) {
  const [open, setOpen] = useState(false)
  // What the ask is about changed while it was open: it closes as this render's state, not after it.
  const [about, setAbout] = useState(subject)
  if (about !== subject) {
    setAbout(subject)
    setOpen(false)
  }
  // The pressed button leaves the page as the ask opens or closes: focus follows, so a keyboard user isn't left on
  // the page's body.
  const wasOpen = useRef(false)
  const cancel = useRef<HTMLButtonElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (wasOpen.current === open) return
    wasOpen.current = open
    ;(open ? cancel : button).current?.focus()
  }, [open])
  if (open) {
    const confirm = (e: ReactMouseEvent<HTMLButtonElement>) => {
      swallowPointerClickBurst(e)
      setOpen(false)
      onConfirm()
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      setOpen(false)
    }
    return (
      <div className="inline-confirm" onKeyDown={onKeyDown}>
        <p className="field-note" role="alert">{question}</p>
        <div className="review-actions">
          <button type="button" className="btn btn-primary" disabled={confirmDisabled}
            onClick={confirm}>
            {confirmLabel}
          </button>
          <button type="button" ref={cancel} className="btn btn-ghost" onClick={() => setOpen(false)}>
            {cancelLabel}
          </button>
        </div>
      </div>
    )
  }
  const held = trigger.disabled === true || trigger['aria-disabled'] === true || trigger['aria-disabled'] === 'true'
  const press = () => {
    if (held) return
    if (ask) setOpen(true)
    else onConfirm()
  }
  return (
    <button type="button" ref={(el) => {
      button.current = el
      triggerRef?.(el)
    }} className="btn btn-ghost" {...trigger} onClick={press}>{label}</button>
  )
}
