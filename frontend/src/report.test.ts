import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchReport, OLDER_FAILED, olderEntries, REPORT_FAILED } from './report.ts'
import { SESSION_ENDED, setOnSignedOut, setProfile, setTokenSource } from './session.ts'

const answer = (status: number, body: unknown) => () => Promise.resolve(new Response(JSON.stringify(body), { status }))

afterEach(() => {
  vi.unstubAllGlobals()
  setTokenSource(null)
  setOnSignedOut(null)
  setProfile(null)
})

describe('fetchReport', () => {
  it('reads the report for the chosen profile, with the session token', async () => {
    const served = { head: { company: 'Nordlicht Komponenten GmbH' } }
    const fetch = vi.fn(answer(200, served))
    vi.stubGlobal('fetch', fetch)
    setTokenSource(() => Promise.resolve('tok'))
    setProfile('alpenwerk')

    expect(await fetchReport()).toEqual({ ok: true, report: served })
    expect(fetch).toHaveBeenCalledWith('/api/report',
      { headers: { Authorization: 'Bearer tok', 'X-Vouch-Profile': 'alpenwerk' } })
  })

  it.each([404, 429, 500, 503])('says the report could not be loaded on a %i', async (status) => {
    vi.stubGlobal('fetch', answer(status, { detail: 'whatever the API says' }))
    expect(await fetchReport()).toEqual({ ok: false, message: REPORT_FAILED })
  })

  it('says the report could not be loaded when the network fails', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')))
    expect(await fetchReport()).toEqual({ ok: false, message: REPORT_FAILED })
  })

  it('ends the session on a 401', async () => {
    const ended = vi.fn()
    setOnSignedOut(ended)
    vi.stubGlobal('fetch', answer(401, {}))
    expect(await fetchReport()).toEqual({ ok: false, message: SESSION_ENDED })
    expect(ended).toHaveBeenCalled()
  })

  it("is the API's own wording", () => {
    expect(REPORT_FAILED).toBe('The readiness report could not be loaded just now. Please try again.')
  })
})

describe('olderEntries', () => {
  it('reads the trail page before the given entry', async () => {
    const page = { entries: [{ action: 'accept' }], next: null }
    const fetch = vi.fn(answer(200, page))
    vi.stubGlobal('fetch', fetch)

    expect(await olderEntries(41)).toEqual({ ok: true, entries: page.entries, next: null })
    expect(fetch).toHaveBeenCalledWith('/api/audit-trail?before=41', { headers: {} })
  })

  it('says the older entries could not be loaded on a failure', async () => {
    vi.stubGlobal('fetch', answer(503, {}))
    expect(await olderEntries(41)).toEqual({ ok: false, message: OLDER_FAILED })
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')))
    expect(await olderEntries(41)).toEqual({ ok: false, message: OLDER_FAILED })
  })
})
