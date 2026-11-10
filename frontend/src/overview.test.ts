import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Assessment } from './assessment.ts'
import { controlPath, isoLabel, type Overview, useOverview } from './overview.ts'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('isoLabel', () => {
  it("shortens ISA's ISO 27001 reference to its Annex A controls", () => {
    expect(isoLabel('ISO 27001:2022: A.5.15, A.8.5')).toBe('ISO A.5.15, A.8.5')
    expect(isoLabel('ISO 27001:2022 A.5.1')).toBe('ISO A.5.1')
    expect(isoLabel('A.5.1')).toBe('A.5.1')
  })
})

describe('controlPath', () => {
  it('encodes the control id, as the address is read back decoded', () => {
    expect(controlPath('4.1.2')).toBe('/controls/4.1.2')
    expect(controlPath('a/b ?')).toBe('/controls/a%2Fb%20%3F')
  })
})

const OVERVIEW = { company: 'Nordlicht Komponenten GmbH', goal: 'audit-readiness' } as Overview

/** `GET /api/overview` answers each of `answers` in turn. */
function serve(...answers: (() => Promise<Response>)[]) {
  const fetch = vi.fn(() => answers.shift()!())
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const ok = (overview: Overview) => () => Promise.resolve(new Response(JSON.stringify(overview)))
const down = () => Promise.resolve(new Response('{}', { status: 503 }))

describe('the overview after a goal change (ticket 369)', () => {
  it('reads it again and shows what it read', async () => {
    serve(ok(OVERVIEW), ok({ ...OVERVIEW, goal: 'internal-improvement', company: 'Read again' }))
    const { result } = renderHook(() => useOverview())
    await act(() => result.current.load())

    let read: boolean | undefined
    await act(async () => { read = await result.current.goalChanged('internal-improvement') })

    expect(read).toBe(true)
    expect(result.current.state).toEqual({ status: 'ready',
      overview: { ...OVERVIEW, goal: 'internal-improvement', company: 'Read again' } })
  })

  it("when it can't be read again, keeps the overview shown with the new goal, and says so", async () => {
    serve(ok(OVERVIEW), down)
    const { result } = renderHook(() => useOverview())
    await act(() => result.current.load())

    let read: boolean | undefined
    await act(async () => { read = await result.current.goalChanged('internal-improvement') })

    expect(read).toBe(false)
    expect(result.current.state).toEqual({ status: 'ready', overview: { ...OVERVIEW, goal: 'internal-improvement' } })
  })
})

describe("a control's card after its assessment changes in this page (ticket 437)", () => {
  const CARD = { id: '4.1.2', question: 'Q?', iso_27001: 'A.5.1', status: 'waiting-for-review' as const,
    verdict: 'partly-evidenced' as const, in_review: false }
  const WAITING = { control_id: '4.1.2', verdict: 'partly-evidenced', status: 'waiting-for-review', remediation: [] }

  it.each([
    ['at Confirm findings', { review_step: 'confirm-findings' }, false],
    ['at Plan fixes', { review_step: 'plan-fixes', remediation: [{ status: 'draft' }] }, true],
    ['at Accept', { review_step: 'accept' }, true],
    ['accepted', { status: 'accepted', review_step: null }, false],
  ] as const)('%s, says whether its review is in progress', async (_, change, inReview) => {
    serve(ok({ ...OVERVIEW, controls: [CARD] }))
    const { result } = renderHook(() => useOverview())
    await act(() => result.current.load())

    act(() => result.current.noteAssessed({ ...WAITING, ...change } as unknown as Assessment))

    const state = result.current.state
    expect(state.status === 'ready' && state.overview.controls[0].in_review).toBe(inReview)
  })
})
