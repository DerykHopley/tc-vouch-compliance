import type { Assessment } from './assessment.ts'
import { authHeaders, refusedBecause, SESSION_ENDED, sessionEnded } from './session.ts'

/** The lead's decisions on remediation items (#43), through `POST .../remediation/{requirement_id}`. */

/** A draft until the lead decides; then approved or discarded, for good (`vouch.remediation.ItemStatus`). */
export type ItemStatus = 'draft' | 'approved' | 'discarded'

/** The owner role of an item Vouch drafted no owner for (`vouch.graph.NO_OWNER`): the lead names one to approve it. */
export const NO_OWNER = 'no owner suggested'

export const ITEM_WORDS: Record<ItemStatus, string> = { draft: 'Draft', approved: 'Approved', discarded: 'Discarded' }

/** A remediation item as the assessment carries it: one per not-evidenced requirement. `due_date` is ISO 8601. */
export type RemediationItem = {
  requirement_id: string
  missing: string
  owner_role: string
  due_date: string
  status: ItemStatus
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

/** The assessment with the kept item in place of its draft. */
export function withItem(assessment: Assessment, item: RemediationItem): Assessment {
  return { ...assessment, remediation: (assessment.remediation ?? []).map((i) =>
    i.requirement_id === item.requirement_id ? item : i) }
}
