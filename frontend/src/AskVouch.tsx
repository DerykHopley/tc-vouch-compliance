import { type FormEvent, useEffect, useRef, useState } from 'react'
import { CHAT_UNREACHABLE, type PageContext, pageLabel, sendChat } from './chat.ts'

// Must match the breakpoints in app.css: the sheet at 700px or less; the pinned column only over 1000px.
const SHEET_QUERY = '(max-width: 700px)'
const NO_PIN_QUERY = '(max-width: 1000px)'
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'

const isSheet = () => window.matchMedia?.(SHEET_QUERY).matches ?? false
const canPin = () => !(window.matchMedia?.(NO_PIN_QUERY).matches ?? false)

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
 * page (only over 1000px). At 700px or less every open state is a full-screen sheet, and pin and enlarge are not
 * offered. */
export type ChatState = 'closed' | 'bubble' | 'enlarged' | 'pinned'

/** Each state's look and the header's toggles: the size toggle's label and the state it leads to, and the state the
 * pin toggle leads to (null: not offered). */
const STATES: Record<ChatState, { className: string; size: { label: string; to: ChatState } | null; pin: ChatState | null }> = {
  closed: { className: '', size: null, pin: null },
  bubble: { className: 'bubble', size: { label: 'Make larger', to: 'enlarged' }, pin: null },
  enlarged: { className: 'bubble enlarged', size: { label: 'Make smaller', to: 'bubble' }, pin: 'pinned' },
  pinned: { className: 'bubble pinned', size: null, pin: 'enlarged' },
}

type Message = { from: 'lead' | 'vouch'; text: string; failed?: boolean }

/** The "Ask Vouch" button, bottom right, and the chat it opens; `context` is the page the compliance lead is on. */
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
      } else if (e.key === 'Escape') {
        // Never a pinned chat beside the page; a full-screen sheet always closes.
        setState((s) => (s === 'pinned' && !isSheet() ? s : 'closed'))
      } else keepFocusInSheet(e, sheetRef.current)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // The width, followed while open: at 700px or less the sheet; at 1000px or less no pin, and a pinned chat falls back
  // to enlarged.
  const [sheet, setSheet] = useState(isSheet)
  const [pinnable, setPinnable] = useState(canPin)
  useEffect(() => {
    const queries = [SHEET_QUERY, NO_PIN_QUERY].map((q) => window.matchMedia?.(q))
    const onChange = () => {
      setSheet(isSheet())
      setPinnable(canPin())
      if (!canPin()) setState((s) => (s === 'pinned' ? 'enlarged' : s))
    }
    for (const query of queries) query?.addEventListener?.('change', onChange)
    return () => { for (const query of queries) query?.removeEventListener?.('change', onChange) }
  }, [])

  // While the sheet covers the page, the page behind it (the sheet's siblings) is inert:
  // nothing there takes focus or clicks, and assistive tech skips it.
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
  const { className, size, pin } = STATES[state]
  return (
    <aside ref={sheetRef} className={className} aria-label="Chat with Vouch">
      <div className="bubble-head">
        <b>Vouch</b>
        <span className="page-context">{pageLabel(context)}</span>
        {!sheet && pinnable && pin && (
          <button className="btn btn-ghost" aria-pressed={state === 'pinned'} onClick={() => setState(pin)}>
            Pin beside the page
          </button>
        )}
        {!sheet && size && (
          <button className="btn btn-ghost" onClick={() => setState(size.to)}>{size.label}</button>
        )}
        <button className="btn btn-ghost" onClick={() => setState('closed')} aria-label="Close the chat">
          Close
        </button>
      </div>
      {/* Busy while a reply streams, so assistive tech announces the finished message, not each piece. */}
      <div className="bubble-log" role="log" aria-live="polite" aria-busy={busy}>
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
