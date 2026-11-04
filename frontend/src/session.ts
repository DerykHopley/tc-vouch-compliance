/** The signed-in user's session for API calls. Clerk mode sets both hooks (ClerkApp.tsx); local mode neither. */

type TokenSource = () => Promise<string | null>

let source: TokenSource | null = null
let onSignedOut: (() => void) | null = null

/** Where a fresh token comes from (Clerk's `getToken`: tokens live 60 s, so one per call), or null when signed out. */
export function setTokenSource(next: TokenSource | null) {
  source = next
}

/** The Authorization header for an API call: empty in local mode or signed out. */
export async function authHeaders(): Promise<Record<string, string>> {
  const token = source ? await source() : null
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** What to do when the API answers 401 (no valid session): Clerk mode signs out, back to the sign-in. */
export function setOnSignedOut(next: (() => void) | null) {
  onSignedOut = next
}

/** The API said this session is no longer valid. */
export function signedOut() {
  onSignedOut?.()
}
