import { useEffect, useRef, useState } from 'react'

/** The "Ask Vouch" button, bottom right, and the chat bubble it opens (empty until M2). */
export default function AskVouch() {
  const [open, setOpen] = useState(false)
  const button = useRef<HTMLButtonElement>(null)
  const close = useRef<HTMLButtonElement>(null)

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
  const opened = useRef(false)
  useEffect(() => {
    if (open) close.current?.focus()
    else if (opened.current) button.current?.focus()
    opened.current = open
  }, [open])

  if (!open) {
    return (
      <button ref={button} className="fab" onClick={() => setOpen(true)} aria-keyshortcuts="Control+K Meta+K">
        Ask Vouch <kbd className="wide">Ctrl K</kbd>
      </button>
    )
  }
  return (
    <aside className="bubble" aria-label="Chat with Vouch">
      <div className="bubble-head">
        <b>Vouch</b>
        <span className="ctx">on Dossier</span>
        <button ref={close} className="btn btn-ghost" onClick={() => setOpen(false)} aria-label="Close the chat">
          Close
        </button>
      </div>
      <div className="bubble-log" />
    </aside>
  )
}
