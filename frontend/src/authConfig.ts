/** How this Vouch signs users in, as `GET /api/health` says: Clerk on the live app, nobody locally. */

import { setBuild } from './build.ts'

/** Who may sign in: members of the course's Discord server (#152), or whoever Clerk's own restrictions let in. */
export type Gate = 'discord-server' | 'clerk'

export type AuthConfig =
  | { mode: 'local' }
  | { mode: 'clerk'; publishableKey: string; gate: Gate }
  | { mode: 'unreachable' }

/**
 * The API's auth mode. Unreachable when health can't be read, or names no mode the page can open in: the page then
 * says so, and never guesses local mode.
 */
export async function authConfig(): Promise<AuthConfig> {
  try {
    const response = await fetch('/api/health')
    if (!response.ok) return { mode: 'unreachable' }
    const { auth, build } = (await response.json()) as {
      auth?: { mode?: string; publishable_key?: string | null; gate?: string }
      build?: unknown
    }
    setBuild(build) // the user menu's last line (issue 580)
    if (auth?.mode === 'local') return { mode: 'local' }
    if (auth?.mode === 'clerk' && auth.publishable_key) {
      const gate: Gate = auth.gate === 'discord-server' ? 'discord-server' : 'clerk'
      return { mode: 'clerk', publishableKey: auth.publishable_key, gate }
    }
  } catch {
    // unreachable, or not JSON: as below
  }
  return { mode: 'unreachable' }
}
