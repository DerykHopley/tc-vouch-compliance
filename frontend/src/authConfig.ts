/** How this Vouch signs users in, as `GET /api/health` says: Clerk on the live app, nobody locally. */

export type AuthConfig = { mode: 'local' } | { mode: 'clerk'; publishableKey: string } | { mode: 'unreachable' }

/**
 * The API's auth mode. Unreachable when health can't be read, or names no mode the page can open in: the page then
 * says so, and never guesses local mode.
 */
export async function authConfig(): Promise<AuthConfig> {
  try {
    const response = await fetch('/api/health')
    if (!response.ok) return { mode: 'unreachable' }
    const { auth } = (await response.json()) as { auth?: { mode?: string; publishable_key?: string | null } }
    if (auth?.mode === 'local') return { mode: 'local' }
    if (auth?.mode === 'clerk' && auth.publishable_key) return { mode: 'clerk', publishableKey: auth.publishable_key }
  } catch {
    // unreachable, or not JSON: as below
  }
  return { mode: 'unreachable' }
}
