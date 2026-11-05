import { afterEach, describe, expect, it, vi } from 'vitest'
import { authConfig } from './authConfig.ts'

const health = (body: unknown, status = 200) =>
  vi.stubGlobal('fetch', (url: string) => {
    expect(url).toBe('/api/health')
    return Promise.resolve(new Response(JSON.stringify(body), { status }))
  })

afterEach(() => vi.unstubAllGlobals())

describe('authConfig', () => {
  it('is clerk, with the publishable key, when health says so', async () => {
    health({ status: 'ok', auth: { mode: 'clerk', publishable_key: 'pk_test_x', gate: 'clerk' } })
    expect(await authConfig()).toEqual({ mode: 'clerk', publishableKey: 'pk_test_x', gate: 'clerk' })
  })

  it("says when the course's Discord server gates sign-in, and Clerk alone otherwise (ticket 152)", async () => {
    health({ status: 'ok', auth: { mode: 'clerk', publishable_key: 'pk_test_x', gate: 'discord-server' } })
    expect(await authConfig()).toEqual({ mode: 'clerk', publishableKey: 'pk_test_x', gate: 'discord-server' })
    health({ status: 'ok', auth: { mode: 'clerk', publishable_key: 'pk_test_x' } })
    expect(await authConfig()).toEqual({ mode: 'clerk', publishableKey: 'pk_test_x', gate: 'clerk' })
  })

  it('is local when health says local', async () => {
    health({ status: 'ok', auth: { mode: 'local', publishable_key: null } })
    expect(await authConfig()).toEqual({ mode: 'local' })
  })

  it('is unreachable, never local, when health cannot be read', async () => {
    health({}, 503)
    expect(await authConfig()).toEqual({ mode: 'unreachable' })
    vi.stubGlobal('fetch', () => Promise.reject(new Error('offline')))
    expect(await authConfig()).toEqual({ mode: 'unreachable' })
    vi.stubGlobal('fetch', () => Promise.resolve(new Response('<html>', { status: 200 })))
    expect(await authConfig()).toEqual({ mode: 'unreachable' })
  })

  it('is unreachable when health names no mode it knows, or clerk mode without a key', async () => {
    health({ status: 'ok', auth: { mode: 'clerk', publishable_key: null } })
    expect(await authConfig()).toEqual({ mode: 'unreachable' })
    health({ status: 'ok' })
    expect(await authConfig()).toEqual({ mode: 'unreachable' })
  })
})
