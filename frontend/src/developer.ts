import { UNREACHABLE, type Usage } from './assessment.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** The developer page's API (#34). Only a developer gets an answer; anyone else gets a 403, and no page. */

export type DeveloperSettings = {
  model: string
  /** Where assessments run: `fake` when no OpenRouter key is set. */
  runs_on: 'openrouter' | 'fake'
  /** Why the fake runs: no key, or ASSESS_FAKE_SCRIPT set; null on OpenRouter. */
  fake_reason: 'no_key' | 'fake_script' | null
  prompt_version: string
  usage_url: string
  /** Where the model choice, the daily counts and the usage are kept: `memory` (no database) lasts until the server
   * restarts. */
  kept_in: 'database' | 'memory'
  /** Assessments each user may start in a UTC day (#35). */
  daily_limit: number
  /** Every assessment's usage added up; `unpriced` ones have no cost in `cost_usd`. */
  usage: UsageTotals
}

export type UsageTotals = Omit<Usage, 'model' | 'cost_usd'> & { assessments: number; cost_usd: number; unpriced: number }

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
