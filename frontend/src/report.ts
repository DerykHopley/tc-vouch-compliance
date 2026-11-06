import type { EvidenceKind, Verdict } from './assessment.ts'
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
  /** ISO 639-1: the report's language. */
  answer_language: string
  /** ISO 8601: the day the report was drawn up, in UTC. */
  date: string
}

export type ReportQuote = { doc_id: string; version: string; section: string; quote: string; kind: EvidenceKind; stale: boolean }

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

export type TrailAction = 'accept' | 'reject' | 'override' | 'approve' | 'discard'

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

export type Report = {
  head: ReportHead
  readiness: Readiness
  controls: ReportControl[]
  remediation: { control_id: string; question: string; items: ReportItem[] }[]
  not_applicable: NotApplicable[]
  /** The newest page; `next` reads on at `olderEntries`, null when there are no more. */
  trail: { entries: TrailEntry[]; next: number | null }
  /** The finished sentence, the reviewers' names joined. */
  disclaimer: string
  words: ReportWords
}

/** The API's own wording (vouch.api.REPORT_FAILED). */
export const REPORT_FAILED = 'The readiness report could not be loaded just now. Please try again.'
export const OLDER_FAILED = 'The older entries could not be loaded just now. Please try again.'

type Failed = { ok: false; message: string }

/** The report, or the sentence to show with Try again. */
export async function fetchReport(): Promise<{ ok: true; report: Report } | Failed> {
  try {
    const response = await fetch('/api/report', { headers: await authHeaders() })
    if (response.ok) return { ok: true, report: (await response.json()) as Report }
    return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : REPORT_FAILED }
  } catch {
    return { ok: false, message: REPORT_FAILED }
  }
}

/** The trail's page after the entry `before` (the report's or the last page's `next`). */
export async function olderEntries(before: number):
  Promise<{ ok: true; entries: TrailEntry[]; next: number | null } | Failed> {
  try {
    const response = await fetch(`/api/audit-trail?before=${before}`, { headers: await authHeaders() })
    if (!response.ok) return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : OLDER_FAILED }
    const page = (await response.json()) as { entries: TrailEntry[]; next: number | null }
    return { ok: true, entries: page.entries, next: page.next }
  } catch {
    return { ok: false, message: OLDER_FAILED }
  }
}
