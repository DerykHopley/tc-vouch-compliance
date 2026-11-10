import { type ReactNode, useEffect, useId, useState } from 'react'
import { plural } from './assessment.ts'
import { type CompanyFacts, COMPANY_FAILED, fetchCompany } from './company.ts'
import Documents from './Documents.tsx'
import { GoalControl, RoundControl } from './GoalControls.tsx'
import type { OverviewState } from './overview.ts'
import { COLUMN_WORDS } from './paper.ts'
import type { Goal } from './report.ts'
import { SESSION_ENDED } from './session.ts'
import { LANGUAGES, PROTECTION_LEVELS } from './setup.ts'

/** The Company page (#505; `docs/design.md`, Components: Company page): the current company's facts, read-only apart
 * from the Goal, over a grid of panels (Goal, Review rounds, Scope, Not applicable, Upload documents) beside the
 * Documents. Its words are English, as the interface; what the profile writes in its answer language (overlay names,
 * not-applicable reasons) carries that `lang`. The facts are read each time the page opens; the Goal and review
 * rounds are the overview's, which App reads. */

type Read = { status: 'loading' } | { status: 'ready'; company: CompanyFacts } | { status: 'failed'; message: string }

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
  const [read, setRead] = useState<Read>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0) // bumped by Try again to read the company again
  useEffect(() => {
    const answer = { wanted: true } // until the page closes or reads again
    void fetchCompany().then((got) => answer.wanted && setRead(got.ok
      ? { status: 'ready', company: got.company } : { status: 'failed', message: got.message }))
    return () => { answer.wanted = false }
  }, [attempt])
  const tryAgain = () => {
    if (read.status === 'failed') {
      setRead({ status: 'loading' })
      setAttempt((n) => n + 1)
    }
    if (overview.status === 'failed') onRetryOverview()
  }

  const failed = read.status === 'failed' ? read.message
    : overview.status === 'failed' ? (overview.message === SESSION_ENDED ? SESSION_ENDED : COMPANY_FAILED) : null
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
  if (read.status !== 'ready' || overview.status !== 'ready') {
    return <main className="company wrap"><p className="lbl" role="status">Loading the company…</p></main>
  }
  const { company } = read
  const lang = company.answer_language
  return (
    <main className="company wrap">
      <div className="company-head">
        <h1>{company.company}</h1>
        <dl className="company-facts">
          <div><dt>Country</dt><dd>{company.country}</dd></div>
          <div><dt>Answer language</dt><dd>{LANGUAGES.find((l) => l.code === lang)?.name ?? lang}</dd></div>
          <div><dt>Base framework</dt><dd>{company.base_framework}</dd></div>
          <div><dt>Protection level</dt><dd>{PROTECTION_LEVELS[company.protection_level].name}</dd></div>
          <div>
            <dt>Overlays</dt>
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
          <Panel title="Goal">
            <GoalControl overview={overview.overview} onChanged={onGoalChanged} arriving={arriving}
              notSet={goalNotSet} />
          </Panel>
          <Panel title="Review rounds">
            <RoundControl overview={overview.overview} onChanged={onRoundChanged} />
          </Panel>
          <Panel title="Scope"><Scope company={company} /></Panel>
          <Panel title="Not applicable"><NotApplicable company={company} /></Panel>
          <Panel title="Upload documents">
            <p>Uploading documents isn't available yet. For now, Vouch reads the documents listed under Documents.</p>
          </Panel>
        </div>
        <section className="company-documents" aria-label="Documents">
          <Documents reloads={documentsReloads} />
        </section>
      </div>
    </main>
  )
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  const id = useId()
  return (
    <section className="sheet company-panel" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {children}
    </section>
  )
}

function Scope({ company }: { company: CompanyFacts }) {
  const { scope, overlays, answer_language: lang } = company
  const level = PROTECTION_LEVELS[scope.level].name
  return (
    <>
      <p>
        {level} protection brings in {plural(scope.total, 'requirement')} of {company.base_framework}, in{' '}
        {plural(scope.controls, 'control')}.
      </p>
      <ul className="company-list">
        {Object.entries(scope.requirements).map(([column, n]) => (
          <li key={column}>{COLUMN_WORDS[column as keyof typeof COLUMN_WORDS] ?? column}: {n}</li>
        ))}
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
