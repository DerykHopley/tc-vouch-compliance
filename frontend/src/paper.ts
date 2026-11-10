import type { Column, EvidenceKind, RequirementVerdict } from './assessment.ts'

/** The working paper's words and groupings (#41). */

/**
 * A requirement the profile brings in for a control, as the overview lists them for the paper before an assessment
 * (`vouch.dossier.PaperRequirement`): applicable ones first, then the not applicable ones.
 */
export type PaperRequirement = {
  id: string
  column: Column
  text: string
  /** The base framework, or the country overlay's name. */
  source: string
  /** The profile's reason; null when the requirement applies. */
  not_applicable: string | null
}

/** How each column reads on the paper (`docs/design.md`, Components: Requirement row). */
export const COLUMN_WORDS: Record<Column, string> = {
  must: 'Must',
  should: 'Should',
  high: 'High protection',
  'very high': 'Very high protection',
  overlay: 'Country overlay',
}

/**
 * The kinds of evidence a requirement's document quotes are (implemented first), whether any quote is stale, and
 * whether a lead's statement shows it (#498): a statement has no kind, as its quote shows none (#439).
 */
export function evidenceOf({ citations }: Pick<RequirementVerdict, 'citations'>):
  { kinds: EvidenceKind[]; stale: boolean; statement: boolean } {
  const documents = citations.filter((c) => !c.lead_statement)
  const kinds = (['implemented', 'documented'] as const).filter((k) => documents.some((c) => c.kind === k))
  return { kinds, stale: citations.some((c) => c.stale), statement: documents.length < citations.length }
}

/**
 * The control index's areas: the ISA chapter, the first part of a control's id, named as in the prototype (branch
 * `prototype/ui`, variant E); a chapter it doesn't name reads "Chapter N".
 */
const AREA_NAMES: Record<string, string> = {
  1: 'Policies and incidents',
  2: 'People',
  4: 'Access',
  5: 'IT security',
}

/** The controls grouped by area, each area and its controls in the order given. */
export function areas<C extends { id: string }>(controls: C[]): [string, C[]][] {
  const grouped = new Map<string, C[]>()
  for (const control of controls) {
    const chapter = control.id.split('.')[0]
    const name = AREA_NAMES[chapter] ?? `Chapter ${chapter}`
    grouped.set(name, [...(grouped.get(name) ?? []), control])
  }
  return [...grouped]
}

/** The answer language's English name ("German"), or its code when there is none. */
export function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(['en'], { type: 'language', fallback: 'code' }).of(code) ?? code
  } catch {
    return code // not a language code at all
  }
}
