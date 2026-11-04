/** The signed-in user's session token for API calls. Clerk mode sets the source (ClerkApp.tsx); local mode never does. */

type TokenSource = () => Promise<string | null>

let source: TokenSource | null = null

/** Where a fresh token comes from (Clerk's `getToken`: tokens live 60 s, so one per call), or null when signed out. */
export function setSessionToken(next: TokenSource | null) {
  source = next
}

/** The Authorization header for an API call: empty in local mode or signed out. */
export async function authHeaders(): Promise<Record<string, string>> {
  const token = source ? await source() : null
  return token ? { Authorization: `Bearer ${token}` } : {}
}
