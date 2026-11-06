import { postEvents, type ReviewStatus, type Step, type Verdict } from './assessment.ts'
import { authHeaders, sessionEnded } from './session.ts'
import type { NextStep } from './overview.ts'

/**
 * Vouch's chat (#44, #45): one message to `POST /api/chat`, its reply read as server-sent events. A chat opens with
 * Vouch's greeting (no message: where the lead stands, and the next step); each reply names its chat thread first (`thread`),
 * which the next message goes on with.
 */

/** A line in the chat: the lead's message, Vouch's reply (or a notice that it failed), an assessment's step, or an
 * assessment's verdict with the way to its working paper. */
export type ChatMessage =
  | { from: 'lead'; text: string }
  /** `papers`: the controls a cross-control answer may name, linked to their working papers when named (#217). */
  | { from: 'vouch'; text: string; failed?: boolean; papers?: string[] }
  | { from: 'step'; step: Step }
  | { from: 'verdict'; controlId: string; verdict: Verdict; status: ReviewStatus; language?: string }

/** A kept line, as `GET /api/chat/current` gives it (#251): a ChatMessage, a verdict's fields as the API names them. */
export type KeptLine =
  | Exclude<ChatMessage, { from: 'verdict' }>
  | { from: 'verdict'; control_id: string; verdict: Verdict; status: ReviewStatus; answer_language?: string | null }

/** The kept lines as the messages the chat showed (#251): the same lines, so they render the same. */
export function restoredMessages(lines: KeptLine[]): ChatMessage[] {
  return lines.map((line) => line.from === 'verdict'
    ? { from: 'verdict', controlId: line.control_id, verdict: line.verdict, status: line.status,
      ...(line.answer_language ? { language: line.answer_language } : {}) }
    : line)
}

/** The conversation the chat opens on (#251): its thread and its messages; none yet, none. `note`: it couldn't be read. */
export type Conversation = { threadId: string | null; messages: ChatMessage[]; note: string | null }

/** Shown where the kept conversation can't be reached at all. Must match CHAT_HISTORY_UNAVAILABLE in
 * src/vouch/api.py, said where the server can't read it. */
export const HISTORY_UNREACHABLE = "Your earlier conversation can't be shown just now. New messages work as usual."

/** The user's current conversation under this profile (`GET /api/chat/current`), kept on the server (#251). */
export async function fetchConversation(): Promise<Conversation> {
  try {
    const response = await fetch('/api/chat/current', { headers: await authHeaders() })
    if (sessionEnded(response)) return { threadId: null, messages: [], note: null }
    // The database down answers before the route can (a 503): the earlier conversation exists, but not now.
    if (!response.ok) return { threadId: null, messages: [], note: HISTORY_UNREACHABLE }
    const body = (await response.json()) as { thread_id: string | null; lines: KeptLine[]; note?: string }
    return { threadId: body.thread_id, messages: restoredMessages(body.lines), note: body.note ?? null }
  } catch {
    return { threadId: null, messages: [], note: HISTORY_UNREACHABLE }
  }
}

/** Starts a new conversation (`DELETE /api/chat/current`): the old one stays on the server (#251). */
export async function newConversation(): Promise<void> {
  try {
    await fetch('/api/chat/current', { method: 'DELETE', headers: await authHeaders() })
  } catch {
    // The chat clears its own view all the same; the next message starts the new thread.
  }
}

/** The page the compliance lead is on, as the chat request and the chat's header name it. */
export type PageContext = { page: 'dossier' | 'paper' | 'developer'; controlId: string | null }

/** An assessment the chat started has finished: proposed, waiting for the lead's review on its working paper. */
export type ChatAssessment = {
  control_id: string
  verdict: Verdict
  status: ReviewStatus
  thread_id: string
  /** The profile's (ISO 639-1): the verdict chip's words are in it (#200). */
  answer_language?: string
}

export type ChatEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'step'; data: Step }
  | { event: 'suggestion'; data: NonNullable<NextStep> }
  | { event: 'assessment'; data: ChatAssessment }
  | { event: 'thread'; data: { thread_id: string } }
  /** The controls a cross-control answer may name (#217): the chat links those it names to their working papers. */
  | { event: 'papers'; data: { control_ids: string[] } }
  | { event: 'done'; data: Record<string, never> }
  | { event: 'error'; data: { message: string } }

/** The controls among `ids` (from a `papers` event, never the model) that `reply` names, whole ids only, in `ids`'
 * order: what the chat links to their working papers (#217). */
export function namedControls(reply: string, ids: string[]): string[] {
  return ids.filter((id) => new RegExp(`(?<![\\w.])${id.replaceAll('.', '\\.')}(?![\\w]|\\.\\w)`).test(reply))
}

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

/** A piece of Vouch's reply: its own words, or a quote it cites from a document (#46). */
export type ReplyPart = { text: string } | { quote: string; doc_id: string }

/** Must match CITE in vouch/chat_citations.py: the one form of cite the backend checks and keeps. */
const CITE = /<cite doc="([^"<>]+)">([\s\S]*?)<\/cite>/g

/** The reply split into its words and its cites. Only that tag is read; everything else stays text, shown as
 * written (never as HTML). A line break straight after a cite is dropped: the quote is a block of its own. */
export function replyParts(reply: string): ReplyPart[] {
  const parts: ReplyPart[] = []
  let at = 0
  for (const cite of reply.matchAll(CITE)) {
    parts.push({ text: reply.slice(at, cite.index) }, { quote: cite[2], doc_id: cite[1] })
    at = cite.index + cite[0].length
    if (reply[at] === '\n') at += 1
  }
  parts.push({ text: reply.slice(at) })
  return parts.filter((part) => !('text' in part) || part.text)
}

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
