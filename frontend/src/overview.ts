import { useCallback, useRef, useState } from 'react'
import type { ReviewStatus, Verdict } from './assessment.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** The Dossier overview from `GET /api/overview` (#40), as `vouch.dossier.overview` sends it. */

/** A control's review state: not assessed, proposed (waiting for the lead's review), accepted or rejected. */
export type ReviewState = 'not-assessed' | ReviewStatus

export type ControlCard = { id: string; question: string; iso_27001: string; status: ReviewState; verdict: Verdict | null }

/** Counted from accepted verdicts only; `open` is every control not accepted. */
export type Readiness = {
  controls: number
  accepted: number
  by_verdict: Record<Verdict, number>
  open: number
  remediation_items: number
}

export type Quote = {
  control_id: string
  requirement_id: string
  doc_id: string
  version: string
  section: string
  quote: string
  kind: 'documented' | 'implemented'
  stale: boolean
}

/** What the lead does next, on which control; null once every control is accepted. */
export type NextStep = { control_id: string; action: 'review' | 'assess-again' | 'assess' } | null

export type Overview = {
  company: string
  country: string
  readiness: Readiness
  /** How many controls are in each review state. */
  by_status: Record<ReviewState, number>
  next_step: NextStep
  controls: ControlCard[]
  quotes: Quote[]
}

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

export type OverviewState =
  | { status: 'loading' }
  | { status: 'ready'; overview: Overview }
  | { status: 'failed'; message: string }

/** The overview as the page shows it; `load` reads it again (only the latest answer is shown). */
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
  return { state, load, retry }
}
