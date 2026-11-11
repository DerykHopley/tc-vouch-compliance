import { authHeaders, sessionEnded } from './session.ts'

/** The compliance profiles from `GET /api/profiles` (#47): the companies listed for users. */

export type ProfileSummary = { id: string; company: string; country: string; answer_language: string }

/** `default`: the default profile (Nordlicht); null when it can't be loaded and was left out. `current`: the current
 * company (#480), the one the app opens under and a call naming none is under; null with none listed. */
export type Profiles = { profiles: ProfileSummary[]; default: string | null; current: string | null }

/** The profiles, or null when they can't be read: then every call names none, and is under the current company. */
export async function fetchProfiles(): Promise<Profiles | null> {
  try {
    const response = await fetch('/api/profiles', { headers: await authHeaders() })
    if (response.ok) return (await response.json()) as Profiles
    sessionEnded(response) // a 401 signs out, as every API call's does
    return null
  } catch {
    return null
  }
}

/** `held` with `profile` added at its end, unless it is listed already; with nothing held, that profile alone. A
 * company made in company setup (#409) is named even when the list can't be read again. */
export function withProfile(held: Profiles | null, profile: ProfileSummary): Profiles {
  const list = held ?? { profiles: [], default: null, current: null }
  return list.profiles.some((p) => p.id === profile.id) ? list : { ...list, profiles: [...list.profiles, profile] }
}

/** `held` less the profile `id`, and with no default or current when it was that one (so the first listed opens);
 * none when nothing is held. A company removed on the developer page (#418) leaves the list even when it can't be read
 * again. */
export function withoutProfile(held: Profiles | null, id: string): Profiles | null {
  return held && {
    profiles: held.profiles.filter((p) => p.id !== id),
    default: held.default === id ? null : held.default,
    current: held.current === id ? null : held.current,
  }
}

/** How a fresh start went: started, refused while an assessment runs (the 409), the session ended (a 401: signed out
 * already, so nothing more to say), or failed any other way. */
export type FreshStart = 'started' | 'running' | 'signed-out' | 'failed'

/** Starts the lead's progress under the chosen profile afresh (#285): from now on, only what they start is listed. Nothing
 * is deleted, and the audit trail keeps every entry. */
export async function startAfresh(): Promise<FreshStart> {
  try {
    const response = await fetch('/api/progress/fresh-start', { method: 'POST', headers: await authHeaders() })
    if (response.ok) return 'started'
    if (sessionEnded(response)) return 'signed-out' // a 401 signs out, as every API call's does
    return response.status === 409 ? 'running' : 'failed'
  } catch {
    return 'failed'
  }
}

const regions = new Intl.DisplayNames(['en'], { type: 'region' })

/** A country's English name from its ISO 3166 code ("DE": "Germany"), as a profile stores it; the code where Intl
 * names none. countryCode finds the code again. */
export const regionName = (code: string): string => regions.of(code) ?? code

/** The ISO 3166 code of a country named in English ("Germany": "DE"), found through Intl; null when none matches. */
const codes = new Map<string, string | null>()
function countryCode(country: string): string | null {
  if (!codes.has(country)) {
    let found: string | null = null
    for (let a = 65; a <= 90 && !found; a++) {
      for (let b = 65; b <= 90 && !found; b++) {
        const code = String.fromCharCode(a, b)
        try {
          // A code current today only: an old one (DD, East Germany) is canonicalised to its successor (DE).
          const current = Intl.getCanonicalLocales(`und-${code}`)[0].split('-')[1] === code
          if (current && regionName(code) === country) found = code
        } catch {
          // not a region code
        }
      }
    }
    codes.set(country, found)
  }
  return codes.get(country) ?? null
}

/** A company's tag in the top bar (#222, #495): its country's code and its answer language's, "DE · EN"; the
 * country's name where it has no code. A profile's summary fits it, and so does the overview's company. */
export const profileTag = (p: Pick<ProfileSummary, 'country' | 'answer_language'>) =>
  `${countryCode(p.country) ?? p.country} · ${p.answer_language.toUpperCase()}`
