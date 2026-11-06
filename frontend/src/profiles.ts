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

/** The ISO 3166 code of a country named in English ("Germany": "DE"), found through Intl; null when none matches. */
const regions = new Intl.DisplayNames(['en'], { type: 'region' })
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
          if (current && regions.of(code) === country) found = code
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
