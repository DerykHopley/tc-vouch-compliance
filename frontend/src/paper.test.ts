import { describe, expect, it } from 'vitest'
import type { Citation, RequirementVerdict } from './assessment.ts'
import { areas, evidenceOf, languageName } from './paper.ts'

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
    expect(evidence).toEqual({ kinds: ['implemented', 'documented'], stale: true })
  })

  it('has no kind and is not stale without quotes', () => {
    expect(evidenceOf(requirement([]))).toEqual({ kinds: [], stale: false })
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
