import { type FormEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { REVIEW_WORDS, type ReviewStatus, type Step, type Verdict, VERDICT_WORDS } from './assessment.ts'
import {
  CHAT_UNREACHABLE, GREETING_UNREACHABLE, type PageContext, pageLabel, sendChat, suggestionLabel, suggestionMessage,
} from './chat.ts'
import { controlPath, type NextStep } from './overview.ts'

// Must match the breakpoints in app.css: the sheet at 700px or less; the pinned column only over 1000px.
const SHEET_QUERY = '(max-width: 700px)'
const NO_PIN_QUERY = '(max-width: 1000px)'
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'

/** How close to the log's bottom (px) still counts as reading the newest message. */
const NEAR_BOTTOM = 48
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

/** A line in the chat: the lead's message, Vouch's reply (or a notice that it failed), an assessment's step, or an
 * assessment's verdict with the way to its working paper. */
type Message =
  | { from: 'lead' | 'vouch'; text: string; failed?: boolean }
  | { from: 'step'; step: Step }
  | { from: 'verdict'; controlId: string; verdict: Verdict; status: ReviewStatus }

type OnOpen = ((controlId: string) => void) | undefined

/** A link to a control's working paper: shown in the app (`onOpen`), or followed as a link without it. */
function PaperLink({ controlId, onOpen, className, children }:
  { controlId: string; onOpen: OnOpen; className?: string; children: ReactNode }) {
  return (
    <a className={className} href={controlPath(controlId)}
      onClick={(e) => { if (onOpen) { e.preventDefault(); onOpen(controlId) } }}>
      {children}
    </a>
  )
}

/** An assessment's step, as the working paper's trace shows it: its node, then what it did; a guard step marked
 * apart (design.md, Agent steps). */
function StepLine({ step }: { step: Step }) {
  return <p className={step.node === 'guard' ? 'msg msg-step guard' : 'msg msg-step'}><b>{step.node}</b> {step.text}</p>
}

/** The verdict an assessment from the chat ended with, its review state tag, and "Open the working paper". */
function VerdictLine({ controlId, verdict, status, onOpen }:
  { controlId: string; verdict: Verdict; status: ReviewStatus; onOpen: OnOpen }) {
  return (
    <p className="msg">
      {controlId}: <span className={`chip v-${verdict}`}>{VERDICT_WORDS[verdict]}</span>{' '}
      <span className={`tag tag-${status}`}>{REVIEW_WORDS[status]}</span>{' '}
      <PaperLink controlId={controlId} onOpen={onOpen}>Open the working paper</PaperLink>
    </p>
  )
}

type Props = {
  /** The page the compliance lead is on. */
  context: PageContext
  /** Shows a control's working paper (a link from the chat). */
  onOpen?: (controlId: string) => void
  /** An assessment the chat started has its verdict. */
  onAssessed?: (controlId: string) => void
}

/** The "Ask Vouch" button, bottom right, and the chat it opens: on first opening, Vouch's greeting (where the lead
 * stands, and the next step as a chip). */
export default function AskVouch({ context, onOpen, onAssessed }: Props) {
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
  const [suggestion, setSuggestion] = useState<NonNullable<NextStep> | null>(null)
  const [busy, setBusy] = useState(false)
  const sending = useRef(false) // a ref, so Enter twice in one tick still sends once
  const threadId = useRef<string | null>(null) // the chat thread, named first in the first reply
  const greeted = useRef(false) // asked for the greeting
  const greetAgain = useRef(false) // it failed: asked for again on the next opening

  // The log follows the newest message while the lead is at, or near, its bottom; scrolled up to read, it stays put.
  const logRef = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const noteWhereRead = () => {
    const log = logRef.current
    if (log) following.current = log.scrollHeight - log.scrollTop - log.clientHeight <= NEAR_BOTTOM
  }
  useLayoutEffect(() => {
    const log = logRef.current
    if (log && following.current) log.scrollTop = log.scrollHeight
  }, [messages, suggestion, busy, open])

  /** Sends `text` (null: asks for the greeting) and shows the reply as it streams: Vouch's words, an assessment's
   * steps and its verdict, the next step as a chip. A failure is a notice; a typed message is kept, to send again. */
  async function converse(text: string | null) {
    if (sending.current) return
    sending.current = true
    setBusy(true)
    setSuggestion(null)
    if (text !== null) setMessages((all) => [...all, { from: 'lead', text }])
    const add = (m: Message) => setMessages((all) => [...all, m])
    const write = (piece: string) => setMessages((all) => {
      const last = all[all.length - 1]
      return last?.from === 'vouch' && !last.failed
        ? [...all.slice(0, -1), { ...last, text: last.text + piece }] : [...all, { from: 'vouch', text: piece }]
    })
    const failed = (message: string) => {
      add({ from: 'vouch', text: message, failed: true })
      if (text !== null) setDraft((now) => now || text)
      else greetAgain.current = true
    }
    // A finished assessment's verdict line, with its link, is the last thing the turn adds: after Vouch's words on it.
    const verdicts: Message[] = []
    try {
      for await (const item of sendChat(text, context, threadId.current)) {
        if (item.event === 'token') write(item.data.text)
        else if (item.event === 'thread') threadId.current = item.data.thread_id
        else if (item.event === 'step') add({ from: 'step', step: item.data })
        else if (item.event === 'suggestion') setSuggestion(item.data)
        else if (item.event === 'assessment') {
          verdicts.push({ from: 'verdict', controlId: item.data.control_id, verdict: item.data.verdict, status: item.data.status })
          onAssessed?.(item.data.control_id)
        } else if (item.event === 'error') failed(item.data.message)
      }
    } catch {
      failed(text === null ? GREETING_UNREACHABLE : CHAT_UNREACHABLE)
    } finally {
      for (const verdict of verdicts) add(verdict)
      sending.current = false
      setBusy(false)
    }
  }

  // Vouch greets the lead the first time the chat opens: where they stand, and the next step. A greeting that failed
  // is asked for again the next time the chat opens (never over and over while it stays open).
  useEffect(() => {
    if (!open && greetAgain.current) {
      greetAgain.current = false
      greeted.current = false
    }
    if (!open || greeted.current) return
    greeted.current = true
    void converse(null)
  })

  function send(e: FormEvent) {
    e.preventDefault()
    const text = draft.trim()
    if (!text || sending.current) return
    setDraft('')
    void converse(text)
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
      <div ref={logRef} onScroll={noteWhereRead} className="bubble-log" role="log" aria-live="polite" aria-busy={busy}>
        {messages.map((m, i) => m.from === 'verdict'
          ? <VerdictLine key={i} controlId={m.controlId} verdict={m.verdict} status={m.status} onOpen={onOpen} />
          : m.from === 'step' ? <StepLine key={i} step={m.step} />
            : <p key={i} className={m.from === 'lead' ? 'msg msg-lead' : m.failed ? 'msg notice' : 'msg'}>{m.text}</p>)}
        {suggestion && !busy && (suggestion.action === 'review'
          ? <PaperLink className="btn btn-ghost suggestion" controlId={suggestion.control_id} onOpen={onOpen}>
            {suggestionLabel(suggestion)}
          </PaperLink>
          : <button className="btn btn-ghost suggestion" onClick={() => void converse(suggestionMessage(suggestion))}>
            {suggestionLabel(suggestion)}
          </button>)}
      </div>
      <form className="bubble-foot" onSubmit={(e) => void send(e)}>
        <input ref={inputRef} className="field" type="text" aria-label="Message to Vouch" placeholder="Ask a question"
          autoComplete="off" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button className="btn btn-primary" type="submit" disabled={!draft.trim() || busy} aria-busy={busy}>Send</button>
      </form>
    </aside>
  )
}
