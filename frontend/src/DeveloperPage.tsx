import { type FormEvent, useId, useState } from 'react'
import { count, dollars, plural } from './assessment.ts'
import { changeDailyLimit, changeModel, type DeveloperSettings, type RunTotals, type SettingChange, UNAVAILABLE } from './developer.ts'

type Props = { settings: DeveloperSettings | typeof UNAVAILABLE; onChange: (settings: DeveloperSettings) => void }

const FAKE_BECAUSE: Record<NonNullable<DeveloperSettings['fake_reason']>, string> = {
  no_key: 'No OpenRouter key is set: assessments run on the fake model.',
  fake_script: 'ASSESS_FAKE_SCRIPT is set: assessments run on the scripted fake model.',
}

type Saving = { status: 'idle' } | { status: 'saving' } | { status: 'saved'; settings: DeveloperSettings } | { status: 'refused'; message: string }

/**
 * The developer page (#34): the model assessments run on, and a form to change it; the prompt version; each user's
 * daily limit, and a form to change it; every assessment's usage totalled, and a link to OpenRouter's (#35). Only a
 * developer reaches it (App.tsx); the compliance lead never sees it.
 */
export default function DeveloperPage({ settings, onChange }: Props) {
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
      ) : <Settings settings={settings} onChange={onChange} />}
    </section>
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
        <a href={settings.usage_url} target="_blank" rel="noopener noreferrer">Usage and costs on OpenRouter</a>
      </section>
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
