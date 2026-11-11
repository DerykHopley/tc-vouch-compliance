import { useEffect, useState } from 'react'
import { type Verdict, VERDICT_WORDS } from './assessment.ts'
import type { NowAndThen } from './overview.ts'
import type { ListedRound } from './rounds.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** The round comparison from `GET /api/rounds/{number}/comparison` (#571), for its view (#573; GLOSSARY: Round
 * comparison). Never called progress. */

/** How a control's accepted verdict changed since the round; the scope changes are counted apart. */
export type ChangeKind = 'better' | 'worse' | 'new' | 'unchanged' | 'new-since' | 'out-of-scope-since'

/** The change words the view shows, in the summary's order. */
export const CHANGE_WORDS: Record<ChangeKind, string> = {
  better: 'Better',
  worse: 'Worse',
  new: 'New',
  unchanged: 'Unchanged',
  'new-since': 'New since',
  'out-of-scope-since': 'Out of scope since',
}

/** One control: its accepted verdict then and now (null: not accepted); `proposed` while its newest assessment waits. */
export type ControlChange = {
  control_id: string
  chapter: string
  change: ChangeKind
  before: Verdict | null
  after: Verdict | null
  proposed: boolean
}

export type RoundComparison = {
  round: ListedRound
  /** Better, worse, new and unchanged only: the scope changes are left out. */
  changes: Record<'better' | 'worse' | 'new' | 'unchanged', number>
  /** `then` when the round closed, `now` as the overview counts it. */
  readiness: { accepted: NowAndThen; by_verdict: Record<Verdict, NowAndThen> }
  /** In the base framework's order. */
  controls: ControlChange[]
  remediation_done: { control_id: string; requirement_id: string; at: string }[]
  questions_answered: { control_id: string; question_id: number; at: string }[]
}

/** The view's own sentence when the comparison can't be read. */
export const COMPARISON_FAILED = 'The round comparison could not be loaded.'

/** A read: the comparison; `refused` with the server's sentence (404: no such round; 409: still open, or closed before
 * the fresh start), which no retry changes; or `failed`, which one may. */
export type ComparisonRead = { ok: true; comparison: RoundComparison }
  | { ok: false; refused: boolean; message: string }

export async function fetchComparison(number: number): Promise<ComparisonRead> {
  try {
    const response = await fetch(`/api/rounds/${number}/comparison`, { headers: await authHeaders() })
    if (response.ok) return { ok: true, comparison: (await response.json()) as RoundComparison }
    if (response.status === 404 || response.status === 409) {
      const { detail } = (await response.json()) as { detail?: unknown }
      if (typeof detail === 'string') return { ok: false, refused: true, message: detail }
    }
    return { ok: false, refused: false, message: sessionEnded(response) ? SESSION_ENDED : COMPARISON_FAILED }
  } catch {
    return { ok: false, refused: false, message: COMPARISON_FAILED }
  }
}

export type ComparisonState = { status: 'loading' } | { status: 'ready'; comparison: RoundComparison }
  | { status: 'refused' | 'failed'; message: string }

/** The comparison with round `number`, read as the caller mounts; `tryAgain` reads it again. As rounds.ts' `useRounds`. */
export function useComparison(number: number): { state: ComparisonState; tryAgain: () => void } {
  const [state, setState] = useState<ComparisonState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const answer = { wanted: true }
    void fetchComparison(number).then((read) => answer.wanted && setState(read.ok
      ? { status: 'ready', comparison: read.comparison }
      : { status: read.refused ? 'refused' : 'failed', message: read.message }))
    return () => { answer.wanted = false }
  }, [number, attempt])
  const tryAgain = () => {
    setState({ status: 'loading' })
    setAttempt((n) => n + 1)
  }
  return { state, tryAgain }
}

/** A chapter's controls: the changed ones first, then the unchanged ones (folded), each in the served order. */
export function changesFirst(controls: ControlChange[]): { changed: ControlChange[]; unchanged: ControlChange[] } {
  return {
    changed: controls.filter((c) => c.change !== 'unchanged'),
    unchanged: controls.filter((c) => c.change === 'unchanged'),
  }
}

/**
 * Where the lead was on each comparison, kept for this page's life (#573: Back from a working paper returns to the same
 * place): the chapters whose unchanged controls are unfolded, and the scroll position when a control was opened, used
 * once on the way back.
 */
type Place = { unfolded: string[]; scrollY: number | null }
const places = new Map<number, Place>()

export const placeOf = (number: number): Place => places.get(number) ?? { unfolded: [], scrollY: null }

export function keepPlace(number: number, change: Partial<Place>) {
  places.set(number, { ...placeOf(number), ...change })
}

/** Leaving for a view other than a working paper or a comparison: no comparison comes back to its old scroll. */
export function forgetScrolls() {
  for (const [number, place] of places) places.set(number, { ...place, scrollY: null })
}

/** An accepted verdict, or none, as a comparison names it (the Dossier's and the round comparison view's). */
export const acceptedWord = (verdict: Verdict | null) => verdict === null ? 'Not accepted' : VERDICT_WORDS[verdict]
