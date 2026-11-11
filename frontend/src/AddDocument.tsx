import { type FormEvent, useId, useRef, useState } from 'react'
import { ADD_FAILED, addDocument, type AddedKind, CYCLES, type DocumentFields, prefill, type ProfileDocument,
  versionFor } from './documents.ts'

export { ADD_FAILED }

/** The note under the form (Q1): the live app holds invented data only. */
export const INVENTED_ONLY = 'Vouch holds invented data only: add an invented document, never a real company\'s.'
const NO_FILE = 'Choose a .md or .txt file first.'
const CYCLE_WORDS: Record<(typeof CYCLES)[number], string> = { quarterly: 'Quarterly', 'half-yearly': 'Half-yearly',
  yearly: 'Yearly', 'two-yearly': 'Two-yearly' }

type Props = {
  /** The documents listed, for the version the add will be. */
  documents: readonly ProfileDocument[]
  /** Add new version: the added document's title, fixed; none for Add document. */
  title?: string
  onAdded: () => void
  onCancel: () => void
}

type Sent = { status: 'open' } | { status: 'sending' } | { status: 'refused' | 'failed' | 'warning'; message: string }

/** Today's date in the lead's time zone, as a date field holds it (YYYY-MM-DD). */
function today(): string {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10)
}

/** The newest version's fields of the added document with this title, for Add new version's prefill. */
function fieldsOf(documents: readonly ProfileDocument[], title: string | undefined): DocumentFields {
  const newest = title === undefined ? undefined : documents.find((d) => d.origin === 'added' && d.title === title)
  return {
    title: title ?? '',
    kind: newest?.kind === 'implemented' ? 'record' : 'policy',
    approval_date: newest?.approval_date ?? today(),
    review_cycle: newest?.review_cycle ?? 'yearly',
  }
}

/**
 * The Add document form on the Documents panel (#585, docs/design.md's Documents tab): the file (.md or .txt), Title,
 * Kind, Approval date and Review cycle, prefilled from the file's front matter when it has one; the version Vouch will
 * give it; the invented-data note. Add new version opens it with the title fixed. Instruction-like text is a warning
 * first, with Add anyway and Cancel in the buttons' place. Interface words, English.
 */
export default function AddDocument({ documents, title, onAdded, onCancel }: Props) {
  const [fields, setFields] = useState<DocumentFields>(() => fieldsOf(documents, title))
  const [file, setFile] = useState<File | null>(null)
  const [sent, setSent] = useState<Sent>({ status: 'open' })
  const id = useId()
  const heading = title === undefined ? 'Add document' : 'Add new version'
  const chosen = useRef(0) // the latest file chosen: an earlier one's read mustn't prefill over it
  const set = <K extends keyof DocumentFields>(key: K, value: DocumentFields[K]) => setFields((f) => ({ ...f, [key]: value }))

  const choose = async (picked: File | null) => {
    setFile(picked)
    const mine = ++chosen.current
    if (!picked) return
    const found = prefill(await picked.text().catch(() => ''))
    if (mine !== chosen.current) return
    setFields((f) => ({ ...f, ...found, title: title ?? found.title ?? f.title }))
  }

  const send = async (addAnyway: boolean) => {
    if (!file) {
      setSent({ status: 'refused', message: NO_FILE })
      return
    }
    setSent({ status: 'sending' })
    const answer = await addDocument(file, fields, addAnyway)
    if (answer.status === 'added') onAdded()
    else setSent(answer)
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (sent.status !== 'sending') void send(false)
  }

  const sending = sent.status === 'sending'
  const message = sent.status === 'refused' || sent.status === 'failed' || sent.status === 'warning' ? sent.message : null
  return (
    <form className="doc-form" aria-labelledby={`${id}-h`} onSubmit={submit}>
      <h3 id={`${id}-h`}>{heading}</h3>
      <div className="doc-form-fields">
        <label className="doc-form-field doc-form-file">
          <span>File (.md or .txt)</span>
          <input className="field" type="file" accept=".md,.txt,text/markdown,text/plain" disabled={sending}
            onChange={(e) => void choose(e.target.files?.[0] ?? null)} />
        </label>
        <label className="doc-form-field">
          <span>Title</span>
          <input className="field" type="text" value={fields.title} readOnly={title !== undefined} maxLength={120}
            disabled={sending} onChange={(e) => set('title', e.target.value)} />
        </label>
        <label className="doc-form-field">
          <span>Kind</span>
          <select className="field" value={fields.kind} disabled={sending}
            onChange={(e) => set('kind', e.target.value as AddedKind)}>
            <option value="policy">Policy</option>
            <option value="record">Record</option>
          </select>
        </label>
        <label className="doc-form-field">
          <span>Approval date</span>
          <input className="field" type="date" value={fields.approval_date} disabled={sending}
            onChange={(e) => set('approval_date', e.target.value)} />
        </label>
        <label className="doc-form-field">
          <span>Review cycle</span>
          <select className="field" value={fields.review_cycle} disabled={sending}
            onChange={(e) => set('review_cycle', e.target.value)}>
            {CYCLES.map((c) => <option key={c} value={c}>{CYCLE_WORDS[c]}</option>)}
          </select>
        </label>
      </div>
      <p className="field-note">Vouch will add this as version {versionFor(documents, fields.title)}.</p>
      <p className="field-note">{INVENTED_ONLY}</p>
      {message !== null && <p role="alert" className="field-note notice">{message}</p>}
      <div className="doc-form-actions">
        {sent.status === 'warning' ? (
          <>
            <button className="btn btn-primary" type="button" onClick={() => void send(true)}>Add anyway</button>
            <button className="btn btn-ghost" type="button" onClick={() => setSent({ status: 'open' })}>Cancel</button>
          </>
        ) : (
          <>
            <button className="btn btn-primary" type="submit" disabled={sending} aria-busy={sending || undefined}>
              {heading === 'Add document' ? 'Add document' : 'Add version'}
            </button>
            <button className="btn btn-ghost" type="button" disabled={sending} onClick={onCancel}>Cancel</button>
          </>
        )}
      </div>
    </form>
  )
}
