import { useEffect, useState } from 'react'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** The lead's review rounds from `GET /api/rounds` (#571), for the Company page's Rounds section (#572). */

/** A round as the listing serves it: `number` its position from 1 in the order opened; `opened_at` null for the
 * first round, `closed_at` null while open (ISO times). */
export type ListedRound = {
  number: number
  name: string
  opened_at: string | null
  closed_at: string | null
  before_fresh_start: boolean
}

/** The rounds oldest first (the open one last), and the number of the default choice to compare with, the last round
 * closed since the fresh start, or null. */
export type RoundList = { rounds: ListedRound[]; default: number | null }

/** The page's own sentence when the rounds can't be read. */
export const ROUNDS_FAILED = 'The review rounds could not be loaded.'

/** A round's comparison's address (the view is #573's). */
export const roundPath = (number: number) => `/company/rounds/${number}`

export async function fetchRounds(): Promise<{ ok: true; rounds: RoundList } | { ok: false; message: string }> {
  try {
    const response = await fetch('/api/rounds', { headers: await authHeaders() })
    if (response.ok) return { ok: true, rounds: (await response.json()) as RoundList }
    return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : ROUNDS_FAILED }
  } catch {
    return { ok: false, message: ROUNDS_FAILED }
  }
}

export type RoundsState = { status: 'loading' } | { status: 'ready'; rounds: RoundList }
  | { status: 'failed'; message: string }

/** The rounds, read as the caller mounts; `reloads`: bumped to read them again without the loading line (a round was
 * closed or started). `tryAgain` reads them again after a failure. As documents.ts' `useDocuments`. */
export function useRounds(reloads = 0): { state: RoundsState; tryAgain: () => void } {
  const [state, setState] = useState<RoundsState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const answer = { wanted: true }
    void fetchRounds().then((read) => answer.wanted && setState(read.ok
      ? { status: 'ready', rounds: read.rounds } : { status: 'failed', message: read.message }))
    return () => { answer.wanted = false }
  }, [attempt, reloads])
  const tryAgain = () => {
    setState({ status: 'loading' })
    setAttempt((n) => n + 1)
  }
  return { state, tryAgain }
}
