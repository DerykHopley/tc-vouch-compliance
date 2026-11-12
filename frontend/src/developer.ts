import { UNREACHABLE, type Usage } from './assessment.ts'
import type { ProfileSummary } from './profiles.ts'
import { authHeaders, refusedBecause, SESSION_ENDED, sessionEnded } from './session.ts'

/** The developer page's API (#34). Only a developer gets an answer; anyone else gets a 403, and no page. */

export type DeveloperSettings = {
  model: string
  /** Where assessments run: `fake` when no OpenRouter key is set. */
  runs_on: 'openrouter' | 'fake'
  /** Why the fake runs: no key, or ASSESS_FAKE_SCRIPT set; null on OpenRouter. */
  fake_reason: 'no_key' | 'fake_script' | null
  prompt_version: string
  usage_url: string
  /** Where the model choice, the current company, the daily counts and the usage are kept: `memory` (no database)
   * lasts until the server restarts. */
  kept_in: 'database' | 'memory'
  /** Assessments each user may start in a UTC day (#35). */
  daily_limit: number
  /** Every assessment's usage added up: real spend on OpenRouter, and the fake model's estimates apart. */
  usage: { openrouter: RunTotals; fake: RunTotals }
  /** The compliance profiles left out of the list because they could not be loaded (#47). */
  broken_profiles: BrokenProfile[]
  /** Each company made in setup, oldest first, broken ones included (#418); null when they can't be read just now. */
  companies: MadeCompany[] | null
  /** Every company listed for users, as GET /api/profiles lists them: file companies first, then those made in setup
   * (#480). */
  /** Every company listed for users; `demo` marks the one whose Reset demo plays the demo round (#605). */
  listed_companies: (ProfileSummary & { demo: boolean })[]
  /** The current company's id (#480): every user's company; null with none listed. */
  current_company: string | null
  /** Each model's list price (#496), in vouch.spending.PRICES' order: for a cost OpenRouter doesn't report. */
  prices: ModelPrice[]
  /** The last 10 assessments and profile passes (#496), newest first, for every company; no chat message. */
  recent_runs: RecentRun[]
}

/** A model's list price (#496), US dollars per million tokens. */
export type ModelPrice = { model: string; input_per_million: number; output_per_million: number }

/** The control a profile pass's usage names (vouch.spending.PROFILE_PASS). */
export const PROFILE_PASS = 'profile-pass'

/** One run's usage as kept (#496): when it ended (ISO 8601), the control assessed or PROFILE_PASS, and its tokens and
 * cost. */
export type RecentRun = Pick<Usage, 'model' | 'input_tokens' | 'output_tokens' | 'reported_cost_usd'
  | 'estimated_cost_usd' | 'estimated' | 'cost_complete'> & { at: string; control_id: string }

/** One message of a prompt, under its role. */
export type PromptMessage = { role: 'system' | 'human'; text: string }

/** One prompt the agent gets (#496), as built for the current company and a control: `sent` is false only for the
 * overlay prompt of a control with no overlay requirement, which then has no messages. */
export type PromptPreview = {
  name: 'gather' | 'overlay' | 'judge' | 'remediation' | 'profile_pass' | 'chat'
  title: string
  version: string
  sent: boolean
  note: string
  messages: PromptMessage[]
}

export type PromptControl = { id: string; question: string }

/** Every prompt the agent gets (#496), built for the current company and `control`; `controls` are every applicable
 * control, for the picker. */
export type DeveloperPrompts = {
  company: { id: string; name: string }
  controls: PromptControl[]
  control: PromptControl
  prompts: PromptPreview[]
}

/** How reading the prompts went: read; the session ended (signed out already); or failed (any other refusal, or the
 * API unreachable). */
export type PromptsRead = { status: 'read'; prompts: DeveloperPrompts } | { status: 'signed-out' } | { status: 'failed' }

/** A company made in company setup, as the developer page lists it: its id, its name and when it was made. */
export type MadeCompany = { id: string; company: string; created_at: string }

/** How removing a company went (#418): removed (the new settings, or null when the API removed it but couldn't read
 * them after); refused (the API's detail: not a company made in setup); gone (none of that id: removed already,
 * elsewhere); the database unavailable; the session ended (signed out already); or failed. */
export type CompanyRemoval =
  | { status: 'removed'; settings: DeveloperSettings | null }
  | { status: 'refused'; problem: string }
  | { status: 'gone' }
  | { status: 'unavailable' }
  | { status: 'signed-out' }
  | { status: 'failed' }

/** How making a company current went (#480): made (the new settings, or null when the API changed it but its answer
 * can't be read: the change is done all the same); refused (the API's detail: not a listed
 * company); the database unavailable; the session ended (signed out already); or failed. */
export type CurrentChange =
  | { status: 'made'; settings: DeveloperSettings | null }
  | { status: 'refused'; problem: string }
  | { status: 'unavailable' }
  | { status: 'signed-out' }
  | { status: 'failed' }

/** A compliance profile left out because it can't be loaded: its name and the problem, with no server path. */
export type BrokenProfile = { id: string; problem: string }

/** Some assessments' usage added up; `incomplete` of them have a cost not wholly known. */
export type RunTotals = Pick<Usage, 'input_tokens' | 'output_tokens' | 'reported_cost_usd' | 'estimated_cost_usd'> & {
  assessments: number
  incomplete: number
}

export type SettingChange = { ok: true; settings: DeveloperSettings } | { ok: false; message: string }

const BAD_FORM = 'A model id is written author/model, such as anthropic/claude-haiku-5.5.'
const REFUSED = 'The model could not be changed. Please try again later.'

/** The developer page while its settings can't be read (the database's 503): it says so. */
export const UNAVAILABLE = 'unavailable'

/** Only the database's 503 says this (vouch.api's database_unavailable), and only after the role check passed. */
const isDatabaseDown = (body: unknown) =>
  typeof body === 'object' && body !== null && (body as { unavailable?: unknown }).unavailable === 'database'

/**
 * The developer settings; UNAVAILABLE on the database's 503 (a developer, whose settings can't be read just now); or
 * null for anything else, where the role is unknown or not a developer's: a 403, a 401, the role lookup's 503, any
 * other refusal, or the API unreachable. The answer itself is the only sign of the role the browser gets.
 */
export async function developerSettings(): Promise<DeveloperSettings | typeof UNAVAILABLE | null> {
  try {
    const response = await fetch('/api/developer/settings', { headers: await authHeaders() })
    if (response.ok) return (await response.json()) as DeveloperSettings
    if (response.status !== 503) return null
    return isDatabaseDown(await response.json().catch(() => null)) ? UNAVAILABLE : null
  } catch {
    return null
  }
}

const BAD_LIMIT = 'The daily limit is a whole number of assessments, from 1 to 10,000.'
const LIMIT_REFUSED = 'The daily limit could not be changed. Please try again later.'

/** Asks for the next assessments to run on `model`; the API checks the id with OpenRouter first. */
export function changeModel(model: string): Promise<SettingChange> {
  return put('/api/developer/model', { model }, BAD_FORM, REFUSED)
}

/** Asks for each user's daily limit to be `limit` assessments, from the next one on. */
export function changeDailyLimit(limit: number): Promise<SettingChange> {
  return put('/api/developer/daily-limit', { daily_limit: limit }, BAD_LIMIT, LIMIT_REFUSED)
}

async function put(path: string, body: object, badForm: string, refused: string): Promise<SettingChange> {
  let response: Response
  try {
    response = await fetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify(body),
    })
  } catch {
    return { ok: false, message: UNREACHABLE }
  }
  if (response.ok) return { ok: true, settings: (await response.json()) as DeveloperSettings }
  if (sessionEnded(response)) {
    return { ok: false, message: SESSION_ENDED }
  }
  const detail = await response.json().then((body: { detail?: unknown }) => body.detail, () => undefined)
  if (typeof detail === 'string') return { ok: false, message: detail }
  return { ok: false, message: response.status === 422 ? badForm : refused } // 422 with a list: pydantic's shape check
}

/** Removes a company made in company setup (#418): it leaves every list for everyone, its audit trail kept. */
export async function removeCompany(id: string): Promise<CompanyRemoval> {
  let response: Response
  try {
    response = await fetch(`/api/developer/profiles/${encodeURIComponent(id)}`,
      { method: 'DELETE', headers: await authHeaders() })
  } catch {
    return { status: 'failed' }
  }
  if (response.ok) {
    const body = (await response.json().catch(() => null)) as DeveloperSettings | { settings_unavailable: true } | null
    if (!body) return { status: 'failed' }
    return { status: 'removed', settings: 'settings_unavailable' in body ? null : body }
  }
  if (sessionEnded(response)) return { status: 'signed-out' } // a 401 signs out, as every API call's does
  if (response.status === 404) return { status: 'gone' }
  if (response.status === 409) return { status: 'refused', problem: await refusedBecause(response) }
  if (response.status === 503 && isDatabaseDown(await response.json().catch(() => null))) return { status: 'unavailable' }
  return { status: 'failed' }
}

/** How a Reset demo went (#605): reset (with the demo round played, or the API's note why not), refused with the
 * API's reason (another reset of the company running, #619), or not. */
export type DemoReset =
  | { status: 'reset'; seeded: boolean; note?: string }
  | { status: 'refused'; problem: string }
  | { status: 'signed-out' }
  | { status: 'unavailable' }
  | { status: 'failed' }

/** Resets the company `id` to its demo state (#605): every user's progress and history under it deleted, and on
 * Nordlicht the demo round played. A developer's only. */
export async function resetDemo(id: string): Promise<DemoReset> {
  let response: Response
  try {
    response = await fetch(`/api/developer/profiles/${encodeURIComponent(id)}/reset-demo`,
      { method: 'POST', headers: await authHeaders() })
  } catch {
    return { status: 'failed' }
  }
  if (response.ok) {
    const body = (await response.json().catch(() => null)) as { seeded?: boolean; note?: string } | null
    return { status: 'reset', seeded: Boolean(body?.seeded), note: body?.note }
  }
  if (sessionEnded(response)) return { status: 'signed-out' }
  if (response.status === 409) return { status: 'refused', problem: await refusedBecause(response) }
  if (response.status === 503 && isDatabaseDown(await response.json().catch(() => null))) return { status: 'unavailable' }
  return { status: 'failed' }
}

/** Makes the company `id` the current company (#480): every user's, from their next page open. */
export async function makeCurrent(id: string): Promise<CurrentChange> {
  let response: Response
  try {
    response = await fetch('/api/developer/current-company', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify({ company: id }),
    })
  } catch {
    return { status: 'failed' }
  }
  if (response.ok) {
    // A 2xx changed it: an answer that can't be read is still a change, never "Nothing changed".
    const body: unknown = await response.json().catch(() => null)
    return { status: 'made', settings: typeof body === 'object' && body !== null ? body as DeveloperSettings : null }
  }
  if (sessionEnded(response)) return { status: 'signed-out' } // a 401 signs out, as every API call's does
  if (response.status === 404) return { status: 'refused', problem: await refusedBecause(response) }
  if (response.status === 503 && isDatabaseDown(await response.json().catch(() => null))) return { status: 'unavailable' }
  return { status: 'failed' }
}

/** Every prompt the agent gets (#496), built for the current company and `control` (the API's first applicable one
 * when none is given). */
export async function developerPrompts(control?: string): Promise<PromptsRead> {
  const query = control === undefined ? '' : `?control=${encodeURIComponent(control)}`
  let response: Response
  try {
    response = await fetch(`/api/developer/prompts${query}`, { headers: await authHeaders() })
  } catch {
    return { status: 'failed' }
  }
  if (response.ok) {
    const body = (await response.json().catch(() => null)) as DeveloperPrompts | null
    return Array.isArray(body?.prompts) && Array.isArray(body.controls) ? { status: 'read', prompts: body }
      : { status: 'failed' }
  }
  if (sessionEnded(response)) return { status: 'signed-out' } // a 401 signs out, as every API call's does
  return { status: 'failed' }
}
