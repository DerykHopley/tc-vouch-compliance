import type { Citation, Verdict } from './assessment.ts'
import type { Readiness } from './overview.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** The readiness report from `GET /api/report` (#234), as `vouch.report.readiness_report` sends it, with its own words
 * in the profile's language (`words`); and the audit trail's older pages from `GET /api/audit-trail` (#39). */

export type ReportHead = {
  company: string
  country: string
  base_framework: string
  protection_level: 'normal' | 'high' | 'very high'
  /** The country overlays' names. */
  overlays: string[]
  /** ISO 639-1: the profile's answer language (the quotes and the model's text). */
  answer_language: string
  /** ISO 639-1: the language of `words`, the report's own (English for an answer language without its own words):
   * the report's `lang`. */
  words_language: string
  /** ISO 8601: the day the report was drawn up, in UTC. */
  date: string
}

/** A quote an accepted control's verdict rests on: a citation's fields, less why it is stale. */
export type ReportQuote = Pick<Citation, 'doc_id' | 'version' | 'section' | 'quote' | 'kind' | 'stale'>

/** A control as the report shows it: accepted with its reviewed verdict, or open with nulls and no quotes. */
export type ReportControl = {
  id: string
  question: string
  status: 'accepted' | 'open'
  verdict: Verdict | null
  /** The reviewer's display name. */
  reviewer: string | null
  /** ISO 8601, a full time. */
  reviewed_at: string | null
  quotes: ReportQuote[]
}

export type ReportItem = { requirement_id: string; missing: string; owner_role: string; due_date: string }

export type NotApplicable = { control_id: string; requirement_id: string; text: string; reason: string }

export type TrailAction = 'accept' | 'reject' | 'override' | 'confirm' | 'approve' | 'discard'

/** One audit-trail entry (`vouch.audit.AuditEntry.to_dict`). */
export type TrailEntry = {
  id: number | null
  at: string
  reviewer_name: string
  control_id: string
  requirement_id: string | null
  action: TrailAction
  note: string
  verdict_before: Verdict | null
  verdict_after: Verdict | null
  evidence: { doc_id: string; version: string }[]
  model: string
  prompt_version: string
}

/** The report's own words, in its language (English for one without a table): every heading, column name, status,
 * action and sentence the report shows. `sentences.accepted_of` has `{accepted}` and `{controls}` to fill in. */
export type ReportWords = {
  title: string
  labels: Record<string, string>
  verdicts: Record<Verdict, string>
  statuses: Record<ReportControl['status'], string>
  actions: Record<TrailAction, string>
  protection_levels: Record<ReportHead['protection_level'], string>
  languages: Record<string, string>
  sentences: Record<string, string>
}

/** A page of the audit trail, newest first; `next` reads on at `olderEntries`, null when there are no more. */
export type TrailPage = { entries: TrailEntry[]; next: number | null }

export type ReadinessReport = {
  head: ReportHead
  readiness: Readiness
  controls: ReportControl[]
  remediation: { control_id: string; question: string; items: ReportItem[] }[]
  not_applicable: NotApplicable[]
  /** The newest page. */
  trail: TrailPage
  /** The finished sentence, the reviewers' names joined. */
  disclaimer: string
  words: ReportWords
}

/** The API's own wording (vouch.api.REPORT_FAILED). */
export const REPORT_FAILED = 'The readiness report could not be loaded just now. Please try again.'
export const OLDER_FAILED = 'The older entries could not be loaded just now. Please try again.'
/** The API's own wording (vouch.api.EXPORT_FAILED). */
export const EXPORT_FAILED = 'The readiness report could not be exported just now. Please try again.'

type Failed = { ok: false; message: string }

/** The report, or the sentence to show with Try again. */
export async function fetchReport(): Promise<{ ok: true; report: ReadinessReport } | Failed> {
  try {
    const response = await fetch('/api/report', { headers: await authHeaders() })
    if (response.ok) return { ok: true, report: (await response.json()) as ReadinessReport }
    return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : REPORT_FAILED }
  } catch {
    return { ok: false, message: REPORT_FAILED }
  }
}

/** The trail's page after the entry `before` (the report's or the last page's `next`). */
export async function olderEntries(before: number): Promise<({ ok: true } & TrailPage) | Failed> {
  try {
    const response = await fetch(`/api/audit-trail?before=${before}`, { headers: await authHeaders() })
    if (!response.ok) return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : OLDER_FAILED }
    const page = (await response.json()) as TrailPage
    return { ok: true, entries: page.entries, next: page.next }
  } catch {
    return { ok: false, message: OLDER_FAILED }
  }
}

/** How long the browser keeps a saved file's object URL: a minute, as FileSaver.js does, since revoking it sooner can
 * cancel the download in Safari and some Firefox versions. */
const KEEP_URL_MS = 60_000

/** The report's two downloads (#57, #308): the file each endpoint (`GET /api/report/<format>`) sends. */
export type ReportFormat = 'markdown' | 'pdf'

const EXTENSION: Record<ReportFormat, string> = { markdown: 'md', pdf: 'pdf' }

/** The name a `Content-Disposition` gives (vouch.api.attachment): `filename*`'s UTF-8 name when it decodes, else the
 * ASCII `filename`, else a plain one for the format. */
function fileName(disposition: string | null, format: ReportFormat): string {
  const utf8 = /filename\*=UTF-8''([^;\s]+)/i.exec(disposition ?? '')?.[1]
  if (utf8) {
    try {
      return decodeURIComponent(utf8)
    } catch {
      // malformed: the ASCII name stands in
    }
  }
  return /filename="([^"]+)"/.exec(disposition ?? '')?.[1] ?? `readiness-report.${EXTENSION[format]}`
}

/** The report as one file in the format, Markdown (`GET /api/report/markdown`, #57) or PDF (`GET /api/report/pdf`,
 * #308), saved under the server's name. The whole body is read before anything is saved, so a failure saves nothing
 * and no file is ever half-downloaded. */
export async function exportReport(format: ReportFormat): Promise<{ ok: true } | Failed> {
  let file: Blob
  let name: string
  try {
    const response = await fetch(`/api/report/${format}`, { headers: await authHeaders() })
    if (!response.ok) return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : EXPORT_FAILED }
    file = await response.blob()
    name = fileName(response.headers.get('Content-Disposition'), format)
  } catch {
    return { ok: false, message: EXPORT_FAILED }
  }
  const url = URL.createObjectURL(file)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.hidden = true
  document.body.append(link) // Firefox saves only from a link in the document
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), KEEP_URL_MS)
  return { ok: true }
}
