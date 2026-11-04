import { afterEach, describe, expect, it, vi } from 'vitest'
import { changeModel, developerSettings, type DeveloperSettings } from './developer.ts'
import { setOnSignedOut, setTokenSource } from './session.ts'

const SETTINGS: DeveloperSettings = {
  model: 'anthropic/claude-haiku-5.5',
  runs_on: 'openrouter',
  prompt_version: '1',
  usage_url: 'https://openrouter.ai/activity',
  kept_in: 'database',
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

  it.each([403, 401, 404, 503])('is null when the API answers %i: no developer page', async (status) => {
    vi.stubGlobal('fetch', answer(status, { detail: 'This is available to developers only.' }))
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
