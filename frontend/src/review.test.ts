import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Column, RequirementVerdict, Verdict } from './assessment.ts'
import { ALREADY_REVIEWED, REVIEW_FAILED, REVIEW_REFUSED, rollUp, submitReview, withOverrides } from './review.ts'

const req = (id: string, column: Column, verdict: Verdict): RequirementVerdict =>
  ({ id, column, text: `${id}.`, verdict, reason: `Why ${id}.`, citations: [] })

afterEach(() => vi.unstubAllGlobals())

/** The cases graph.roll_up is checked against too (tests/test_roll_up_parity.py): the copy can't drift.
 *  Read from the working directory (npm test runs in frontend/): under jsdom import.meta.url isn't a file URL. */
const { cases } = JSON.parse(readFileSync(resolve(process.cwd(), '../tests/roll_up_cases.json'), 'utf8')) as
  { cases: { requirements: [Column, Verdict][]; verdict: Verdict }[] }

describe('rollUp', () => {
  it.each(cases)("gives $verdict for the shared case, as graph.roll_up does", ({ requirements, verdict }) => {
    expect(rollUp(requirements.map(([column, v], n) => req(`R${n}`, column, v)))).toBe(verdict)
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
  const decision = { decision: 'accept' as const, note: 'Checked.', overrides: [] }

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

  it('says "already reviewed" for a 409: another tab reviewed it first', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ detail: 'x' }, { status: 409 }))))
    expect(await submitReview('t1', decision)).toEqual({ ok: false, refusal: 'conflict', message: ALREADY_REVIEWED })
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
