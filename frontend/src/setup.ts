import { type Column, type EvidenceKind, plural, type ProtectionLevel } from './assessment.ts'
import { fetchProfiles, type Profiles, type ProfileSummary, regionName } from './profiles.ts'
import { authHeaders, refusedBecause, sessionEnded } from './session.ts'

/** Company setup's data (#403): the countries a company may be in, and what each protection level brings into scope. */

/** As vouch.company_profiles.MAX_COMPANY_LENGTH: the longest company name the API keeps. */
export const MAX_COMPANY_LENGTH = 100

/** The EU's 27, Iceland, Liechtenstein, Norway, Switzerland and the United Kingdom, as ISO 3166 codes. */
const COUNTRY_CODES = [
  'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT',
  'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE', 'IS', 'LI', 'NO', 'CH', 'GB',
]

/** The countries step 1 offers, each named as a profile stores it (regionName, so profileTag finds the code
 * again), in alphabetical order. */
export const COUNTRIES: { code: string; name: string }[] = COUNTRY_CODES
  .map((code) => ({ code, name: regionName(code) }))
  .sort((a, b) => a.name.localeCompare(b.name, 'en'))

/** The languages a company's answers may be in (step 2), by code, each named in English: the interface is English. */
export const LANGUAGES = [{ code: 'en', name: 'English' }, { code: 'de', name: 'German' }] as const

/** A company's answer language, as CompanySetup.answer_language takes it. */
export type AnswerLanguage = (typeof LANGUAGES)[number]['code']

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
  /** ISO 639-1: the language it is written in, from its front matter (#424); null when it names none. */
  language: string | null
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

/** A company to make, as POST /api/developer/profiles takes it (vouch.company_profiles.CompanySetup, #385). */
export type CompanySetup = {
  company: string
  country: string
  answer_language: AnswerLanguage
  protection_level: ProtectionLevel
  not_applicable: NotApplicable[]
  evidence: ChosenEvidence
}

/** How creating a company went (#409): created, with the profile as GET /api/profiles lists it; refused by the
 * profile's checks (a 422), with the problem in words; the database unavailable (a 503); the session ended (a 401:
 * signed out already); failed with nothing made (any other 4xx); or unknown: the answer was lost (unreachable, another
 * 5xx, or a 201 that won't parse) and the list read again doesn't show the company, so it may have been made or not. */
export type CompanyCreated =
  | { status: 'created'; profile: ProfileSummary }
  | { status: 'refused'; problem: string }
  | { status: 'unavailable' }
  | { status: 'signed-out' }
  | { status: 'failed' }
  | { status: 'unknown' }

/** Creates the company `setup` describes (developers only), its name and each not-applicable reason trimmed: steps 1
 * and 5 keep them as typed (#407's review), step 7 shows them trimmed. The profile list is read before the create, so
 * that when the create's answer is lost, the list read again shows whether the company was made after all. */
export async function createCompany(setup: CompanySetup): Promise<CompanyCreated> {
  const body: CompanySetup = {
    ...setup,
    company: setup.company.trim(),
    not_applicable: setup.not_applicable.map(({ control, reason }) => ({ control, reason: reason.trim() })),
  }
  const before = await fetchProfiles()
  const sent = await post(body)
  if (sent !== 'lost') return sent
  const made = newlyListed(before, await fetchProfiles(), body.company)
  return made ? { status: 'created', profile: made } : { status: 'unknown' }
}

/** POSTs a company; 'lost' when the server may have made it but its answer didn't arrive whole. */
async function post(body: CompanySetup): Promise<CompanyCreated | 'lost'> {
  let response: Response
  try {
    response = await fetch('/api/developer/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify(body),
    })
  } catch {
    return 'lost'
  }
  if (response.ok) {
    const profile = (await response.json().catch(() => null)) as ProfileSummary | null
    return profile?.id ? { status: 'created', profile } : 'lost'
  }
  if (sessionEnded(response)) return { status: 'signed-out' } // a 401 signs out, as every API call's does
  if (response.status === 422) return { status: 'refused', problem: await refusedBecause(response) }
  if (response.status === 503) return { status: 'unavailable' }
  return response.status >= 500 ? 'lost' : { status: 'failed' }
}

/** The profile named `company` that `after` lists and `before` didn't: the company a lost answer made. None when
 * either list is missing (no way to tell a new one from one already there). */
export function newlyListed(before: Profiles | null, after: Profiles | null, company: string): ProfileSummary | null {
  if (!before || !after) return null
  const known = new Set(before.profiles.map((p) => p.id))
  return after.profiles.find((p) => p.company === company && !known.has(p.id)) ?? null
}
