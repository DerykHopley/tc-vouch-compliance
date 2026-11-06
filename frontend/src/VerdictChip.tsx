import { type Verdict, verdictWord } from './assessment.ts'

/**
 * A control's or a requirement's verdict as a chip (`docs/design.md`, Components: Verdict chip): Assessing while a
 * run is in progress, the verdict in words, or hollow Not assessed. A dot and the word: colour is never the only cue.
 * `language`: an assessment's answer language (ISO 639-1), for its own verdict (#48); the interface's English without.
 */
export default function VerdictChip({ verdict, assessing = false, language }:
  { verdict: Verdict | null; assessing?: boolean; language?: string }) {
  if (assessing) return <span className="chip v-assessing">Assessing</span>
  if (verdict) return <span className={`chip v-${verdict}`} lang={language}>{verdictWord(verdict, language)}</span>
  return <span className="chip v-none">Not assessed</span>
}
