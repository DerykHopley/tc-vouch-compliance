import { type FormEvent, useId, useRef, useState } from 'react'
import { count, dollars, plural } from './assessment.ts'
import {
  changeDailyLimit, changeModel, type DeveloperSettings, type MadeCompany, removeCompany, type RunTotals, type SettingChange,
  UNAVAILABLE,
} from './developer.ts'

type Props = {
  settings: DeveloperSettings | typeof UNAVAILABLE
  onChange: (settings: DeveloperSettings) => void
  /** Opens company setup (#403). */
  onSetup: () => void
  /** A company made in setup was removed (#418): ProfiledApp reads the profile list again. */
  onRemoved?: (id: string) => void
}

const FAKE_BECAUSE: Record<NonNullable<DeveloperSettings['fake_reason']>, string> = {
  no_key: 'No OpenRouter key is set: assessments run on the fake model.',
  fake_script: 'ASSESS_FAKE_SCRIPT is set: assessments run on the scripted fake model.',
}

type Saving = { status: 'idle' } | { status: 'saving' } | { status: 'saved'; settings: DeveloperSettings } | { status: 'refused'; message: string }

/**
 * The developer page (#34): the model assessments run on, and a form to change it; the prompt version; each user's
 * daily limit, and a form to change it; every assessment's usage totalled, and a link to OpenRouter's (#35); the way into
 * company setup (#403), and the companies made in it, each of which can be removed (#418). Only a developer reaches it
 * (App.tsx); the compliance lead never sees it.
 */
export default function DeveloperPage({ settings, onChange, onSetup, onRemoved }: Props) {
  return (
    <section className="paper wrap" aria-label="Developer settings">
      <div className="paper-head">
        <span className="lbl">Developers only</span>
        <h2>Developer settings</h2>
      </div>
      {settings === UNAVAILABLE ? (
        <p className="sheet notice" role="alert">
          The developer settings are unavailable just now. Please try again shortly.
        </p>
      ) : (
        <>
          <section className="sheet" aria-label="Company setup">
            <span className="lbl">Company setup</span>
            <button type="button" className="btn btn-primary" onClick={onSetup}>Set up a company</button>
            <Companies companies={settings.companies} onChange={onChange} onRemoved={onRemoved} />
          </section>
          <Settings settings={settings} onChange={onChange} />
        </>
      )}
    </section>
  )
}

type Removing = { status: 'idle' } | { status: 'removing'; id: string } | { status: 'removed'; company: string }
  | { status: 'refused'; message: string }

const REMOVE_UNAVAILABLE = 'The database is unavailable just now. Nothing was removed; try again in a moment.'
const REMOVE_FAILED = 'The company could not be removed. Nothing changed.'

/** "9 Nov 2026": the day a company was made, in the reader's time zone; as written when it doesn't parse. */
const madeOn = (iso: string) => {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

/**
 * The companies made in company setup (#418), each with its id, the day it was made and a Remove button. Remove asks
 * with the browser's own confirm (the user menu's fresh start's way), then removes it once, however often pressed; a
 * refusal says why under the list, which stays. A 401 has signed out already: nothing to say.
 */
function Companies({ companies, onChange, onRemoved }: {
  companies: MadeCompany[] | null; onChange: Props['onChange']; onRemoved: Props['onRemoved']
}) {
  const [removing, setRemoving] = useState<Removing>({ status: 'idle' })
  const busy = useRef(false) // set at once, so a second press before the next render sends nothing

  const remove = async ({ id, company }: MadeCompany) => {
    if (busy.current) return
    const question = `Remove ${company}? It leaves the profile switcher for everyone and can't be opened again. `
      + 'Its audit trail is kept.'
    if (!window.confirm(question)) return
    busy.current = true
    setRemoving({ status: 'removing', id })
    const result = await removeCompany(id)
    busy.current = false
    if (result.status === 'removed') {
      setRemoving({ status: 'removed', company })
      onChange(result.settings)
      onRemoved?.(id)
    } else if (result.status === 'refused') {
      setRemoving({ status: 'refused', message: `Vouch could not remove this company: ${result.problem}` })
    } else if (result.status === 'unavailable') {
      setRemoving({ status: 'refused', message: REMOVE_UNAVAILABLE })
    } else if (result.status === 'failed') {
      setRemoving({ status: 'refused', message: REMOVE_FAILED })
    } else {
      setRemoving({ status: 'idle' })
    }
  }

  return (
    <>
      {companies === null ? <p className="field-note">The companies made in setup can't be read just now.</p>
        : companies.length === 0 ? <p className="field-note">No companies made in setup yet.</p>
        : (
          <ul className="companies">
            {companies.map((c) => {
              const out = removing.status === 'removing' && removing.id === c.id
              return (
                <li key={c.id}>
                  <span className="company-name">{c.company}</span>
                  <span className="mono">{c.id}</span>
                  <time className="field-note" dateTime={c.created_at}>Made {madeOn(c.created_at)}</time>
                  <button type="button" className="btn" disabled={removing.status === 'removing'} aria-busy={out}
                    aria-label={out ? undefined : `Remove ${c.company}`} onClick={() => void remove(c)}>
                    {out ? 'Removing…' : 'Remove'}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      {removing.status === 'refused' && <p className="notice field-note" role="alert">{removing.message}</p>}
      {removing.status === 'removed' && <p className="field-note" role="status">Removed {removing.company}.</p>}
    </>
  )
}

/** Some assessments' totals in words: assessments, tokens in and out. */
const runWords = (t: RunTotals) =>
  `${plural(t.assessments, 'assessment')} · ${count(t.input_tokens)} tokens in · ${count(t.output_tokens)} tokens out`

function Settings({ settings, onChange }: { settings: DeveloperSettings; onChange: Props['onChange'] }) {
  const { openrouter, fake } = settings.usage
  return (
    <>
      {settings.fake_reason && <p className="sheet notice">{FAKE_BECAUSE[settings.fake_reason]}</p>}
      <div className="sheet">
        <span className="lbl">Model</span>
        <span className="mono">{settings.model}</span>
        <SettingForm label="Model id" button="Change model" initial={settings.model} mono save={changeModel}
          saved={(s) => `The next assessment runs on ${s.model}.`} onChange={onChange} />
      </div>
      <div className="sheet">
        <span className="lbl">Prompt version</span>
        <span className="mono">{settings.prompt_version}</span>
      </div>
      <div className="sheet">
        <span className="lbl">Daily limit</span>
        <span>{plural(settings.daily_limit, 'assessment')} per user per day (UTC)</span>
        <SettingForm label="Assessments per day" button="Change limit" initial={String(settings.daily_limit)}
          inputMode="numeric" save={(value) => changeDailyLimit(Number(value))}
          saved={(s) => `Each user may now start ${plural(s.daily_limit, 'assessment')} a day.`}
          onChange={onChange} />
      </div>
      <section className="sheet" aria-label="Usage">
        <span className="lbl">Usage</span>
        <p className="mono">
          OpenRouter: {runWords(openrouter)} · {dollars(openrouter.reported_cost_usd)} reported
          {' · '}{dollars(openrouter.estimated_cost_usd)} estimated
        </p>
        {openrouter.incomplete > 0 && (
          <p className="field-note">
            {count(openrouter.incomplete)} of them {openrouter.incomplete === 1 ? 'has' : 'have'} an incomplete cost: some
            calls' cost could not be known.
          </p>
        )}
        {fake.assessments > 0 && (
          <p className="mono">Fake model: {runWords(fake)} · {dollars(fake.estimated_cost_usd)} estimated, not spent</p>
        )}
        <a className="link-target" href={settings.usage_url} target="_blank" rel="noopener noreferrer">Usage and costs on OpenRouter</a>
      </section>
      {settings.broken_profiles.length > 0 && (
        <section className="sheet notice" aria-label="Profiles left out">
          <span className="lbl">Profiles left out</span>
          <p>These compliance profiles could not be loaded, so the profile switcher leaves them out:</p>
          <ul>
            {settings.broken_profiles.map((b) => (
              <li key={b.id}><span className="mono">{b.id}</span>: <span>{b.problem}</span></li>
            ))}
          </ul>
        </section>
      )}
      {settings.kept_in === 'memory' && (
        <p className="field-note">
          No database is set, so the model choice, the daily limit and counts, and the usage are kept until the server
          restarts.
        </p>
      )}
    </>
  )
}

type FormProps = {
  label: string
  button: string
  initial: string
  mono?: boolean
  inputMode?: 'numeric'
  save: (value: string) => Promise<SettingChange>
  saved: (settings: DeveloperSettings) => string
  onChange: Props['onChange']
}

/** One setting's field: saving, saved (its sentence), or refused (the API's message, the field marked invalid). */
function SettingForm({ label, button, initial, mono, inputMode, save, saved, onChange }: FormProps) {
  const [value, setValue] = useState(initial)
  const [saving, setSaving] = useState<Saving>({ status: 'idle' })
  const fieldId = useId()
  const errorId = useId()

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving.status === 'saving') return
    setSaving({ status: 'saving' })
    const change = await save(value.trim())
    if (change.ok) {
      setSaving({ status: 'saved', settings: change.settings })
      onChange(change.settings)
    } else {
      setSaving({ status: 'refused', message: change.message })
    }
  }

  const refused = saving.status === 'refused'
  return (
    <>
      <form className="field-row" onSubmit={(e) => void submit(e)}>
        <label htmlFor={fieldId}>{label}</label>
        <input id={fieldId} className={mono ? 'field mono' : 'field'} value={value}
          onChange={(e) => setValue(e.target.value)} inputMode={inputMode}
          autoComplete="off" spellCheck={false} aria-invalid={refused || undefined}
          aria-describedby={refused ? errorId : undefined} />
        <button className="btn btn-primary" type="submit" disabled={saving.status === 'saving'}
          aria-busy={saving.status === 'saving'}>{button}</button>
      </form>
      {refused && <p id={errorId} className="notice field-note" role="alert">{saving.message}</p>}
      {saving.status === 'saved' && <p className="field-note" role="status">{saved(saving.settings)}</p>}
    </>
  )
}
