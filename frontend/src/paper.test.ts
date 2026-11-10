import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { Citation, RequirementVerdict } from './assessment.ts'
import { areas, evidenceOf, indexLabel, languageName } from './paper.ts'

const cite = (over: Partial<Citation> = {}): Citation => ({
  doc_id: 'authentication-standard', version: '3.1', section: 'Scope', quote: 'Passwords have 12 characters.',
  kind: 'documented', stale: false, stale_because: null, ...over,
})

const requirement = (citations: Citation[]): RequirementVerdict => ({
  id: 'M1', column: 'must', text: 'Text.', verdict: 'evidenced', reason: 'Reason.', citations,
})

describe('evidenceOf', () => {
  it('names each kind of evidence once, implemented first, and is stale when any quote is', () => {
    const evidence = evidenceOf(requirement([cite(), cite({ kind: 'implemented', stale: true }), cite()]))
    expect(evidence).toEqual({ kinds: ['implemented', 'documented'], stale: true, statement: false })
  })

  it('has no kind and is not stale without quotes', () => {
    expect(evidenceOf(requirement([]))).toEqual({ kinds: [], stale: false, statement: false })
  })

  it("gives a lead's statement no kind, only that a statement shows it, issue 498", () => {
    const statement = cite({ doc_id: 'Q1', section: "Lead's statement", lead_statement: true })
    expect(evidenceOf(requirement([statement]))).toEqual({ kinds: [], stale: false, statement: true })
    expect(evidenceOf(requirement([statement, cite({ kind: 'implemented' })])))
      .toEqual({ kinds: ['implemented'], stale: false, statement: true })
  })
})

describe('areas', () => {
  it("groups the controls by their ISA chapter, in the overview's order", () => {
    const ids = ['1.1.1', '1.6.1', '2.1.3', '4.1.2', '5.1.1', '7.1.1']
    expect(areas(ids.map((id) => ({ id }))).map(([name, cs]) => [name, cs.map((c) => c.id)])).toEqual([
      ['Policies and incidents', ['1.1.1', '1.6.1']],
      ['People', ['2.1.3']],
      ['Access', ['4.1.2']],
      ['IT security', ['5.1.1']],
      ['Chapter 7', ['7.1.1']],
    ])
  })
})

describe('languageName', () => {
  it("names the answer language in English, or gives its code when there's no name", () => {
    expect(languageName('en')).toBe('English')
    expect(languageName('de')).toBe('German')
    expect(languageName('not a code')).toBe('not a code')
  })
})

/**
 * Each control's question in the ISA edition a profile reads: nordlicht the English one, alpenwerk the German one
 * (its `answer_language: de`). Read from the working directory (npm test runs in frontend/), as proseCorpus does.
 */
const EDITION = join(process.cwd(), '..', 'src', 'vouch', 'data', 'vda-isa-2027.1')
const QUESTIONS: Record<string, string[]> = Object.fromEntries(
  [['nordlicht', 'controls.yaml'], ['alpenwerk', 'controls-de.yaml']].map(([profile, file]) => [
    profile,
    readFileSync(join(EDITION, file), 'utf8').split('\n')
      .map((line) => /^\s*question:\s*(.+)$/.exec(line)?.[1].trim())
      .filter((q): q is string => !!q),
  ]),
)

describe('indexLabel', () => {
  it.each(Object.keys(QUESTIONS))("tells each of %s's controls apart, without the shared opening (issue 466)", (profile) => {
    const questions = QUESTIONS[profile]
    expect(questions).toHaveLength(12)
    const labels = questions.map(indexLabel)
    expect(new Set(labels).size).toBe(labels.length)
    for (const label of labels) {
      expect(label).not.toMatch(/^(to what extent|inwieweit)\b/i)
      expect(label).toMatch(/^\p{Lu}/u) // reads as a question of its own
    }
  })

  it('drops the opening in either language and keeps the rest of the question', () => {
    expect(indexLabel('To what extent are information security policies available?'))
      .toBe('Are information security policies available?')
    expect(indexLabel('Inwieweit werden Zugriffsrechte vergeben und verwaltet?')).toBe('Werden Zugriffsrechte vergeben und verwaltet?')
  })

  it('leaves a question with another opening as it is', () => {
    expect(indexLabel('Is a backup tested?')).toBe('Is a backup tested?')
  })
})
