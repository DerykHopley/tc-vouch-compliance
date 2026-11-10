import DocumentTable from './DocumentTable.tsx'
import { type DocumentsState, type ProfileDocument, type ProfileDocuments, wordFor } from './documents.ts'

/** The Documents tab (#424), the Company page's Documents panel since #505: each evidence document the profile uses,
 * read-only, one table per source, with the controls citing it. Its words are the served ones, and the sheet carries
 * their language (`words_language`); the panel around it (loading, the notice, Try again) stays English, as the
 * report's does. Read each time the page opens, alongside the company (documents.ts' `useDocuments`, which the page calls); its states
 * stay inside the panel, so the rest of the page stays up. */

/** The Documents panel's content for `state`: the loading line, the notice with Try again, or the sheet. */
export default function Documents({ state, tryAgain }: { state: DocumentsState; tryAgain: () => void }) {
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
  return <Sheet {...state.documents} />
}

function Sheet({ documents, words, words_language }: ProfileDocuments) {
  // The sources in the order the documents come: the profile's.
  const sources = [...new Set(documents.map((d) => d.source))]
  const citedBy = {
    heading: words.columns.cited_by,
    cell: (d: ProfileDocument) => d.cited_by.length
      ? d.cited_by.join(', ') : <span className="report-muted">{words.not_cited}</span>,
  }
  return (
    <article className="sheet report-doc documents-doc" lang={words_language} aria-labelledby="documents-title">
      <h2 id="documents-title">{words.title}</h2>
      {documents.length === 0 ? <p className="report-muted">{words.no_documents}</p>
        : sources.map((source) => (
          <DocumentTable key={source} caption={wordFor(words.sources, source) ?? source}
            documents={documents.filter((d) => d.source === source)} words={words} extraColumn={citedBy} />
        ))}
    </article>
  )
}
