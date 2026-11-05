import { type FormEvent, useEffect, useRef, useState } from 'react'
import { CHAT_UNREACHABLE, type PageContext, pageLabel, sendChat } from './chat.ts'

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

/** Patch's chat pattern (design.md, Decisions: Chat): closed (the button), a bubble, enlarged, or pinned beside the
 * page. At 700px or less every open state is a full-screen sheet, and pin and enlarge are not offered. */
export type ChatState = 'closed' | 'bubble' | 'enlarged' | 'pinned'

type Message = { from: 'lead' | 'vouch'; text: string; failed?: boolean }

/** The "Ask Vouch" button, bottom right, and the chat it opens; `context` is the page the lead is on. */
export default function AskVouch({ context }: { context: PageContext }) {
  const [state, setState] = useState<ChatState>('closed')
  const open = state !== 'closed'
  const fabRef = useRef<HTMLButtonElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const sheetRef = useRef<HTMLElement>(null)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setState((s) => (s === 'closed' ? 'bubble' : s))
        inputRef.current?.focus() // already open: straight to the field
      } else if (e.key === 'Escape') setState((s) => (s === 'pinned' ? s : 'closed')) // never a pinned chat
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

  // Pinned, the page makes room for the chat's column (app.css, :root[data-chat="pinned"]).
  useEffect(() => {
    if (state !== 'pinned') return
    document.documentElement.dataset.chat = 'pinned'
    return () => { delete document.documentElement.dataset.chat }
  }, [state])

  // Focus follows the chat: into its field when it opens, back to the button when it closes.
  const wasOpen = useRef(false)
  useEffect(() => {
    if (open && !wasOpen.current) inputRef.current?.focus()
    else if (!open && wasOpen.current) fabRef.current?.focus()
    wasOpen.current = open
  }, [open])

  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [busy, setBusy] = useState(false)
  const sending = useRef(false) // a ref, so Enter twice in one tick still sends once

  async function send(e: FormEvent) {
    e.preventDefault()
    const text = draft.trim()
    if (!text || sending.current) return
    sending.current = true
    setBusy(true)
    setDraft('')
    setMessages((all) => [...all, { from: 'lead', text }, { from: 'vouch', text: '' }])
    const reply = (change: (m: Message) => Message) =>
      setMessages((all) => [...all.slice(0, -1), change(all[all.length - 1])])
    const failed = (message: string) => {
      reply(() => ({ from: 'vouch', text: message, failed: true }))
      setDraft((now) => now || text) // the typed message is kept, to send again
    }
    try {
      for await (const item of sendChat(text, context)) {
        if (item.event === 'token') reply((m) => ({ ...m, text: m.text + item.data.text }))
        else if (item.event === 'error') failed(item.data.message)
      }
    } catch {
      failed(CHAT_UNREACHABLE)
    } finally {
      sending.current = false
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button ref={fabRef} className="fab" onClick={() => setState('bubble')} aria-keyshortcuts="Control+K Meta+K">
        Ask Vouch <kbd className="shortcut-hint">Ctrl K</kbd>
      </button>
    )
  }
  const pinned = state === 'pinned'
  return (
    <aside ref={sheetRef} className={state === 'bubble' ? 'bubble' : `bubble ${state}`} aria-label="Chat with Vouch">
      <div className="bubble-head">
        <b>Vouch</b>
        <span className="page-context">{pageLabel(context)}</span>
        {!sheet && state !== 'bubble' && (
          <button className="btn btn-ghost" aria-pressed={pinned} onClick={() => setState(pinned ? 'enlarged' : 'pinned')}>
            {pinned ? 'Unpin' : 'Pin beside the page'}
          </button>
        )}
        {!sheet && !pinned && (
          <button className="btn btn-ghost" onClick={() => setState(state === 'enlarged' ? 'bubble' : 'enlarged')}>
            {state === 'enlarged' ? 'Make smaller' : 'Make larger'}
          </button>
        )}
        <button className="btn btn-ghost" onClick={() => setState('closed')} aria-label="Close the chat">
          Close
        </button>
      </div>
      <div className="bubble-log" role="log" aria-live="polite">
        {messages.map((m, i) => (
          <p key={i} className={m.from === 'lead' ? 'msg msg-lead' : m.failed ? 'msg notice' : 'msg'}>{m.text}</p>
        ))}
      </div>
      <form className="bubble-foot" onSubmit={(e) => void send(e)}>
        <input ref={inputRef} className="field" type="text" aria-label="Message to Vouch" placeholder="Ask a question"
          autoComplete="off" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button className="btn btn-primary" type="submit" disabled={!draft.trim() || busy} aria-busy={busy}>Send</button>
      </form>
    </aside>
  )
}
