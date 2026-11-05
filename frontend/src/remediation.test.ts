import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Assessment } from './assessment.ts'
import { DECISION_FAILED, decideItem, type RemediationItem, withItem } from './remediation.ts'
import { SESSION_ENDED } from './session.ts'

afterEach(() => vi.unstubAllGlobals())

const DRAFT: RemediationItem = { requirement_id: 'M1', missing: 'An exposure assessment.', owner_role: 'CISO',
  due_date: '2027-02-01', status: 'draft' }

describe('decideItem', () => {
  it("POSTs the decision to the item's route and gives back the item as kept", async () => {
    const kept = { ...DRAFT, owner_role: 'IT security officer', status: 'approved' }
    const fetch = vi.fn(() => Promise.resolve(Response.json({ item: kept })))
    vi.stubGlobal('fetch', fetch)
    const decision = { decision: 'approve' as const, owner_role: 'IT security officer', due_date: '2027-02-01' }
    expect(await decideItem('t 1', 'M1', decision)).toEqual({ ok: true, item: kept })
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/assessments/t%201/remediation/M1')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(decision)
  })

  it("gives the server's reason for a 422 (a past due date) or a 409 (decided already)", async () => {
    const hint = 'The due date 2026-11-01 has passed. Choose today or a later date.'
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ detail: hint }, { status: 422 }))))
    expect(await decideItem('t1', 'M1', { decision: 'approve' })).toEqual({ ok: false, message: hint, invalid: true })
    const decided = 'This remediation item was discarded already.'
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ detail: decided }, { status: 409 }))))
    expect(await decideItem('t1', 'M1', { decision: 'approve' })).toEqual({ ok: false, message: decided, invalid: false })
  })

  it('says the decision was not recorded for any other answer, or when the service is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('', { status: 503 }))))
    expect(await decideItem('t1', 'M1', { decision: 'discard' })).toEqual({ ok: false, message: DECISION_FAILED, invalid: false })
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    expect(await decideItem('t1', 'M1', { decision: 'discard' })).toEqual({ ok: false, message: DECISION_FAILED, invalid: false })
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('', { status: 401 }))))
    expect(await decideItem('t1', 'M1', { decision: 'discard' })).toEqual({ ok: false, message: SESSION_ENDED, invalid: false })
  })
})

describe('withItem', () => {
  it("puts the kept item in place of its draft, leaving the assessment's others", () => {
    const other = { ...DRAFT, requirement_id: 'M2' }
    const assessment: Assessment = { control_id: '4.1.2', verdict: 'not-evidenced', answer: 'A.',
      remediation: [DRAFT, other] }
    const kept = { ...DRAFT, status: 'discarded' as const }
    expect(withItem(assessment, kept).remediation).toEqual([kept, other])
    expect(assessment.remediation).toEqual([DRAFT, other])
  })
})
