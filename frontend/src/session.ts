/** The signed-in user's session for API calls. Clerk mode sets both hooks (ClerkApp.tsx); local mode neither. */

type TokenSource = () => Promise<string | null>

let source: TokenSource | null = null
let onSignedOut: (() => void) | null = null

/** Where a fresh token comes from (Clerk's `getToken`: tokens live 60 s, so one per call), or null when signed out. */
export function setTokenSource(next: TokenSource | null) {
  source = next
}

let profile: string | null = null

/** The compliance profile every API call is for (#47): set by the top bar's switcher; null, the API's default. */
export function setProfile(next: string | null) {
  profile = next
}

/** The headers every API call carries: Authorization (none in local mode or signed out), and the chosen profile. */
export async function authHeaders(): Promise<Record<string, string>> {
  const token = source ? await source() : null
  return { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(profile ? { 'X-Vouch-Profile': profile } : {}) }
}

/** What to do when the API answers 401 (no valid session): Clerk mode signs out, back to the sign-in. */
export function setOnSignedOut(next: (() => void) | null) {
  onSignedOut = next
}

/** The API said this session is no longer valid. */
export function signedOut() {
  onSignedOut?.()
}

/** Shown where an API call found the sign-in ended. */
export const SESSION_ENDED = 'Your sign-in has ended. Please sign in again.'

/** True, after signing out, when `response` is a 401: the caller then shows SESSION_ENDED. */
export function sessionEnded(response: Response): boolean {
  if (response.status !== 401) return false
  signedOut()
  return true
}
