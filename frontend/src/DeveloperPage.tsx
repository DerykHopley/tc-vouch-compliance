import { type FormEvent, useEffect, useId, useRef, useState } from 'react'
import { count, dollars, plural } from './assessment.ts'
import {
  changeDailyLimit, changeModel, type CompanyRemoval, type CurrentChange, developerSettings, type DeveloperSettings,
  makeCurrent, type MadeCompany, removeCompany, type RunTotals, type SettingChange, UNAVAILABLE,
} from './developer.ts'
import { fetchProfiles, type ProfileSummary } from './profiles.ts'

type Props = {
  settings: DeveloperSettings | typeof UNAVAILABLE
  onChange: (settings: DeveloperSettings) => void
  /** Opens company setup (#403). */
  onSetup: () => void
  /** A company made in setup was removed (#418), with the note saying so: ProfiledApp reads the list again, and opens
   * the app afresh under the current company when the one shown is no longer it (#480), carrying the note. */
  onRemoved?: (id: string, note: string) => void
  /** A company was made current (#480), with the note saying so: ProfiledApp opens the app afresh under it, here. */
  onMadeCurrent?: (id: string, note: string) => void
  /** A note carried through the app opened afresh (a removal, a Make current, a company made but not made current):
   * said once, on arrival. */
  developerNote?: string
  /** The developerNote was shown: ProfiledApp forgets it, so it isn't said again. */
  onNoteShown?: () => void
}

const FAKE_BECAUSE: Record<NonNullable<DeveloperSettings['fake_reason']>, string> = {
  no_key: 'No OpenRouter key is set: assessments run on the fake model.',
  fake_script: 'ASSESS_FAKE_SCRIPT is set: assessments run on the scripted fake model.',
}

type Saving = { status: 'idle' } | { status: 'saving' } | { status: 'saved'; settings: DeveloperSettings } | { status: 'refused'; message: string }

/**
 * The developer page (#34): the Companies section (#480: every listed company, the current one marked, Make current on
 * the others, Remove on those made in setup, #418), under the way into company setup (#403); the model assessments run
 * on, and a form to change it; the prompt version; each user's daily limit, and a form to change it; every
 * assessment's usage totalled, and a link to OpenRouter's (#35). Only a developer reaches it (App.tsx); the compliance
 * lead never sees it.
 */
export default function DeveloperPage({ settings, onChange, onSetup, onRemoved, onMadeCurrent, developerNote,
  onNoteShown }: Props) {
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
          <section className="sheet" aria-label="Companies">
            <span className="lbl">Companies</span>
            <button type="button" className="btn btn-primary" onClick={onSetup}>Set up a company</button>
            <Companies settings={settings} onChange={onChange} onRemoved={onRemoved} onMadeCurrent={onMadeCurrent}
              developerNote={developerNote} onNoteShown={onNoteShown} />
          </section>
          <Settings settings={settings} onChange={onChange} />
        </>
      )}
    </section>
  )
}

/** What the Companies section is doing: nothing, removing or making current one company, done (its note), or a
 * problem (its message). */
type Acting = { status: 'idle' } | { status: 'removing'; id: string } | { status: 'making'; id: string }
  | { status: 'done'; note: string } | { status: 'problem'; message: string }

/** What a removal that didn't happen says, but for a refusal, which says the API's reason. */
const REMOVE_PROBLEM: Record<Extract<CompanyRemoval['status'], 'unavailable' | 'failed'>, string> = {
  unavailable: 'The database is unavailable just now. Nothing was removed; try again in a moment.',
  failed: 'The company could not be removed. Nothing changed.',
}

/** What a Make current that didn't happen says, but for a refusal, which says the API's reason. */
const CURRENT_PROBLEM: Record<Extract<CurrentChange['status'], 'unavailable' | 'failed'>, string> = {
  unavailable: 'The database is unavailable just now. Nothing changed; try again in a moment.',
  failed: 'The company could not be made current. Nothing changed.',
}

/** "9 Nov 2026": the day a company was made, in the reader's time zone; as written when it doesn't parse. */
const madeOn = (iso: string) => {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

/** `settings` less the company `id`: the lists held, when the settings can't be read again after a removal. */
const without = (settings: DeveloperSettings, id: string): DeveloperSettings => ({
  ...settings,
  companies: settings.companies && settings.companies.filter((c) => c.id !== id),
  listed_companies: settings.listed_companies.filter((c) => c.id !== id),
})

/** The company current in `after`, the settings the API answered after a change. */
const currentIn = (after: DeveloperSettings) => after.listed_companies.find((c) => c.id === after.current_company)

/** The company GET /api/profiles says is current, for when the settings can't be read after a removal; undefined when
 * the list can't be read either. Never worked out here: the API's rule (vouch.current_company) has one home. */
async function listedCurrent(): Promise<ProfileSummary | undefined> {
  const listed = await fetchProfiles()
  return listed?.profiles.find((p) => p.id === listed.current)
}

/** `done`, and when the company now current isn't the one `held` had (the one removed was current, decision 8, or
 * another developer changed it meanwhile), a sentence naming it. */
function withCurrent(done: string, held: DeveloperSettings, now: ProfileSummary | undefined): string {
  return now && now.id !== held.current_company ? `${done} ${now.company} is now the current company.` : done
}

/** One row of the Companies list: a listed company (Current, or Make current), with Remove when made in setup; or a
 * company made in setup that isn't listed (broken, or its id taken by a profile folder since), with Remove only. */
type Row = { id: string; company: string; listed: boolean; made: MadeCompany | undefined }

/** Every listed company in the API's order, then the companies made in setup that aren't listed. A company made in
 * setup is listed when it isn't among the profiles left out: a shadowed one is, and its id lists the file one. */
function rowsOf(settings: DeveloperSettings): Row[] {
  const left = new Set(settings.broken_profiles.map((b) => b.id))
  const made = settings.companies ?? []
  const listedMade = new Map(made.filter((c) => !left.has(c.id)).map((c) => [c.id, c]))
  return [
    ...settings.listed_companies.map((c) => ({ id: c.id, company: c.company, listed: true, made: listedMade.get(c.id) })),
    ...made.filter((c) => left.has(c.id)).map((c) => ({ id: c.id, company: c.company, listed: false, made: c })),
  ]
}

/**
 * The Companies section's list (#480, decision 7): every company listed for users, with its name and id; the current
 * one marked Current, each other one with Make current; a company made in setup also with the day it was made and
 * Remove (#418), and one made but not listed with Remove only. Make current and Remove each send their change once,
 * however often pressed (every button disabled while one is out). Make current opens the app afresh under the new
 * current company, here; refused, it reads the settings again and says why. Remove asks with the browser's own confirm
 * first; removing the current company makes Nordlicht current (decision 8), and the note says so. A problem is said
 * under the list, which stays. A 401 has signed out already: nothing to say. A note takes focus, as the button pressed
 * may be gone; it is carried through the app opened afresh (`developerNote`).
 */
function Companies({ settings, onChange, onRemoved, onMadeCurrent, developerNote, onNoteShown }: {
  settings: DeveloperSettings; onChange: Props['onChange']; onRemoved: Props['onRemoved']
  onMadeCurrent: Props['onMadeCurrent']; developerNote: Props['developerNote']; onNoteShown: Props['onNoteShown']
}) {
  const [acting, setActing] = useState<Acting>(
    developerNote ? { status: 'done', note: developerNote } : { status: 'idle' })
  const busy = useRef(false) // set at once, so a second press before the next render sends nothing
  const note = useRef<HTMLParagraphElement>(null)

  const arriving = useRef(Boolean(developerNote)) // said once, on arrival: the note is held here from now on
  useEffect(() => {
    if (!arriving.current) return
    arriving.current = false
    onNoteShown?.()
  }, [onNoteShown])
  useEffect(() => {
    if (acting.status === 'done') note.current?.focus()
  }, [acting])

  const remove = async ({ id, company }: MadeCompany) => {
    if (busy.current) return
    const question = `Remove ${company}? It leaves the Companies list for everyone and can't be opened or made current `
      + 'again. Its audit trail is kept.'
    if (!window.confirm(question)) return
    busy.current = true
    setActing({ status: 'removing', id })
    const result = await removeCompany(id)
    let done: string | null = null
    let after: DeveloperSettings | null = null // the settings the API answered after it, when they could be read
    if (result.status === 'removed') {
      after = result.settings
      done = `Removed ${company}.`
    } else if (result.status === 'gone') {
      const again = await developerSettings()
      after = again && again !== UNAVAILABLE ? again : null
      done = `${company} was already removed.`
    }
    if (done !== null) {
      onChange(after ?? without(settings, id))
      // The company now current, as the API says: in its settings, else (the one removed was current) in the list.
      const now = after ? currentIn(after) : settings.current_company === id ? await listedCurrent() : undefined
      done = withCurrent(done, settings, now)
    }
    busy.current = false
    if (done !== null) {
      setActing({ status: 'done', note: done })
      onRemoved?.(id, done)
    } else if (result.status === 'refused') {
      setActing({ status: 'problem', message: `Vouch could not remove this company: ${result.problem}` })
    } else if (result.status === 'signed-out') {
      setActing({ status: 'idle' })
    } else if (result.status === 'unavailable' || result.status === 'failed') {
      setActing({ status: 'problem', message: REMOVE_PROBLEM[result.status] })
    }
  }

  const makeRowCurrent = async ({ id, company }: Row) => {
    if (busy.current) return
    busy.current = true
    setActing({ status: 'making', id })
    const result = await makeCurrent(id)
    // Refused, or made with an answer that can't be read: the settings as they now stand.
    const again = result.status === 'refused' || (result.status === 'made' && !result.settings)
      ? await developerSettings() : null
    const reread = again && again !== UNAVAILABLE ? again : null
    busy.current = false
    if (result.status === 'made') {
      const now = result.settings ?? reread
      if (now) onChange(now)
      const done = `${company} is now the current company.`
      setActing({ status: 'done', note: done })
      onMadeCurrent?.(id, done)
    } else if (result.status === 'refused') {
      if (reread) onChange(reread)
      setActing({ status: 'problem', message: `Vouch could not make this company current: ${result.problem}` })
    } else if (result.status === 'signed-out') {
      setActing({ status: 'idle' })
    } else {
      setActing({ status: 'problem', message: CURRENT_PROBLEM[result.status] })
    }
  }

  const out = acting.status === 'removing' || acting.status === 'making'
  return (
    <>
      <ul className="companies">
        {rowsOf(settings).map((row) => {
          const removing = acting.status === 'removing' && acting.id === row.id
          const making = acting.status === 'making' && acting.id === row.id
          const current = row.listed && row.id === settings.current_company
          return (
            <li key={`${row.id}:${row.listed ? 'listed' : 'made'}`}>
              <span className="company-name">{row.company}</span>
              <span className="mono">{row.id}</span>
              {row.made && (
                <time className="field-note" dateTime={row.made.created_at}>Made {madeOn(row.made.created_at)}</time>
              )}
              <span className="company-actions">
                {current && <span className="company-current">Current</span>}
                {row.listed && !current && (
                  <button type="button" className="btn" disabled={out} aria-busy={making}
                    aria-label={making ? undefined : `Make ${row.company} current`}
                    onClick={() => void makeRowCurrent(row)}>
                    {making ? 'Making current…' : 'Make current'}
                  </button>
                )}
                {row.made && (
                  <button type="button" className="btn" disabled={out} aria-busy={removing}
                    aria-label={removing ? undefined : `Remove ${row.company}`}
                    onClick={() => { if (row.made) void remove(row.made) }}>
                    {removing ? 'Removing…' : 'Remove'}
                  </button>
                )}
              </span>
            </li>
          )
        })}
      </ul>
      {settings.companies === null && <p className="field-note">The companies made in setup can't be read just now.</p>}
      {acting.status === 'problem' && <p className="notice field-note" role="alert">{acting.message}</p>}
      {acting.status === 'done' && <p ref={note} className="field-note" role="status" tabIndex={-1}>{acting.note}</p>}
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
          <p>These compliance profiles could not be loaded, so the Companies list leaves them out and none can be made current:</p>
          <ul>
            {settings.broken_profiles.map((b) => (
              <li key={b.id}><span className="mono">{b.id}</span>: <span>{b.problem}</span></li>
            ))}
          </ul>
        </section>
      )}
      {settings.kept_in === 'memory' && (
        <p className="field-note">
          No database is set, so the model choice, the current company, the daily limit and counts, and the usage are
          kept until the server restarts.
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
