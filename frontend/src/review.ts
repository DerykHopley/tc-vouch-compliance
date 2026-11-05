import type { Assessment, Column, RequirementVerdict, Verdict } from './assessment.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** The compliance lead's review of a proposed assessment (#42), through #39's review route. */

/** A verdict the lead may give a requirement (`vouch.graph.JudgedVerdict`): not applicable is the profile's call. */
export type JudgedVerdict = Exclude<Verdict, 'not-applicable'>
export const JUDGED_VERDICTS: JudgedVerdict[] = ['evidenced', 'partly-evidenced', 'not-evidenced']

/** One requirement's verdict changed by the lead, with the reason (`vouch.graph.OverrideRequest`). */
export type Override = { requirement_id: string; verdict: JudgedVerdict; reason: string }

/** The lead's decision on a control (`vouch.graph.ReviewDecision`). */
export type ReviewDecision = { decision: 'accept' | 'reject'; note: string; overrides: Override[] }

/** The columns whose verdicts make the control's (`vouch.graph.ROLL_UP_COLUMNS`). */
const ROLL_UP_COLUMNS: Column[] = ['must', 'high', 'very high', 'overlay']

/** Whether a requirement's verdict counts towards the control's (`vouch.graph.counts_towards_roll_up`). */
const countsTowardsRollUp = (r: Pick<RequirementVerdict, 'column' | 'verdict'>) =>
  ROLL_UP_COLUMNS.includes(r.column) && r.verdict !== 'not-applicable'

/**
 * The control's verdict from its requirements, as `vouch.graph.roll_up` makes it: all counted evidenced gives
 * evidenced, none evidenced gives not evidenced, otherwise partly; with none counted, not applicable.
 */
export function rollUp(requirements: Pick<RequirementVerdict, 'column' | 'verdict'>[]): Verdict {
  const counted = requirements.filter(countsTowardsRollUp).map((r) => r.verdict)
  if (counted.length === 0) return 'not-applicable'
  if (counted.every((v) => v === 'evidenced')) return 'evidenced'
  if (!counted.some((v) => v === 'evidenced')) return 'not-evidenced'
  return 'partly-evidenced'
}

/** The requirements with the lead's verdicts and reasons in place (`vouch.graph.apply_overrides`). */
export function withOverrides(requirements: RequirementVerdict[], overrides: Override[]): RequirementVerdict[] {
  return requirements.map((r) => {
    const change = overrides.find((o) => o.requirement_id === r.id)
    return change ? { ...r, verdict: change.verdict, reason: change.reason } : r
  })
}

export const ALREADY_REVIEWED = 'This control has already been reviewed, perhaps in another tab. Refresh to see the decision.'
export const REVIEW_FAILED = 'Your decision could not be recorded just now. Nothing was changed; please try again.'

export type ReviewAnswer =
  | { ok: true; assessment: Assessment }
  | { ok: false; conflict: boolean; message: string }

/** Records the decision: the reviewed assessment, or why not. A 409 is `conflict`: it was reviewed already. */
export async function submitReview(threadId: string, decision: ReviewDecision): Promise<ReviewAnswer> {
  try {
    const response = await fetch(`/api/assessments/${encodeURIComponent(threadId)}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify(decision),
    })
    if (response.ok) return { ok: true, assessment: ((await response.json()) as { assessment: Assessment }).assessment }
    if (response.status === 409) return { ok: false, conflict: true, message: ALREADY_REVIEWED }
    return { ok: false, conflict: false, message: sessionEnded(response) ? SESSION_ENDED : REVIEW_FAILED }
  } catch {
    return { ok: false, conflict: false, message: REVIEW_FAILED }
  }
}

/** Who decided on an assessment, and when (ISO 8601). */
export type Reviewer = { who: string; at: string }

type TrailEntry = { thread_id: string; action: string; user_id: string; reviewer_name?: string; at: string }

/** How many of the newest audit entries are searched for a decision: the trail's largest page. */
const TRAIL_PAGE = 500

/**
 * Who accepted or rejected the assessment, and when, from the audit trail (#39): the reviewer's name when the entry
 * keeps one (#154), else their user id. Null when the trail can't be read, or its newest page has no decision on it.
 */
export async function reviewedBy(threadId: string): Promise<Reviewer | null> {
  try {
    const response = await fetch(`/api/audit-trail?limit=${TRAIL_PAGE}`, { headers: await authHeaders() })
    if (!response.ok) return null
    const { entries } = (await response.json()) as { entries: TrailEntry[] }
    const decided = entries.find((e) => e.thread_id === threadId && (e.action === 'accept' || e.action === 'reject'))
    return decided ? { who: decided.reviewer_name || decided.user_id, at: decided.at } : null
  } catch {
    return null
  }
}
