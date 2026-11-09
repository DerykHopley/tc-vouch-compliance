import { authHeaders, refusedBecause, SESSION_ENDED, sessionEnded } from './session.ts'

/** Open questions on a control (#414): logged by the lead, answered once (final). */

/** One open question as `vouch.questions.shown` sends it. `due_date` is YYYY-MM-DD; `asked_at` and `answered_at` are
 * ISO 8601; `answer` and `answered_at` are null until it is answered. `overdue`: not answered, and its due date is
 * before today in UTC, as the server works it out. */
export type OpenQuestion = {
  id: number
  question: string
  asked_of: string
  due_date: string
  asked_at: string
  answer: string | null
  answered_at: string | null
  overdue: boolean
}

/** What the lead logs (`vouch.questions.QuestionRequest`). */
export type NewQuestion = { question: string; asked_of: string; due_date: string }

/** A question is at most this many characters, an answer at most that many, as the API takes them
 * (`vouch.questions.MAX_QUESTION`, `MAX_ANSWER`); who it is for, as an item's owner role (`MAX_OWNER_ROLE`). */
export const QUESTION_LENGTH = 1000
export const ANSWER_LENGTH = 2000
export const ASKED_OF_LENGTH = 200

export const LOG_FAILED = 'The question could not be logged just now. Please try again.'
export const ANSWER_FAILED = 'The answer could not be saved just now. Please try again.'

/** The question as kept, or the server's sentence (a 404, 409 or 422), else `failed`. */
export type QuestionAnswer = { ok: true; question: OpenQuestion } | { ok: false; message: string }

async function send(url: string, body: object, failed: string): Promise<QuestionAnswer> {
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify(body),
    })
    if (response.ok) return { ok: true, question: ((await response.json()) as { question: OpenQuestion }).question }
    if (response.status === 404 || response.status === 409 || response.status === 422) {
      return { ok: false, message: (await refusedBecause(response)) || failed }
    }
    return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : failed }
  } catch {
    return { ok: false, message: failed }
  }
}

const questionsUrl = (controlId: string) => `/api/controls/${encodeURIComponent(controlId)}/questions`

/** Logs an open question on the control (`POST /api/controls/{id}/questions`); an audit trail entry. */
export const logQuestion = (controlId: string, question: NewQuestion) =>
  send(questionsUrl(controlId), question, LOG_FAILED)

/** Records the answer to a question (`POST …/questions/{question_id}/answer`): final, and an audit trail entry. */
export const answerQuestion = (controlId: string, questionId: number, answer: string) =>
  send(`${questionsUrl(controlId)}/${questionId}/answer`, { answer }, ANSWER_FAILED)
