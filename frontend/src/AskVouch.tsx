import { type KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from 'react'

// Must match the sheet breakpoint in app.css.
const SHEET_QUERY = '(max-width: 700px)'
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'

/** While the bubble is a full-screen sheet, Tab and Shift+Tab cycle inside it: the page behind can't be seen. */
function keepFocusInSheet(e: ReactKeyboardEvent<HTMLElement>) {
  if (e.key !== 'Tab' || !window.matchMedia?.(SHEET_QUERY).matches) return
  const inside = [...e.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE)]
  if (inside.length === 0) return
  const at = inside.indexOf(document.activeElement as HTMLElement)
  const step = e.shiftKey ? -1 : 1
  e.preventDefault()
  inside[(at + step + inside.length) % inside.length].focus()
}

/** The "Ask Vouch" button, bottom right, and the chat bubble it opens (empty until M2). */
export default function AskVouch() {
  const [open, setOpen] = useState(false)
  const fabRef = useRef<HTMLButtonElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(true)
      } else if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // Focus follows the bubble: into it when it opens, back to the button when it closes.
  const wasOpen = useRef(false)
  useEffect(() => {
    if (open) closeRef.current?.focus()
    else if (wasOpen.current) fabRef.current?.focus()
    wasOpen.current = open
  }, [open])

  if (!open) {
    return (
      <button ref={fabRef} className="fab" onClick={() => setOpen(true)} aria-keyshortcuts="Control+K Meta+K">
        Ask Vouch <kbd className="shortcut-hint">Ctrl K</kbd>
      </button>
    )
  }
  return (
    <aside className="bubble" aria-label="Chat with Vouch" onKeyDown={keepFocusInSheet}>
      <div className="bubble-head">
        <b>Vouch</b>
        <span className="page-context">on Dossier</span>
        <button ref={closeRef} className="btn btn-ghost" onClick={() => setOpen(false)} aria-label="Close the chat">
          Close
        </button>
      </div>
      <div className="bubble-log" />
    </aside>
  )
}
