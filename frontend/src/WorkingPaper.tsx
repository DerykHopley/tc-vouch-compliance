import { type Control, plural, REVIEW_WORDS, type RequirementVerdict, usageWords, VERDICT_WORDS } from './assessment.ts'
import { controlPath, type Overview } from './overview.ts'
import { areas, COLUMN_WORDS, evidenceOf, languageName, type PaperRequirement } from './paper.ts'
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

/** "← Overview": back to the Dossier, at its address. */
function ToOverview({ onOverview }: { onOverview: () => void }) {
  return (
    <a className="btn btn-ghost crumb" href="/" onClick={(e) => { e.preventDefault(); onOverview() }}>← Overview</a>
  )
}

type PaperProps = {
  /** The overview: the company; the profile's protection level, answer language and requirements; the cards (the
   * control index). */
  overview: Pick<Overview, 'company' | 'protection_level' | 'answer_language' | 'requirements' | 'controls'>
  control: Control
  /** The control being assessed just now, if any (one run at a time, maybe of another control). */
  running: string | null
  run: Run
  /** The control's stored assessment is still being read, so Assess isn't offered yet. */
  checking?: boolean
  onAssess: () => void
  onOpen: (controlId: string) => void
  onOverview: () => void
}

/**
 * The working paper (`docs/design.md`, Decisions: Screens): the control index beside the paper; the paper's head
 * (question, ISO reference, protection, answer language), the agent's steps as they stream in, the verdict and draft
 * answer, then one row per requirement: its column, verdict, evidence kind, stale flag, reason and exact quotes.
 */
export default function WorkingPaper(props: PaperProps) {
  const { overview, control, run, checking = false, onAssess, onOverview } = props
  // Before an assessment the paper lists the profile's requirements; after one, the assessment's own.
  const judged = run.status === 'done' ? run.assessment.requirements : undefined
  const requirements = overview.requirements[control.id] ?? []
  const applying = requirements.filter((r) => r.not_applicable === null).length
  const review = run.status === 'done' ? run.assessment.status : undefined
  const iso = overview.controls.find((c) => c.id === control.id)?.iso_27001
  return (
    <div className="wp">
      <ControlIndex {...props} />
      <section className="paper wrap" aria-label="Working paper">
        <div><ToOverview onOverview={onOverview} /></div>
        <div className="paper-head">
          <span className="lbl">Working paper · {overview.company}</span>
          <h2><span className="mono">{control.id}</span> {control.title}</h2>
          <div className="paper-meta">
            {iso && <span>{iso}</span>}
            <span>Protection: {overview.protection_level}</span>
            <span>Answer language: {languageName(overview.answer_language)}</span>
          </div>
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
        {!checking && (
          <div className="assess">
            {run.status === 'idle' && (
              <p className="field-note">Not assessed yet. {plural(applying, 'requirement')} {applying === 1 ? 'applies' : 'apply'} in this profile.</p>
            )}
            <AssessButton control={control} run={run} onAssess={onAssess} />
          </div>
        )}
        {judged ? <JudgedRequirements requirements={judged} /> : <Requirements requirements={requirements} />}
      </section>
    </div>
  )
}

/** The control index, by area: each control with its verdict and review state; the open one marked. */
function ControlIndex({ control, overview, running, run, onOpen }: PaperProps) {
  return (
    <nav className="wp-index" aria-label="Controls">
      {areas(overview.controls).map(([area, cards]) => (
        <div key={area}>
          <h3>{area}</h3>
          {cards.map((card) => {
            const open = card.id === control.id
            // The open control shows its own run: it may be newer than the overview read on landing.
            const done = open && run.status === 'done' ? run.assessment : null
            const assessing = open ? run.status === 'running' : card.id === running
            const verdict = done ? done.verdict : card.verdict
            const status = done ? done.status : card.status === 'not-assessed' ? undefined : card.status
            return (
              <a key={card.id} className="wp-row" href={controlPath(card.id)} aria-current={open ? 'page' : undefined}
                onClick={(e) => { e.preventDefault(); onOpen(card.id) }}>
                <span className="mono">{card.id}</span>
                <span className="wp-state">
                  {assessing ? <span className="chip v-assessing">Assessing</span>
                    : verdict ? <span className={`chip v-${verdict}`}>{VERDICT_WORDS[verdict]}</span>
                      : <span className="chip v-none">Not assessed</span>}
                  {!assessing && status && <span className={`tag tag-${status}`}>{REVIEW_WORDS[status]}</span>}
                </span>
                <span className="wp-q">{card.question}</span>
              </a>
            )
          })}
        </div>
      ))}
    </nav>
  )
}

/** A requirement's id and column; an overlay's requirement names its overlay. */
function RequirementId({ id, column, source }: { id: string; column: PaperRequirement['column']; source?: string }) {
  return (
    <th scope="row">
      <span className="mono">{id}</span>
      <span className="col">{COLUMN_WORDS[column]}</span>
      {column === 'overlay' && source && <span className="col">{source}</span>}
    </th>
  )
}

/** The requirements before any assessment: what the profile brings in, not applicable ones with their reason. */
function Requirements({ requirements }: { requirements: PaperRequirement[] }) {
  return (
    <div className="table-wrap">
      <table className="reqs" aria-label="Requirements">
        <thead><tr><th scope="col">Req.</th><th scope="col">Requirement (VDA ISA)</th></tr></thead>
        <tbody>
          {requirements.map((r) => (
            <tr key={r.id}>
              <RequirementId id={r.id} column={r.column} source={r.source} />
              <td className="rq">
                {r.text}
                {r.not_applicable && <p className="why">Not applicable: {r.not_applicable}</p>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The assessment's requirements: verdict, evidence kind and stale flag, then the reason and the exact quotes. */
function JudgedRequirements({ requirements }: { requirements: RequirementVerdict[] }) {
  return (
    <div className="table-wrap">
      <table className="reqs judged" aria-label="Requirements">
        <thead>
          <tr>
            <th scope="col">Req.</th><th scope="col">Requirement (VDA ISA)</th><th scope="col">Verdict</th>
            <th scope="col">Evidence</th><th scope="col">Reason and exact quotes</th>
          </tr>
        </thead>
        <tbody>
          {requirements.map((r) => {
            const { kinds, stale } = evidenceOf(r)
            const applies = r.verdict !== 'not-applicable'
            return (
              <tr key={r.id}>
                <RequirementId id={r.id} column={r.column} />
                <td className="rq">{r.text}</td>
                <td><span className={`chip v-${r.verdict}`}>{VERDICT_WORDS[r.verdict]}</span></td>
                <td>
                  {applies && (kinds.length === 0 ? <span className="why">No evidence</span> : (
                    <span className="evidence">
                      {kinds.map((k) => <span key={k} className={`kind kind-${k}`}>{k}</span>)}
                      <span className={stale ? 'flag' : 'why'}>{stale ? 'Stale' : 'Current'}</span>
                    </span>
                  ))}
                </td>
                <td className="quotes-cell"><div>
                  <p className="why">{r.reason}</p>
                  {r.citations.map((c, i) => (
                    // By position: two quotes may read alike once redacted.
                    <figure className="cite" key={i}>
                      <blockquote>{c.quote}</blockquote>
                      <figcaption>
                        <span className="mono">{c.doc_id} v{c.version} · {c.section}</span>
                        {c.stale && <span className="flag">Stale: {c.stale_because}</span>}
                        <span>Quote verified</span>
                      </figcaption>
                    </figure>
                  ))}
                </div></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** A link to a control that isn't in the dossier (`/controls/<unknown>`): says so, with the way back. */
export function ControlNotFound({ controlId, onOverview }: { controlId: string; onOverview: () => void }) {
  return (
    <section className="paper wrap" aria-label="Control not found">
      <div><ToOverview onOverview={onOverview} /></div>
      <div className="paper-head">
        <span className="lbl">Working paper</span>
        <h2>No control <span className="mono">{controlId}</span></h2>
      </div>
      <p className="answer">This dossier has no control with that number. The overview lists every control in it.</p>
    </section>
  )
}
