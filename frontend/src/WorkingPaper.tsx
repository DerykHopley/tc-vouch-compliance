import { type AnchorHTMLAttributes, type ReactNode, useState } from 'react'
import { type Assessment, plural, REVIEW_WORDS, type RequirementVerdict, usageWords, type Verdict, VERDICT_WORDS }
  from './assessment.ts'
import { type ControlCard, controlPath, isoLabel, type Overview } from './overview.ts'
import { areas, COLUMN_WORDS, evidenceOf, languageName, type PaperRequirement } from './paper.ts'
import type { Run } from './useAssessment.ts'
import CitedQuote from './CitedQuote.tsx'
import Remediation from './Remediation.tsx'
import type { RemediationItem } from './remediation.ts'
import { type Override, rollUp, withOverrides } from './review.ts'
import ReviewBar, { OverrideEditor } from './ReviewBar.tsx'
import VerdictChip from './VerdictChip.tsx'

/** The assess button's words for each state of the run; `running` is also the steps' label. */
const ACTION: Record<Run['status'], string> = {
  idle: 'Assess',
  running: 'Assessing',
  done: 'Assess again',
  failed: 'Assess again',
  unreachable: 'Retry',
}

/** The run's verdict chip: Assessing, the verdict (in the answer's language, #48), or Not assessed. */
const RunChip = ({ run }: { run: Run }) =>
  <VerdictChip verdict={run.status === 'done' ? run.assessment.verdict : null} assessing={run.status === 'running'}
    language={run.status === 'done' ? run.assessment.answer_language : undefined} />

/** The assess button: busy while running, Retry when the service could not be reached; `held` while another runs. */
function AssessButton({ id, run, held, onAssess }: { id: string; run: Run; held: boolean; onAssess: () => void }) {
  const busy = run.status === 'running'
  return (
    <button className="btn btn-primary" onClick={onAssess} disabled={busy || held} aria-busy={busy}
      aria-label={`${ACTION[run.status]} ${id}`}>
      {ACTION[run.status]}
    </button>
  )
}

/** A link to a control's working paper, opened in place. */
function PaperLink({ id, onOpen, children, ...rest }: { id: string; onOpen: (id: string) => void; children: ReactNode }
  & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href' | 'onClick'>) {
  return <a href={controlPath(id)} onClick={(e) => { e.preventDefault(); onOpen(id) }} {...rest}>{children}</a>
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
  /** The open control's card. */
  card: ControlCard
  /** The control being assessed just now, if any (one run at a time, maybe of another control). */
  running: string | null
  run: Run
  /** The control's stored assessment is still being read: the paper shows neither verdict nor Assess yet. */
  checking?: boolean
  /** Starts a run of the control (a rejected one's carries the rejection note, on the server's side). */
  onAssess: () => void
  onOpen: (controlId: string) => void
  onOverview: () => void
  /** The lead's decision is recorded: the reviewed assessment. */
  onReviewed: (assessment: Assessment) => void
  /** A remediation item's approval or discard is recorded (#43): the item as kept, to put in the latest copy of the
   * thread's assessment (two decisions may land in either order). */
  onItemDecided: (threadId: string, item: RemediationItem) => void
  /** Reads the assessment again (another tab reviewed it first); false when it couldn't be. */
  onRefresh: () => Promise<boolean>
}

/**
 * The working paper (`docs/design.md`, Decisions: Screens): the control index beside the paper; the paper's head
 * (question, ISO reference, protection level, answer language), the agent's steps as they stream in, the verdict and
 * draft answer, then one row per requirement: its column, verdict, evidence kind, stale flag, reason and exact quotes.
 */
export default function WorkingPaper(props: PaperProps) {
  const { overview, card, running, checking = false, onAssess, onOpen, onOverview, onReviewed, onRefresh,
    onItemDecided } = props
  // The lead's overrides, not yet recorded, recalculate the control's verdict on screen until the decision; the
  // requirement whose override is open. Both belong to the assessment shown: when another is shown (another
  // control's, a new run's, the same one read again, or it reviewed), they are dropped, and the review bar is shown
  // afresh. A decision on an earlier one landing late can't touch them, as nothing but this resets them.
  const assessed = props.run.status === 'done' ? props.run.assessment : null
  const [shownFor, setShownFor] = useState<Assessment | null>(assessed)
  const [overrides, setOverrides] = useState<Override[]>([])
  const [editing, setEditing] = useState<string | null>(null)
  const [reads, setReads] = useState(0)
  if (assessed !== shownFor) {
    setShownFor(assessed)
    setOverrides([])
    setEditing(null)
    setReads((n) => n + 1)
  }
  const run = overridden(props.run, overrides)
  // Before an assessment the paper lists the profile's requirements; after one, the assessment's own.
  const judged = run.status === 'done' ? run.assessment.requirements : undefined
  const requirements = overview.requirements[card.id] ?? []
  const applying = requirements.filter((r) => r.not_applicable === null).length
  const review = run.status === 'done' ? run.assessment.status : undefined
  const elsewhere = running !== null && running !== card.id ? running : null
  const agentVerdicts: Record<string, Verdict> = Object.fromEntries(
    (assessed?.requirements ?? []).map((r) => [r.id, r.verdict]))
  // A proposed control's requirements can be overridden; a reviewed one's show the overrides the lead made.
  const marks: Overrides = review === 'waiting-for-review'
    ? { editable: true, saved: overrides, agentVerdicts,
      replaced: Object.fromEntries(overrides.map((o) => [o.requirement_id, agentVerdicts[o.requirement_id]])) }
    : { editable: false, saved: [], agentVerdicts,
      replaced: Object.fromEntries((assessed?.review?.overrides ?? []).map((o) => [o.requirement_id, o.agent_verdict])) }
  const save = (next: Override) => {
    setOverrides((now) => [...now.filter((o) => o.requirement_id !== next.requirement_id), next])
    setEditing(null)
  }
  const undo = (id: string) => setOverrides((now) => now.filter((o) => o.requirement_id !== id))
  const assessingElsewhere = elsewhere !== null && <AssessingElsewhere controlId={elsewhere} onOpen={onOpen} />
  return (
    <div className="wp">
      <ControlIndex controls={overview.controls} openId={card.id} running={running} run={run} onOpen={onOpen} />
      <section className="paper wrap" aria-label="Working paper">
        <div><ToOverview onOverview={onOverview} /></div>
        <div className="paper-head">
          <span className="lbl">Working paper · {overview.company}</span>
          <h2><span className="mono">{card.id}</span> {card.question}</h2>
          <div className="paper-meta">
            <span>{isoLabel(card.iso_27001)}</span>
            <span>Protection level: {overview.protection_level}</span>
            <span>Answer language: {languageName(overview.answer_language)}</span>
          </div>
          {!checking && (
            <div className="paper-meta">
              <span>Verdict</span><RunChip run={run} />
              {review && <span className={`tag tag-${review}`}>{REVIEW_WORDS[review]}</span>}
            </div>
          )}
        </div>
        {checking ? <p className="field-note" role="status">Reading the working paper…</p> : (
          <>
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
                <p className="answer" lang={run.assessment.answer_language}>{run.assessment.answer}</p>
                {run.usage && (
                  <section aria-label="Usage"><p className="field-note mono">{usageWords(run.usage)}</p></section>
                )}
              </section>
            )}
            {(run.status === 'failed' || run.status === 'unreachable') && (
              <p className="sheet notice" role="alert">{run.message}</p>
            )}
            {review !== 'rejected' && <div className="assess">
              {run.status === 'idle' && (
                <p className="field-note">
                  Not assessed yet. {plural(applying, 'requirement')} {applying === 1 ? 'applies' : 'apply'} in this
                  profile.
                </p>
              )}
              <AssessButton id={card.id} run={run} held={elsewhere !== null} onAssess={() => onAssess()} />
              {assessingElsewhere}
            </div>}
            {judged ? (
              <JudgedRequirements judged={judged} listed={requirements} overrides={marks} editing={editing}
                onEdit={setEditing} onSave={save} onUndo={undo} items={{
                  shown: assessed?.remediation ?? [], threadId: assessed?.thread_id ?? '',
                  decidable: review === 'accepted',
                  onDecided: (item) => { if (assessed?.thread_id) onItemDecided(assessed.thread_id, item) },
                }} />
            ) : <Requirements requirements={requirements} />}
            {assessed?.status && assessed.thread_id && (
              <ReviewBar key={`${assessed.thread_id}:${reads}`} status={assessed.status} threadId={assessed.thread_id}
                review={assessed.review} overrides={overrides} editing={editing} onReviewed={onReviewed}
                onRefresh={onRefresh} assessAgain={<>
                  <AssessButton id={card.id} run={run} held={elsewhere !== null} onAssess={() => onAssess()} />
                  {assessingElsewhere}
                </>} />
            )}
          </>
        )}
      </section>
    </div>
  )
}

type IndexProps = {
  controls: ControlCard[]
  openId: string
  running: string | null
  /** The open control's run: it may be newer than its card. */
  run: Run
  onOpen: (controlId: string) => void
}

/** The run with the lead's overrides in place in its assessment, and the control's verdict recalculated from them. */
function overridden(run: Run, overrides: Override[]): Run {
  if (run.status !== 'done' || overrides.length === 0 || !run.assessment.requirements) return run
  const requirements = withOverrides(run.assessment.requirements, overrides)
  return { ...run, assessment: { ...run.assessment, requirements, verdict: rollUp(requirements) } }
}

/** "Vouch is assessing X. One assessment runs at a time.": why Assess is held, with a link to the run's paper. */
function AssessingElsewhere({ controlId, onOpen }: { controlId: string; onOpen: (id: string) => void }) {
  return (
    <p className="field-note">
      Vouch is assessing <PaperLink id={controlId} onOpen={onOpen} className="mono">{controlId}</PaperLink>.
      One assessment runs at a time.
    </p>
  )
}

/** The control index, by area: each control with its verdict and review state; the open one marked. */
function ControlIndex({ controls, openId, running, run, onOpen }: IndexProps) {
  return (
    <nav className="wp-index" aria-label="Controls">
      {areas(controls).map(([area, cards]) => (
        <div key={area}>
          <h3>{area}</h3>
          {cards.map((card) => {
            const open = card.id === openId
            const done = open && run.status === 'done' ? run.assessment : null
            const assessing = open ? run.status === 'running' : card.id === running
            const verdict = done ? done.verdict : card.verdict
            const status = done ? done.status : card.status === 'not-assessed' ? undefined : card.status
            return (
              <PaperLink key={card.id} id={card.id} onOpen={onOpen} className="wp-row"
                aria-current={open ? 'page' : undefined}>
                <span className="mono">{card.id}</span>
                <span className="wp-state">
                  <VerdictChip verdict={verdict} assessing={assessing} />
                  {!assessing && status && <span className={`tag tag-${status}`}>{REVIEW_WORDS[status]}</span>}
                </span>
                <span className="wp-q">{card.question}</span>
              </PaperLink>
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
        <thead><tr><th scope="col">Req.</th><th scope="col">Requirement</th></tr></thead>
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

/**
 * The lead's overrides on the requirements: `replaced` holds the agent's verdict of each overridden one, `saved` the
 * overrides not yet recorded, `agentVerdicts` every requirement's own. `editable` while the control is proposed,
 * when each requirement that applies has "Change".
 */
type Overrides = {
  editable: boolean
  replaced: Record<string, Verdict>
  saved: Override[]
  agentVerdicts: Record<string, Verdict>
}

/** The assessment's remediation items, one per not-evidenced requirement (#43): `decidable` once it is accepted. */
type RemediationItems = {
  shown: RemediationItem[]
  threadId: string
  decidable: boolean
  onDecided: (item: RemediationItem) => void
}

type JudgedProps = {
  judged: RequirementVerdict[]
  listed: PaperRequirement[]
  overrides: Overrides
  /** The requirement whose override is open. */
  editing: string | null
  onEdit: (requirementId: string | null) => void
  onSave: (override: Override) => void
  onUndo: (requirementId: string) => void
  items: RemediationItems
}

/**
 * The assessment's requirements: verdict, evidence kind and stale flag, then the reason and the exact quotes. An
 * overlay requirement's source comes from the profile's list (`listed`), as the assessment doesn't carry it. An
 * overridden requirement says what the agent's verdict was; on a proposed control, "Change" opens a row to override
 * it. A not-evidenced requirement shows its remediation item under its reason (#43).
 */
function JudgedRequirements({ judged, listed, overrides, editing, onEdit, onSave, onUndo, items }: JudgedProps) {
  const source = (id: string) => listed.find((r) => r.id === id)?.source
  return (
    <div className="table-wrap">
      <table className="reqs judged" aria-label="Requirements">
        <thead>
          <tr>
            <th scope="col">Req.</th><th scope="col">Requirement</th><th scope="col">Verdict</th>
            <th scope="col">Evidence</th><th scope="col">Reason and exact quotes</th>
          </tr>
        </thead>
        <tbody>
          {judged.map((r) => {
            const { kinds, stale } = evidenceOf(r)
            const applies = r.verdict !== 'not-applicable'
            const replaced = overrides.replaced[r.id]
            const item = items.shown.find((i) => i.requirement_id === r.id)
            return [
              <tr key={r.id}>
                <RequirementId id={r.id} column={r.column} source={source(r.id)} />
                <td className="rq">{r.text}</td>
                <td>
                  <div className="verdict-cell">
                    <VerdictChip verdict={r.verdict} />
                    {replaced && <span className="why">Overridden from {VERDICT_WORDS[replaced]}</span>}
                    {overrides.editable && applies && editing !== r.id && (
                      <span>
                        <button className="btn btn-ghost" aria-label={`Change ${r.id}`} onClick={() => onEdit(r.id)}>
                          Change
                        </button>
                        {replaced && (
                          <button className="btn btn-ghost" aria-label={`Undo the override of ${r.id}`}
                            onClick={() => onUndo(r.id)}>Undo</button>
                        )}
                      </span>
                    )}
                  </div>
                </td>
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
                    <CitedQuote key={i} quote={c.quote}>
                      <span className="mono">{c.doc_id} v{c.version} · {c.section}</span>
                      <span className={`kind kind-${c.kind}`}>{c.kind}</span>
                      {c.stale && <span className="flag">Stale: {c.stale_because}</span>}
                      <span>Quote verified</span>
                    </CitedQuote>
                  ))}
                  {item && items.threadId && (
                    // A new key when the item changes, so its fields start from the kept values.
                    <Remediation key={`${item.status}:${item.owner_role}:${item.due_date}`} item={item}
                      threadId={items.threadId} decidable={items.decidable} onDecided={items.onDecided} />
                  )}
                </div></td>
              </tr>,
              overrides.editable && editing === r.id && (
                <tr key={`${r.id}-override`} className="override-row">
                  <td colSpan={5}>
                    <OverrideEditor requirementId={r.id} agentVerdict={overrides.agentVerdicts[r.id] ?? r.verdict}
                      saved={overrides.saved.find((o) => o.requirement_id === r.id)} onCancel={() => onEdit(null)}
                      onSave={onSave} />
                  </td>
                </tr>
              ),
            ]
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
