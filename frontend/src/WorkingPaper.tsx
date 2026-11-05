import { type Control, REVIEW_WORDS, usageWords, VERDICT_WORDS } from './assessment.ts'
import type { Run } from './useAssessment.ts'

/** The assess button's words for each state of the run; `running` is also the chip's and the steps' label. */
const ACTION: Record<Run['status'], string> = {
  idle: 'Assess',
  running: 'Assessing',
  done: 'Assess again',
  failed: 'Assess again',
  unreachable: 'Retry',
}

/** The control's state in words, with a dot: colour is never the only cue. */
export function RunChip({ run }: { run: Run }) {
  if (run.status === 'running') return <span className="chip v-assessing">{ACTION.running}</span>
  if (run.status === 'done') {
    const v = run.assessment.verdict
    return <span className={`chip v-${v}`}>{VERDICT_WORDS[v]}</span>
  }
  return <span className="chip v-none">Not assessed</span>
}

/** The assess button: busy while running, Retry when the service could not be reached. */
export function AssessButton({ control, run, onAssess }: { control: Control; run: Run; onAssess: () => void }) {
  const busy = run.status === 'running'
  return (
    <button className="btn btn-primary" onClick={onAssess} disabled={busy} aria-busy={busy}
      aria-label={`${ACTION[run.status]} ${control.id}`}>
      {ACTION[run.status]}
    </button>
  )
}

/** `checking`: the control's stored assessment is still being read, so Assess isn't offered yet. */
type PaperProps = { company: string; control: Control; run: Run; checking?: boolean; onAssess: () => void }

/** A minimal working paper: the agent's steps as they stream in, then the verdict and the draft answer. */
export default function WorkingPaper({ company, control, run, checking = false, onAssess }: PaperProps) {
  const review = run.status === 'done' ? run.assessment.status : undefined
  return (
    <section className="paper wrap" aria-label="Working paper">
      <div className="paper-head">
        <span className="lbl">Working paper · {company}</span>
        <h2><span className="mono">{control.id}</span> {control.title}</h2>
        <div className="paper-meta">
          <span>Verdict</span><RunChip run={run} />
          {review && <span className={`tag tag-${review}`}>{REVIEW_WORDS[review]}</span>}
        </div>
      </div>
      {run.steps.length > 0 && (
        <div className="sheet">
          <span className="lbl">{run.status === 'running' ? ACTION.running : 'How Vouch reached this'}</span>
          <ol className="trace" aria-label="Agent steps" aria-live="polite">
            {run.steps.map((step, i) => <li key={i}><b>{step.node}</b> {step.text}</li>)}
          </ol>
        </div>
      )}
      {run.status === 'done' && (
        <section className="sheet" aria-label="Verdict and draft answer">
          <span className="lbl">Draft answer</span>
          <RunChip run={run} />
          <p className="answer">{run.assessment.answer}</p>
          {run.usage && (
            <section aria-label="Usage"><p className="field-note mono">{usageWords(run.usage)}</p></section>
          )}
        </section>
      )}
      {(run.status === 'failed' || run.status === 'unreachable') && (
        <p className="sheet notice" role="alert">{run.message}</p>
      )}
      {!checking && <div><AssessButton control={control} run={run} onAssess={onAssess} /></div>}
    </section>
  )
}
