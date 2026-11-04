import { useEffect, useRef, useState } from 'react'

// Must match the sheet breakpoint in app.css.
const SHEET_QUERY = '(max-width: 700px)'
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'

const isSheet = () => window.matchMedia?.(SHEET_QUERY).matches ?? false

/** While the bubble is a full-screen sheet, Tab and Shift+Tab cycle inside it, wherever focus starts:
 * the page behind can't be seen. Listens on the document, so a click that leaves focus on the body is covered. */
function keepFocusInSheet(e: KeyboardEvent, sheet: HTMLElement | null) {
  if (e.key !== 'Tab' || !sheet || !isSheet()) return
  const inside = [...sheet.querySelectorAll<HTMLElement>(FOCUSABLE)]
  if (inside.length === 0) return
  const at = inside.indexOf(document.activeElement as HTMLElement)
  if (at === -1) {
    e.preventDefault()
    inside[e.shiftKey ? inside.length - 1 : 0].focus()
    return
  }
  const step = e.shiftKey ? -1 : 1
  e.preventDefault()
  inside[(at + step + inside.length) % inside.length].focus()
}

/** The "Ask Vouch" button, bottom right, and the chat bubble it opens (empty until M2). */
export default function AskVouch() {
  const [open, setOpen] = useState(false)
  const fabRef = useRef<HTMLButtonElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const sheetRef = useRef<HTMLElement>(null)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(true)
      } else if (e.key === 'Escape') setOpen(false)
      else keepFocusInSheet(e, sheetRef.current)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // While the sheet covers the page, the page behind it (the sheet's siblings) is inert:
  // nothing there takes focus or clicks, and assistive tech skips it. Follows the width while open.
  const [sheet, setSheet] = useState(isSheet)
  useEffect(() => {
    const query = window.matchMedia?.(SHEET_QUERY)
    const onChange = () => setSheet(isSheet())
    query?.addEventListener?.('change', onChange)
    return () => query?.removeEventListener?.('change', onChange)
  }, [])
  useEffect(() => {
    const aside = sheetRef.current
    if (!open || !sheet || !aside?.parentElement) return
    const behind = [...aside.parentElement.children].filter((el) => el !== aside && !el.hasAttribute('inert'))
    for (const el of behind) el.setAttribute('inert', '')
    return () => { for (const el of behind) el.removeAttribute('inert') }
  }, [open, sheet])

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
    <aside ref={sheetRef} className="bubble" aria-label="Chat with Vouch">
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
