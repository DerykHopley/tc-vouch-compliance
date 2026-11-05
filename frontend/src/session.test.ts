import { afterEach, describe, expect, it, vi } from 'vitest'
import { SESSION_ENDED, sessionEnded, setOnSignedOut } from './session.ts'

afterEach(() => setOnSignedOut(null))

describe('sessionEnded', () => {
  it('signs out on a 401, and says so', () => {
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    expect(sessionEnded(new Response(null, { status: 401 }))).toBe(true)
    expect(signOut).toHaveBeenCalledOnce()
    expect(SESSION_ENDED).toBe('Your sign-in has ended. Please sign in again.')
  })

  it.each([200, 403, 503])('leaves the session alone on a %i', (status) => {
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    expect(sessionEnded(new Response(null, { status }))).toBe(false)
    expect(signOut).not.toHaveBeenCalled()
  })
})
