import type { Assessment } from './assessment.ts'
import { authHeaders, refusedBecause, SESSION_ENDED, sessionEnded } from './session.ts'

/** The lead's decisions on remediation items (#43), through `POST .../remediation/{requirement_id}`. */

/** A draft until the lead decides; then approved or discarded, for good (`vouch.remediation.ItemStatus`). */
export type ItemStatus = 'draft' | 'approved' | 'discarded'

/** The owner role of an item Vouch drafted no owner for (`vouch.graph.NO_OWNER`): the lead names one to approve it. */
export const NO_OWNER = 'no owner suggested'

export const ITEM_WORDS: Record<ItemStatus, string> = { draft: 'Draft', approved: 'Approved', discarded: 'Discarded' }

/** An item's priority in the improvement plan (#410, `vouch.database.Priority`). */
export type Priority = 'high' | 'medium' | 'low'

export const PRIORITY_WORDS: Record<Priority, string> = { high: 'High', medium: 'Medium', low: 'Low' }

/** A done mark (#410): when (ISO 8601) and what the lead said was done. */
export type DoneMark = { at: string; note: string }

/** A remediation item as the assessment carries it: one per requirement not or partly evidenced (#212). `due_date` is ISO 8601.
 * `priority` is the lead's, else the default from its requirement's verdict (null on a discarded item); `done` is set
 * once an approved item is marked done (#410). The server sends both in either goal; only Internal improvement shows
 * them. Optional, as the cards' flags are, so a page built before them still reads. */
export type RemediationItem = {
  requirement_id: string
  missing: string
  owner_role: string
  due_date: string
  status: ItemStatus
  priority?: Priority | null
  done?: DoneMark | null
}

/** The lead's decision (`vouch.remediation.DecisionRequest`): an approval may give another owner role or due date. */
export type ItemDecision = { decision: 'approve' | 'discard'; owner_role?: string; due_date?: string }

export const DECISION_FAILED = 'Your decision could not be recorded just now. Nothing was changed; please try again.'

/** `invalid`: the decision itself was refused (a 422, such as a past due date), so the form's input is at fault. */
export type DecisionAnswer =
  | { ok: true; item: RemediationItem }
  | { ok: false; message: string; invalid: boolean }

/** Records the decision: the item as kept, or why not (a 422's or 409's reason from the server, such as a past due
 * date or an item decided already). */
export async function decideItem(threadId: string, requirementId: string, decision: ItemDecision):
  Promise<DecisionAnswer> {
  try {
    const url = `/api/assessments/${encodeURIComponent(threadId)}/remediation/${encodeURIComponent(requirementId)}`
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify(decision),
    })
    if (response.ok) return { ok: true, item: ((await response.json()) as { item: RemediationItem }).item }
    if (response.status === 409 || response.status === 422) {
      const message = (await refusedBecause(response)) || DECISION_FAILED
      return { ok: false, message, invalid: response.status === 422 }
    }
    return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : DECISION_FAILED, invalid: false }
  } catch {
    return { ok: false, message: DECISION_FAILED, invalid: false }
  }
}

/** A done mark's note is at most this many characters, as the API takes it (`vouch.plan.MAX_NOTE`). */
export const DONE_NOTE_LENGTH = 2000

export const PRIORITY_FAILED = 'The priority could not be changed just now. Please try again.'
export const DONE_FAILED = 'The item could not be marked done just now. Please try again.'

/** A plan mark's answer (#410): the item as kept, or the server's sentence (a 409 or 422), else `failed`. */
export type MarkAnswer = { ok: true; item: RemediationItem } | { ok: false; message: string }

async function sendMark(url: string, method: 'PUT' | 'POST', body: object, failed: string): Promise<MarkAnswer> {
  try {
    const response = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify(body),
    })
    if (response.ok) return { ok: true, item: ((await response.json()) as { item: RemediationItem }).item }
    if (response.status === 409 || response.status === 422) {
      return { ok: false, message: (await refusedBecause(response)) || failed }
    }
    return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : failed }
  } catch {
    return { ok: false, message: failed }
  }
}

const itemUrl = (threadId: string, requirementId: string) =>
  `/api/assessments/${encodeURIComponent(threadId)}/remediation/${encodeURIComponent(requirementId)}`

/** Sets an approved item's priority (`PUT .../priority`, #410); the change is an audit trail entry. */
export const setPriority = (threadId: string, requirementId: string, priority: Priority) =>
  sendMark(`${itemUrl(threadId, requirementId)}/priority`, 'PUT', { priority }, PRIORITY_FAILED)

/** Marks an approved item of an accepted control done, with what was done (`POST .../done`, #410); final, and an audit
 * trail entry. */
export const markDone = (threadId: string, requirementId: string, note: string) =>
  sendMark(`${itemUrl(threadId, requirementId)}/done`, 'POST', { note }, DONE_FAILED)

/** The assessment with the kept item in place of its draft. */
export function withItem(assessment: Assessment, item: RemediationItem): Assessment {
  return { ...assessment, remediation: (assessment.remediation ?? []).map((i) =>
    i.requirement_id === item.requirement_id ? item : i) }
}
