import { type FormEvent, type MouseEvent, useEffect, useId, useRef, useState } from 'react'
import type { ProtectionLevel } from './assessment.ts'
import {
  COUNTRIES, type FrameworkControl, frameworkControls, MAX_COMPANY_LENGTH, PROTECTION_LEVELS, protectionLevels,
  type ProtectionScope, scopeWords,
} from './setup.ts'

/** Every step of company setup, by name; steps after BUILT, and those NOT_BUILT, are later slices of #351. */
const STEPS = ['Company', 'Language', 'Protection level', 'Goal', 'Not applicable', 'Evidence', 'Finish']
const COMPANY = 1
const LANGUAGE = 2
const LEVEL = 3
const GOAL = 4
const NOT_APPLICABLE = 5
const BUILT = NOT_APPLICABLE
const NOT_BUILT = [GOAL] // skipped by Next and Back until slice 5 of #351 builds it
const later = (n: number) => n > BUILT || NOT_BUILT.includes(n)

/** A control marked not applicable, as CompanySetup.not_applicable takes it: whole controls only, so no requirement.
 * The reason is kept as typed; Next on step 5 trims it. */
type NotApplicable = { control: string; reason: string }
type Answers = {
  company: string
  country: string
  language: 'en' | 'de'
  level: ProtectionLevel | null
  notApplicable: NotApplicable[]
}
/** `blank`: the controls marked not applicable whose reason is empty; `allMarked`: every control is. */
type Problems = { company: boolean; country: boolean; level: boolean; blank: string[]; allMarked: boolean }
type Read<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'failed' }
type Scope = Read<ProtectionScope[]>
type Controls = Read<FrameworkControl[]>

const NO_PROBLEMS: Problems = { company: false, country: false, level: false, blank: [], allMarked: false }

const LANGUAGES = [{ code: 'en', name: 'English' }, { code: 'de', name: 'German' }] as const
const LEVELS = Object.keys(PROTECTION_LEVELS) as ProtectionLevel[]

/** `developerPath`: the developer page's address, for the breadcrumb's link; `onDeveloper` opens it. */
type Props = { developerPath: string; onDeveloper: () => void }

/**
 * Company setup (#403, M8 #351): a developer walks the steps that make a company, going back and forward without
 * losing an answer. The answers live here only: leaving or reloading starts afresh, and nothing is sent yet. Steps 1
 * to 3 are built (company and country, answer language, protection level with what each brings into scope), and step
 * 5 (#407, controls marked not applicable, each with a reason); step 4 is skipped until it is built.
 */
export default function CompanySetupWizard({ developerPath, onDeveloper }: Props) {
  const [step, setStep] = useState(COMPANY)
  const [answers, setAnswers] = useState<Answers>({ company: '', country: '', language: 'en', level: null, notApplicable: [] })
  const [problems, setProblems] = useState<Problems>(NO_PROBLEMS)
  const [scope, setScope] = useState<Scope | null>(null) // read when step 3 first opens
  const [controls, setControls] = useState<Controls | null>(null) // read when step 5 first opens
  const headingRef = useRef<HTMLHeadingElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const countryRef = useRef<HTMLSelectElement>(null)
  const levelsRef = useRef<HTMLFieldSetElement>(null)
  const controlsRef = useRef<HTMLFieldSetElement>(null)
  const moved = useRef(false)
  useEffect(() => {
    if (moved.current) headingRef.current?.focus() // on each step change, not on opening
  }, [step])

  // Only each read's latest answer lands, and none after the wizard has closed.
  const scopeReads = useReader(protectionLevels, setScope)
  const readScope = scopeReads.read
  const readControls = useReader(frameworkControls, setControls).read
  const go = (to: number) => {
    const by = Math.sign(to - step)
    while (NOT_BUILT.includes(to)) to += by
    moved.current = true
    setStep(to)
    if (to === LEVEL && scope === null) readScope()
    if (to === NOT_APPLICABLE && controls === null) readControls()
  }
  const next = (e: FormEvent) => {
    e.preventDefault()
    if (step === COMPANY) {
      const found = { company: answers.company.trim() === '', country: answers.country === '' }
      setProblems({ ...NO_PROBLEMS, ...found })
      if (found.company) return nameRef.current?.focus()
      if (found.country) return countryRef.current?.focus()
    }
    if (step === LEVEL && answers.level === null) {
      setProblems({ ...NO_PROBLEMS, level: true })
      return levelsRef.current?.querySelector('input')?.focus()
    }
    if (step === NOT_APPLICABLE) {
      const marked = answers.notApplicable
      const order = controls?.status === 'ready' ? controls.data.map((c) => c.id) : []
      const blank = order.filter((c) => marked.some((m) => m.control === c && m.reason.trim() === ''))
      const allMarked = controls?.status === 'ready' && marked.length === controls.data.length
      setProblems({ ...NO_PROBLEMS, blank, allMarked })
      if (allMarked) return controlsRef.current?.querySelector('input')?.focus()
      if (blank.length > 0) return controlsRef.current?.querySelector<HTMLElement>(`[data-control="${blank[0]}"]`)?.focus()
      setAnswers((now) => ({ ...now, notApplicable: now.notApplicable.map((m) => ({ ...m, reason: m.reason.trim() })) }))
    }
    if (step < BUILT) go(step + 1)
  }
  // A double-click on Next moves one step: its second click (detail 2) would land on the next step's Next.
  const once = (e: MouseEvent) => {
    if (e.detail > 1) e.preventDefault()
  }
  // A changed answer's problem clears at once; Next checks again.
  const answer = (change: Partial<Answers>) => {
    setAnswers((now) => ({ ...now, ...change }))
    setProblems((now) => ({
      ...now,
      company: now.company && !('company' in change),
      country: now.country && !('country' in change),
      level: now.level && !('level' in change),
    }))
  }
  // Step 5: ticking a control asks why, unticking drops its reason; a changed control's problems clear.
  const mark = (control: string, reason: string | null) => {
    setAnswers((now) => {
      const others = now.notApplicable.filter((m) => m.control !== control)
      const at = now.notApplicable.findIndex((m) => m.control === control)
      if (reason === null) return { ...now, notApplicable: others }
      const marked = [...others]
      marked.splice(at === -1 ? others.length : at, 0, { control, reason })
      return { ...now, notApplicable: marked }
    })
    setProblems((now) => ({ ...now, blank: now.blank.filter((c) => c !== control), allMarked: now.allMarked && reason !== null }))
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
      <form className="sheet setup-step" noValidate onSubmit={next}>
        <span className="lbl">Step {step} of {STEPS.length}</span>
        <h3 ref={headingRef} tabIndex={-1}>{STEPS[step - 1]}</h3>
        {step === COMPANY && (
          <CompanyStep answers={answers} problems={problems} answer={answer} nameRef={nameRef} countryRef={countryRef} />
        )}
        {step === LANGUAGE && <LanguageStep answers={answers} answer={answer} />}
        {step === LEVEL && (
          <LevelStep answers={answers} answer={answer} scope={scope} onRetry={readScope} problem={problems.level}
            fieldsetRef={levelsRef} />
        )}
        {step === NOT_APPLICABLE && (
          <NotApplicableStep marked={answers.notApplicable} mark={mark} controls={controls} onRetry={readControls}
            problems={problems} fieldsetRef={controlsRef} />
        )}
        <div className="setup-nav">
          {step > COMPANY && <button type="button" className="btn btn-ghost" onClick={() => go(step - 1)}>Back</button>}
          <button type="submit" className="btn btn-primary" onClick={once}>Next</button>
        </div>
        {step === BUILT && (
          <p className="field-note">The next steps (evidence, finish) are not built yet. Nothing has been saved.</p>
        )}
      </form>
    </section>
  )
}

/** A read that starts on `read()` and lands its answer in `set`; only the latest read's answer lands, and none after
 * the wizard has closed. */
function useReader<T>(fetch: () => Promise<T | null>, set: (read: Read<T>) => void) {
  const reads = useRef(0)
  useEffect(() => () => { reads.current += 1 }, [])
  return {
    read: () => {
      const read = ++reads.current
      set({ status: 'loading' })
      void fetch().then((data) => {
        if (read === reads.current) set(data ? { status: 'ready', data } : { status: 'failed' })
      })
    },
  }
}

type StepProps = { answers: Answers; answer: (change: Partial<Answers>) => void }

function CompanyStep({ answers, problems, answer, nameRef, countryRef }: StepProps & {
  problems: Problems
  nameRef: React.RefObject<HTMLInputElement | null>
  countryRef: React.RefObject<HTMLSelectElement | null>
}) {
  const nameId = useId()
  const countryId = useId()
  return (
    <>
      <div className="setup-field">
        <label htmlFor={nameId}>Company name</label>
        <input id={nameId} ref={nameRef} className="field" value={answers.company} maxLength={MAX_COMPANY_LENGTH}
          autoComplete="organization" onChange={(e) => answer({ company: e.target.value })}
          aria-invalid={problems.company || undefined} aria-describedby={problems.company ? `${nameId}-problem` : undefined} />
        {problems.company && <p id={`${nameId}-problem`} className="notice field-note">Give the company a name.</p>}
      </div>
      <div className="setup-field">
        <label htmlFor={countryId}>Country</label>
        <select id={countryId} ref={countryRef} className="field" value={answers.country}
          onChange={(e) => answer({ country: e.target.value })}
          aria-invalid={problems.country || undefined} aria-describedby={problems.country ? `${countryId}-problem` : undefined}>
          <option value="">Choose a country</option>
          {COUNTRIES.map((c) => <option key={c.code} value={c.name}>{c.name}</option>)}
        </select>
        {problems.country && <p id={`${countryId}-problem`} className="notice field-note">Choose the company's country.</p>}
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

function LevelStep({ answers, answer, scope, onRetry, problem, fieldsetRef }: StepProps & {
  scope: Scope | null
  onRetry: () => void
  problem: boolean
  fieldsetRef: React.RefObject<HTMLFieldSetElement | null>
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
    <fieldset ref={fieldsetRef} className="setup-choices" aria-invalid={problem || undefined}
      aria-describedby={problem ? problemId : undefined}>
      <legend>Protection level</legend>
      {LEVELS.map((level) => {
        const { name } = PROTECTION_LEVELS[level]
        const words = brings(level)
        return (
          <label key={level} className="setup-choice setup-option">
            <input type="radio" name="protection-level" value={level} checked={answers.level === level}
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

function NotApplicableStep({ marked, mark, controls, onRetry, problems, fieldsetRef }: {
  marked: NotApplicable[]
  mark: (control: string, reason: string | null) => void
  controls: Controls | null
  onRetry: () => void
  problems: Problems
  fieldsetRef: React.RefObject<HTMLFieldSetElement | null>
}) {
  const id = useId()
  const allMarkedId = `${id}-all`
  return (
    <fieldset ref={fieldsetRef} className="setup-choices" aria-describedby={problems.allMarked ? allMarkedId : undefined}>
      <legend>Controls not applicable</legend>
      <p className="field-note">
        Mark a control not applicable only when it doesn't apply to the company at all; the assessment leaves it out
        and the readiness report lists it with your reason.
      </p>
      {(controls === null || controls.status === 'loading') && <p className="field-note">Loading the controls…</p>}
      {controls?.status === 'ready' && controls.data.map((control) => {
        const entry = marked.find((m) => m.control === control.id)
        const reasonId = `${id}-${control.id}`
        const blank = problems.blank.includes(control.id)
        return (
          <div key={control.id} className="setup-na">
            <label className="setup-choice setup-check">
              <input type="checkbox" checked={entry !== undefined}
                onChange={(e) => mark(control.id, e.target.checked ? '' : null)} />
              <span className="setup-option-text"><span className="setup-option-name">{control.id}</span> {control.question}</span>
            </label>
            {entry && (
              <div className="setup-field setup-reason">
                <label htmlFor={reasonId}>Why {control.id} doesn't apply</label>
                <textarea id={reasonId} className="field" rows={2} value={entry.reason} data-control={control.id}
                  onChange={(e) => mark(control.id, e.target.value)}
                  aria-invalid={blank || undefined} aria-describedby={blank ? `${reasonId}-problem` : undefined} />
                {blank && <p id={`${reasonId}-problem`} className="notice field-note">Say why {control.id} doesn't apply.</p>}
              </div>
            )}
          </div>
        )
      })}
      {problems.allMarked && <p id={allMarkedId} className="notice field-note">At least one control must apply.</p>}
      {controls?.status === 'failed' && (
        <div className="notice field-note setup-alert" role="alert">
          <p>The controls could not be loaded.</p>
          <button type="button" className="btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </fieldset>
  )
}
