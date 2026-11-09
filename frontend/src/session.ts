/** The signed-in user's session for API calls. Clerk mode sets both hooks (ClerkApp.tsx); local mode neither. */

import { readStored, store } from './stored.ts'

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

// The profile last chosen in the switcher, kept in this browser so a reload or a pasted link opens it (#201).
// Storage refused: nothing is remembered, and the next visit opens the default.
const REMEMBERED = 'vouch.profile'

/** The profile last chosen in this browser, or null. */
export function rememberedProfile(): string | null {
  return readStored(REMEMBERED)
}

/** Keeps `id` as the profile to open next time; null forgets it. */
export function rememberProfile(id: string | null) {
  store(REMEMBERED, id || null)
}

/** The headers every API call carries: Authorization (none in local mode or signed out), and the chosen profile. */
export async function authHeaders(): Promise<Record<string, string>> {
  const chosen = profile // read before the await: a switch meanwhile mustn't change where this call goes
  const token = source ? await source() : null
  return { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(chosen ? { 'X-Vouch-Profile': chosen } : {}) }
}

/** What to do when the API answers 401 (no valid session): Clerk mode signs out, back to the sign-in. */
export function setOnSignedOut(next: (() => void) | null) {
  onSignedOut = next
}

let signOuts = 0

/** The API said this session is no longer valid. */
export function signedOut() {
  signOuts += 1
  onSignedOut?.()
}

/** How many times this page has signed out: a call that started before a sign-out compares it after, so its late
 * answer changes nothing (the sign-in replaces the app only on its next render). */
export const signOutCount = () => signOuts

/** Shown where an API call found the sign-in ended. */
export const SESSION_ENDED = 'Your sign-in has ended. Please sign in again.'

/** True, after signing out, when `response` is a 401: the caller then shows SESSION_ENDED. */
export function sessionEnded(response: Response): boolean {
  if (response.status !== 401) return false
  signedOut()
  return true
}

/** A refusal's detail (a 422's or a 409's) in words: FastAPI's own string, or each validation error's message. */
export async function refusedBecause(response: Response): Promise<string> {
  const { detail } = (await response.json().catch(() => ({}))) as { detail?: unknown }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d: { msg?: unknown }) => String(d.msg ?? '')).join('; ')
  return ''
}
