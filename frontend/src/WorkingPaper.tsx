import { type AnchorHTMLAttributes, type ReactNode, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import {
  type Assessment, CLOSED_WORD, plural, REVIEW_WORDS, type RequirementVerdict, usageWords, type Verdict, VERDICT_WORDS, verdictLang,
} from './assessment.ts'
import { type ControlCard, controlPath, isoLabel, isReviewState, type Overview } from './overview.ts'
import { areas, COLUMN_WORDS, evidenceOf, indexLabel, languageName, type PaperRequirement } from './paper.ts'
import type { Run } from './useAssessment.ts'
import { useScrollMore } from './useScrollMore.ts'
import CitedQuote from './CitedQuote.tsx'
import { setUnsaved } from './leaving.ts'
import OpenQuestions from './OpenQuestions.tsx'
import Prose from './Prose.tsx'
import Remediation, { type Plan } from './Remediation.tsx'
import type { RemediationItem } from './remediation.ts'
import type { Goal } from './report.ts'
import { type Override, REVIEW_STEP_WORDS, reviewStep, rollUp, rollUpWords, withOverrides } from './review.ts'
import ReviewBar, { OverrideEditor } from './ReviewBar.tsx'
import StepText from './StepText.tsx'
import InlineConfirm from './InlineConfirm.tsx'
import VerdictChip from './VerdictChip.tsx'

/** The assess button's words for each state of the run; `running` is also the steps' label. */
const ACTION: Record<Run['status'], string> = {
  idle: 'Assess',
  running: 'Assessing',
  done: 'Assess again',
  failed: 'Assess again',
  unreachable: 'Retry',
}

/** The steps the trace marks apart (#232): a guard step and an overlay branch step, by their chip's border. */
// The class that marks a step's chip apart, by node name (docs/design.md, Agent steps).
const STEP_CLASS: Record<string, string> = { guard: 'guard', overlay_branch: 'branch' }

/** The run's answer language, once it is done: its verdicts are in it (#48, #200). */
const answerLanguage = (run: Run) => run.status === 'done' ? run.assessment.answer_language : undefined

/** The run's verdict chip: Assessing, the verdict (in the answer's language, #48), or Not assessed. */
const RunChip = ({ run }: { run: Run }) =>
  <VerdictChip verdict={run.status === 'done' ? run.assessment.verdict : null} assessing={run.status === 'running'}
    language={answerLanguage(run)} />

/**
 * The assess button: busy while running, Retry when the service could not be reached; `held` while another runs. On a
 * review in progress (`warnFirst`: at Plan fixes or Accept, #437) a click starts nothing: an in-app warning takes the
 * button's place (InlineConfirm, #441), with Assess <id> again anyway, which starts the run, and Keep the review (or
 * Esc), which closes it.
 */
function AssessButton({ id, run, held, onAssess, warnFirst = false }:
  { id: string; run: Run; held: boolean; onAssess: () => void; warnFirst?: boolean }) {
  const busy = run.status === 'running'
  return (
    <InlineConfirm label={ACTION[run.status]} ask={warnFirst} onConfirm={onAssess} confirmDisabled={held}
      confirmLabel={`Assess ${id} again anyway`} cancelLabel="Keep the review"
      trigger={{ className: 'btn btn-primary', disabled: busy || held, 'aria-busy': busy,
        'aria-label': `${ACTION[run.status]} ${id}` }}
      question={`Assessing ${id} again sets its review aside: the findings you confirmed and the remediation items you `
        + "decided won't carry over to the new assessment. Nothing is deleted; they stay in the audit trail."} />
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
  overview: Pick<Overview, 'company' | 'protection_level' | 'answer_language' | 'requirements' | 'controls'
    | 'questions' | 'new_evidence'> & { goal?: Goal }
  /** The open control's card. */
  card: ControlCard
  /** The control being assessed just now, if any (one run at a time, maybe of another control). */
  running: string | null
  /** A profile pass is running (#390): Assess waits for it, as for another control's run. */
  passing?: boolean
  run: Run
  /** The control's stored assessment is still being read: the paper shows neither verdict nor Assess yet. */
  checking?: boolean
  /** Starts a run of the control (a rejected one's carries the rejection note, on the server's side). */
  onAssess: () => void
  onOpen: (controlId: string) => void
  onOverview: () => void
  /** The breadcrumb's way back when the paper was opened from elsewhere than the Overview (#573: a round comparison):
   * its words and address; the Overview's without. */
  back?: { label: string; href: string; onBack: () => void }
  /** The lead's decision is recorded: the reviewed assessment. */
  onReviewed: (assessment: Assessment) => void
  /** A remediation item's approval or discard is recorded (#43): the item as kept, to put in the latest copy of the
   * thread's assessment (two decisions may land in either order). */
  onItemDecided: (threadId: string, item: RemediationItem) => void
  /** Reads the assessment again (another tab reviewed it first); false when it couldn't be. */
  onRefresh: () => Promise<boolean>
  /** An open question was logged or answered (#414): the overview is read again, for the list and the card's flags. */
  onQuestionsChanged: () => void
}

/**
 * The working paper (`docs/design.md`, Decisions: Screens): the control index beside the paper; the paper's head
 * (question, ISO reference, protection level, answer language), the agent's steps as they stream in, the verdict and
 * draft answer, then one row per requirement: its column, verdict, evidence kind, stale flag, reason and exact quotes.
 */
export default function WorkingPaper(props: PaperProps) {
  const { overview, card, running, passing = false, checking = false, onAssess, onOpen, onOverview, onReviewed, onRefresh,
    onItemDecided, onQuestionsChanged, back } = props
  // The lead's overrides, not yet recorded, recalculate the control's verdict on screen until Confirm findings; the
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
  // The requirement whose open editor holds input (a changed verdict or a typed reason) not yet Saved (#356).
  const [changedEditor, setChangedEditor] = useState<string | null>(null)
  const onChanged = useCallback((requirementId: string, changed: boolean) =>
    setChangedEditor((now) => changed ? requirementId : now === requirementId ? null : now), [])
  // The open editor's requirement while it holds input not yet Saved: another requirement's Change asks first (#559).
  const typedIn = editing !== null && changedEditor === editing ? editing : null
  // Leaving asks first while overrides are saved and not recorded (#182), or typed in the open editor and not yet
  // Saved (#356), an override opened again counted once; none once the paper is gone.
  const editorUnsaved = typedIn !== null && !overrides.some((o) => o.requirement_id === typedIn)
  const unsaved = overrides.length + (editorUnsaved ? 1 : 0)
  useEffect(() => setUnsaved(unsaved), [unsaved])
  useEffect(() => () => setUnsaved(0), [])
  const run = overridden(props.run, overrides)
  // Before an assessment the paper lists the profile's requirements; after one, the assessment's own.
  const judged = run.status === 'done' ? run.assessment.requirements : undefined
  // Why the control has its verdict when a row's differs from it (#212), as the lead's overrides change it.
  const explained = run.status === 'done' && judged
    ? rollUpWords(judged, overview.answer_language, run.assessment.verdict) : null
  const requirements = overview.requirements[card.id] ?? []
  const applying = requirements.filter((r) => r.not_applicable === null).length
  const review = run.status === 'done' ? run.assessment.status : undefined
  const elsewhere = running !== null && running !== card.id ? running : null
  const agentVerdicts: Record<string, Verdict> = Object.fromEntries(
    (assessed?.requirements ?? []).map((r) => [r.id, r.verdict]))
  // The step of a proposed control's review (#372), its items as the page holds them.
  const step = assessed ? reviewStep(assessed) : null
  const remediation = assessed?.remediation ?? []
  // At Confirm findings the requirements can be overridden; after, they show the overrides the lead recorded.
  const recorded = assessed?.review?.overrides ?? assessed?.confirmed ?? []
  const marks: Overrides = step === 'confirm-findings'
    ? { editable: true, saved: overrides, agentVerdicts,
      replaced: Object.fromEntries(overrides.map((o) => [o.requirement_id, agentVerdicts[o.requirement_id]])) }
    : { editable: false, saved: [], agentVerdicts,
      replaced: Object.fromEntries(recorded.map((o) => [o.requirement_id, o.agent_verdict])) }
  const save = (next: Override) => {
    setOverrides((now) => [...now.filter((o) => o.requirement_id !== next.requirement_id), next])
    setEditing(null)
  }
  const undo = (id: string) => setOverrides((now) => now.filter((o) => o.requirement_id !== id))
  const assessingElsewhere = elsewhere !== null ? <AssessingElsewhere controlId={elsewhere} onOpen={onOpen} />
    : passing && <p className="field-note">Vouch is running a profile pass. One run at a time.</p>
  const held = elsewhere !== null || passing
  // The improvement plan (#410), in the Internal improvement goal only: each item's priority and done mark; a
  // re-assessment suggested once every approved item of the accepted control is done; Closed from its card.
  const improving = overview.goal === 'internal-improvement'
  const plan: Plan | undefined = improving ? { accepted: review === 'accepted' } : undefined
  const approvedItems = remediation.filter((i) => i.status === 'approved')
  const reassess = improving && review === 'accepted' && approvedItems.length > 0 && approvedItems.every((i) => i.done)
  const closed = improving && review === 'accepted' && card.closed === true
  // The open questions' reasons (#414), in both goals, while the control's newest assessment is accepted.
  const questionReasons = review === 'accepted' ? card.reassess_reasons ?? [] : []
  // A newer version of a document the newest assessment cites (#586): whatever its review state.
  const newEvidence = card.reassess_reasons?.includes('new-evidence') ? overview.new_evidence?.[card.id] ?? [] : []
  // Not applicable as a whole under the profile (#454): its chip and the profile's reason, and no assessment to start.
  const excluded = card.status === 'not-applicable'
  return (
    <div className="wp">
      <ControlIndex controls={overview.controls} openId={card.id} running={running} run={run} onOpen={onOpen}
        language={overview.answer_language} improving={improving} />
      <section className="paper wrap" aria-label="Working paper">
        <nav className="crumbs" aria-label="Breadcrumb">
          {back
            ? <a href={back.href} onClick={(e) => { e.preventDefault(); back.onBack() }}>{back.label}</a>
            : <a href="/" onClick={(e) => { e.preventDefault(); onOverview() }}>Overview</a>}
          <span aria-hidden="true">›</span>
          <span className="mono" aria-current="page">{card.id}</span>
        </nav>
        <div className="paper-head">
          <span className="lbl">Working paper · {overview.company}</span>
          <h2><span className="mono">{card.id}</span> <span lang={overview.answer_language}>{card.question}</span></h2>
          <div className="paper-meta">
            <span>{isoLabel(card.iso_27001)}</span>
            <span>Protection level: {overview.protection_level}</span>
            <span>Answer language: {languageName(overview.answer_language)}</span>
          </div>
          {!checking && (
            <div className="paper-meta">
              <span>Verdict</span>
              {excluded ? <VerdictChip verdict="not-applicable" language={overview.answer_language} /> : <RunChip run={run} />}
              {review && <span className={`tag tag-${review}`}>{closed ? CLOSED_WORD : REVIEW_WORDS[review]}</span>}
              {/* The step a proposed control's review is at, near the head (#467), as its bar may be out of view. */}
              {step && <span className="review-step-now">Review step: {REVIEW_STEP_WORDS[step]}</span>}
            </div>
          )}
          {excluded && card.not_applicable && (
            <p className="field-note" lang={overview.answer_language}>{card.not_applicable}</p>
          )}
          {!checking && explained && (
            <p className="field-note roll-up" lang={verdictLang(overview.answer_language)}>{explained}</p>
          )}
        </div>
        {checking ? <p className="field-note" role="status">Reading the working paper…</p> : (
          <>
            {run.steps.length > 0 && (
              <div className="sheet">
                <span className="lbl">{run.status === 'running' ? ACTION.running : 'How Vouch reached this'}</span>
                <ol className="trace" aria-label="Agent steps" aria-live="polite">
                  {run.steps.map((step, i) => (
                    <li key={i} className={STEP_CLASS[step.node]}>
                      <span className="step-node">{step.node}</span> <span><StepText step={step} language={overview.answer_language} /></span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
            {run.status === 'done' && (
              <section className="sheet" aria-label="Verdict and draft answer">
                <span className="lbl">Draft answer</span>
                <RunChip run={run} />
                <div className="answer" lang={run.assessment.answer_language}><Prose text={run.assessment.answer} /></div>
                {run.usage && (
                  <section aria-label="Usage"><p className="field-note mono">{usageWords(run.usage)}</p></section>
                )}
              </section>
            )}
            {(run.status === 'failed' || run.status === 'unreachable') && (
              <p className="sheet notice" role="alert">{run.message}</p>
            )}
            {review !== 'rejected' && !excluded && <div className="assess">
              {run.status === 'idle' && (
                <p className="field-note">
                  Not assessed yet. {plural(applying, 'requirement')} {applying === 1 ? 'applies' : 'apply'} in this
                  profile.
                </p>
              )}
              {reassess && (
                <p className="field-note">
                  Every approved remediation item is done. Assess {card.id} again: the control is Closed once that
                  assessment is accepted with no gaps.
                </p>
              )}
              {questionReasons.includes('question-answered') && (
                <p className="field-note">
                  An open question was answered. Assess {card.id} again so its verdict takes the answer into account.
                </p>
              )}
              {questionReasons.includes('question-overdue') && (
                <p className="field-note">An open question is overdue. Assess {card.id} again with what is known now.</p>
              )}
              {newEvidence.length > 0 && (
                <p className="field-note">
                  A new version of {newEvidence.join(' and ')} was added. Assess {card.id} again so its verdict takes it
                  into account.
                </p>
              )}
              {/* Keyed by the assessment shown, so a warning open on one never shows on another. */}
              <AssessButton key={assessed?.thread_id} id={card.id} run={run} held={held} onAssess={() => onAssess()}
                warnFirst={step === 'plan-fixes' || step === 'accept'} />
              {assessingElsewhere}
            </div>}
            {judged ? (
              <JudgedRequirements judged={judged} listed={requirements} overrides={marks} editing={editing}
                typedIn={typedIn} language={answerLanguage(run)} staleWord={assessed?.stale_word} currentWord={assessed?.current_word}
                statementNote={assessed?.statement_note} statementWord={assessed?.statement_word}
                onEdit={setEditing} onChanged={onChanged} onSave={save} onUndo={undo} items={{
                  shown: remediation, threadId: assessed?.thread_id ?? '',
                  // Plan fixes, once the findings are confirmed; an accept from before #372 may still have drafts.
                  decidable: step === 'plan-fixes' || step === 'accept' || review === 'accepted',
                  onDecided: (item) => { if (assessed?.thread_id) onItemDecided(assessed.thread_id, item) },
                  plan,
                }} />
            ) : <Requirements requirements={requirements} language={overview.answer_language} />}
            {assessed?.status && assessed.thread_id && (
              <ReviewBar key={`${assessed.thread_id}:${reads}`} status={assessed.status} threadId={assessed.thread_id}
                review={assessed.review} beforeThreeStep={assessed.before_three_step_review} step={step}
                overrides={overrides} editing={editing} onReviewed={onReviewed}
                items={{ decided: remediation.filter((i) => i.status !== 'draft').length, total: remediation.length }}
                onRefresh={onRefresh} assessAgain={<>
                  <AssessButton id={card.id} run={run} held={held} onAssess={() => onAssess()} />
                  {assessingElsewhere}
                </>} />
            )}
            <OpenQuestions key={card.id} controlId={card.id} questions={overview.questions?.[card.id] ?? []}
              onChanged={onQuestionsChanged} />
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
  /** The profile's answer language: the verdicts' (#200). */
  language: string
  /** The Internal improvement goal: a Closed card's tag reads Closed (#410). */
  improving: boolean
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

/** The control index, by area: each control with its verdict and review state; the open one marked. Its list stays in
 * view beside the paper as the paper scrolls and scrolls on its own (#466): the open control's row is brought into
 * view in it when the paper opens, and a focused row too, as Firefox leaves one cut off at the list's foot. Stacked
 * above the paper (1000px or less), the index scrolls in its own box: `data-more` marks it while rows lie below its
 * foot, which app.css fades (#555). */
function ControlIndex({ controls, openId, running, run, onOpen, language, improving }: IndexProps) {
  const list = useRef<HTMLDivElement>(null)
  const nav = useRef<HTMLElement>(null)
  const more = useScrollMore(nav, list, 'y')
  useEffect(() => {
    list.current?.querySelector('[aria-current="page"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [openId])
  return (
    <nav className="wp-index" aria-label="Controls" ref={nav} data-more={more || undefined}>
      <div className="wp-index-list" ref={list}
        onFocus={(e) => e.target.closest('.wp-row')?.scrollIntoView?.({ block: 'nearest' })}>
        {areas(controls).map(([area, cards]) => (
          <div key={area}>
            <h3>{area}</h3>
            {cards.map((card) => {
              const open = card.id === openId
              const done = open && run.status === 'done' ? run.assessment : null
              const assessing = open ? run.status === 'running' : card.id === running
              const verdict = done ? done.verdict : card.verdict
              const status = done ? done.status : isReviewState(card.status) ? card.status : undefined
              return (
                <PaperLink key={card.id} id={card.id} onOpen={onOpen} className="wp-row"
                  aria-current={open ? 'page' : undefined}>
                  <span className="mono">{card.id}</span>
                  <span className="wp-state">
                    <VerdictChip verdict={verdict} assessing={assessing} language={language} />
                    {!assessing && status && <span className={`tag tag-${status}`}>
                      {improving && status === 'accepted' && card.closed ? CLOSED_WORD : REVIEW_WORDS[status]}</span>}
                  </span>
                  <span className="wp-q" lang={language}>{indexLabel(card.question)}</span>
                </PaperLink>
              )
            })}
          </div>
        ))}
      </div>
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
function Requirements({ requirements, language }: { requirements: PaperRequirement[]; language: string }) {
  return (
    <div className="table-wrap">
      <table className="reqs" aria-label="Requirements">
        <thead><tr><th scope="col">Req.</th><th scope="col">Requirement</th></tr></thead>
        <tbody>
          {requirements.map((r) => (
            <tr key={r.id}>
              <RequirementId id={r.id} column={r.column} source={r.source} />
              <td className="rq">
                <span lang={language}>{r.text}</span>
                {r.not_applicable && <p className="why">Not applicable: <span lang={language}>{r.not_applicable}</span></p>}
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
 * overrides not yet recorded, `agentVerdicts` every requirement's own. `editable` at Confirm findings (#372), when each
 * requirement that applies has "Change".
 */
type Overrides = {
  editable: boolean
  replaced: Record<string, Verdict>
  saved: Override[]
  agentVerdicts: Record<string, Verdict>
}

/** The assessment's remediation items, one per requirement not or partly evidenced (#43, #212): `decidable` once its
 * findings are confirmed (Plan fixes, #372). */
type RemediationItems = {
  shown: RemediationItem[]
  threadId: string
  decidable: boolean
  onDecided: (item: RemediationItem) => void
  /** The improvement plan (#410); undefined under Audit readiness. */
  plan?: Plan
}

type JudgedProps = {
  judged: RequirementVerdict[]
  listed: PaperRequirement[]
  overrides: Overrides
  /** The requirement whose override is open. */
  editing: string | null
  /** The open editor's requirement while it holds input not yet Saved (#559): another's Change asks first. */
  typedIn: string | null
  onEdit: (requirementId: string | null) => void
  /** Whether the requirement's open editor holds input not yet Saved (#356). */
  onChanged: (requirementId: string, changed: boolean) => void
  onSave: (override: Override) => void
  onUndo: (requirementId: string) => void
  items: RemediationItems
  /** The assessment's answer language: its requirements' verdicts are in it (#200). */
  language?: string
  /** A stale citation's flag word, served in the answer language with each stale reason (#359); English's when the
   * API sends none. */
  staleWord?: string
  /** The evidence column's word for evidence that isn't stale, served in the answer language (#484); English's when
   * the API sends none. */
  currentWord?: string
  /** Under the quotes of a requirement a lead's statement caps (#439), served in the answer language; English's when
   * the API sends none. */
  statementNote?: string
  /** The evidence column's word for a lead's statement (#498), served in the answer language; English's when the API
   * sends none. */
  statementWord?: string
}

/**
 * The assessment's requirements: verdict, evidence kind and stale flag, then the reason and the exact quotes. An
 * overlay requirement's source comes from the profile's list (`listed`), as the assessment doesn't carry it. An
 * overridden requirement says what the agent's verdict was; at Confirm findings, "Change" opens a row to override
 * it. A requirement not or partly evidenced shows its remediation item on a row of its own under it, spanning the
 * table (#43, #212, #464; under the override row while that is open); one overridden to evidenced on screen hides its
 * draft, which the review drops. On a phone each row is a stacked card, by CSS, its text clamped to 2 lines with a
 * toggle (`RequirementText`, #211), and its item closes the card.
 */
/** A requirement's text cell. On a phone (#211) the text is clamped to 2 lines, and "Show full requirement" /
 * "Show less" toggles it (`aria-expanded`), offered only when the clamp hides some of it (measured again as the width
 * changes and as the web fonts load, #516; where nothing can be measured, it is offered). On a desktop the toggle is
 * hidden and the text shown whole. */
function RequirementText({ text, language }: { text: string; language?: string }) {
  const [expanded, setExpanded] = useState(false)
  const [cut, setCut] = useState(true)
  const shown = useRef<HTMLSpanElement>(null)
  const id = useId()
  useLayoutEffect(() => {
    const el = shown.current
    if (!el || expanded) return
    const measure = () => setCut(el.clientHeight === 0 || el.scrollHeight > el.clientHeight + 1)
    measure()
    // A web font that loads after this rewraps the text in a box the clamp keeps 2 lines high, so no resize fires
    // (#516): measured again once the fonts are ready, and after any font that loads later.
    // Stops this measure's font listeners when the text changes, it expands, or the cell goes.
    const stopMeasuring = new AbortController()
    const fonts = document.fonts as FontFaceSet | undefined
    fonts?.ready.then(() => { if (!stopMeasuring.signal.aborted) measure() })
    fonts?.addEventListener('loadingdone', measure, { signal: stopMeasuring.signal })
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(el)
    return () => {
      stopMeasuring.abort()
      observer?.disconnect()
    }
  }, [expanded, text])
  return (
    <td className="rq">
      <span ref={shown} id={id} className={expanded ? 'rq-text' : 'rq-text clamped'} lang={language}>{text}</span>
      {(cut || expanded) && (
        <button className="btn btn-ghost rq-toggle" aria-expanded={expanded} aria-controls={id}
          onClick={() => setExpanded((now) => !now)}>{expanded ? 'Show less' : 'Show full requirement'}</button>
      )}
    </td>
  )
}

/** The judged table's columns: the override and remediation item rows span them all (#464). */
const JUDGED_COLUMNS = 5

/** The statement note in English, for an API that serves none (#439); `vouch.language` holds it in each language. */
const STATEMENT_NOTE = "A lead's statement can't fully evidence a requirement: a document or record that shows it is "
  + 'needed to close it.'

function JudgedRequirements({ judged, listed, overrides, editing, typedIn, onEdit, onChanged, onSave, onUndo, items, language,
  staleWord = 'Stale', currentWord = 'Current', statementNote = STATEMENT_NOTE,
  statementWord = "Lead's statement" }: JudgedProps) {
  const source = (id: string) => listed.find((r) => r.id === id)?.source
  // Closing a requirement's override (Save or Cancel) gives focus back to its "Change" (#226): only when focus fell
  // to the page with the form, never taken from where the lead has moved it meanwhile.
  const changeButtons = useRef<Record<string, HTMLButtonElement | null>>({})
  const wasEditing = useRef<string | null>(null)
  useEffect(() => {
    const fell = !document.activeElement || document.activeElement === document.body
    if (wasEditing.current && editing === null && fell) changeButtons.current[wasEditing.current]?.focus()
    wasEditing.current = editing
  }, [editing])
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
            const { kinds, stale, statement } = evidenceOf(r)
            const applies = r.verdict !== 'not-applicable'
            const replaced = overrides.replaced[r.id]
            const toFix = r.verdict === 'not-evidenced' || r.verdict === 'partly-evidenced'
            const item = toFix && items.threadId ? items.shown.find((i) => i.requirement_id === r.id) : undefined
            return [
              <tr key={r.id} className={item ? 'has-item' : undefined}>
                <RequirementId id={r.id} column={r.column} source={source(r.id)} />
                <RequirementText text={r.text} language={language} />
                <td className="verdict-col">
                  <div className="verdict-cell">
                    <VerdictChip verdict={r.verdict} language={language} />
                    {replaced && <span className="why">Overridden from {VERDICT_WORDS[replaced]}</span>}
                    {overrides.editable && applies && editing !== r.id && (
                      <span>
                        {/* Another requirement's typed override is dropped only once the lead says so (#559). */}
                        <InlineConfirm label="Change" trigger={{ 'aria-label': `Change ${r.id}` }}
                          triggerRef={(el) => { changeButtons.current[r.id] = el }}
                          ask={typedIn !== null}
                          question={typedIn ? `You have an override of ${typedIn} not yet saved. Drop it and change ${r.id}?` : ''}
                          confirmLabel={`Change ${r.id} anyway`} cancelLabel={`Keep editing ${typedIn}`}
                          onConfirm={() => onEdit(r.id)} />
                        {replaced && (
                          <button className="btn btn-ghost" aria-label={`Undo the override of ${r.id}`}
                            onClick={() => {
                              onUndo(r.id)
                              changeButtons.current[r.id]?.focus() // Undo goes; its Change stays (#226)
                            }}>Undo</button>
                        )}
                      </span>
                    )}
                  </div>
                </td>
                <td className="evidence-col">
                  {applies && (kinds.length === 0 && !statement ? <span className="why">No evidence</span> : (
                    <span className="evidence">
                      {kinds.map((k) => <span key={k} className={`kind kind-${k}`}>{k}</span>)}
                      {statement && <span className="why" lang={language}>{statementWord}</span>}
                      {kinds.length > 0 && ( // a statement alone is no document to be current or stale (#498)
                        <span className={stale ? 'flag' : 'why'} lang={language}>
                          {stale ? staleWord : currentWord}
                        </span>
                      )}
                    </span>
                  ))}
                </td>
                <td className="quotes-cell"><div>
                  <p className="why reason" lang={language}>{r.reason}</p>
                  {r.citations.map((c, i) => (
                    // By position: two quotes may read alike once redacted.
                    <CitedQuote key={i} quote={c.quote} lang={language}>
                      <span className="mono" data-doc-id={c.doc_id}>{c.doc_id} v{c.version} · {c.section}</span>
                      {!c.lead_statement && ( // a statement's section names it; it has no kind (#439)
                        <span className={`kind kind-${c.kind}`}>{c.kind}</span>
                      )}
                      {c.stale && ( // in the answer language, like the reason (#359)
                        <span className="flag" lang={language}>
                          {c.stale_because ? `${staleWord}: ${c.stale_because}` : staleWord}
                        </span>
                      )}
                      <span>Quote verified</span>
                    </CitedQuote>
                  ))}
                  {r.verdict === 'partly-evidenced' && r.citations.some((c) => c.lead_statement) && (
                    <p className="why" lang={language}>{statementNote}</p> // what the cap note's statement asks (#439)
                  )}
                </div></td>
              </tr>,
              overrides.editable && editing === r.id && (
                <tr key={`${r.id}-override`} className="override-row">
                  <td colSpan={JUDGED_COLUMNS}>
                    <OverrideEditor requirementId={r.id} agentVerdict={overrides.agentVerdicts[r.id] ?? r.verdict}
                      saved={overrides.saved.find((o) => o.requirement_id === r.id)} onCancel={() => onEdit(null)}
                      onSave={onSave} onChanged={onChanged} />
                  </td>
                </tr>
              ),
              item && (
                <tr key={`${r.id}-item`} className="remediation-row">
                  <td colSpan={JUDGED_COLUMNS}>
                    {/* A new key when the item changes, so its fields start from the kept values. */}
                    <Remediation key={`${item.status}:${item.owner_role}:${item.due_date}`} item={item}
                      threadId={items.threadId} decidable={items.decidable} onDecided={items.onDecided} language={language}
                      plan={items.plan} />
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
