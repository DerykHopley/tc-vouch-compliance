import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RequirementVerdict, Verdict } from './assessment.ts'
import { ALREADY_REVIEWED, REVIEW_FAILED, reviewedBy, rollUp, submitReview, withOverrides } from './review.ts'

const req = (id: string, column: RequirementVerdict['column'], verdict: Verdict): RequirementVerdict =>
  ({ id, column, text: `${id}.`, verdict, reason: `Why ${id}.`, citations: [] })

afterEach(() => vi.unstubAllGlobals())

describe('rollUp', () => {
  it("is the control's verdict over must, high, very high and overlay requirements that apply (graph.roll_up)", () => {
    // 4.1.2 as the demo script judges it: M1 partly, M2 evidenced; should requirements don't count.
    const demo = [req('M1', 'must', 'partly-evidenced'), req('M2', 'must', 'evidenced'),
      req('S1', 'should', 'not-evidenced')]
    expect(rollUp(demo)).toBe('partly-evidenced')
    expect(rollUp([req('M1', 'must', 'evidenced'), req('M2', 'must', 'evidenced'), req('S1', 'should', 'not-evidenced')]))
      .toBe('evidenced')
    expect(rollUp([req('M1', 'must', 'not-evidenced'), req('H1', 'high', 'partly-evidenced')])).toBe('not-evidenced')
    expect(rollUp([req('V1', 'very high', 'evidenced'), req('O1', 'overlay', 'not-evidenced')]))
      .toBe('partly-evidenced')
    expect(rollUp([req('M1', 'must', 'not-applicable'), req('S1', 'should', 'evidenced')])).toBe('not-applicable')
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

  it('says "already reviewed" for a 409: another tab signed it off first', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ detail: 'x' }, { status: 409 }))))
    expect(await submitReview('t1', decision)).toEqual({ ok: false, conflict: true, message: ALREADY_REVIEWED })
  })

  it('says the decision was not recorded for any other refusal, or when the service is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('', { status: 503 }))))
    expect(await submitReview('t1', decision)).toEqual({ ok: false, conflict: false, message: REVIEW_FAILED })
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    expect(await submitReview('t1', decision)).toEqual({ ok: false, conflict: false, message: REVIEW_FAILED })
  })
})

describe('reviewedBy', () => {
  it("finds who decided on the thread, and when, in the audit trail; the reviewer's name when the entry has one", async () => {
    const entries = [
      { thread_id: 't2', action: 'accept', user_id: 'local-user', at: '2026-11-05T09:00:00+00:00' },
      { thread_id: 't1', action: 'reject', user_id: 'user_1', reviewer_name: 'Jana Weber', at: '2026-11-04T08:30:00+00:00' },
      { thread_id: 't1', action: 'override', user_id: 'user_1', at: '2026-11-04T08:30:00+00:00' },
    ]
    const fetch = vi.fn(() => Promise.resolve(Response.json({ entries, next: null })))
    vi.stubGlobal('fetch', fetch)
    expect(await reviewedBy('t1')).toEqual({ who: 'Jana Weber', at: '2026-11-04T08:30:00+00:00' })
    expect(await reviewedBy('t2')).toEqual({ who: 'local-user', at: '2026-11-05T09:00:00+00:00' })
    expect(await reviewedBy('t3')).toBeNull()
    expect((fetch.mock.calls[0] as unknown as [string])[0]).toBe('/api/audit-trail?limit=500')
  })

  it('is null when the trail cannot be read', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('', { status: 503 }))))
    expect(await reviewedBy('t1')).toBeNull()
  })
})
