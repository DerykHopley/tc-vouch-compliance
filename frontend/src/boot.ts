/** How this Vouch signs users in, as `GET /api/health` says: Clerk on the live app, nobody locally. */

export type AuthConfig = { mode: 'local' } | { mode: 'clerk'; publishableKey: string }

/**
 * The API's auth mode. When health can't be read the page opens as in local mode: nothing is let through by that,
 * as the API itself refuses every call that needs sign-in.
 */
export async function authConfig(): Promise<AuthConfig> {
  try {
    const response = await fetch('/api/health')
    if (!response.ok) return { mode: 'local' }
    const { auth } = (await response.json()) as { auth?: { mode?: string; publishable_key?: string | null } }
    if (auth?.mode === 'clerk' && auth.publishable_key) return { mode: 'clerk', publishableKey: auth.publishable_key }
  } catch {
    // unreachable: as local, below
  }
  return { mode: 'local' }
}
