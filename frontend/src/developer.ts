import { UNREACHABLE } from './assessment.ts'
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
  /** Where the model choice is kept: `memory` (no database) lasts until the server restarts. */
  kept_in: 'database' | 'memory'
}

export type ModelChange = { ok: true; settings: DeveloperSettings } | { ok: false; message: string }

const BAD_FORM = 'A model id is written author/model, such as anthropic/claude-haiku-5.5.'
const REFUSED = 'The model could not be changed. Please try again later.'

/** The developer page while its settings can't be read (a 503: the database is down): it says so. */
export const UNAVAILABLE = 'unavailable'

/**
 * The developer settings; UNAVAILABLE when the API answered but couldn't give them; or null when this user has no
 * developer page: a compliance lead (403), signed out (401), or the API unreachable. The answer itself is the only
 * sign of the role the browser gets.
 */
export async function developerSettings(): Promise<DeveloperSettings | typeof UNAVAILABLE | null> {
  try {
    const response = await fetch('/api/developer/settings', { headers: await authHeaders() })
    if (response.status === 401 || response.status === 403) return null
    return response.ok ? ((await response.json()) as DeveloperSettings) : UNAVAILABLE
  } catch {
    return null
  }
}

/** Asks for the next assessments to run on `model`; the API checks the id with OpenRouter first. */
export async function changeModel(model: string): Promise<ModelChange> {
  let response: Response
  try {
    response = await fetch('/api/developer/model', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify({ model }),
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
  return { ok: false, message: response.status === 422 ? BAD_FORM : REFUSED } // 422 with a list: pydantic's shape check
}
