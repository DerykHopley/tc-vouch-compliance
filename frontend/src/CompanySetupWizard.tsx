import { type FormEvent, useEffect, useId, useRef, useState } from 'react'
import type { ProtectionLevel } from './assessment.ts'
import DocumentTable from './DocumentTable.tsx'
import { GOAL_LINES, GOAL_WORDS } from './overview.ts'
import type { ProfileSummary } from './profiles.ts'
import type { Goal } from './report.ts'
import {
  type AnswerLanguage, type ChosenEvidence, type CompanyCreated, COUNTRIES, createCompany, type FrameworkControl,
  frameworkControls, LANGUAGES, type LibraryDocument, type LibrarySource, MAX_COMPANY_LENGTH, type NotApplicable,
  notApplicableProblems, PROTECTION_LEVELS, protectionLevels, type ProtectionScope, readyToCite, sampleLibrary, scopeWords,
} from './setup.ts'

/** Every step of company setup, by name. */
const STEPS = ['Company', 'Language', 'Protection level', 'Goal', 'Not applicable', 'Evidence', 'Finish']
const COMPANY = 1
const LANGUAGE = 2
const LEVEL = 3
const GOAL = 4
const NOT_APPLICABLE = 5
const EVIDENCE = 6
const FINISH = 7

/** Step 4's Goals in the order shown, Internal improvement first (chosen at the start) (#447). */
const GOALS: Goal[] = ['internal-improvement', 'audit-readiness']

/** A press of its own (detail 1) within this long of the last move is that move's press landing again (two quick
 * clicks, a double-tap) on the new step's Next, Back or Create company: it is ignored, so each press moves one step and
 * none creates the company. From #403's verifier. A double-click's second press is kept from acting at all (see
 * `secondPress`). */
const SETTLE_MS = 400

type Answers = {
  company: string
  country: string
  language: AnswerLanguage
  level: ProtectionLevel | null
  goal: Goal
  notApplicable: NotApplicable[]
  evidence: ChosenEvidence
}
/** What Next found wrong on the current step, each by the `data-problem` of the field that takes focus for it: an
 * answer's name ('company', 'country', 'level'), ALL_MARKED, a marked control's id (its reason blank), or
 * NO_EVIDENCE. */
type Problems = readonly string[]
const NO_PROBLEMS: Problems = []
const ALL_MARKED = 'all marked'
const NO_EVIDENCE = 'no evidence'
/** What a problem of the whole step says. It shows below Back and Next, so a refused Next never moves them: a
 * double-click's second press lands on Next again, not on the form, which would take focus off the field Next just
 * focused (#408, from #407's verifier). */
const STEP_NOTICES: Readonly<Record<string, string>> = {
  level: 'Choose a protection level.',
  [ALL_MARKED]: 'At least one control must apply.',
  [NO_EVIDENCE]: 'Choose at least one document.',
}
/** What the current step shows until the next move: Next's problems, and why the last Create company made no
 * company. */
type Shown = { problems: Problems; failure: string | null }
const NOTHING_SHOWN: Shown = { problems: NO_PROBLEMS, failure: null }
type Read<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'failed' }
type Scope = Read<ProtectionScope[]>
type Controls = Read<FrameworkControl[]>
type Library = Read<LibraryDocument[]>

const LEVELS = Object.keys(PROTECTION_LEVELS) as ProtectionLevel[]

/** What step 7 says when Create company didn't make the company: the problem a refusal names (as one sentence), why
 * it failed, or that it may have been made after all. */
function failureWords(created: Exclude<CompanyCreated, { status: 'created' | 'signed-out' }>): string {
  if (created.status === 'refused') {
    const problem = created.problem.trim().replace(/\.$/, '')
    return problem ? `Vouch could not create this company: ${problem}.` : 'Vouch could not create this company.'
  }
  if (created.status === 'unavailable') return 'The database is unavailable just now. Nothing was saved; try again in a moment.'
  if (created.status === 'unknown')
    return 'The company may not have been created. Check the Companies section of the Developer page before trying again.'
  return 'The company could not be created. Nothing was saved.'
}

/** A double-click's second press (detail 2 or more) lands on whatever the first press left under the pointer: the next
 * step's Next or Create company, a checkbox it ticked, the page's body after a move or a scroll. Kept from acting
 * anywhere, it neither moves, creates, ticks a box back nor takes focus off where the first press put it. A text field
 * keeps it, to select a word. From #403's, #407's and #408's verifiers. */
function secondPress(e: MouseEvent) {
  const typing = e.target instanceof HTMLTextAreaElement
    || (e.target instanceof HTMLInputElement && TEXT_INPUTS.has(e.target.type))
  if (e.detail > 1 && !typing) e.preventDefault()
}
const TEXT_INPUTS = new Set(['text', 'search', 'email', 'url', 'tel', 'password', 'number'])

/** `developerPath`: the developer page's address, for the breadcrumb's link; `onDeveloper` opens it. `onCreated`: the
 * company was created, as GET /api/profiles lists it, with the Goal chosen on step 4; the app then opens under it, sets
 * that Goal and runs a profile pass (#447). The Goal isn't part of the create: it is kept per user and profile. */
type Props = { developerPath: string; onDeveloper: () => void; onCreated?: (profile: ProfileSummary, goal: Goal) => void }

/**
 * Company setup (#403, M8 #351): a developer walks the steps that make a company, going back and forward without
 * losing an answer. The answers live here only: leaving or reloading starts afresh. Steps 1 to 3 (company and
 * country, answer language, protection level with what each brings into scope), step 4 (#447, the Goal), step 5
 * (#407, controls marked not applicable, each with a reason), step 6 (#408, the evidence chosen from the sample
 * library) and step 7 (#409, a summary and Create company, which sends them).
 */
export default function CompanySetupWizard({ developerPath, onDeveloper, onCreated }: Props) {
  const [step, setStep] = useState(COMPANY)
  const [answers, setAnswers] = useState<Answers>({ company: '', country: '', language: 'en', level: null, goal: 'internal-improvement',
    notApplicable: [],
    evidence: { policies: [], records: [] } })
  const [shown, setShown] = useState<Shown>(NOTHING_SHOWN)
  const { problems, failure } = shown
  const setProblems = (change: (now: Problems) => Problems) =>
    setShown((now) => ({ ...now, problems: change(now.problems) }))
  const [scope, setScope] = useState<Scope | null>(null) // read when step 3 first opens
  const [controls, setControls] = useState<Controls | null>(null) // read when step 5 first opens
  const [library, setLibrary] = useState<Library | null>(null) // read when step 6 first opens
  const formRef = useRef<HTMLFormElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const [creating, setCreating] = useState(false)
  const createRef = useRef<HTMLButtonElement>(null)
  // Disabled while the create was out, Create company lost focus: it takes it back with the alert, to press again.
  useEffect(() => {
    if (failure) createRef.current?.focus()
  }, [failure])
  const lastMove = useRef(-Infinity)
  const noticeId = useId()
  const notice = problems.map((p) => STEP_NOTICES[p]).find((words) => words !== undefined)
  const moved = useRef(false)
  useEffect(() => {
    if (moved.current) headingRef.current?.focus() // on each step change, not on opening
  }, [step])

  // Only each read's latest answer lands, and none after the wizard has closed.
  const readScope = useReader(protectionLevels, setScope)
  const readControls = useReader(frameworkControls, setControls)
  const readLibrary = useReader(sampleLibrary, setLibrary)
  const settling = () => performance.now() - lastMove.current < SETTLE_MS
  useEffect(() => {
    window.addEventListener('mousedown', secondPress, true)
    window.addEventListener('click', secondPress, true)
    return () => {
      window.removeEventListener('mousedown', secondPress, true)
      window.removeEventListener('click', secondPress, true)
    }
  }, [])
  // Leaving a step drops what it showed: its problems show again only when Next checks it again.
  const go = (to: number) => {
    if (settling()) return
    lastMove.current = performance.now()
    moved.current = true
    setStep(to)
    setShown(NOTHING_SHOWN)
    if (to === LEVEL && scope === null) readScope()
    if (to === NOT_APPLICABLE && controls === null) readControls()
    if (to === EVIDENCE && library === null) readLibrary()
  }
  const check = (): Problems => {
    if (step === COMPANY)
      return [answers.company.trim() === '' && 'company', answers.country === '' && 'country'].filter((p) => p !== false)
    if (step === LEVEL) return answers.level === null ? ['level'] : NO_PROBLEMS
    if (step === NOT_APPLICABLE) {
      const found = notApplicableProblems(answers.notApplicable, controls?.status === 'ready' ? controls.data : [])
      return [...(found.allMarked ? [ALL_MARKED] : []), ...found.blank]
    }
    if (step === EVIDENCE) return Object.values(answers.evidence).flat().length === 0 ? [NO_EVIDENCE] : NO_PROBLEMS
    return NO_PROBLEMS
  }
  const next = (e: FormEvent) => {
    e.preventDefault()
    // A press of its own landing again just after a move (not a check of this step), or Enter while a create is out.
    if (settling() || creating) return
    if (step === FINISH) {
      void create()
      return
    }
    const found = check()
    setShown((now) => ({ ...now, problems: found }))
    if (found.length > 0) {
      focusProblem(found[0])
      return
    }
    go(step + 1)
  }
  // Step 7's Create company: one press makes one company (`creating` disables it, and Back, while it is out). A
  // created company opens the app under it, which closes the wizard, so nothing here changes after that.
  const create = async () => {
    const setFailure = (words: string | null) => setShown((now) => ({ ...now, failure: words }))
    if (answers.level === null) { // step 3 never lets it through
      setFailure(failureWords({ status: 'failed' }))
      return
    }
    setCreating(true)
    setFailure(null)
    const created = await createCompany({
      company: answers.company, country: answers.country, answer_language: answers.language,
      protection_level: answers.level, not_applicable: answers.notApplicable, evidence: answers.evidence,
    })
    if (created.status === 'created') {
      onCreated?.(created.profile, answers.goal)
      return
    }
    setCreating(false)
    if (created.status !== 'signed-out') setFailure(failureWords(created)) // signed out: the sign-in says so
  }
  // Next focuses the field a problem names (its `data-problem`), or a group's first field. With no field to take it (a
  // list still loading, or one that failed), the step's heading: it stays when the list lands or Try again goes.
  const focusProblem = (problem: string) => {
    const found = formRef.current?.querySelector<HTMLElement>(`[data-problem="${problem}"]`)
    const field = found instanceof HTMLFieldSetElement ? found.querySelector<HTMLElement>('input') : found
    ;(field ?? headingRef.current)?.focus()
  }
  // A changed answer's problem clears at once; Next checks again.
  const answer = (change: Partial<Answers>) => {
    setAnswers((now) => ({ ...now, ...change }))
    setProblems((now) => now.filter((p) => !(p in change)))
  }
  // Step 5: ticking a control asks why, unticking drops its reason; a changed control's problems clear. The list's
  // order is the framework's, from `controls`, so the answers' order doesn't matter.
  const mark = (control: string, reason: string | null) => {
    setAnswers((now) => {
      const others = now.notApplicable.filter((m) => m.control !== control)
      return { ...now, notApplicable: reason === null ? others : [...others, { control, reason }] }
    })
    setProblems((now) => now.filter((p) => p !== control && !(p === ALL_MARKED && reason === null)))
  }
  // Step 6: a folder's chosen documents become what `change` makes of them; any change clears NO_EVIDENCE.
  const changeFolder = (source: LibrarySource, change: (chosen: string[]) => string[]) => {
    setAnswers((now) => ({ ...now, evidence: { ...now.evidence, [source]: change(now.evidence[source]) } }))
    setProblems((now) => now.filter((p) => p !== NO_EVIDENCE))
  }
  // Ticking a document chooses it as evidence, unticking drops it.
  const choose = (id: string, source: LibrarySource) =>
    changeFolder(source, (chosen) => chosen.includes(id) ? chosen.filter((c) => c !== id) : [...chosen, id])
  // Select all and Deselect all (#475): the folder's chosen documents become `ids` (all of them, or none); the other
  // folder stays as it is.
  const setFolder = (source: LibrarySource, ids: string[]) => changeFolder(source, () => ids)

  return (
    <section className="paper wrap" aria-label="Company setup">
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href={developerPath} onClick={(e) => { e.preventDefault(); onDeveloper() }}>Developer</a>
        <span aria-hidden="true">›</span>
        <span aria-current="page">Company setup</span>
      </nav>
      <div className="paper-head">
        <span className="lbl">Developers only</span>
        <h2>Company setup</h2>
      </div>
      <ol className="setup-steps" aria-label="Setup steps">
        {STEPS.map((name, i) => (
          <li key={name} aria-current={i + 1 === step ? 'step' : undefined}>
            <span className="setup-step-n">{i + 1}</span>{name}
          </li>
        ))}
      </ol>
      <form ref={formRef} className="sheet setup-step" noValidate onSubmit={next}>
        <span className="lbl">Step {step} of {STEPS.length}</span>
        <h3 ref={headingRef} tabIndex={-1}>{STEPS[step - 1]}</h3>
        {step === COMPANY && <CompanyStep answers={answers} problems={problems} answer={answer} />}
        {step === LANGUAGE && <LanguageStep answers={answers} answer={answer} />}
        {step === LEVEL && (
          <LevelStep answers={answers} answer={answer} scope={scope} onRetry={readScope} problem={problems.includes('level')}
            problemId={noticeId} />
        )}
        {step === GOAL && <GoalStep answers={answers} answer={answer} />}
        {step === NOT_APPLICABLE && (
          <NotApplicableStep marked={answers.notApplicable} mark={mark} controls={controls} onRetry={readControls}
            problems={problems} problemId={noticeId} />
        )}
        {step === EVIDENCE && (
          <EvidenceStep chosen={answers.evidence} choose={choose} setFolder={setFolder} library={library} onRetry={readLibrary}
            problem={problems.includes(NO_EVIDENCE)} problemId={noticeId} />
        )}
        {step === FINISH && <FinishStep answers={answers} controls={controls} library={library} />}
        <div className="setup-nav">
          {step > COMPANY && (
            <button type="button" className="btn btn-ghost" disabled={creating} onClick={() => go(step - 1)}>Back</button>
          )}
          {step === FINISH
            ? <button ref={createRef} type="submit" className="btn btn-primary" disabled={creating} aria-busy={creating}>
              {creating ? 'Creating…' : 'Create company'}
            </button>
            : <button type="submit" className="btn btn-primary">Next</button>}
        </div>
        {notice && <p id={noticeId} className="notice field-note">{notice}</p>}
        {failure && <p className="notice field-note setup-alert" role="alert">{failure}</p>}
      </form>
    </section>
  )
}

/** A read that starts when the returned function is called and lands its answer in `set`; only the latest read's
 * answer lands, and none after the wizard has closed. */
function useReader<T>(load: () => Promise<T | null>, set: (read: Read<T>) => void) {
  const reads = useRef(0)
  useEffect(() => () => { reads.current += 1 }, [])
  return () => {
    const read = ++reads.current
    set({ status: 'loading' })
    void load().then((data) => {
      if (read === reads.current) set(data ? { status: 'ready', data } : { status: 'failed' })
    })
  }
}

type StepProps = { answers: Answers; answer: (change: Partial<Answers>) => void }

function CompanyStep({ answers, problems, answer }: StepProps & { problems: Problems }) {
  const nameId = useId()
  const countryId = useId()
  const noName = problems.includes('company')
  const noCountry = problems.includes('country')
  return (
    <>
      <div className="setup-field">
        <label htmlFor={nameId}>Company name</label>
        <input id={nameId} data-problem="company" className="field" value={answers.company} maxLength={MAX_COMPANY_LENGTH}
          autoComplete="organization" onChange={(e) => answer({ company: e.target.value })}
          aria-invalid={noName || undefined} aria-describedby={noName ? `${nameId}-problem` : undefined} />
        {noName && <p id={`${nameId}-problem`} className="notice field-note">Give the company a name.</p>}
      </div>
      <div className="setup-field">
        <label htmlFor={countryId}>Country</label>
        <select id={countryId} data-problem="country" className="field" value={answers.country}
          onChange={(e) => answer({ country: e.target.value })}
          aria-invalid={noCountry || undefined} aria-describedby={noCountry ? `${countryId}-problem` : undefined}>
          <option value="">Choose a country</option>
          {COUNTRIES.map((c) => <option key={c.code} value={c.name}>{c.name}</option>)}
        </select>
        {noCountry && <p id={`${countryId}-problem`} className="notice field-note">Choose the company's country.</p>}
      </div>
    </>
  )
}

function LanguageStep({ answers, answer }: StepProps) {
  const noteId = useId()
  return (
    <fieldset className="setup-choices" aria-describedby={noteId}>
      <legend>Answer language</legend>
      {LANGUAGES.map((l) => (
        <label key={l.code} className="setup-choice">
          <input type="radio" name="answer-language" value={l.code} checked={answers.language === l.code}
            onChange={() => answer({ language: l.code })} />
          <span>{l.name}</span>
        </label>
      ))}
      <p id={noteId} className="field-note">
        Vouch's answers, the chat and the readiness report use this language; the interface stays English.
      </p>
    </fieldset>
  )
}

function LevelStep({ answers, answer, scope, onRetry, problem, problemId }: StepProps & {
  scope: Scope | null
  onRetry: () => void
  problem: boolean
  /** The id of the wizard's notice that says what the problem is. */
  problemId: string
}) {
  const brings = (level: ProtectionLevel) => {
    if (scope?.status === 'ready') {
      const found = scope.data.find((s) => s.level === level)
      return found ? scopeWords(found) : null
    }
    return scope?.status === 'failed' ? null : 'Loading what this level brings in…'
  }
  return (
    <fieldset className="setup-choices" aria-invalid={problem || undefined} aria-describedby={problem ? problemId : undefined}>
      <legend>Protection level</legend>
      {LEVELS.map((level, i) => {
        const { name } = PROTECTION_LEVELS[level]
        const words = brings(level)
        return (
          <label key={level} className="setup-choice setup-option">
            <input type="radio" name="protection-level" value={level} checked={answers.level === level}
              data-problem={i === 0 ? 'level' : undefined}
              onChange={() => answer({ level })} />
            <span className="setup-option-text">
              <span className="setup-option-name">{name}</span>
              {words && <span className="field-note">{words}</span>}
            </span>
          </label>
        )
      })}
      {scope?.status === 'failed' && (
        <div className="notice field-note setup-alert" role="alert">
          <p>What each level brings into scope could not be loaded.</p>
          <button type="button" className="btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </fieldset>
  )
}

/** Step 4 (#447): the Goal, in steps 2 and 3's pattern. One is always chosen, so Next never refuses it. */
function GoalStep({ answers, answer }: StepProps) {
  const noteId = useId()
  return (
    <fieldset className="setup-choices" aria-describedby={noteId}>
      <legend>Goal</legend>
      {GOALS.map((goal) => (
        <label key={goal} className="setup-choice setup-option">
          <input type="radio" name="goal" value={goal} checked={answers.goal === goal} onChange={() => answer({ goal })} />
          <span className="setup-option-text">
            <span className="setup-option-name">{GOAL_WORDS[goal]}</span>
            <span className="field-note">{GOAL_LINES[goal]}</span>
          </span>
        </label>
      ))}
      <p id={noteId} className="field-note">You can change the Goal later on the overview.</p>
    </fieldset>
  )
}

function NotApplicableStep({ marked, mark, controls, onRetry, problems, problemId }: {
  marked: NotApplicable[]
  mark: (control: string, reason: string | null) => void
  controls: Controls | null
  onRetry: () => void
  problems: Problems
  problemId: string
}) {
  const id = useId()
  const allMarked = problems.includes(ALL_MARKED)
  return (
    <fieldset className="setup-choices" aria-invalid={allMarked || undefined}
      aria-describedby={allMarked ? problemId : undefined}>
      <legend>Controls not applicable</legend>
      <p className="field-note">
        Mark a control not applicable only when it doesn't apply to the company at all; the assessment leaves it out
        and the readiness report lists it with your reason.
      </p>
      {(controls === null || controls.status === 'loading') && <p className="field-note">Loading the controls…</p>}
      {controls?.status === 'ready' && controls.data.map((control, i) => {
        const entry = marked.find((m) => m.control === control.id)
        const reasonId = `${id}-${control.id}`
        const blank = problems.includes(control.id)
        return (
          <div key={control.id} className="setup-na">
            <label className="setup-choice setup-check">
              <input type="checkbox" checked={entry !== undefined} data-problem={i === 0 ? ALL_MARKED : undefined}
                onChange={(e) => mark(control.id, e.target.checked ? '' : null)} />
              <span className="setup-option-text"><span className="setup-option-name">{control.id}</span> {control.question}</span>
            </label>
            {entry && (
              <div className="setup-field setup-reason">
                <label htmlFor={reasonId}>Why {control.id} doesn't apply</label>
                <textarea id={reasonId} className="field" rows={2} value={entry.reason} data-problem={control.id}
                  onChange={(e) => mark(control.id, e.target.value)}
                  aria-invalid={blank || undefined} aria-describedby={blank ? `${reasonId}-problem` : undefined} />
                {blank && <p id={`${reasonId}-problem`} className="notice field-note">Say why {control.id} doesn't apply.</p>}
              </div>
            )}
          </div>
        )
      })}
      {controls?.status === 'failed' && (
        <div className="notice field-note setup-alert" role="alert">
          <p>The controls could not be loaded.</p>
          <button type="button" className="btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </fieldset>
  )
}

/** Step 6's folders, in the order it lists them, each with its table's caption and the word its Select all and
 * Deselect all add for a screen reader ("Select all policies"). */
const FOLDERS: { source: LibrarySource; caption: string; noun: string }[] = [
  { source: 'policies', caption: 'Policies', noun: 'policies' }, { source: 'records', caption: 'Records', noun: 'records' }]

function EvidenceStep({ chosen, choose, setFolder, library, onRetry, problem, problemId }: {
  chosen: ChosenEvidence
  choose: (id: string, source: LibrarySource) => void
  setFolder: (source: LibrarySource, ids: string[]) => void
  library: Library | null
  onRetry: () => void
  problem: boolean
  problemId: string
}) {
  const documents = library?.status === 'ready' ? library.data : []
  const inFolder = (source: LibrarySource) => documents.filter((d) => d.source === source)
  const picked = documents.filter((d) => chosen[d.source].includes(d.id))
  return (
    // NO_EVIDENCE names the whole group: Next focuses its first checkbox, whichever folder it is in.
    <fieldset className="setup-choices" aria-invalid={problem || undefined} data-problem={NO_EVIDENCE}
      aria-describedby={problem ? problemId : undefined}>
      <legend className="sr-only">Evidence documents</legend>
      <p className="field-note">Choose the documents that are this company's evidence. Vouch searches and cites only these.</p>
      {(library === null || library.status === 'loading') && <p className="field-note">Loading the sample library…</p>}
      {library?.status === 'ready' && (
        <>
          {FOLDERS.map(({ source, caption, noun }) => {
            const folder = inFolder(source)
            return (
              <div key={source} className="setup-folder">
                {/* Above its table, so Tab reaches them before its checkboxes; each named with its folder. */}
                {folder.length > 0 && (
                  <div className="setup-folder-actions">
                    <button type="button" className="btn btn-ghost"
                      onClick={() => setFolder(source, folder.map((d) => d.id))}>
                      Select all{' '}<span className="sr-only">{noun}</span>
                    </button>
                    <button type="button" className="btn btn-ghost" onClick={() => setFolder(source, [])}>
                      Deselect all{' '}<span className="sr-only">{noun}</span>
                    </button>
                  </div>
                )}
                <DocumentTable caption={caption} documents={folder} selected={chosen} onToggle={choose} />
              </div>
            )
          })}
          <p className="field-note setup-ready" role="status">{readyToCite(picked)}</p>
        </>
      )}
      {library?.status === 'failed' && (
        <div className="notice field-note setup-alert" role="alert">
          <p>The sample library could not be loaded.</p>
          <button type="button" className="btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </fieldset>
  )
}

/** Step 7 (#409): every answer, as Create company will send it (each reason trimmed), and the documents chosen. */
function FinishStep({ answers, controls, library }: { answers: Answers; controls: Controls | null; library: Library | null }) {
  const order = controls?.status === 'ready' ? controls.data.map((c) => c.id) : []
  const marked = [...answers.notApplicable].sort((a, b) => order.indexOf(a.control) - order.indexOf(b.control))
  const documents = library?.status === 'ready' ? library.data : []
  const picked = documents.filter((d) => answers.evidence[d.source].includes(d.id))
  const folders: { source: LibrarySource; caption: string }[] = [
    { source: 'policies', caption: 'Policies' }, { source: 'records', caption: 'Records' }]
  return (
    <>
      <dl className="setup-summary">
        <dt>Company</dt><dd>{answers.company.trim()}</dd>
        <dt>Country</dt><dd>{answers.country}</dd>
        <dt>Answer language</dt><dd>{LANGUAGES.find((l) => l.code === answers.language)?.name}</dd>
        <dt>Protection level</dt><dd>{answers.level && PROTECTION_LEVELS[answers.level].name}</dd>
        <dt>Goal</dt><dd>{GOAL_WORDS[answers.goal]}</dd>
        <dt>Not applicable</dt>
        <dd>
          {marked.length === 0 ? 'None' : (
            <ul className="setup-summary-list">
              {marked.map((m) => <li key={m.control}><span className="mono">{m.control}</span>: {m.reason.trim()}</li>)}
            </ul>
          )}
        </dd>
        <dt>Ready to cite</dt><dd>{readyToCite(picked)}</dd>
      </dl>
      {folders.map(({ source, caption }) => {
        const chosen = picked.filter((d) => d.source === source)
        return chosen.length > 0 && <DocumentTable key={source} caption={caption} documents={chosen} />
      })}
      <p className="field-note">
        Creating the company adds it to the Companies list and makes it the current company, for every user. Then
        Vouch sets its Goal and runs a profile pass: every applicable control drafted, waiting for your review.
      </p>
    </>
  )
}
