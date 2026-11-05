import { type Verdict, VERDICT_WORDS } from './assessment.ts'

/**
 * A control's or a requirement's verdict as a chip (`docs/design.md`, Components: Verdict chip): Assessing while a
 * run is in progress, the verdict in words, or hollow Not assessed. A dot and the word: colour is never the only cue.
 */
export default function VerdictChip({ verdict, assessing = false }: { verdict: Verdict | null; assessing?: boolean }) {
  if (assessing) return <span className="chip v-assessing">Assessing</span>
  if (verdict) return <span className={`chip v-${verdict}`}>{VERDICT_WORDS[verdict]}</span>
  return <span className="chip v-none">Not assessed</span>
}
