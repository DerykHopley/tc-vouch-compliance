import { type Column, plural, type ProtectionLevel } from './assessment.ts'
import { regionName } from './profiles.ts'
import { authHeaders, sessionEnded } from './session.ts'

/** Company setup's data (#403): the countries a company may be in, and what each protection level brings into scope. */

/** As vouch.company_profiles.MAX_COMPANY_LENGTH: the longest company name the API keeps. */
export const MAX_COMPANY_LENGTH = 100

/** The EU's 27, Iceland, Liechtenstein, Norway, Switzerland and the United Kingdom, as ISO 3166 codes. */
const COUNTRY_CODES = [
  'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT',
  'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE', 'IS', 'LI', 'NO', 'CH', 'GB',
]

/** The countries step 1 offers, each named as a profile stores it (regionName, so the profile switcher's tag finds the
 * code again), in alphabetical order. */
export const COUNTRIES: { code: string; name: string }[] = COUNTRY_CODES
  .map((code) => ({ code, name: regionName(code) }))
  .sort((a, b) => a.name.localeCompare(b.name, 'en'))

/** Each protection level, lowest first: its name in the step, and the kind of requirement it adds over the one below. */
export const PROTECTION_LEVELS: Record<ProtectionLevel, { name: string; kind: string }> = {
  normal: { name: 'Normal', kind: 'must and should' },
  high: { name: 'High', kind: 'high-protection' },
  'very high': { name: 'Very high', kind: 'very-high-protection' },
}

/** One level's scope, as vouch.profile.ProtectionScope sends it. */
export type ProtectionScope = {
  level: ProtectionLevel
  /** The base requirements in each column it brings in, in the framework's order. */
  requirements: Partial<Record<Column, number>>
  total: number
  /** The controls with a requirement in scope. */
  controls: number
  /** The requirements it adds over the level below (0 for normal), and the controls they are in. */
  added: number
  added_controls: string[]
}

/** What each protection level brings into scope, normal to very high; null when it could not be read. */
export async function protectionLevels(): Promise<ProtectionScope[] | null> {
  try {
    const response = await fetch('/api/developer/protection-levels', { headers: await authHeaders() })
    if (response.ok) return ((await response.json()) as { levels: ProtectionScope[] }).levels
    sessionEnded(response) // a 401 signs out, as every API call's does
    return null
  } catch {
    return null
  }
}

/** The words a level's option shows: what it brings in, and for a higher level what it adds and the total. */
export function scopeWords(scope: ProtectionScope): string {
  if (scope.added === 0)
    return `Must and should requirements: ${scope.total} in ${plural(scope.controls, 'control')}.`
  const where = scope.added_controls
  const added = plural(scope.added, `${PROTECTION_LEVELS[scope.level].kind} requirement`)
  return `Adds ${added}, in ${plural(where.length, 'control')} (${where.join(', ')}): ${scope.total} in all.`
}
