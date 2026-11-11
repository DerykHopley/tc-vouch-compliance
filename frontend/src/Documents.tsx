import { useState } from 'react'
import AddDocument from './AddDocument.tsx'
import DocumentTable from './DocumentTable.tsx'
import { type DocumentsState, type ProfileDocument, type ProfileDocuments, withdrawDocument, wordFor }
  from './documents.ts'
import InlineConfirm from './InlineConfirm.tsx'

type Props = { state: DocumentsState; tryAgain: () => void; onAdded: () => void }

/** The Documents tab (#424), the Company page's Documents panel since #505: each evidence document the profile uses,
 * one table per source, with the controls citing it and its origin. A lead adds a document there, or a new version of
 * an added one (#585, AddDocument), or withdraws an added one (#586, Withdraw), which then stays listed as Withdrawn
 * with no actions; built-in documents are labelled "Built in" and offer nothing. Its words are the
 * served ones, and the sheet carries their language (`words_language`); the panel around it (loading, the notice, Try
 * again, Add document and its form) stays English, as the report's does. Read each time the page opens, alongside the
 * company (documents.ts' `useDocuments`, which the page calls), and again after an add (`onAdded`); its states stay
 * inside the panel, so the rest of the page stays up.
 *
 * The panel's content for `state`: the loading line, the notice with Try again, or the sheet. */
export default function Documents({ state, tryAgain, onAdded }: Props) {
  if (state.status === 'loading') {
    return <p className="lbl" role="status">Loading the documents…</p>
  }
  if (state.status === 'failed') {
    return (
      <div className="sheet notice">
        <p role="alert" className="answer">{state.message}</p>
        <button className="btn" onClick={tryAgain}>Try again</button>
      </div>
    )
  }
  return <Sheet {...state.documents} onAdded={onAdded} />
}

/** The form open: Add document (no title) or Add new version of an added document's title; null, none. */
type Open = { title?: string } | null

function Sheet({ documents, words, words_language, onAdded }: ProfileDocuments & { onAdded: () => void }) {
  const [open, setOpen] = useState<Open>(null)
  // The sources in the order the documents come: the profile's.
  const sources = [...new Set(documents.map((d) => d.source))]
  const citedBy = {
    heading: words.columns.cited_by,
    cell: (d: ProfileDocument) => d.cited_by.length
      ? d.cited_by.join(', ') : <span className="report-muted">{words.not_cited}</span>,
  }
  const origin = {
    heading: words.columns.origin,
    cell: (d: ProfileDocument) => d.origin === 'built-in'
      ? <span className="report-muted">{words.origins['built-in']}</span>
      : (
        <span className="doc-origin">
          <span>{words.origins.added}</span>
          {!d.withdrawn && <>
            <button className="btn btn-ghost" type="button" lang="en" aria-label={`Add new version of ${d.title}`}
              onClick={() => setOpen({ title: d.title })}>Add new version</button>
            <Withdraw document={d} onWithdrawn={onAdded} />
          </>}
        </span>
      ),
  }
  const added = () => {
    setOpen(null)
    onAdded()
  }
  return (
    <article className="sheet report-doc documents-doc" lang={words_language} aria-labelledby="documents-title">
      <div className="documents-head">
        <h2 id="documents-title">{words.title}</h2>
        {open === null && (
          <button className="btn" type="button" lang="en" onClick={() => setOpen({})}>Add document</button>
        )}
      </div>
      {open !== null && (
        <div lang="en">
          <AddDocument key={open.title ?? ''} documents={documents} title={open.title} onAdded={added}
            onCancel={() => setOpen(null)} />
        </div>
      )}
      {documents.length === 0 ? <p className="report-muted">{words.no_documents}</p>
        : sources.map((source) => (
          <DocumentTable key={source} caption={wordFor(words.sources, source) ?? source}
            documents={documents.filter((d) => d.source === source)} words={words} extraColumns={[citedBy, origin]} />
        ))}
    </article>
  )
}

/** An added document's Withdraw (#586): the in-app ask, then `POST /api/documents/{id}/withdraw`. States: asking (the
 * ask), withdrawing (the button busy and disabled), failed (the sentence, Withdraw again), withdrawn (`onWithdrawn`:
 * the panel reads its documents again, and the row shows Withdrawn with no actions). English, as the interface. */
function Withdraw({ document, onWithdrawn }: { document: ProfileDocument; onWithdrawn: () => void }) {
  const [state, setState] = useState<{ status: 'idle' | 'withdrawing' } | { status: 'failed'; message: string }>(
    { status: 'idle' })
  const busy = state.status === 'withdrawing'
  const withdraw = () => {
    setState({ status: 'withdrawing' })
    void withdrawDocument(document.id).then((answer) => {
      if (answer.ok) onWithdrawn()
      else setState({ status: 'failed', message: answer.message })
    })
  }
  return (
    <span className="doc-withdraw" lang="en">
      <InlineConfirm label={busy ? 'Withdrawing…' : 'Withdraw'} confirmLabel="Withdraw" onConfirm={withdraw}
        question={`Withdraw "${document.title}"? It is no longer searched or cited. Its versions are kept, so the `
          + 'audit trail stays true.'}
        trigger={{ 'aria-label': `${busy ? 'Withdrawing' : 'Withdraw'} ${document.title}`, 'aria-busy': busy,
          'aria-disabled': busy }} />
      {state.status === 'failed' && <p className="field-note" role="alert">{state.message}</p>}
    </span>
  )
}
