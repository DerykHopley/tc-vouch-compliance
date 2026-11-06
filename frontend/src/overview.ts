import { useCallback, useRef, useState } from 'react'
import type { Assessment, Citation, ReviewStatus, Verdict } from './assessment.ts'
import type { PaperRequirement } from './paper.ts'
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

/** One checked quote on the overview: a citation's fields, less why it is stale, and what it is for. */
export type Quote = { control_id: string; requirement_id: string }
  & Pick<Citation, 'doc_id' | 'version' | 'section' | 'quote' | 'kind' | 'stale'>

/** What the lead does next, on which control; null once every control is accepted. */
export type NextStep = { control_id: string; action: 'review' | 'assess-again' | 'assess' } | null

export type Overview = {
  company: string
  country: string
  /** The working paper's head shows both (#41). */
  protection_level: 'normal' | 'high' | 'very high'
  /** ISO 639-1: the language the answers are written in; the interface stays English. */
  answer_language: string
  /** Each control's requirements under the profile, by control id: the working paper lists them before an
   * assessment, at no request of its own (#41). */
  requirements: Record<string, PaperRequirement[]>
  readiness: Readiness
  /** How many controls are in each review state. */
  by_status: Record<ReviewState, number>
  next_step: NextStep
  controls: ControlCard[]
  quotes: Quote[]
}

/** A control's ISO 27001 reference as the screens show it: "ISO A.5.15, A.8.5" (ISA writes "ISO 27001:2022: …"). */
export const isoLabel = (iso: string) => iso.replace(/^ISO 27001:2022:? /, 'ISO ')

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

/**
 * The overview as the page shows it; `load` reads it again (only the latest answer is shown). `noteAssessed` puts a
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
        controls: now.overview.controls.map((c) => c.id !== assessment.control_id ? c
          : { ...c, verdict: assessment.verdict, status: assessment.status ?? 'waiting-for-review' }),
      },
    })
  }, [])
  return { state, load, retry, noteAssessed }
}
