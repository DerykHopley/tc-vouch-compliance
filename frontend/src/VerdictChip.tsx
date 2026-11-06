import { type Verdict, verdictLang, verdictWord } from './assessment.ts'

/**
 * A control's or a requirement's verdict as a chip (`docs/design.md`, Components: Verdict chip): Assessing while a
 * run is in progress, the verdict in words, or hollow Not assessed. A dot and the word: colour is never the only cue.
 * `language`: the profile's answer language (ISO 639-1), for an assessment's own verdict (#48, #200), marked `lang`
 * when it has its own words; the interface's English without. `label`: the verdict's word as a page was served it
 * (the readiness report's `words.verdicts`, #56), shown instead and in the page's own `lang`.
 */
export default function VerdictChip({ verdict, assessing = false, language, label }:
  { verdict: Verdict | null; assessing?: boolean; language?: string; label?: string }) {
  if (assessing) return <span className="chip v-assessing">Assessing</span>
  if (verdict && label !== undefined) return <span className={`chip v-${verdict}`}>{label}</span>
  if (verdict) return <span className={`chip v-${verdict}`} lang={verdictLang(language)}>{verdictWord(verdict, language)}</span>
  return <span className="chip v-none">Not assessed</span>
}
