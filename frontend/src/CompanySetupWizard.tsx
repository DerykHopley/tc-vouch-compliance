import { useEffect, useId, useRef, useState } from 'react'
import { COUNTRIES, MAX_COMPANY_LENGTH, protectionLevels, type ProtectionLevel, type ProtectionScope, scopeWords } from './setup.ts'

/** Every step of company setup, by name; steps after BUILT are later slices of #351. */
const STEPS = ['Company', 'Language', 'Protection level', 'Goal', 'Not applicable', 'Evidence', 'Finish']
const BUILT = 3

type Answers = { company: string; country: string; language: 'en' | 'de'; level: ProtectionLevel | null }
type Problems = { company: boolean; country: boolean }
type Scope = { status: 'loading' } | { status: 'ready'; levels: ProtectionScope[] } | { status: 'failed' }

const LANGUAGES = [{ code: 'en', name: 'English' }, { code: 'de', name: 'German' }] as const
const LEVELS: { level: ProtectionLevel; name: string }[] = [
  { level: 'normal', name: 'Normal' }, { level: 'high', name: 'High' }, { level: 'very high', name: 'Very high' },
]

type Props = { onDeveloper: () => void }

/**
 * Company setup (#403, M8 #351): a developer walks the steps that make a company, going back and forward without
 * losing an answer. The answers live here only: leaving or reloading starts afresh, and nothing is sent yet. Steps 1
 * to 3 are built (company and country, answer language, protection level with what each brings into scope).
 */
export default function CompanySetupWizard({ onDeveloper }: Props) {
  const [step, setStep] = useState(1)
  const [answers, setAnswers] = useState<Answers>({ company: '', country: '', language: 'en', level: null })
  const [problems, setProblems] = useState<Problems>({ company: false, country: false })
  const [scope, setScope] = useState<Scope | null>(null) // read when step 3 first opens
  const headingRef = useRef<HTMLHeadingElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const countryRef = useRef<HTMLSelectElement>(null)
  const moved = useRef(false)
  useEffect(() => {
    if (moved.current) headingRef.current?.focus() // on each step change, not on opening
  }, [step])

  const readScope = () => {
    setScope({ status: 'loading' })
    void protectionLevels().then((levels) => setScope(levels ? { status: 'ready', levels } : { status: 'failed' }))
  }
  const go = (to: number) => {
    moved.current = true
    setStep(to)
    if (to === 3 && scope === null) readScope()
  }
  const next = () => {
    if (step === 1) {
      const found = { company: answers.company.trim() === '', country: answers.country === '' }
      setProblems(found)
      if (found.company) return nameRef.current?.focus()
      if (found.country) return countryRef.current?.focus()
    }
    go(step + 1)
  }
  const answer = (change: Partial<Answers>) => setAnswers((now) => ({ ...now, ...change }))

  return (
    <section className="paper wrap" aria-label="Company setup">
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href="/developer" onClick={(e) => { e.preventDefault(); onDeveloper() }}>Developer</a>
        <span aria-hidden="true">›</span>
        <span aria-current="page">Company setup</span>
      </nav>
      <div className="paper-head">
        <span className="lbl">Developers only</span>
        <h2>Company setup</h2>
      </div>
      <ol className="setup-steps" aria-label="Setup steps">
        {STEPS.map((name, i) => (
          <li key={name} className={i + 1 > BUILT ? 'later' : undefined} aria-current={i + 1 === step ? 'step' : undefined}>
            <span className="setup-step-n">{i + 1}</span>{name}
            {i + 1 > BUILT && <span className="sr-only"> (not yet available)</span>}
          </li>
        ))}
      </ol>
      <div className="sheet setup-step">
        <span className="lbl">Step {step} of {STEPS.length}</span>
        <h3 ref={headingRef} tabIndex={-1}>{STEPS[step - 1]}</h3>
        {step === 1 ? <CompanyStep answers={answers} problems={problems} answer={answer} nameRef={nameRef} countryRef={countryRef} />
          : step === 2 ? <LanguageStep answers={answers} answer={answer} />
          : <LevelStep answers={answers} answer={answer} scope={scope} onRetry={readScope} />}
        <div className="setup-nav">
          {step > 1 && <button type="button" className="btn btn-ghost" onClick={() => go(step - 1)}>Back</button>}
          {step < BUILT && <button type="button" className="btn btn-primary" onClick={next}>Next</button>}
        </div>
        {step === BUILT && (
          <p className="field-note">
            The next steps (Goal, not applicable, evidence, finish) are not built yet. Nothing has been saved.
          </p>
        )}
      </div>
    </section>
  )
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

function LevelStep({ answers, answer, scope, onRetry }: StepProps & { scope: Scope | null; onRetry: () => void }) {
  const brings = (level: ProtectionLevel) => {
    if (scope?.status === 'ready') {
      const found = scope.levels.find((s) => s.level === level)
      return found ? scopeWords(found) : null
    }
    return scope?.status === 'failed' ? null : 'Loading what this level brings in…'
  }
  return (
    <fieldset className="setup-choices">
      <legend>Protection level</legend>
      {LEVELS.map(({ level, name }) => {
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
      {scope?.status === 'failed' && (
        <div className="notice field-note setup-alert" role="alert">
          <p>What each level brings into scope could not be loaded.</p>
          <button type="button" className="btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </fieldset>
  )
}
