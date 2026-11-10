import { useCallback, useEffect, useState } from 'react'
import { changeGoal, type OverviewState } from './overview.ts'
import type { PassState } from './profilePass.ts'
import type { Goal } from './report.ts'

/** The app opens under a company just made in company setup (#447), with the Goal chosen there: it sets that Goal and
 * runs the company's first profile pass. Only a create makes one. */
export type Arrival = { goal: Goal }

/** Where the arrival sequence is: waiting on the first overview read, the Goal change out, the pass to start, done. */
type Step = 'overview' | 'goal' | 'pass' | 'done'

type Needs = {
  /** The overview as the page holds it: the sequence waits for its first read. */
  overview: OverviewState
  /** Reads the overview again after the Goal change (`useOverview().goalChanged`). */
  goalChanged: (goal: Goal) => Promise<boolean>
  /** The page's profile pass and its start (`useProfilePass()`): the sequence is over once the pass has started. */
  pass: PassState
  startPass: () => Promise<void>
}

/**
 * The arrival sequence on a company just made (#447). The arrival is taken once, as the app mounts: a later change to
 * it (ProfiledApp clearing it once used) changes nothing here. Then, in order:
 * 1. it waits for the first overview read, so no read of its own overtakes that one;
 * 2. when the overview's Goal isn't the one chosen (or the overview couldn't be read), it changes the Goal and reads
 *    the overview again. A failure keeps the company and says so (`goalNotSet`) until a later Goal change goes
 *    through; a 401 hands over to the sign-in and ends the sequence there, with no pass;
 * 3. it starts the profile pass.
 * `arriving` holds until the pass has started (or the sequence ended), so the hero offers no second Goal change or pass
 * meanwhile. The steps move on during render, from what the page holds (React's way of adjusting state on a change),
 * or as the Goal change answers; the effects only make the calls. Each call starts on an update, never on the mount,
 * so StrictMode's replayed mount sends nothing twice and needs no ref.
 */
export function useFirstPass(arrival: Arrival | null, { overview, goalChanged, pass, startPass }: Needs) {
  const [goal] = useState(() => arrival?.goal)
  const [step, setStep] = useState<Step>(goal === undefined ? 'done' : 'overview')
  const [goalNotSet, setGoalNotSet] = useState<Goal | undefined>(undefined)

  if (step === 'overview' && overview.status !== 'loading') {
    setStep(overview.status === 'ready' && overview.overview.goal === goal ? 'pass' : 'goal')
  }
  if (step === 'pass' && pass.kind !== 'idle') setStep('done')

  useEffect(() => {
    if (step !== 'goal' || goal === undefined) return
    let current = true // false once the app closed (a switch): nothing more happens under that company
    void changeGoal(goal).then((result) => {
      if (!current) return
      if (result === 'signed-out') return setStep('done')
      if (result === 'changed') void goalChanged(goal)
      else setGoalNotSet(goal)
      setStep('pass')
    })
    return () => { current = false }
  }, [step, goal, goalChanged])

  useEffect(() => {
    if (step === 'pass') void startPass()
  }, [step, startPass])

  /** A later Goal change went through: the note that the chosen one couldn't be set is over. */
  const goalSet = useCallback(() => setGoalNotSet(undefined), [])
  return { arriving: step !== 'done', goalNotSet, goalSet }
}
