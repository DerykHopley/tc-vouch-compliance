import { afterEach, describe, expect, it, vi } from 'vitest'
import { profileTag } from './profiles.ts'
import { COUNTRIES, frameworkControls, protectionLevels, type ProtectionScope, scopeWords } from './setup.ts'

afterEach(() => vi.unstubAllGlobals())

describe('the countries company setup offers', () => {
  it('are the EU, Iceland, Liechtenstein, Norway, Switzerland and the UK', () => {
    expect(COUNTRIES).toHaveLength(32)
    const names = COUNTRIES.map((c) => c.name)
    for (const name of ['Germany', 'Iceland', 'Liechtenstein', 'Norway', 'Switzerland', 'United Kingdom', 'Austria'])
      expect(names).toContain(name)
  })

  it("each name maps back to its ISO code through Intl's English region names, as the profile tag reads it", () => {
    const regions = new Intl.DisplayNames(['en'], { type: 'region' })
    for (const { code, name } of COUNTRIES) {
      expect(regions.of(code)).toBe(name)
      expect(profileTag({ id: 'x', company: 'X', country: name, answer_language: 'en' })).toBe(`${code} · EN`)
    }
  })
})

const HIGH: ProtectionScope = {
  level: 'high', requirements: { must: 37, should: 48, high: 14 }, total: 99, controls: 12, added: 14,
  added_controls: ['1.6.2', '4.1.2'],
}

describe('the protection levels', () => {
  it("read the route's levels", async () => {
    const fetch = vi.fn((_url: string) => Promise.resolve(new Response(JSON.stringify({ levels: [HIGH] }))))
    vi.stubGlobal('fetch', fetch)
    expect(await protectionLevels()).toEqual([HIGH])
    expect(fetch.mock.calls[0][0]).toBe('/api/developer/protection-levels')
  })

  it('are null when refused or unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 500 }))))
    expect(await protectionLevels()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    expect(await protectionLevels()).toBeNull()
  })

  it('say what normal brings in, and what a higher level adds', () => {
    const normal = { ...HIGH, level: 'normal' as const, total: 85, added: 0, added_controls: [] }
    expect(scopeWords(normal)).toBe('Must and should requirements: 85 in 12 controls.')
    expect(scopeWords(HIGH)).toBe('Adds 14 high-protection requirements, in 2 controls (1.6.2, 4.1.2): 99 in all.')
    expect(scopeWords({ ...HIGH, level: 'very high', added: 10, total: 109 }))
      .toBe('Adds 10 very-high-protection requirements, in 2 controls (1.6.2, 4.1.2): 109 in all.')
    expect(scopeWords({ ...HIGH, added: 1, added_controls: ['1.6.2'] }))
      .toBe('Adds 1 high-protection requirement, in 1 control (1.6.2): 99 in all.')
    expect(scopeWords({ ...normal, controls: 1 })).toBe('Must and should requirements: 85 in 1 control.')
  })
})

describe("the framework's controls", () => {
  const CONTROLS = [{ id: '1.1.1', question: 'To what extent are policies available?' }]

  it("read the route's controls", async () => {
    const fetch = vi.fn((_url: string) => Promise.resolve(new Response(JSON.stringify({ controls: CONTROLS }))))
    vi.stubGlobal('fetch', fetch)
    expect(await frameworkControls()).toEqual(CONTROLS)
    expect(fetch.mock.calls[0][0]).toBe('/api/developer/controls')
  })

  it('are null when refused or unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 500 }))))
    expect(await frameworkControls()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    expect(await frameworkControls()).toBeNull()
  })
})
