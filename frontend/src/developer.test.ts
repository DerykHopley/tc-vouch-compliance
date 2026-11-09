import { afterEach, describe, expect, it, vi } from 'vitest'
import { changeModel, developerSettings, type DeveloperSettings, removeCompany } from './developer.ts'
import { setOnSignedOut, setTokenSource } from './session.ts'

const NO_RUNS = { assessments: 0, input_tokens: 0, output_tokens: 0, reported_cost_usd: 0, estimated_cost_usd: 0, incomplete: 0 }

const SETTINGS: DeveloperSettings = {
  model: 'anthropic/claude-haiku-5.5',
  runs_on: 'openrouter',
  fake_reason: null,
  prompt_version: '1',
  usage_url: 'https://openrouter.ai/activity',
  kept_in: 'database',
  daily_limit: 30,
  usage: { openrouter: NO_RUNS, fake: NO_RUNS },
  broken_profiles: [],
  companies: [],
}

const answer = (status: number, body: unknown) => () => Promise.resolve(new Response(JSON.stringify(body), { status }))

afterEach(() => {
  vi.unstubAllGlobals()
  setTokenSource(null)
  setOnSignedOut(null)
})

describe('developerSettings', () => {
  it('reads the settings, with the session token', async () => {
    const fetch = vi.fn(answer(200, SETTINGS))
    vi.stubGlobal('fetch', fetch)
    setTokenSource(() => Promise.resolve('tok'))
    expect(await developerSettings()).toEqual(SETTINGS)
    expect(fetch).toHaveBeenCalledWith('/api/developer/settings', { headers: { Authorization: 'Bearer tok' } })
  })

  it.each([403, 401])('is null when the API answers %i: no developer page', async (status) => {
    vi.stubGlobal('fetch', answer(status, { detail: 'This is available to developers only.' }))
    expect(await developerSettings()).toBeNull()
  })

  it("is unavailable when the API's 503 says the database is: the page says so", async () => {
    vi.stubGlobal('fetch', answer(503, { detail: 'The database is unavailable just now. Please try again shortly.', unavailable: 'database' }))
    expect(await developerSettings()).toBe('unavailable')
  })

  it.each([
    ['a role lookup 503 (Clerk unreachable)', 503, { detail: 'Vouch cannot check your sign-in just now. Please try again shortly.' }],
    ['a 500', 500, { detail: 'Internal Server Error' }],
    ['a 404', 404, { detail: 'Not Found' }],
    ['a 503 that is not JSON', 503, 'Service Unavailable'],
  ])('is null on %s: the role is unknown, so no page', async (_, status, body) => {
    vi.stubGlobal('fetch', answer(status, body))
    expect(await developerSettings()).toBeNull()
  })

  it('is null when the API cannot be reached', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')))
    expect(await developerSettings()).toBeNull()
  })
})

describe('changeModel', () => {
  it('puts the model and gives back the new settings', async () => {
    const fetch = vi.fn(answer(200, { ...SETTINGS, model: 'openai/gpt-5.5' }))
    vi.stubGlobal('fetch', fetch)
    expect(await changeModel('openai/gpt-5.5')).toEqual({ ok: true, settings: { ...SETTINGS, model: 'openai/gpt-5.5' } })
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/developer/model')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body as string)).toEqual({ model: 'openai/gpt-5.5' })
  })

  it("gives the API's message for a refused id", async () => {
    const detail = 'OpenRouter has no model “x/y”. Check the id on openrouter.ai/models.'
    vi.stubGlobal('fetch', answer(422, { detail }))
    expect(await changeModel('x/y')).toEqual({ ok: false, message: detail })
  })

  it("says the id's form is wrong when the API refuses its shape (pydantic's list)", async () => {
    vi.stubGlobal('fetch', answer(422, { detail: [{ type: 'string_pattern_mismatch' }] }))
    expect(await changeModel('haiku')).toEqual({
      ok: false,
      message: 'A model id is written author/model, such as anthropic/claude-haiku-5.5.',
    })
  })

  it('says so when the API cannot be reached', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')))
    expect(await changeModel('x/y')).toEqual({ ok: false, message: 'Vouch could not reach its service. Try again shortly.' })
  })

  it('signs out on a 401', async () => {
    const signedOut = vi.fn()
    setOnSignedOut(signedOut)
    vi.stubGlobal('fetch', answer(401, { detail: 'Sign in to use Vouch.' }))
    expect((await changeModel('x/y')).ok).toBe(false)
    expect(signedOut).toHaveBeenCalled()
  })
})

describe('removeCompany, issue 418', () => {
  it('deletes the company and gives back the new settings', async () => {
    const fetch = vi.fn(answer(200, SETTINGS))
    vi.stubGlobal('fetch', fetch)
    setTokenSource(() => Promise.resolve('tok'))
    expect(await removeCompany('musterfirma-sud-gmbh')).toEqual({ status: 'removed', settings: SETTINGS })
    expect(fetch).toHaveBeenCalledWith('/api/developer/profiles/musterfirma-sud-gmbh',
      { method: 'DELETE', headers: { Authorization: 'Bearer tok' } })
  })

  it('names the id safely in the address', async () => {
    const fetch = vi.fn(answer(404, { detail: 'There is no such compliance profile.' }))
    vi.stubGlobal('fetch', fetch)
    await removeCompany('a/b?c')
    expect((fetch.mock.calls[0] as unknown as [string])[0]).toBe('/api/developer/profiles/a%2Fb%3Fc')
  })

  it.each([
    [409, 'Only a company made in company setup can be removed.'],
    [404, 'There is no such compliance profile.'],
  ])("is refused on a %i, with the API's detail", async (status, detail) => {
    vi.stubGlobal('fetch', answer(status, { detail }))
    expect(await removeCompany('x')).toEqual({ status: 'refused', problem: detail })
  })

  it("is unavailable on the database's 503", async () => {
    vi.stubGlobal('fetch', answer(503, { detail: 'The database is unavailable just now.', unavailable: 'database' }))
    expect(await removeCompany('x')).toEqual({ status: 'unavailable' })
  })

  it.each([
    ['a 500', () => answer(500, { detail: 'Internal Server Error' })()],
    ['a 403', () => answer(403, { detail: 'This is available to developers only.' })()],
    ['no answer', () => Promise.reject(new TypeError('offline'))],
  ])('fails on %s', async (_, fetch) => {
    vi.stubGlobal('fetch', fetch)
    expect(await removeCompany('x')).toEqual({ status: 'failed' })
  })

  it('signs out on a 401', async () => {
    const signedOut = vi.fn()
    setOnSignedOut(signedOut)
    vi.stubGlobal('fetch', answer(401, { detail: 'Sign in to use Vouch.' }))
    expect(await removeCompany('x')).toEqual({ status: 'signed-out' })
    expect(signedOut).toHaveBeenCalledOnce()
  })
})
