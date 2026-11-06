import { afterEach, describe, expect, it, vi } from 'vitest'
import { EXPORT_FAILED, exportReport, fetchReport, OLDER_FAILED, olderEntries, REPORT_FAILED } from './report.ts'
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

describe('exportReport', () => {
  const MARKDOWN = '# Readiness report\n\nDrafted by an AI assistant; nothing has been reviewed yet; not an audit result.\n'
  const markdown = (status = 200, name = 'readiness-report-nordlicht-2026-11-06.md') => () =>
    Promise.resolve(new Response(MARKDOWN, { status, headers: {
      'Content-Type': 'text/markdown; charset=utf-8', 'Content-Disposition': `attachment; filename="${name}"` } }))

  /** Each file saved: its name and its text, read from the Blob its object URL was made of. */
  function saves() {
    const blobs = new Map<string, Blob>()
    const saved: Promise<{ name: string; text: string }>[] = []
    let n = 0
    vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
      const url = `blob:saved-${++n}`
      blobs.set(url, blob as Blob)
      return url
    })
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      const blob = blobs.get(this.href)
      if (blob) saved.push(blob.text().then((text) => ({ name: this.download, text })))
    })
    return { all: () => Promise.all(saved) }
  }

  afterEach(() => vi.restoreAllMocks())

  it("saves the whole body as one file under the server's name, read with the session token", async () => {
    const fetch = vi.fn(markdown())
    vi.stubGlobal('fetch', fetch)
    setTokenSource(() => Promise.resolve('tok'))
    setProfile('nordlicht')
    const files = saves()

    expect(await exportReport()).toEqual({ ok: true })
    expect(fetch).toHaveBeenCalledWith('/api/report/markdown',
      { headers: { Authorization: 'Bearer tok', 'X-Vouch-Profile': 'nordlicht' } })
    expect(await files.all()).toEqual([{ name: 'readiness-report-nordlicht-2026-11-06.md', text: MARKDOWN }])
  })

  it.each([404, 429, 500, 503])('says the report could not be exported on a %i, and saves nothing', async (status) => {
    vi.stubGlobal('fetch', markdown(status))
    const files = saves()
    expect(await exportReport()).toEqual({ ok: false, message: EXPORT_FAILED })
    expect(await files.all()).toEqual([])
  })

  it('says so when the network fails, and saves nothing', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')))
    const files = saves()
    expect(await exportReport()).toEqual({ ok: false, message: EXPORT_FAILED })
    expect(await files.all()).toEqual([])
  })

  it('says so when the body breaks off before its end, and saves nothing', async () => {
    const broken = new ReadableStream<Uint8Array>({ start: (c) => {
      c.enqueue(new TextEncoder().encode('# Readiness'))
      c.error(new TypeError('network'))
    } })
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(broken, { status: 200 })))
    const files = saves()
    expect(await exportReport()).toEqual({ ok: false, message: EXPORT_FAILED })
    expect(await files.all()).toEqual([])
  })

  it('ends the session on a 401, and saves nothing', async () => {
    const ended = vi.fn()
    setOnSignedOut(ended)
    vi.stubGlobal('fetch', markdown(401))
    const files = saves()
    expect(await exportReport()).toEqual({ ok: false, message: SESSION_ENDED })
    expect(ended).toHaveBeenCalled()
    expect(await files.all()).toEqual([])
  })

  it("is the API's own wording", () => {
    expect(EXPORT_FAILED).toBe('The readiness report could not be exported just now. Please try again.')
  })
})
