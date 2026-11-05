import { postEvents } from './assessment.ts'

/** Vouch's chat (#44): one message to `POST /api/chat`, its reply read as server-sent events. */

/** The page the compliance lead is on, as the chat request and the chat's header name it. */
export type PageContext = { page: 'dossier' | 'paper' | 'developer'; controlId: string | null }

export type ChatEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'step'; data: { node: string; text: string } }
  | { event: 'done'; data: Record<string, never> }
  | { event: 'error'; data: { message: string } }

/** Shown in the chat for any throw from `sendChat`; the typed message stays in the field. */
export const CHAT_UNREACHABLE = 'Vouch could not be reached just now. Your message is kept: please try again shortly.'
/** Must match CHAT_FAILED in src/vouch/chat.py: the one sentence for a chat that failed. */
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
 * Sends the message and yields the reply's events until `done` or `error`; at the daily limit, its 429's message.
 * Throws when the service can't be reached, or the stream breaks off before either.
 */
export function sendChat(message: string, context: PageContext): AsyncGenerator<ChatEvent> {
  const body = { message, page: context.page, control_id: context.controlId }
  return postEvents<ChatEvent>('/api/chat', body, ['done'], NOT_ANSWERED)
}
