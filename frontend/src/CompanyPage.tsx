import { type ReactNode, useEffect, useId, useState } from 'react'
import { type Column, plural } from './assessment.ts'
import { type CompanyFacts, COMPANY_FAILED, fetchCompany } from './company.ts'
import { useDocuments } from './documents.ts'
import Documents from './Documents.tsx'
import { GoalControl, RoundControl } from './GoalControls.tsx'
import type { Overview, OverviewState } from './overview.ts'
import { COLUMN_WORDS } from './paper.ts'
import type { Goal } from './report.ts'
import { SESSION_ENDED } from './session.ts'
import Rounds from './Rounds.tsx'
import { useRounds } from './rounds.ts'
import { LANGUAGES, PROTECTION_LEVELS } from './setup.ts'

/** The Company page (#505; `docs/design.md`, Components: Company page): the current company's facts, read-only apart
 * from the Goal, over a grid of panels (Goal, Review rounds, Rounds, Scope, Not applicable) beside the
 * Documents. Its words are English, as the interface; what the profile writes in its answer language (overlay names,
 * not-applicable reasons) carries that `lang`. The facts are read each time the page opens; the Goal and review
 * rounds are the overview's, which App reads. */

type State = { status: 'loading' } | { status: 'ready'; company: CompanyFacts } | { status: 'failed'; message: string }

const NOTES: Record<CompanyFacts['defined_in'], string> = {
  files: "This company is defined in files: its details can't be changed here, apart from the Goal.",
  setup: "This company was made in Company setup: its details can't be changed here, apart from the Goal.",
}

type CompanyPageProps = {
  /** App's overview: its Goal and review rounds feed the panels. */
  overview: OverviewState
  /** Reads the overview again (Try again, when it failed). */
  onRetryOverview: () => void
  onGoalChanged: (goal: Goal) => Promise<boolean>
  onRoundChanged: () => Promise<boolean>
  /** The arrival sequence is changing the Goal (#447): Change goal waits. */
  arriving: boolean
  goalNotSet?: Goal
  /** Bumped to read the documents again (their Cited by changed). */
  documentsReloads?: number
}

export default function CompanyPage({ overview, onRetryOverview, onGoalChanged, onRoundChanged, arriving, goalNotSet,
  documentsReloads = 0 }: CompanyPageProps) {
  const [read, setRead] = useState<State>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0) // bumped by Try again to read the company again
  useEffect(() => {
    const answer = { wanted: true } // until the page closes or reads again
    void fetchCompany().then((got) => answer.wanted && setRead(got.ok
      ? { status: 'ready', company: got.company } : { status: 'failed', message: got.message }))
    return () => { answer.wanted = false }
  }, [attempt])
  // The documents: read alongside the company, so the panel is ready with the page, and again after a lead adds one
  // in the panel (#585); a company Try again leaves them as read.
  const [added, setAdded] = useState(0)
  const documents = useDocuments(documentsReloads + added)
  // The rounds too (#572), read again after a round is closed or started in the Review rounds panel.
  const [roundsReloads, setRoundsReloads] = useState(0)
  const rounds = useRounds(roundsReloads)
  const roundChanged = () => {
    setRoundsReloads((n) => n + 1)
    return onRoundChanged()
  }
  // The overview as last read: a later read that fails (on arriving, after a pass or a chat assessment) keeps it, as
  // useOverview's refresh does, so only a first read that fails shows the notice.
  const [kept, setKept] = useState<Overview | null>(overview.status === 'ready' ? overview.overview : null)
  if (overview.status === 'ready' && overview.overview !== kept) setKept(overview.overview)
  const shownOverview = overview.status === 'ready' ? overview.overview : kept
  const overviewFailed = shownOverview === null && overview.status === 'failed' ? overview.message : null
  const tryAgain = () => {
    if (read.status === 'failed') {
      setRead({ status: 'loading' })
      setAttempt((n) => n + 1)
    }
    if (overviewFailed !== null) onRetryOverview()
  }

  const failed = read.status === 'failed' ? read.message
    : overviewFailed !== null ? (overviewFailed === SESSION_ENDED ? SESSION_ENDED : COMPANY_FAILED) : null
  if (failed !== null) {
    return (
      <main className="company wrap">
        <section className="sheet notice">
          <p role="alert" className="answer">{failed}</p>
          <button className="btn" onClick={tryAgain}>Try again</button>
        </section>
      </main>
    )
  }
  if (read.status !== 'ready' || shownOverview === null) {
    return <main className="company wrap"><p className="lbl" role="status">Loading the company…</p></main>
  }
  const { company } = read
  const lang = company.answer_language
  return (
    <main className="company wrap">
      <div className="company-head">
        <h1>{company.company}</h1>
        <dl className="company-facts">
          <div><dt className="lbl">Country</dt><dd>{company.country}</dd></div>
          <div><dt className="lbl">Answer language</dt><dd>{LANGUAGES.find((l) => l.code === lang)?.name ?? lang}</dd></div>
          <div><dt className="lbl">Base framework</dt><dd>{company.base_framework}</dd></div>
          <div><dt className="lbl">Protection level</dt><dd>{PROTECTION_LEVELS[company.protection_level].name}</dd></div>
          <div>
            <dt className="lbl">Overlays</dt>
            <dd>{company.overlays.length === 0 ? 'None'
              : company.overlays.map((o, i) => <span key={i}>{i > 0 && ', '}<span lang={lang}>{o.name}</span></span>)}
            </dd>
          </div>
        </dl>
        <p className="company-note">{NOTES[company.defined_in]}</p>
      </div>
      <div className="company-grid">
        {/* The panels come first in the page, as a phone shows them, so the reading order is the visual one. */}
        <div className="company-panels">
          <Panel title="Goal" className="goal-controls">
            <GoalControl overview={shownOverview} onChanged={onGoalChanged} arriving={arriving}
              notSet={goalNotSet} />
          </Panel>
          <Panel title="Review rounds" className="goal-controls">
            <RoundControl overview={shownOverview} onChanged={roundChanged} />
          </Panel>
          <Panel title="Rounds"><Rounds state={rounds.state} tryAgain={rounds.tryAgain} /></Panel>
          <Panel title="Scope"><Scope company={company} /></Panel>
          <Panel title="Not applicable"><NotApplicable company={company} /></Panel>
        </div>
        <section className="company-documents" aria-label="Documents">
          <Documents state={documents.state} tryAgain={documents.tryAgain} onAdded={() => setAdded((n) => n + 1)} />
        </section>
      </div>
    </main>
  )
}

/** One panel: a sheet headed by its title; `className` adds to it (`goal-controls` where the Goal or round control
 * sits, as on the Dossier's hero). */
function Panel({ title, className, children }: { title: string; className?: string; children: ReactNode }) {
  const id = useId()
  return (
    <section className={`sheet company-panel${className ? ` ${className}` : ''}`} aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {children}
    </section>
  )
}

/** The base framework's columns in its order (vouch.isa.COLUMN_PREFIX): the order the Scope panel lists them in. */
const BASE_COLUMNS: Column[] = ['must', 'should', 'high', 'very high']

function Scope({ company }: { company: CompanyFacts }) {
  const { scope, overlays, answer_language: lang } = company
  const level = PROTECTION_LEVELS[scope.level].name
  const brought: [Column, number][] = BASE_COLUMNS.flatMap((column): [Column, number][] => {
    const n = scope.requirements[column]
    return n === undefined ? [] : [[column, n]]
  })
  return (
    <>
      <p>
        {level} protection brings in {plural(scope.total, 'requirement')} of {company.base_framework}, in{' '}
        {plural(scope.controls, 'control')}.
      </p>
      <ul className="company-list">
        {brought.map(([column, n]) => <li key={column}>{COLUMN_WORDS[column]}: {n}</li>)}
      </ul>
      {overlays.length === 0 ? <p>No country overlay applies.</p>
        : overlays.map((o, i) => (
          <p key={i}><span lang={lang}>{o.name}</span> adds {plural(o.requirements, 'requirement')}.</p>
        ))}
    </>
  )
}

function NotApplicable({ company }: { company: CompanyFacts }) {
  if (company.not_applicable.length === 0) return <p>Nothing is marked not applicable.</p>
  return (
    <ul className="company-list company-na">
      {company.not_applicable.map((entry) => (
        <li key={`${entry.control_id} ${entry.requirement_id ?? ''}`}>
          <span className="mono">
            {entry.requirement_id === null ? `${entry.control_id}, the whole control`
              : `${entry.control_id} ${entry.requirement_id}`}
          </span>
          <p lang={company.answer_language}>{entry.reason}</p>
        </li>
      ))}
    </ul>
  )
}
