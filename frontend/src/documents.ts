import { useEffect, useState } from 'react'
import type { EvidenceKind } from './assessment.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'
import type { ShownDocument } from './DocumentTable.tsx'

/** The Documents tab's data from `GET /api/documents` (#424): each evidence document the profile uses, as
 * `vouch.documents_tab.profile_documents` sends it, with the tab's own words in the profile's language. */

/** A document of the profile, from any of its sources (patch too), with the controls whose newest assessment cites
 * it, in control order. */
export type ProfileDocument = ShownDocument & { cited_by: string[] }

/** The tab's own words (vouch.documents_tab.DOCUMENT_WORDS), in `words_language`. */
export type DocumentWords = {
  title: string
  /** Each evidence source's name: policies, records, patch. */
  sources: Record<string, string>
  columns: Record<'document' | 'kind' | 'version' | 'approved' | 'review' | 'status' | 'cited_by', string>
  kinds: Record<EvidenceKind, string>
  /** Each known review cycle's word; a cycle not here shows as written. */
  cycles: Record<string, string>
  /** The report's stale word (vouch.report.WORDS' labels.stale). */
  stale: string
  /** Why a stale document's review date can't be worked out, beside the stale word: an overdue one needs none. */
  stale_reasons: Record<'no_approval_date' | 'no_review_cycle' | 'unknown_cycle', string>
  current: string
  no_version: string
  no_date: string
  no_cycle: string
  not_cited: string
  no_documents: string
}

export type ProfileDocuments = {
  documents: ProfileDocument[]
  words: DocumentWords
  /** ISO 639-1: the language of `words`, the sheet's `lang`. */
  words_language: string
}

/** The page's own sentence when the documents can't be read. */
export const DOCUMENTS_FAILED = 'The documents could not be loaded.'

/** The profile's documents, or the sentence to show with Try again. */
export async function fetchDocuments(): Promise<{ ok: true; documents: ProfileDocuments } | { ok: false; message: string }> {
  try {
    const response = await fetch('/api/documents', { headers: await authHeaders() })
    if (response.ok) return { ok: true, documents: (await response.json()) as ProfileDocuments }
    return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : DOCUMENTS_FAILED }
  } catch {
    return { ok: false, message: DOCUMENTS_FAILED }
  }
}

/** The words a document table takes (DocumentTable's `words`): its headings and cell words. */
export type TableWords = Pick<DocumentWords, 'columns' | 'kinds' | 'cycles' | 'stale' | 'stale_reasons' | 'current'
  | 'no_version' | 'no_date' | 'no_cycle'>

/** The word for `key` in a table of words, or undefined: its own keys only, never an Object.prototype member (a review
 * cycle or source named `constructor` is shown as written). */
export function wordFor(words: Record<string, string>, key: string): string | undefined {
  return Object.hasOwn(words, key) ? words[key] : undefined
}

export type DocumentsState = { status: 'loading' } | { status: 'ready'; documents: ProfileDocuments }
  | { status: 'failed'; message: string }

/** The documents, read as the caller mounts; `reloads`: bumped to read them again without the loading line (a chat
 * assessment changes Cited by). `tryAgain` reads them again after a failure. */
export function useDocuments(reloads = 0): { state: DocumentsState; tryAgain: () => void } {
  const [state, setState] = useState<DocumentsState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0) // bumped by Try again to read the documents again
  useEffect(() => {
    const answer = { wanted: true } // until the page closes or reads again
    void fetchDocuments().then((read) => answer.wanted && setState(read.ok
      ? { status: 'ready', documents: read.documents } : { status: 'failed', message: read.message }))
    return () => { answer.wanted = false }
  }, [attempt, reloads])
  const tryAgain = () => {
    setState({ status: 'loading' })
    setAttempt((n) => n + 1)
  }
  return { state, tryAgain }
}
