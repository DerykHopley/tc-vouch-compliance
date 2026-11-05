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

// The interface is English (docs/design.md, Language), so the answer language is named in English.
const languages = new Intl.DisplayNames(['en'], { type: 'language' })

/** How the top bar names a profile: company, country and answer language. */
export const profileLabel = (p: ProfileSummary) =>
  `${p.company} · ${p.country} · ${languages.of(p.answer_language) ?? p.answer_language}`
