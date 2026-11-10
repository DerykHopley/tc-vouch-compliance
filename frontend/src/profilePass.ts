import { useCallback, useRef, useState } from 'react'
import { type ReviewStatus, UNREACHABLE, type Verdict } from './assessment.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** The profile pass (GLOSSARY.md) from `POST /api/profile-pass` (#371, #390): every open control assessed in one
 * run, each new assessment waiting for the lead's review. */

/** One new assessment of a pass, as the API lists it. */
export type PassAssessment = { thread_id: string; control_id: string; status: ReviewStatus; verdict: Verdict | null }

/** A control the pass left out, and why: its newest assessment is accepted, or its review is in progress (#437: its
 * findings confirmed, at Plan fixes or Accept). */
export type SkippedControl = { control_id: string; reason: 'accepted' | 'in-review' }

export type PassResult =
  | { kind: 'done'; assessments: PassAssessment[]; skipped: SkippedControl[] }
  /** The 422 offering the per-control agent: the prompt is over the threshold, or a source can't be read. */
  | { kind: 'refused'; message: string }
  /** The 429 of the pass's own daily limit: the per-control assessments have their own. */
  | { kind: 'limit'; message: string }
  | { kind: 'failed'; message: string }
  | { kind: 'unreachable'; message: string }

export const PASS_NOT_STARTED = 'The profile pass could not be started. Please try again later.'

/** Runs a profile pass and reads its answer; never throws. */
export async function runProfilePass(): Promise<PassResult> {
  let response: Response
  try {
    response = await fetch('/api/profile-pass', { method: 'POST', headers: await authHeaders() })
  } catch {
    return { kind: 'unreachable', message: UNREACHABLE }
  }
  if (sessionEnded(response)) return { kind: 'failed', message: SESSION_ENDED }
  const body = await response.json().catch(() => ({})) as
    { assessments?: PassAssessment[]; skipped?: SkippedControl[]; detail?: unknown; fallback?: unknown; limit?: unknown }
  if (response.ok) return { kind: 'done', assessments: body.assessments ?? [], skipped: body.skipped ?? [] }
  const message = typeof body.detail === 'string' ? body.detail : PASS_NOT_STARTED
  if (response.status === 422 && body.fallback === 'per-control') return { kind: 'refused', message }
  if (response.status === 429 && body.limit === 'profile-pass') return { kind: 'limit', message }
  return { kind: 'failed', message }
}

export type PassState = { kind: 'idle' } | { kind: 'running' } | PassResult

/**
 * The page's profile pass, held above the views as the per-control run is (`useAssessment`), so it carries on while
 * the lead opens a working paper and its result waits on the Dossier. `start` is ignored while a pass runs; `onDone`
 * is called once a pass made or skipped its assessments, with the ones it made.
 */
export function useProfilePass(onDone?: (made: PassAssessment[]) => void) {
  const [pass, setPass] = useState<PassState>({ kind: 'idle' })
  const running = useRef(false) // a ref, so two clicks in one tick still start one pass

  const start = useCallback(async () => {
    if (running.current) return
    running.current = true
    setPass({ kind: 'running' })
    try {
      const result = await runProfilePass()
      setPass(result)
      if (result.kind === 'done') onDone?.(result.assessments)
    } finally {
      running.current = false
    }
  }, [onDone])

  return { pass, start }
}
