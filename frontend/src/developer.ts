import { UNREACHABLE } from './assessment.ts'
import { authHeaders, signedOut } from './session.ts'

/** The developer page's API (#34). Only a developer gets an answer; anyone else gets a 403, and no page. */

export type DeveloperSettings = {
  model: string
  /** Where assessments run: `fake` when no OpenRouter key is set. */
  runs_on: 'openrouter' | 'fake'
  prompt_version: string
  usage_url: string
  /** Where the model choice is kept: `memory` (no database) lasts until the server restarts. */
  kept_in: 'database' | 'memory'
}

export type ModelChange = { ok: true; settings: DeveloperSettings } | { ok: false; message: string }

const BAD_FORM = 'A model id is written author/model, such as anthropic/claude-haiku-5.5.'
const REFUSED = 'The model could not be changed. Please try again later.'
const SESSION_ENDED = 'Your sign-in has ended. Please sign in again.'

/**
 * The developer settings, or null when this user has no developer page: a compliance lead (403), signed out, or the
 * API unreachable. The answer itself is the only sign of the role the browser gets.
 */
export async function developerSettings(): Promise<DeveloperSettings | null> {
  try {
    const response = await fetch('/api/developer/settings', { headers: await authHeaders() })
    return response.ok ? ((await response.json()) as DeveloperSettings) : null
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
  if (response.status === 401) {
    signedOut()
    return { ok: false, message: SESSION_ENDED }
  }
  const detail = await response.json().then((body: { detail?: unknown }) => body.detail, () => undefined)
  if (typeof detail === 'string') return { ok: false, message: detail }
  return { ok: false, message: response.status === 422 ? BAD_FORM : REFUSED } // 422 with a list: pydantic's shape check
}
