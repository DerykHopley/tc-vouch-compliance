import { postEvents, type ReviewStatus, type Step, type Verdict } from './assessment.ts'
import type { NextStep } from './overview.ts'

/**
 * Vouch's chat (#44, #45): one message to `POST /api/chat`, its reply read as server-sent events. A chat opens with
 * Vouch's greeting (no message: where the lead stands, and the next step); each reply names its chat thread first (`thread`),
 * which the next message goes on with.
 */

/** The page the compliance lead is on, as the chat request and the chat's header name it. */
export type PageContext = { page: 'dossier' | 'paper' | 'developer'; controlId: string | null }

/** An assessment the chat started has finished: proposed, waiting for the lead's review on its working paper. */
export type ChatAssessment = { control_id: string; verdict: Verdict; status: ReviewStatus; thread_id: string }

export type ChatEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'step'; data: Step }
  | { event: 'suggestion'; data: NonNullable<NextStep> }
  | { event: 'assessment'; data: ChatAssessment }
  | { event: 'thread'; data: { thread_id: string } }
  | { event: 'done'; data: Record<string, never> }
  | { event: 'error'; data: { message: string } }

/** Shown in the chat for any throw from `sendChat`; the typed message stays in the field. */
export const CHAT_UNREACHABLE = 'Vouch could not be reached just now. Your message is kept: please try again shortly.'
/** Shown when the greeting can't be fetched: there is no typed message to keep. */
export const GREETING_UNREACHABLE = 'Vouch could not be reached just now. Please try again shortly.'
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

/** A suggestion chip's words; for an assessment, also the message it sends (a review is on the working paper). */
export function suggestionLabel({ control_id, action }: NonNullable<NextStep>): string {
  return action === 'review' ? `Review ${control_id}` : action === 'assess-again' ? `Assess ${control_id} again`
    : `Assess ${control_id}`
}
export const suggestionMessage = ({ control_id }: NonNullable<NextStep>) => `Assess ${control_id}`

/**
 * Sends the message (null: ask for the greeting) on the chat thread `threadId` (null: a new one), and yields the
 * reply's events until `done` or `error`; at the daily limit, its 429's message.
 * Throws when the service can't be reached, or the stream breaks off before either.
 */
export function sendChat(message: string | null, context: PageContext, threadId: string | null = null):
  AsyncGenerator<ChatEvent> {
  const body = {
    ...(message === null ? {} : { message }),
    page: context.page,
    control_id: context.controlId,
    thread_id: threadId,
  }
  return postEvents<ChatEvent>('/api/chat', body, ['done'], NOT_ANSWERED)
}
