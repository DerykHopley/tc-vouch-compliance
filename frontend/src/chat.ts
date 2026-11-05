import { readEvents } from './assessment.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** Vouch's chat (#44): one message to `POST /api/chat`, its reply read as server-sent events. */

/** The page the lead is on, as the chat request and the chat's header name it. */
export type PageContext = { page: 'dossier' | 'paper' | 'developer'; controlId: string | null }

export type ChatEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'step'; data: { node: string; text: string } }
  | { event: 'done'; data: Record<string, never> }
  | { event: 'error'; data: { message: string } }

/** Shown in the chat for any throw from `sendChat`; the typed message stays in the field. */
export const CHAT_UNREACHABLE = 'Vouch could not be reached just now. Your message is kept: please try again shortly.'
const NOT_ANSWERED = 'Vouch could not answer just now. Please try again shortly.'

const PAGE_WORDS: Record<PageContext['page'], string> = {
  dossier: 'Dossier',
  paper: 'Working paper',
  developer: 'Developer',
}

/** The chat header's "on …": the page, and on a working paper its control first. */
export function pageLabel({ page, controlId }: PageContext): string {
  return controlId ? `on ${controlId} · ${PAGE_WORDS[page]}` : `on ${PAGE_WORDS[page]}`
}

/**
 * Sends the message and yields the reply's events until `done` or `error`.
 * Throws when the service can't be reached, or the stream breaks off before either.
 */
export async function* sendChat(message: string, context: PageContext): AsyncGenerator<ChatEvent> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(await authHeaders()) },
    body: JSON.stringify({ message, page: context.page, control_id: context.controlId }),
  })
  if (response.status >= 500 || !response.body) throw new Error(`service answered ${response.status}`)
  if (sessionEnded(response)) {
    yield { event: 'error', data: { message: SESSION_ENDED } }
    return
  }
  if (!response.ok) {
    // The request rate's 429 says why; any other refusal doesn't.
    const detail = response.status === 429
      ? await response.json().then((body: { detail?: unknown }) => body.detail, () => undefined)
      : undefined
    yield { event: 'error', data: { message: typeof detail === 'string' ? detail : NOT_ANSWERED } }
    return
  }
  for await (const item of readEvents(response.body)) {
    yield item as ChatEvent
    if (item.event === 'done' || item.event === 'error') return
  }
  throw new Error('the stream ended before the reply')
}
