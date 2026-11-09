import type { EvidenceKind } from './assessment.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'
import type { LibraryDocument } from './setup.ts'

/** The Documents tab's data from `GET /api/documents` (#424): each evidence document the profile uses, as
 * `vouch.documents_tab.profile_documents` sends it, with the tab's own words in the profile's language. */

/** A document of the profile, from any of its sources (patch too), with the controls whose newest assessment cites
 * it, in control order. */
export type ProfileDocument = Omit<LibraryDocument, 'source'> & { source: string; cited_by: string[] }

/** The tab's own words (vouch.documents_tab.DOCUMENT_WORDS), in `words_language`. */
export type DocumentWords = {
  title: string
  /** Each evidence source's name: policies, records, patch. */
  sources: Record<string, string>
  columns: Record<'document' | 'kind' | 'version' | 'approved' | 'review' | 'status' | 'cited_by', string>
  kinds: Record<EvidenceKind, string>
  /** Each known review cycle's word; a cycle not here shows as written. */
  cycles: Record<string, string>
  stale: string
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
export type TableWords = Pick<DocumentWords, 'columns' | 'kinds' | 'cycles' | 'stale' | 'current' | 'no_version'
  | 'no_date' | 'no_cycle'>
