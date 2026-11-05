import { authHeaders, sessionEnded } from './session.ts'

/**
 * Whether the signed-in user may use Vouch (#152): the API's answer to `GET /api/access`. With the course's Discord
 * server set, someone outside it is `not-a-member`; a Discord token the API can't use, `sign-in-again`. Anything else
 * that isn't a yes (an outage, an answer it doesn't know, no answer) is `unavailable`: never the app. A 401 signs out.
 */
export type Access = 'allowed' | 'not-a-member' | 'sign-in-again' | 'unavailable' | 'signed-out'

export async function checkAccess(): Promise<Access> {
  try {
    const response = await fetch('/api/access', { headers: await authHeaders() })
    if (sessionEnded(response)) return 'signed-out'
    if (response.ok) return 'allowed'
    if (response.status === 403) {
      const { reason } = (await response.json()) as { reason?: string }
      if (reason === 'not-a-member' || reason === 'sign-in-again') return reason
    }
  } catch {
    // unreachable, or not JSON: as below
  }
  return 'unavailable'
}
