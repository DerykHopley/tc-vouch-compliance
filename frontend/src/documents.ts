import { useEffect, useState } from 'react'
import type { EvidenceKind } from './assessment.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'
import type { ShownDocument } from './DocumentTable.tsx'

/** The Documents tab's data from `GET /api/documents` (#424): each evidence document the profile uses, as
 * `vouch.documents_tab.profile_documents` sends it, with the tab's own words in the profile's language. */

/** Where a document comes from: built in (a profile's files, the sample library) or added by a lead (#585). */
export type Origin = 'built-in' | 'added'

/** A document of the profile, from any of its sources (patch too), with the controls whose newest assessment cites
 * it, in control order, and its origin. */
export type ProfileDocument = ShownDocument & { cited_by: string[]; origin: Origin
  /** An added document the lead withdrew (#586): listed, no longer searched or cited. */
  withdrawn?: boolean }

/** The tab's own words (vouch.documents_tab.DOCUMENT_WORDS), in `words_language`. */
export type DocumentWords = {
  title: string
  /** Each evidence source's name: policies, records, patch. */
  sources: Record<string, string>
  columns: Record<'document' | 'kind' | 'version' | 'approved' | 'review' | 'status' | 'cited_by' | 'origin', string>
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
  /** Each origin's word: "Built in", "Added". */
  origins: Record<Origin, string>
  /** A withdrawn document's status (#586). */
  withdrawn: string
}

export type ProfileDocuments = {
  documents: ProfileDocument[]
  words: DocumentWords
  /** ISO 639-1: the language of `words`, the sheet's `lang`. */
  words_language: string
  /** Whether this user may add, version or withdraw documents: the company's creator or a developer (#624). */
  can_change: boolean
}

/** Shown in place of Add document to a user who may not change the documents (#624). */
export const CHANGE_REFUSED_NOTE = 'Only the user who set up this company, or a developer, can add or withdraw documents.'

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
  | 'no_version' | 'no_date' | 'no_cycle' | 'withdrawn'>

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

/** What a lead says an added document is (#585): a policy (Documented) or a record (Implemented). */
export type AddedKind = 'policy' | 'record'

/** The Add document form's fields, as `POST /api/documents` takes them. */
export type DocumentFields = { title: string; kind: AddedKind; approval_date: string; review_cycle: string }

/** The review cycles an added document may have (vouch.documents.CYCLE_MONTHS), in order. */
export const CYCLES = ['quarterly', 'half-yearly', 'yearly', 'two-yearly'] as const

const KIND_OF: Record<string, AddedKind> = { policy: 'policy', documented: 'policy', record: 'record',
  implemented: 'record' }

/** The fields a file's front matter gives (the `---` block at its top, as vouch.front_matter reads it): its title,
 * kind (policy or documented, record or implemented), approval date (YYYY-MM-DD) and a known review cycle. A value
 * it can't use is left out, so the form keeps its own. Flat `key: value` lines only, quotes dropped. */
export function prefill(text: string): Partial<DocumentFields> {
  const block = /^﻿?---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(text)
  if (!block) return {}
  const values: Record<string, string> = {}
  for (const line of block[1].split(/\r?\n/)) {
    const pair = /^([A-Za-z_]+):\s*(.*?)\s*$/.exec(line)
    if (pair) values[pair[1]] = pair[2].replace(/^(["'])(.*)\1$/, '$2')
  }
  const found: Partial<DocumentFields> = {}
  if (values.title) found.title = values.title
  if (values.kind && Object.hasOwn(KIND_OF, values.kind)) found.kind = KIND_OF[values.kind]
  if (/^\d{4}-\d{2}-\d{2}$/.test(values.approval_date ?? '')) found.approval_date = values.approval_date
  if ((CYCLES as readonly string[]).includes(values.review_cycle ?? '')) found.review_cycle = values.review_cycle
  return found
}

/** Whether two titles name the same document, as the server matches them: ignoring case and spaces at their ends. */
export function sameTitle(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase()
}

/** The version Vouch will give a document added with this title: the next of the added document with that title
 * (ignoring case and spaces at its ends, as the server does), else 1. */
export function versionFor(documents: readonly ProfileDocument[], title: string): number {
  const same = documents.find((d) => d.origin === 'added' && sameTitle(d.title, title))
  return same ? Number(same.version) + 1 : 1
}

/** The answer to an add: added; a warning to confirm (instruction-like text); refused with the server's sentence;
 * or failed. */
export type AddAnswer = { status: 'added' } | { status: 'warning' | 'refused' | 'failed'; message: string }

/** The page's own sentence when an add fails for any reason but a refusal. */
export const ADD_FAILED = 'The document could not be added just now. Please try again.'

/** The file's bytes in base64. */
async function base64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}

/** Add the file as an evidence document of the current company (`POST /api/documents`, #585); `addAnyway` once the
 * lead has seen the warning. */
export async function addDocument(file: File, fields: DocumentFields, addAnyway: boolean): Promise<AddAnswer> {
  try {
    const body = { filename: file.name, content: await base64(file), ...fields, add_anyway: addAnyway }
    const response = await fetch('/api/documents', {
      method: 'POST',
      headers: { ...(await authHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (response.ok) return { status: 'added' }
    if (sessionEnded(response)) return { status: 'failed', message: SESSION_ENDED }
    const read = (await response.json().catch(() => ({}))) as { detail?: unknown }
    if (typeof read.detail !== 'string') return { status: 'failed', message: ADD_FAILED }
    if (response.status === 409) return { status: 'warning', message: read.detail }
    if (response.status === 400 || response.status === 403 || response.status === 413) return { status: 'refused', message: read.detail }
    return { status: 'failed', message: ADD_FAILED }
  } catch {
    return { status: 'failed', message: ADD_FAILED }
  }
}

/** The page's own sentence when a withdraw fails for any reason (#586). */
export const WITHDRAW_FAILED = 'The document could not be withdrawn just now. Please try again.'

/** Withdraw an added document of the current company (`POST /api/documents/{id}/withdraw`, #586): true once it is
 * withdrawn, else the sentence to show. */
export async function withdrawDocument(id: string): Promise<{ ok: true } | { ok: false; message: string }> {
  try {
    const response = await fetch(`/api/documents/${encodeURIComponent(id)}/withdraw`, {
      method: 'POST', headers: await authHeaders(),
    })
    if (response.ok) return { ok: true }
    if (sessionEnded(response)) return { ok: false, message: SESSION_ENDED }
    const read = (await response.json().catch(() => ({}))) as { detail?: unknown }
    // A 403 (#624): who may change documents, in the server's words; anything else, the page's own.
    if (response.status === 403 && typeof read.detail === 'string') return { ok: false, message: read.detail }
    return { ok: false, message: WITHDRAW_FAILED }
  } catch {
    return { ok: false, message: WITHDRAW_FAILED }
  }
}
