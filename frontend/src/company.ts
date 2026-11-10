import type { ProtectionLevel } from './assessment.ts'
import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'
import type { ProtectionScope } from './setup.ts'

/** The Company page's facts (#505), as `GET /api/company` sends them (vouch.company_page.company_facts). */
export type CompanyFacts = {
  company: string
  country: string
  /** ISO 639-1: en, de. */
  answer_language: string
  base_framework: string
  protection_level: ProtectionLevel
  /** Each country overlay, named in the answer language, with the number of requirements it adds. */
  overlays: { name: string; requirements: number }[]
  /** What the protection level brings in, as `GET /api/developer/protection-levels` sends that level's entry. */
  scope: ProtectionScope
  /** In the profile's order; `requirement_id` null for a whole control. The reason is in the answer language. */
  not_applicable: { control_id: string; requirement_id: string | null; reason: string }[]
  /** Where the company is defined: a profile file, or company setup. */
  defined_in: 'files' | 'setup'
}

/** The page's own sentence when the company can't be read. */
export const COMPANY_FAILED = 'The company could not be loaded.'

/** The current company's facts, or the sentence to show with Try again (a 401 signs out). */
export async function fetchCompany(): Promise<{ ok: true; company: CompanyFacts } | { ok: false; message: string }> {
  try {
    const response = await fetch('/api/company', { headers: await authHeaders() })
    if (response.ok) return { ok: true, company: (await response.json()) as CompanyFacts }
    return { ok: false, message: sessionEnded(response) ? SESSION_ENDED : COMPANY_FAILED }
  } catch {
    return { ok: false, message: COMPANY_FAILED }
  }
}
