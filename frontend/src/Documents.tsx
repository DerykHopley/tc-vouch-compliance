import { useEffect, useState } from 'react'
import DocumentTable from './DocumentTable.tsx'
import { fetchDocuments, type ProfileDocument, type ProfileDocuments } from './documents.ts'

/** The Documents tab (#424): each evidence document the profile uses, read-only, one table per source, with the
 * controls citing it. Its words are the served ones, and the sheet carries their language (`words_language`); the
 * page around it (loading, the notice, Try again) stays English, as the report's does. Read each time it opens. */

type State = { status: 'loading' } | { status: 'ready'; documents: ProfileDocuments } | { status: 'failed'; message: string }

export default function Documents() {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0) // bumped by Try again to read the documents again
  useEffect(() => {
    const answer = { wanted: true } // until the page closes or reads again
    void fetchDocuments().then((read) => answer.wanted && setState(read.ok
      ? { status: 'ready', documents: read.documents } : { status: 'failed', message: read.message }))
    return () => { answer.wanted = false }
  }, [attempt])
  const tryAgain = () => {
    setState({ status: 'loading' })
    setAttempt((n) => n + 1)
  }

  if (state.status === 'loading') {
    return <main className="report wrap"><p className="lbl" role="status">Loading the documents…</p></main>
  }
  if (state.status === 'failed') {
    return (
      <main className="report wrap">
        <section className="sheet notice">
          <p role="alert" className="answer">{state.message}</p>
          <button className="btn" onClick={tryAgain}>Try again</button>
        </section>
      </main>
    )
  }
  return <main className="report wrap"><Sheet {...state.documents} /></main>
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
      <h1 id="documents-title">{words.title}</h1>
      {documents.length === 0 ? <p className="report-muted">{words.no_documents}</p>
        : sources.map((source) => (
          <DocumentTable key={source} caption={words.sources[source] ?? source}
            documents={documents.filter((d) => d.source === source)} words={words} extraColumn={citedBy} />
        ))}
    </article>
  )
}
