import { useCallback, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { assess, type Assessment, type Step, UNREACHABLE, type Usage } from './assessment.ts'

export type Run =
  | { status: 'idle'; steps: Step[] }
  | { status: 'running'; steps: Step[] }
  | { status: 'done'; steps: Step[]; assessment: Assessment; usage?: Usage }
  | { status: 'failed'; steps: Step[]; message: string }
  | { status: 'unreachable'; steps: Step[]; message: string }

/** The next task: the browser paints, and observers see, each step before the next one lands. */
const nextTask = () => new Promise((resolve) => setTimeout(resolve, 0))

/**
 * The page's assessment run, one at a time, of the control `start` names: held above the views, so it carries on
 * while the lead leaves the working paper. `start` is ignored while a run is in progress; `onDone` is called once a
 * run has its result, with it.
 */
export function useAssessment(onDone?: (assessment: Assessment) => void) {
  const [current, setCurrent] = useState<{ controlId: string | null; run: Run }>(
    { controlId: null, run: { status: 'idle', steps: [] } })
  const running = useRef(false) // a ref, so two clicks in one tick still start one run

  const start = useCallback(async (controlId: string) => {
    if (running.current) return
    running.current = true
    const setRun = (run: Run) => setCurrent({ controlId, run })
    const steps: Step[] = []
    setRun({ status: 'running', steps })
    try {
      for await (const item of assess(controlId)) {
        if (item.event === 'step') {
          steps.push(item.data)
          flushSync(() => setRun({ status: 'running', steps: [...steps] }))
          await nextTask()
        } else if (item.event === 'requirement') {
          continue // the result carries every requirement too; the page shows none yet
        } else if (item.event === 'result') {
          setRun({ status: 'done', steps, assessment: item.data.assessment, usage: item.data.usage })
          onDone?.(item.data.assessment)
        } else {
          setRun({ status: 'failed', steps, message: item.data.message })
        }
      }
    } catch {
      setRun({ status: 'unreachable', steps, message: UNREACHABLE })
    } finally {
      running.current = false
    }
  }, [onDone])

  return { run: current.run, controlId: current.controlId, start }
}
