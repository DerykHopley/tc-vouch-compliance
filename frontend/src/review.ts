import type { Assessment, Column, RequirementVerdict, Verdict } from './assessment.ts'
import { authHeaders, refusedBecause, SESSION_ENDED, sessionEnded } from './session.ts'

/** The compliance lead's review of a proposed assessment (#42), through #39's review route. */

/** A verdict the lead may give a requirement (`vouch.graph.JudgedVerdict`): not applicable is the profile's call. */
export type JudgedVerdict = Exclude<Verdict, 'not-applicable'>
export const JUDGED_VERDICTS: JudgedVerdict[] = ['evidenced', 'partly-evidenced', 'not-evidenced']

/** An override: one requirement's verdict changed by the lead, with the reason (`vouch.graph.OverrideRequest`). */
export type Override = { requirement_id: string; verdict: JudgedVerdict; reason: string }

/** The lead's decision on a control (`vouch.graph.ReviewDecision`). */
export type ReviewDecision = { decision: 'accept' | 'reject'; note: string; overrides: Override[] }

/**
 * The decision as the assessment keeps it (`vouch.graph.Review`): each override with the agent's verdict and reason,
 * and who decided and when (empty on a review kept before #42).
 */
export type Review = Omit<ReviewDecision, 'overrides'> & {
  overrides: (Override & { agent_verdict: Verdict; agent_reason: string })[]
  reviewer_id: string
  reviewer_name: string
  /** ISO 8601. */
  reviewed_at: string
}

/** The columns whose verdicts make the control's (`vouch.graph.ROLL_UP_COLUMNS`). */
const ROLL_UP_COLUMNS: Column[] = ['must', 'high', 'very high', 'overlay']

/** Whether a requirement's verdict counts towards the control's (`vouch.graph.counts_towards_roll_up`). */
const countsTowardsRollUp = (r: Pick<RequirementVerdict, 'column' | 'verdict'>) =>
  ROLL_UP_COLUMNS.includes(r.column) && r.verdict !== 'not-applicable'

/**
 * The control's verdict from its requirements, as `vouch.graph.roll_up` makes it: all counted evidenced gives
 * evidenced, none evidenced gives not evidenced, otherwise partly; with none counted, not applicable. Both are
 * checked against the same cases (`tests/roll_up_cases.json`).
 */
export function rollUp(requirements: Pick<RequirementVerdict, 'column' | 'verdict'>[]): Verdict {
  const counted = requirements.filter(countsTowardsRollUp).map((r) => r.verdict)
  if (counted.length === 0) return 'not-applicable'
  if (counted.every((v) => v === 'evidenced')) return 'evidenced'
  if (!counted.some((v) => v === 'evidenced')) return 'not-evidenced'
  return 'partly-evidenced'
}

/** The requirements with the lead's overrides in place (`vouch.graph.apply_overrides`). */
export function withOverrides(requirements: RequirementVerdict[], overrides: Override[]): RequirementVerdict[] {
  return requirements.map((r) => {
    const override = overrides.find((o) => o.requirement_id === r.id)
    return override ? { ...r, verdict: override.verdict, reason: override.reason } : r
  })
}

export const ALREADY_REVIEWED = 'This control has already been reviewed, perhaps in another tab. Refresh to see the decision.'
export const REVIEW_FAILED = 'Your decision could not be recorded just now. Nothing was changed; please try again.'
export const REVIEW_REFUSED = 'Vouch refused this decision, and nothing was changed:'

/**
 * Why a decision wasn't recorded: `conflict`, reviewed already (409); `refused`, the decision itself was refused (422),
 * so sending it again can't help; `failed`, anything else, worth trying again.
 */
export type Refusal = 'conflict' | 'refused' | 'failed'

export type ReviewAnswer = { ok: true; assessment: Assessment } | { ok: false; refusal: Refusal; message: string }

/** Records the decision: the reviewed assessment, or why not. */
export async function submitReview(threadId: string, decision: ReviewDecision): Promise<ReviewAnswer> {
  try {
    const response = await fetch(`/api/assessments/${encodeURIComponent(threadId)}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify(decision),
    })
    if (response.ok) return { ok: true, assessment: ((await response.json()) as { assessment: Assessment }).assessment }
    if (response.status === 409) return { ok: false, refusal: 'conflict', message: ALREADY_REVIEWED }
    if (response.status === 422) {
      return { ok: false, refusal: 'refused', message: `${REVIEW_REFUSED} ${await refusedBecause(response)}`.trim() }
    }
    return { ok: false, refusal: 'failed', message: sessionEnded(response) ? SESSION_ENDED : REVIEW_FAILED }
  } catch {
    return { ok: false, refusal: 'failed', message: REVIEW_FAILED }
  }
}
