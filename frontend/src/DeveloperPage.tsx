import { type FormEvent, useId, useState } from 'react'
import { changeModel, type DeveloperSettings } from './developer.ts'

type Props = { settings: DeveloperSettings; onChange: (settings: DeveloperSettings) => void }

type Saving = { status: 'idle' } | { status: 'saving' } | { status: 'saved'; model: string } | { status: 'refused'; message: string }

/**
 * The developer page (#34): the model assessments run on, and a form to change it; the prompt version; a link to
 * usage and costs. Only a developer reaches it (App.tsx); the compliance lead never sees it.
 */
export default function DeveloperPage({ settings, onChange }: Props) {
  const [model, setModel] = useState(settings.model)
  const [saving, setSaving] = useState<Saving>({ status: 'idle' })
  const fieldId = useId()
  const errorId = useId()

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving.status === 'saving') return
    setSaving({ status: 'saving' })
    const change = await changeModel(model.trim())
    if (change.ok) {
      setSaving({ status: 'saved', model: change.settings.model })
      onChange(change.settings)
    } else {
      setSaving({ status: 'refused', message: change.message })
    }
  }

  const refused = saving.status === 'refused'
  return (
    <section className="paper wrap" aria-label="Developer settings">
      <div className="paper-head">
        <span className="lbl">Developers only</span>
        <h2>Developer settings</h2>
      </div>
      {settings.runs_on === 'fake' && (
        <p className="sheet notice">No OpenRouter key is set: assessments run on the fake model.</p>
      )}
      <div className="sheet">
        <span className="lbl">Model</span>
        <span className="mono">{settings.model}</span>
        <form className="field-row" onSubmit={(e) => void submit(e)}>
          <label htmlFor={fieldId}>Model id</label>
          <input id={fieldId} className="field mono" value={model} onChange={(e) => setModel(e.target.value)}
            autoComplete="off" spellCheck={false} aria-invalid={refused || undefined}
            aria-describedby={refused ? errorId : undefined} />
          <button className="btn btn-primary" type="submit" disabled={saving.status === 'saving'}
            aria-busy={saving.status === 'saving'}>Change model</button>
        </form>
        {refused && <p id={errorId} className="notice field-note" role="alert">{saving.message}</p>}
        {saving.status === 'saved' && (
          <p className="field-note" role="status">The next assessment runs on {saving.model}.</p>
        )}
        {settings.kept_in === 'memory' && (
          <p className="field-note">No database is set, so the choice is kept until the server restarts.</p>
        )}
      </div>
      <div className="sheet">
        <span className="lbl">Prompt version</span>
        <span className="mono">{settings.prompt_version}</span>
      </div>
      <div className="sheet">
        <span className="lbl">Usage</span>
        <a href={settings.usage_url} target="_blank" rel="noopener noreferrer">Usage and costs on OpenRouter</a>
      </div>
    </section>
  )
}
