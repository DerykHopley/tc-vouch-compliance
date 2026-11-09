import { type FormEvent, type MouseEvent, useEffect, useId, useRef, useState } from 'react'
import type { ProtectionLevel } from './assessment.ts'
import DocumentTable from './DocumentTable.tsx'
import {
  type ChosenEvidence, COUNTRIES, type FrameworkControl, frameworkControls, type LibraryDocument, type LibrarySource,
  MAX_COMPANY_LENGTH, type NotApplicable, notApplicableProblems, PROTECTION_LEVELS, protectionLevels,
  type ProtectionScope, readyToCite, sampleLibrary, scopeWords,
} from './setup.ts'

/** Every step of company setup, by name; steps after BUILT, and those NOT_BUILT, are later slices of #351. */
const STEPS = ['Company', 'Language', 'Protection level', 'Goal', 'Not applicable', 'Evidence', 'Finish']
const COMPANY = 1
const LANGUAGE = 2
const LEVEL = 3
const GOAL = 4
const NOT_APPLICABLE = 5
const EVIDENCE = 6
const BUILT = EVIDENCE
const NOT_BUILT = [GOAL] // skipped by Next and Back until slice 5 of #351 builds it
const later = (n: number) => n > BUILT || NOT_BUILT.includes(n)

/** A press within this long of the last move is that move's press landing again (a double-click, a double-tap, two
 * quick clicks) on the new step's Next or Back: it is ignored, so each press moves one step. From #403's verifier. */
const SETTLE_MS = 400

type Answers = {
  company: string
  country: string
  language: 'en' | 'de'
  level: ProtectionLevel | null
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
type Read<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'failed' }
type Scope = Read<ProtectionScope[]>
type Controls = Read<FrameworkControl[]>
type Library = Read<LibraryDocument[]>

const LANGUAGES = [{ code: 'en', name: 'English' }, { code: 'de', name: 'German' }] as const
const LEVELS = Object.keys(PROTECTION_LEVELS) as ProtectionLevel[]

/** `developerPath`: the developer page's address, for the breadcrumb's link; `onDeveloper` opens it. */
type Props = { developerPath: string; onDeveloper: () => void }

/**
 * Company setup (#403, M8 #351): a developer walks the steps that make a company, going back and forward without
 * losing an answer. The answers live here only: leaving or reloading starts afresh, and nothing is sent yet. Steps 1
 * to 3 are built (company and country, answer language, protection level with what each brings into scope), step 5
 * (#407, controls marked not applicable, each with a reason) and step 6 (#408, the evidence chosen from the sample
 * library); step 4 is skipped until it is built.
 */
export default function CompanySetupWizard({ developerPath, onDeveloper }: Props) {
  const [step, setStep] = useState(COMPANY)
  const [answers, setAnswers] = useState<Answers>({ company: '', country: '', language: 'en', level: null, notApplicable: [],
    evidence: { policies: [], records: [] } })
  const [problems, setProblems] = useState<Problems>(NO_PROBLEMS)
  const [scope, setScope] = useState<Scope | null>(null) // read when step 3 first opens
  const [controls, setControls] = useState<Controls | null>(null) // read when step 5 first opens
  const [library, setLibrary] = useState<Library | null>(null) // read when step 6 first opens
  const formRef = useRef<HTMLFormElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const notBuiltRef = useRef<HTMLParagraphElement>(null)
  const lastMove = useRef(-Infinity)
  const lastRefusal = useRef(-Infinity) // when Next last found a problem
  const moved = useRef(false)
  useEffect(() => {
    if (moved.current) headingRef.current?.focus() // on each step change, not on opening
  }, [step])

  // Only each read's latest answer lands, and none after the wizard has closed.
  const readScope = useReader(protectionLevels, setScope)
  const readControls = useReader(frameworkControls, setControls)
  const readLibrary = useReader(sampleLibrary, setLibrary)
  const settling = () => performance.now() - lastMove.current < SETTLE_MS
  // Leaving a step drops its problems: they show again only when Next checks it again.
  const go = (to: number) => {
    if (settling()) return
    lastMove.current = performance.now()
    const by = Math.sign(to - step)
    while (NOT_BUILT.includes(to)) to += by
    moved.current = true
    setStep(to)
    setProblems(NO_PROBLEMS)
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
    if (settling()) return // the last move's press landing on this step's Next: not a check of this step
    const found = check()
    setProblems(found)
    if (found.length > 0) {
      lastRefusal.current = performance.now()
      formRef.current?.querySelector<HTMLElement>(`[data-problem="${found[0]}"]`)?.focus()
      return
    }
    if (step < BUILT) go(step + 1)
    else notBuiltRef.current?.focus() // nowhere to go yet (#409): say so, so Next doesn't look dead
  }
  // A refused Next shows its problem above the buttons, so they move down: a double-click's second press lands on
  // the form around them, which would take focus off the field Next just focused (to the page's body). Within
  // SETTLE_MS of a refusal, a press on anything that can't take focus leaves focus where it is (#408, from #407's
  // verifier).
  const keepFocus = (e: MouseEvent) => {
    const target = e.target as HTMLElement
    if (performance.now() - lastRefusal.current < SETTLE_MS && !target.closest('input, select, textarea, button, a, label'))
      e.preventDefault()
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
  // Step 6: ticking a document chooses it as evidence, unticking drops it; any change clears NO_EVIDENCE.
  const choose = (id: string, source: LibrarySource) => {
    setAnswers((now) => {
      const chosen = now.evidence[source]
      const evidence = { ...now.evidence, [source]: chosen.includes(id) ? chosen.filter((c) => c !== id) : [...chosen, id] }
      return { ...now, evidence }
    })
    setProblems((now) => now.filter((p) => p !== NO_EVIDENCE))
  }

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
          <li key={name} className={later(i + 1) ? 'later' : undefined} aria-current={i + 1 === step ? 'step' : undefined}>
            <span className="setup-step-n">{i + 1}</span>{name}
            {later(i + 1) && <span className="sr-only"> (not yet available)</span>}
          </li>
        ))}
      </ol>
      <form ref={formRef} className="sheet setup-step" noValidate onSubmit={next} onMouseDown={keepFocus}>
        <span className="lbl">Step {step} of {STEPS.length}</span>
        <h3 ref={headingRef} tabIndex={-1}>{STEPS[step - 1]}</h3>
        {step === COMPANY && <CompanyStep answers={answers} problems={problems} answer={answer} />}
        {step === LANGUAGE && <LanguageStep answers={answers} answer={answer} />}
        {step === LEVEL && (
          <LevelStep answers={answers} answer={answer} scope={scope} onRetry={readScope} problem={problems.includes('level')} />
        )}
        {step === NOT_APPLICABLE && (
          <NotApplicableStep marked={answers.notApplicable} mark={mark} controls={controls} onRetry={readControls}
            problems={problems} />
        )}
        {step === EVIDENCE && (
          <EvidenceStep chosen={answers.evidence} choose={choose} library={library} onRetry={readLibrary}
            problem={problems.includes(NO_EVIDENCE)} />
        )}
        <div className="setup-nav">
          {step > COMPANY && <button type="button" className="btn btn-ghost" onClick={() => go(step - 1)}>Back</button>}
          <button type="submit" className="btn btn-primary">Next</button>
        </div>
        {step === BUILT && (
          <p ref={notBuiltRef} tabIndex={-1} className="field-note">
            The finish is not built yet. Nothing has been saved.
          </p>
        )}
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

function LevelStep({ answers, answer, scope, onRetry, problem }: StepProps & {
  scope: Scope | null
  onRetry: () => void
  problem: boolean
}) {
  const problemId = useId()
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
      {problem && <p id={problemId} className="notice field-note">Choose a protection level.</p>}
      {scope?.status === 'failed' && (
        <div className="notice field-note setup-alert" role="alert">
          <p>What each level brings into scope could not be loaded.</p>
          <button type="button" className="btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </fieldset>
  )
}

function NotApplicableStep({ marked, mark, controls, onRetry, problems }: {
  marked: NotApplicable[]
  mark: (control: string, reason: string | null) => void
  controls: Controls | null
  onRetry: () => void
  problems: Problems
}) {
  const id = useId()
  const allMarkedId = `${id}-all`
  const allMarked = problems.includes(ALL_MARKED)
  return (
    <fieldset className="setup-choices" aria-invalid={allMarked || undefined}
      aria-describedby={allMarked ? allMarkedId : undefined}>
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
      {allMarked && <p id={allMarkedId} className="notice field-note">At least one control must apply.</p>}
      {controls?.status === 'failed' && (
        <div className="notice field-note setup-alert" role="alert">
          <p>The controls could not be loaded.</p>
          <button type="button" className="btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </fieldset>
  )
}

function EvidenceStep({ chosen, choose, library, onRetry, problem }: {
  chosen: ChosenEvidence
  choose: (id: string, source: LibrarySource) => void
  library: Library | null
  onRetry: () => void
  problem: boolean
}) {
  const problemId = useId()
  const documents = library?.status === 'ready' ? library.data : []
  const of = (source: LibrarySource) => documents.filter((d) => d.source === source)
  const picked = documents.filter((d) => chosen[d.source].includes(d.id))
  return (
    <fieldset className="setup-choices" aria-invalid={problem || undefined}
      aria-describedby={problem ? problemId : undefined}>
      <legend className="sr-only">Evidence documents</legend>
      <p className="field-note">Choose the documents that are this company's evidence. Vouch searches and cites only these.</p>
      {/* With nothing loaded there is no checkbox: Next focuses this note, or Try again, in its place. */}
      {(library === null || library.status === 'loading') && (
        <p className="field-note" tabIndex={-1} data-problem={NO_EVIDENCE}>Loading the sample library…</p>
      )}
      {library?.status === 'ready' && (
        <>
          <DocumentTable caption="Policies" documents={of('policies')} selected={chosen} onToggle={choose}
            firstProblem={NO_EVIDENCE} />
          <DocumentTable caption="Records" documents={of('records')} selected={chosen} onToggle={choose} />
          <p className="field-note setup-ready" role="status">{readyToCite(picked)}</p>
        </>
      )}
      {problem && <p id={problemId} className="notice field-note">Choose at least one document.</p>}
      {library?.status === 'failed' && (
        <div className="notice field-note setup-alert" role="alert">
          <p>The sample library could not be loaded.</p>
          <button type="button" className="btn" onClick={onRetry} data-problem={NO_EVIDENCE}>Try again</button>
        </div>
      )}
    </fieldset>
  )
}
