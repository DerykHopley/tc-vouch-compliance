import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Column, RequirementVerdict, Verdict } from './assessment.ts'
import {
  confirmFindings, MOVED_ON, REVIEW_FAILED, REVIEW_REFUSED, reviewStep, rollUp, rollUpWords, submitReview,
  withOverrides,
} from './review.ts'

const req = (id: string, column: Column, verdict: Verdict): RequirementVerdict =>
  ({ id, column, text: `${id}.`, verdict, reason: `Why ${id}.`, citations: [] })

afterEach(() => vi.unstubAllGlobals())

/** The cases graph.roll_up is checked against too (tests/test_roll_up_parity.py): the copy can't drift.
 *  Read from the working directory (npm test runs in frontend/), not by new URL with a literal path: under jsdom Vite
 *  makes that an http:// address (#194, testFiles.test.ts). */
const { cases } = JSON.parse(readFileSync(resolve(process.cwd(), '../tests/roll_up_cases.json'), 'utf8')) as
  { cases: { requirements: [Column, Verdict][]; verdict: Verdict }[] }

describe('rollUp', () => {
  it.each(cases)("gives $verdict for the shared case, as graph.roll_up does", ({ requirements, verdict }) => {
    expect(rollUp(requirements.map(([column, v], n) => req(`R${n}`, column, v)))).toBe(verdict)
  })
})

/** The explanation's English cases, which graph.roll_up_words is checked against too (#212). */
const wordsCases = (JSON.parse(readFileSync(resolve(process.cwd(), '../tests/roll_up_words_cases.json'), 'utf8')) as
  { cases: { name: string; requirements: [Column, Verdict][]; words: string | null }[] }).cases

const of = (requirements: [Column, Verdict][]) => requirements.map(([column, v], n) => req(`R${n}`, column, v))

describe('rollUpWords', () => {
  it.each(wordsCases)('explains "$name" as graph.roll_up_words does', ({ requirements, words }) => {
    expect(rollUpWords(of(requirements), 'en')).toBe(words)
  })

  it('explains in German for a German profile', () => {
    expect(rollUpWords(of([['must', 'partly-evidenced'], ['must', 'partly-evidenced']]), 'de')).toBe(
      'Nicht nachgewiesen: keine Muss-Anforderung ist vollständig nachgewiesen (2 von 2 teilweise nachgewiesen).')
    expect(rollUpWords(of([['must', 'evidenced'], ['must', 'not-evidenced']]), 'de')).toBe(
      'Teilweise nachgewiesen: 1 von 2 Muss-Anforderungen ist vollständig nachgewiesen.')
    expect(rollUpWords(of([['must', 'evidenced'], ['should', 'not-evidenced']]), 'de')).toBe(
      'Nachgewiesen: die eine Muss-Anforderung ist vollständig nachgewiesen; Soll-Anforderungen zählen nur zur '
      + 'Soll-Quote.')
  })

  it('falls back to English for a language it has no words for', () => {
    expect(rollUpWords(of([['must', 'partly-evidenced']]), 'fr')).toBe(rollUpWords(of([['must', 'partly-evidenced']]), 'en'))
  })
})

describe('withOverrides', () => {
  it("puts the lead's verdicts and reasons in place of the agent's", () => {
    const changed = withOverrides([req('M1', 'must', 'partly-evidenced'), req('M2', 'must', 'evidenced')],
      [{ requirement_id: 'M1', verdict: 'evidenced', reason: 'The MFA record shows it.' }])
    expect(changed.map((r) => [r.id, r.verdict, r.reason])).toEqual(
      [['M1', 'evidenced', 'The MFA record shows it.'], ['M2', 'evidenced', 'Why M2.']])
  })
})

describe('submitReview', () => {
  const decision = { decision: 'accept' as const, note: 'Checked.' }

  it("POSTs the decision to the assessment's review route and gives back the reviewed assessment", async () => {
    const reviewed = { control_id: '4.1.2', verdict: 'evidenced', answer: 'A.', status: 'accepted', thread_id: 't 1' }
    const fetch = vi.fn(() => Promise.resolve(Response.json({ assessment: reviewed })))
    vi.stubGlobal('fetch', fetch)
    expect(await submitReview('t 1', decision)).toEqual({ ok: true, assessment: reviewed })
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/assessments/t%201/review')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(decision)
  })

  it('says the review moved on for a 409: another tab reviewed it first', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ detail: 'x' }, { status: 409 }))))
    expect(await submitReview('t1', decision)).toEqual({ ok: false, refusal: 'conflict', message: MOVED_ON })
  })

  it("says a 422 refused the decision, with the server's detail: retrying the same one can't help", async () => {
    const detail = "override names M9, which this assessment didn't judge"
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ detail }, { status: 422 }))))
    expect(await submitReview('t1', decision)).toEqual(
      { ok: false, refusal: 'refused', message: `${REVIEW_REFUSED} ${detail}` })
    const invalid = [{ loc: ['body', 'note'], msg: 'String should match pattern' }]
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ detail: invalid }, { status: 422 }))))
    expect(await submitReview('t1', decision)).toEqual(
      { ok: false, refusal: 'refused', message: `${REVIEW_REFUSED} String should match pattern` })
  })

  it('says the decision was not recorded for any other refusal, or when the service is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('', { status: 503 }))))
    expect(await submitReview('t1', decision)).toEqual({ ok: false, refusal: 'failed', message: REVIEW_FAILED })
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    expect(await submitReview('t1', decision)).toEqual({ ok: false, refusal: 'failed', message: REVIEW_FAILED })
  })
})

describe('confirmFindings (issue 372)', () => {
  it("POSTs the overrides to the assessment's findings route and gives back the assessment at its next step", async () => {
    const next = { control_id: '4.1.2', verdict: 'evidenced', answer: 'A.', review_step: 'plan-fixes' }
    const fetch = vi.fn(() => Promise.resolve(Response.json({ assessment: next })))
    vi.stubGlobal('fetch', fetch)
    const overrides = [{ requirement_id: 'M1', verdict: 'evidenced' as const, reason: 'Seen.' }]
    expect(await confirmFindings('t 1', overrides)).toEqual({ ok: true, assessment: next })
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/assessments/t%201/findings')
    expect(JSON.parse(init.body as string)).toEqual({ overrides })
  })

  it('says the review moved on for a 409: another tab confirmed them first', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ detail: 'x' }, { status: 409 }))))
    expect(await confirmFindings('t1', [])).toEqual({ ok: false, refusal: 'conflict', message: MOVED_ON })
  })
})

describe('reviewStep (issue 372)', () => {
  const item = (status: 'draft' | 'approved') =>
    ({ requirement_id: 'M1', missing: 'x', owner_role: 'CISO', due_date: '2027-01-01', status })

  it("reads the server's step, Plan fixes and Accept from the items as the page holds them", () => {
    const waiting = { status: 'waiting-for-review' as const }
    expect(reviewStep({ ...waiting, review_step: 'confirm-findings', remediation: [item('draft')] }))
      .toBe('confirm-findings')
    expect(reviewStep({ ...waiting, review_step: 'plan-fixes', remediation: [item('draft')] })).toBe('plan-fixes')
    expect(reviewStep({ ...waiting, review_step: 'plan-fixes', remediation: [item('approved')] })).toBe('accept')
    expect(reviewStep({ ...waiting, review_step: 'accept', remediation: [] })).toBe('accept')
    expect(reviewStep({ status: 'accepted', review_step: null, remediation: [item('draft')] })).toBeNull()
  })
})
