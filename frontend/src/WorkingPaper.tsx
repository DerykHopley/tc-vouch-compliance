import { VERDICT_WORDS } from './assessment.ts'
import type { Run } from './useAssessment.ts'

type Control = { id: string; title: string }

/** The control's state in words, with a dot: colour is never the only cue. */
export function RunChip({ run }: { run: Run }) {
  if (run.status === 'running') return <span className="chip v-running">Assessing</span>
  if (run.status === 'done') {
    const v = run.result.verdict
    return <span className={`chip v-${v}`}>{VERDICT_WORDS[v]}</span>
  }
  return <span className="chip v-none">Not assessed</span>
}

/** The assess button: busy while running, Retry when the service could not be reached. */
export function AssessButton({ control, run, onAssess }: { control: Control; run: Run; onAssess: () => void }) {
  const busy = run.status === 'running'
  const label = busy ? 'Assessing' : run.status === 'unreachable' ? 'Retry' : run.status === 'idle' ? 'Assess' : 'Assess again'
  return (
    <button className="btn btn-primary" onClick={onAssess} disabled={busy} aria-busy={busy}
      aria-label={label === 'Assess' ? `Assess ${control.id}` : undefined}>
      {label}
    </button>
  )
}

/** A minimal working paper: the agent's steps as they stream in, then the verdict and the draft answer. */
export default function WorkingPaper({ control, run, onAssess }: { control: Control; run: Run; onAssess: () => void }) {
  return (
    <section className="paper wrap" aria-label="Working paper">
      <div className="paper-head">
        <span className="lbl">Working paper · Nordlicht Komponenten GmbH</span>
        <h2><span className="mono">{control.id}</span> {control.title}</h2>
        <div className="paper-meta"><span>Verdict</span><RunChip run={run} /></div>
      </div>
      {run.steps.length > 0 && (
        <div className="sheet">
          <span className="lbl">{run.status === 'running' ? 'Assessing' : 'How Vouch reached this'}</span>
          <ol className="trace" aria-label="Agent steps" aria-live="polite">
            {run.steps.map((step, i) => <li key={i}><b>{step.node}</b> {step.text}</li>)}
          </ol>
        </div>
      )}
      {run.status === 'done' && (
        <section className="sheet" aria-label="Result">
          <span className="lbl">Draft answer</span>
          <RunChip run={run} />
          <p className="answer">{run.result.answer}</p>
        </section>
      )}
      {(run.status === 'failed' || run.status === 'unreachable') && (
        <p className="sheet notice" role="alert">{run.message}</p>
      )}
      <div><AssessButton control={control} run={run} onAssess={onAssess} /></div>
    </section>
  )
}
