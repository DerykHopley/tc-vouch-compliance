import { afterEach, describe, expect, it, vi } from 'vitest'
import { authConfig } from './boot.ts'

const health = (body: unknown, status = 200) =>
  vi.stubGlobal('fetch', (url: string) => {
    expect(url).toBe('/api/health')
    return Promise.resolve(new Response(JSON.stringify(body), { status }))
  })

afterEach(() => vi.unstubAllGlobals())

describe('authConfig', () => {
  it('is clerk, with the publishable key, when health says so', async () => {
    health({ status: 'ok', auth: { mode: 'clerk', publishable_key: 'pk_test_x' } })
    expect(await authConfig()).toEqual({ mode: 'clerk', publishableKey: 'pk_test_x' })
  })

  it('is local when health says local', async () => {
    health({ status: 'ok', auth: { mode: 'local', publishable_key: null } })
    expect(await authConfig()).toEqual({ mode: 'local' })
  })

  it('is local when health cannot be read: the API still refuses what needs sign-in', async () => {
    health({}, 503)
    expect(await authConfig()).toEqual({ mode: 'local' })
    vi.stubGlobal('fetch', () => Promise.reject(new Error('offline')))
    expect(await authConfig()).toEqual({ mode: 'local' })
  })

  it('is local when clerk mode comes without a key', async () => {
    health({ status: 'ok', auth: { mode: 'clerk', publishable_key: null } })
    expect(await authConfig()).toEqual({ mode: 'local' })
  })
})
