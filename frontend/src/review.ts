import type { Assessment, Column, RequirementVerdict, Verdict } from './assessment.ts'
import { authHeaders, refusedBecause, SESSION_ENDED, sessionEnded } from './session.ts'

/**
 * The compliance lead's review of a proposed assessment (#42), in three steps (#372): Confirm findings (the findings
 * route, with the overrides), Plan fixes (each remediation item decided, `remediation.ts`), then Accept (#39's review
 * route). Reject ends it at any step.
 */

/** A verdict the lead may give a requirement (`vouch.graph.JudgedVerdict`): not applicable is the profile's call. */
export type JudgedVerdict = Exclude<Verdict, 'not-applicable'>
export const JUDGED_VERDICTS: JudgedVerdict[] = ['evidenced', 'partly-evidenced', 'not-evidenced']

/** An override: one requirement's verdict changed by the lead, with the reason (`vouch.graph.OverrideRequest`). */
export type Override = { requirement_id: string; verdict: JudgedVerdict; reason: string }

/** The lead's decision on a control (`vouch.graph.ReviewDecision`): Accept, the last step, or Reject, at any. */
export type ReviewDecision = { decision: 'accept' | 'reject'; note: string }

/** An override as the assessment keeps it (`vouch.graph.Override`): with the agent's verdict and reason. */
export type KeptOverride = Override & { agent_verdict: Verdict; agent_reason: string }

/**
 * The decision as the assessment keeps it (`vouch.graph.Review`): each override confirmed with the findings, who
 * decided and when (empty on a review kept before #42), and whether it was made in three steps (false on one kept
 * before #372).
 */
export type Review = ReviewDecision & {
  overrides: KeptOverride[]
  reviewer_id: string
  reviewer_name: string
  /** ISO 8601. */
  reviewed_at: string
  three_step?: boolean
}

/** The step of the review a proposed assessment waits at (`vouch.remediation.ReviewStep`). */
export type ReviewStep = 'confirm-findings' | 'plan-fixes' | 'accept'

/** Each step's name on screen (`GLOSSARY.md`): in the review bar's steps and the paper's head (#467). */
export const REVIEW_STEP_WORDS: Record<ReviewStep, string> = {
  'confirm-findings': 'Confirm findings', 'plan-fixes': 'Plan fixes', accept: 'Accept',
}

/** The three steps, in order. */
export const REVIEW_STEPS: ReviewStep[] = ['confirm-findings', 'plan-fixes', 'accept']

/**
 * The step a proposed assessment's review is at: Confirm findings until the server says they are confirmed; then Plan
 * fixes while any remediation item is a draft, Accept once none is, counted from the items as the page holds them, so
 * an item decided on the page moves it on at once. Null for a reviewed assessment.
 */
export function reviewStep(assessment: Pick<Assessment, 'status' | 'review_step' | 'remediation'>): ReviewStep | null {
  if (assessment.status !== 'waiting-for-review') return null
  if (!assessment.review_step || assessment.review_step === 'confirm-findings') return 'confirm-findings'
  return (assessment.remediation ?? []).some((i) => i.status === 'draft') ? 'plan-fixes' : 'accept'
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

/** The roll-up explanation's words in one answer language; `rollUpWords` puts them together. */
type RollUpPhrases = {
  verdicts: Record<Verdict, string>
  /** The counted requirements' noun, singular and plural: musts only, or musts with protection-level or overlay. */
  noun: (mustsOnly: boolean, plural: boolean) => string
  shoulds: string
  is: (count: number) => string
  allFully: (count: number, nouns: string) => string
  oneFully: (noun: string) => string
  partlyOf: (evidenced: number, count: number, nouns: string, is: string) => string
  noneFully: (noun: string, partly: number, count: number) => string
  noneAtAll: (noun: string) => string
  noneApply: (noun: string) => string
}

/** Per answer language; English is also `vouch.graph.roll_up_words`'s (#212). */
const ROLL_UP_PHRASES: Record<string, RollUpPhrases> = {
  en: {
    verdicts: { evidenced: 'Evidenced', 'partly-evidenced': 'Partly evidenced', 'not-evidenced': 'Not evidenced',
      'not-applicable': 'Not applicable' },
    noun: (mustsOnly, plural) => (mustsOnly ? 'must requirement' : 'must, protection-level or overlay requirement')
      + (plural ? 's' : ''),
    shoulds: 'should requirements count towards the should score only',
    is: (count) => (count === 1 ? 'is' : 'are'),
    allFully: (count, nouns) => `all ${count} ${nouns} are fully evidenced`,
    oneFully: (noun) => `its one ${noun} is fully evidenced`,
    partlyOf: (evidenced, count, nouns, is) => `${evidenced} of ${count} ${nouns} ${is} fully evidenced`,
    noneFully: (noun, partly, count) => `no ${noun} is fully evidenced (${partly} of ${count} partly evidenced)`,
    noneAtAll: (noun) => `no ${noun} is evidenced`,
    noneApply: (noun) => `no ${noun} applies`,
  },
  de: {
    verdicts: { evidenced: 'Nachgewiesen', 'partly-evidenced': 'Teilweise nachgewiesen',
      'not-evidenced': 'Nicht nachgewiesen', 'not-applicable': 'Nicht anwendbar' },
    noun: (mustsOnly, plural) => (mustsOnly ? 'Muss-Anforderung' : 'Muss-, Schutzbedarfs- oder Overlay-Anforderung')
      + (plural ? 'en' : ''),
    shoulds: 'Soll-Anforderungen zählen nur zur Soll-Quote',
    is: (count) => (count === 1 ? 'ist' : 'sind'),
    allFully: (count, nouns) => `alle ${count} ${nouns} sind vollständig nachgewiesen`,
    oneFully: (noun) => `die eine ${noun} ist vollständig nachgewiesen`,
    partlyOf: (evidenced, count, nouns, is) => `${evidenced} von ${count} ${nouns} ${is} vollständig nachgewiesen`,
    noneFully: (noun, partly, count) =>
      `keine ${noun} ist vollständig nachgewiesen (${partly} von ${count} teilweise nachgewiesen)`,
    noneAtAll: (noun) => `keine ${noun} ist nachgewiesen`,
    noneApply: (noun) => `keine ${noun} ist anwendbar`,
  },
}

/**
 * Why the control has its `verdict` (the one shown; `rollUp`'s by default), in the profile's `language` (English for
 * one without phrases of its own), when a row's verdict differs from it, as when partly evidenced musts roll up to
 * not evidenced (#212); null when every applicable row matches. The English matches `vouch.graph.roll_up_words`
 * (`tests/roll_up_words_cases.json`).
 */
export function rollUpWords(
  requirements: Pick<RequirementVerdict, 'column' | 'verdict'>[], language: string,
  verdict: Verdict = rollUp(requirements),
): string | null {
  const applying = requirements.filter((r) => r.verdict !== 'not-applicable')
  if (applying.every((r) => r.verdict === verdict)) return null
  const counted = requirements.filter(countsTowardsRollUp)
  const count = counted.length
  const evidenced = counted.filter((r) => r.verdict === 'evidenced').length
  const partly = counted.filter((r) => r.verdict === 'partly-evidenced').length
  const mustsOnly = count > 0 && counted.every((r) => r.column === 'must')
  const say = ROLL_UP_PHRASES[language] ?? ROLL_UP_PHRASES.en
  const noun = say.noun(mustsOnly, false)
  const nouns = say.noun(mustsOnly, true)
  const why = verdict === 'evidenced'
    ? `${count === 1 ? say.oneFully(noun) : say.allFully(count, nouns)}; ${say.shoulds}`
    : verdict === 'partly-evidenced' ? say.partlyOf(evidenced, count, nouns, say.is(evidenced))
      : verdict === 'not-evidenced'
        ? (partly > 0 ? say.noneFully(noun, partly, count) : `${say.noneAtAll(noun)}; ${say.shoulds}`)
        : `${say.noneApply(noun)}; ${say.shoulds}`
  return `${say.verdicts[verdict]}: ${why}.`
}

/** The requirements with the lead's overrides in place (`vouch.graph.apply_overrides`). */
export function withOverrides(requirements: RequirementVerdict[], overrides: Override[]): RequirementVerdict[] {
  return requirements.map((r) => {
    const override = overrides.find((o) => o.requirement_id === r.id)
    return override ? { ...r, verdict: override.verdict, reason: override.reason } : r
  })
}

export const MOVED_ON = "This control's review has moved on, perhaps in another tab. Refresh to see where it stands."
export const REVIEW_FAILED = 'This step could not be recorded just now. Nothing was changed; please try again.'
export const REVIEW_REFUSED = 'Vouch refused this step, and nothing was changed:'

/**
 * Why a step wasn't recorded: `conflict`, the review moved on (409: reviewed, or confirmed, already, perhaps in
 * another tab); `refused`, the step itself was refused (422), so sending it again can't help; `failed`, anything else,
 * worth trying again.
 */
export type Refusal = 'conflict' | 'refused' | 'failed'

export type ReviewAnswer = { ok: true; assessment: Assessment } | { ok: false; refusal: Refusal; message: string }

/** Records the decision: the reviewed assessment, or why not. */
export function submitReview(threadId: string, decision: ReviewDecision): Promise<ReviewAnswer> {
  return send(threadId, 'review', decision)
}

/** Confirms the findings with the lead's overrides (Confirm findings): the assessment at its next step, or why not. */
export function confirmFindings(threadId: string, overrides: Override[]): Promise<ReviewAnswer> {
  return send(threadId, 'findings', { overrides })
}

async function send(threadId: string, route: 'review' | 'findings', body: unknown): Promise<ReviewAnswer> {
  try {
    const response = await fetch(`/api/assessments/${encodeURIComponent(threadId)}/${route}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify(body),
    })
    if (response.ok) return { ok: true, assessment: ((await response.json()) as { assessment: Assessment }).assessment }
    if (response.status === 409) return { ok: false, refusal: 'conflict', message: MOVED_ON }
    if (response.status === 422) {
      return { ok: false, refusal: 'refused', message: `${REVIEW_REFUSED} ${await refusedBecause(response)}`.trim() }
    }
    return { ok: false, refusal: 'failed', message: sessionEnded(response) ? SESSION_ENDED : REVIEW_FAILED }
  } catch {
    return { ok: false, refusal: 'failed', message: REVIEW_FAILED }
  }
}
