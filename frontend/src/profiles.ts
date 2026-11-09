import { authHeaders, sessionEnded } from './session.ts'

/** The compliance profiles from `GET /api/profiles` (#47): those the user may switch between. */

export type ProfileSummary = { id: string; company: string; country: string; answer_language: string }

/** `default`: the profile a call naming none is under; null when it can't be loaded and was left out. */
export type Profiles = { profiles: ProfileSummary[]; default: string | null }

/** The profiles, or null when they can't be read: then there's no switcher, and every call is under the default. */
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
 * company made in company setup (#409) is named by the switcher even when the list can't be read again. */
export function withProfile(held: Profiles | null, profile: ProfileSummary): Profiles {
  const list = held ?? { profiles: [], default: null }
  return list.profiles.some((p) => p.id === profile.id) ? list : { ...list, profiles: [...list.profiles, profile] }
}

/** `held` less the profile `id`, and with no default when it was that one (so the first listed opens); none when
 * nothing is held. A company removed on the developer page (#418) leaves the switcher even when the list can't be read
 * again. */
export function withoutProfile(held: Profiles | null, id: string): Profiles | null {
  return held && {
    profiles: held.profiles.filter((p) => p.id !== id),
    default: held.default === id ? null : held.default,
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

/** The profile to open with: the remembered one while the list holds it, else the default (or the first listed), and
 * whether it fell back from a remembered one (#201). Without a list, none: calls name no profile. */
export function startingProfile(
  found: Profiles | null, remembered: string | null,
): { id: string | null; fellBack: boolean } {
  if (!found) return { id: null, fellBack: false }
  if (remembered && found.profiles.some((p) => p.id === remembered)) return { id: remembered, fellBack: false }
  return { id: found.default ?? found.profiles[0]?.id ?? null, fellBack: Boolean(remembered) }
}

// The interface is English (docs/design.md, Language), so the answer language is named in English.
const languages = new Intl.DisplayNames(['en'], { type: 'language' })

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

/** The profile button's tag (#222): its country's code and its answer language's, "DE · EN"; the country's name where
 * it has no code. */
export const profileTag = (p: ProfileSummary) =>
  `${countryCode(p.country) ?? p.country} · ${p.answer_language.toUpperCase()}`

/** How the top bar names a profile: company, country and answer language. */
export const profileLabel = (p: ProfileSummary) =>
  `${p.company} · ${p.country} · ${languages.of(p.answer_language) ?? p.answer_language}`
