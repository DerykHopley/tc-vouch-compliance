import { type Column, type EvidenceKind, plural, type ProtectionLevel } from './assessment.ts'
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
  return (await read<{ levels: ProtectionScope[] }>('/api/developer/protection-levels'))?.levels ?? null
}

/** A developer route's JSON answer; null when it is refused or unreachable. */
async function read<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(path, { headers: await authHeaders() })
    if (response.ok) return (await response.json()) as T
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

/** One control of the base framework, as GET /api/developer/controls sends it (#407). */
export type FrameworkControl = { id: string; question: string }

/** Every control of the base framework's English edition, in its order; null when it could not be read. */
export async function frameworkControls(): Promise<FrameworkControl[] | null> {
  return (await read<{ controls: FrameworkControl[] }>('/api/developer/controls'))?.controls ?? null
}

/** A control marked not applicable on step 5 (#407), as CompanySetup.not_applicable takes it: whole controls only, so
 * no requirement. The reason is kept as typed; it is trimmed where the create request is built (#409). */
export type NotApplicable = { control: string; reason: string }

/** Step 5's checks (#407): the marked controls whose reason is empty or only spaces, in the framework's order, and
 * whether every control is marked (at least one must apply). Nothing is found while `controls` is empty (unread). */
export function notApplicableProblems(marked: readonly NotApplicable[], controls: readonly FrameworkControl[]) {
  const ids = new Set(marked.map((m) => m.control))
  return {
    blank: controls.filter((c) => marked.some((m) => m.control === c.id && m.reason.trim() === '')).map((c) => c.id),
    allMarked: controls.length > 0 && controls.every((c) => ids.has(c.id)),
  }
}

/** The sample library's folders a company's evidence chooses from, as CompanySetup.evidence takes them. */
export type LibrarySource = 'policies' | 'records'

/** One document of the sample library with what Vouch has read from it, as GET /api/developer/sample-library sends it
 * (vouch.documents.DocumentSummary, #408). */
export type LibraryDocument = {
  id: string
  source: LibrarySource
  title: string
  kind: EvidenceKind
  version: string | null
  approval_date: string | null
  review_cycle: string | null
  /** Why it is stale on the server's today, or null when it is current. */
  stale_because: string | null
  stale: boolean
}

/** Every document of the sample library, policies then records, each by title; null when it could not be read. */
export async function sampleLibrary(): Promise<LibraryDocument[] | null> {
  return (await read<{ documents: LibraryDocument[] }>('/api/developer/sample-library'))?.documents ?? null
}

/** The documents chosen on step 6, by folder: the shape CompanySetup.evidence takes. */
export type ChosenEvidence = Record<LibrarySource, string[]>

/** Step 6's live line: how many chosen documents Vouch can cite, by kind, and how many of them are stale. */
export function readyToCite(chosen: readonly LibraryDocument[]): string {
  if (chosen.length === 0) return 'No documents chosen yet.'
  const countOf = (kind: EvidenceKind) => chosen.filter((d) => d.kind === kind).length
  const stale = chosen.filter((d) => d.stale).length
  return `Ready to cite: ${plural(chosen.length, 'document')} (${countOf('documented')} documented, `
    + `${countOf('implemented')} implemented${stale > 0 ? `, ${stale} stale` : ''}).`
}
