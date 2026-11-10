import { useCallback, useRef, useState } from 'react'
import type { Assessment, Citation, ProtectionLevel, ReviewStatus, Verdict } from './assessment.ts'
import type { OpenQuestion } from './questions.ts'
import type { PaperRequirement } from './paper.ts'
import type { Goal } from './report.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** The Dossier overview from `GET /api/overview` (#40), as `vouch.dossier.overview` sends it. */

/** A control's review state: not assessed, proposed (waiting for the lead's review), accepted or rejected; or not
 * applicable as a whole under the profile (#454), which nothing reviews or counts. */
export type ReviewState = 'not-assessed' | 'not-applicable' | ReviewStatus

/** Whether a card's state is a review state, with its tag: an assessment of the control is stored and counts. */
export const isReviewed = (status: ReviewState): status is ReviewStatus =>
  status !== 'not-assessed' && status !== 'not-applicable'

/** Why a re-assessment is suggested (`vouch.plan.ReassessReason`), in this order: every fix done (#410, Internal
 * improvement only), an open question answered or overdue (#414, both goals). */
export type ReassessReason = 'fixes-done' | 'question-answered' | 'question-overdue'

/** `closed` is the improvement plan's (#410): false under Audit readiness. `reassess_suggested` is true exactly when
 * `reassess_reasons` isn't empty (#414). A Closed control is still accepted, and no reason changes the status or a
 * count. */
export type ControlCard = {
  id: string
  question: string
  iso_27001: string
  status: ReviewState
  verdict: Verdict | null
  reassess_suggested?: boolean
  reassess_reasons?: ReassessReason[]
  closed?: boolean
  /** The profile's reason when the control is not applicable as a whole (#454); null on every other card. */
  not_applicable?: string | null
}

/** Counted from accepted verdicts only; `open` is every control not accepted. */
export type Readiness = {
  controls: number
  accepted: number
  by_verdict: Record<Verdict, number>
  open: number
  remediation_items: number
  /** Of those, how many the lead approved: the readiness report's count (#310). */
  remediation_approved: number
}

/** The verdicts in the readiness bar's order (the Dossier's band, the readiness report); the open rest stays the
 * bar's own colour. */
export const SEGMENTS: Verdict[] = ['evidenced', 'partly-evidenced', 'not-evidenced', 'not-applicable']

/** One checked quote on the overview: a citation's fields, less why it is stale, and what it is for. */
export type Quote = { control_id: string; requirement_id: string }
  & Pick<Citation, 'doc_id' | 'version' | 'section' | 'quote' | 'kind' | 'stale'>

/** What the lead does next, on which control; null once every control is accepted. */
export type NextStep = { control_id: string; action: 'review' | 'assess-again' | 'assess' } | null

export type Overview = {
  company: string
  country: string
  /** The working paper's head shows both (#41). */
  protection_level: ProtectionLevel
  /** ISO 639-1: the language the answers are written in; the interface stays English. */
  answer_language: string
  /** What the profile is used for (#369): the user's goal under it, Audit readiness until they change it. */
  goal: Goal
  /** Each control's requirements under the profile, by control id: the working paper lists them before an
   * assessment, at no request of its own (#41). */
  requirements: Record<string, PaperRequirement[]>
  readiness: Readiness
  /** How many controls are in each review state. */
  by_status: Record<ReviewState, number>
  next_step: NextStep
  controls: ControlCard[]
  quotes: Quote[]
  /** The lead's review rounds under the profile (#402). */
  rounds: Rounds
  /** The open questions asked since the fresh start, by control id, oldest first (#414); a control with none isn't
   * named. Optional, as the cards' flags are, so a page built before them still reads. */
  questions?: Record<string, OpenQuestion[]>
}

/** The open review round: `opened_at` null for the first one, open since the start. */
export type OpenRound = { name: string; opened_at: string | null }

/** A count now, and when the compared round closed. */
export type NowAndThen = { now: number; then: number }

/** Now against the last closed review round (#402), accepted verdicts only: `now` is the overview's readiness. */
export type RoundComparison = {
  against: { name: string; closed_at: string }
  accepted: NowAndThen
  by_verdict: Record<Verdict, NowAndThen>
  /** Each control whose accepted verdict changed, in the base framework's order; null: not accepted. */
  changed: { control_id: string; before: Verdict | null; after: Verdict | null }[]
}

/** The open round (null when none is), and the comparison (null when no round was closed since the fresh start). */
export type Rounds = { current: OpenRound | null; comparison: RoundComparison | null }

/** A control's ISO 27001 reference as the screens show it: "ISO A.5.15, A.8.5" (ISA writes "ISO 27001:2022: …"). */
export const isoLabel = (iso: string) => iso.replace(/^ISO 27001:2022:? /, 'ISO ')

/** A date as the screens say it: "9 November 2026" (English, as the rest of the interface). */
export const dateWords = (at: string) =>
  new Date(at).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })

/** A control's working paper's address. */
export const controlPath = (id: string) => `/controls/${encodeURIComponent(id)}`

export const OVERVIEW_FAILED = 'The overview could not be loaded just now. Please try again.'

/** The overview, or the formal sentence to show with Retry. */
export async function fetchOverview(): Promise<{ ok: true; overview: Overview } | { ok: false; message: string }> {
  try {
    const response = await fetch('/api/overview', { headers: await authHeaders() })
    if (response.ok) return { ok: true, overview: (await response.json()) as Overview }
    return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : OVERVIEW_FAILED }
  } catch {
    return { ok: false, message: OVERVIEW_FAILED }
  }
}

/** The goals as the interface names them (English, as the rest of the interface: `docs/design.md`, Language). */
export const GOAL_WORDS: Record<Goal, string> = {
  'audit-readiness': 'Audit readiness',
  'internal-improvement': 'Internal improvement',
}

/** Each goal in one line, as GLOSSARY.md has it: company setup's step 4 (#447) shows it under the goal's name. */
export const GOAL_LINES: Record<Goal, string> = {
  'audit-readiness': 'Preparing for an external audit.',
  'internal-improvement': "Improving the company's own security, with no audit planned.",
}

/** How a goal change went: changed, the session ended (a 401: signed out already, so nothing more to say), or failed
 * any other way. */
export type GoalChange = 'changed' | 'signed-out' | 'failed'

/** Changes the user's goal under the chosen profile (`PUT /api/goal`, #369); the change is an audit trail entry. */
export async function changeGoal(goal: Goal): Promise<GoalChange> {
  try {
    const response = await fetch('/api/goal', {
      method: 'PUT',
      headers: { ...(await authHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal }),
    })
    if (response.ok) return 'changed'
    return sessionEnded(response) ? 'signed-out' : 'failed'
  } catch {
    return 'failed'
  }
}

/** How starting or closing a review round went: changed, refused with the server's sentence (a 409: a round is open
 * already, or none is), the session ended (a 401), or failed any other way. */
export type RoundChange = { result: 'changed' } | { result: 'refused'; message: string } | { result: 'signed-out' }
  | { result: 'failed' }

async function changeRound(path: string, body?: object): Promise<RoundChange> {
  try {
    const response = await fetch(path, {
      method: 'POST',
      headers: { ...(await authHeaders()), ...(body ? { 'Content-Type': 'application/json' } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
    })
    if (response.ok) return { result: 'changed' }
    if (sessionEnded(response)) return { result: 'signed-out' }
    if (response.status === 409) {
      const detail = await response.json().then((answer: { detail?: unknown }) => answer.detail, () => undefined)
      if (typeof detail === 'string') return { result: 'refused', message: detail }
    }
    return { result: 'failed' }
  } catch {
    return { result: 'failed' }
  }
}

/** A round's name is at most this many characters, as the API takes it (`vouch.rounds.NAME_LENGTH`). */
export const ROUND_NAME_LENGTH = 60

/** Starts the next review round by name (`POST /api/rounds`, #402); the start is an audit trail entry. */
export const startRound = (name: string) => changeRound('/api/rounds', { name })

/** Closes the open review round, freezing its results (`POST /api/rounds/close`, #402); an audit trail entry. */
export const closeRound = () => changeRound('/api/rounds/close')

export type OverviewState =
  | { status: 'loading' }
  | { status: 'ready'; overview: Overview }
  | { status: 'failed'; message: string }

/**
 * The overview as the page shows it; `load` reads it again (only the latest answer is shown). `goalChanged` reads it
 * again after a goal change (#369) without failing the page: the change was kept, so when the read fails the overview
 * stays, showing the new goal, and it answers false for the hero to say so. `roundChanged` does the same after a
 * review round is started or closed (#402), the overview kept as it was when the read fails. `noteAssessed` puts a
 * finished run's verdict and review state on its control's card without a read (each counts to the rate limit), so
 * the working paper's index stays current; the Dossier reads the overview afresh anyway.
 */
export function useOverview() {
  const [state, setState] = useState<OverviewState>({ status: 'loading' })
  const latest = useRef(0)
  const load = useCallback(async () => {
    const asked = ++latest.current
    const answer = await fetchOverview()
    if (asked !== latest.current) return
    setState(answer.ok ? { status: 'ready', overview: answer.overview } : { status: 'failed', message: answer.message })
  }, [])
  const retry = useCallback(() => {
    setState({ status: 'loading' })
    void load()
  }, [load])
  const noteAssessed = useCallback((assessment: Assessment) => {
    setState((now) => now.status !== 'ready' ? now : {
      status: 'ready',
      overview: {
        ...now.overview,
        // The improvement plan's flags (#410) and the questions' reasons (#414) are the server's to work out: off until
        // the overview is read again (a new run or review clears every question reason anyway).
        controls: now.overview.controls.map((c) => c.id !== assessment.control_id ? c
          : { ...c, verdict: assessment.verdict, status: assessment.status ?? 'waiting-for-review',
            reassess_suggested: false, reassess_reasons: [], closed: false }),
      },
    })
  }, [])
  const goalChanged = useCallback(async (goal: Goal) => {
    const asked = ++latest.current
    const answer = await fetchOverview()
    if (asked !== latest.current) return true
    setState((now) => answer.ok ? { status: 'ready', overview: answer.overview }
      : now.status === 'ready' ? { status: 'ready', overview: { ...now.overview, goal } } : now)
    return answer.ok
  }, [])
  // Reads the overview again after a change the page has shown already, keeping the overview shown when the read
  // fails (only the first read shows the failure): a review round started or closed (#402), a review or a done mark
  // in the improvement goal (#410), an open question logged or answered (#414).
  const refresh = useCallback(async () => {
    const asked = ++latest.current
    const answer = await fetchOverview()
    if (asked !== latest.current) return true
    if (answer.ok) setState({ status: 'ready', overview: answer.overview })
    return answer.ok
  }, [])
  return { state, load, retry, noteAssessed, goalChanged, roundChanged: refresh, refresh }
}
