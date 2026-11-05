import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ClerkApp from './ClerkApp.tsx'
import { authHeaders, setTokenSource, signedOut } from './session.ts'

// Clerk's React package, faked: these tests cover what Vouch does with each of its states.
const clerk = vi.hoisted(() => ({
  auth: { isLoaded: true, isSignedIn: false, getToken: async (): Promise<string | null> => 'session-token' },
  failed: false,
  signOut: vi.fn(),
  provider: { publishableKey: '' },
  signInProps: {} as { appearance?: { variables?: Record<string, string> } },
}))

vi.mock('@clerk/react', () => ({
  ClerkProvider: ({ publishableKey, children }: { publishableKey: string; children: ReactNode }) => {
    clerk.provider.publishableKey = publishableKey
    return <>{children}</>
  },
  ClerkLoading: ({ children }: { children: ReactNode }) =>
    clerk.auth.isLoaded || clerk.failed ? null : <>{children}</>,
  ClerkLoaded: ({ children }: { children: ReactNode }) => (clerk.auth.isLoaded ? <>{children}</> : null),
  ClerkFailed: ({ children }: { children: ReactNode }) => (clerk.failed ? <>{children}</> : null),
  SignIn: (props: { appearance?: { variables?: Record<string, string> } }) => {
    clerk.signInProps = props
    return <form aria-label="Clerk sign-in" />
  },
  useAuth: () => clerk.auth,
  useClerk: () => ({ signOut: clerk.signOut }),
}))

beforeEach(() => {
  window.matchMedia = (query: string) => ({ matches: false, media: query }) as MediaQueryList
  clerk.auth = { isLoaded: true, isSignedIn: false, getToken: async () => 'session-token' }
  clerk.failed = false
  clerk.signOut.mockReset()
})

afterEach(() => {
  cleanup()
  setTokenSource(null)
})

describe('signed out', () => {
  it('shows the invitation page with Clerk sign-in, and not the app', () => {
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect(clerk.provider.publishableKey).toBe('pk_test_x')
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Access is by invitation')
    expect(screen.getByText(/available to invited users only/i)).toBeTruthy()
    expect(screen.getByText(/sign in with the Discord account that uses the e-mail address it was sent to/i)).toBeTruthy()
    expect(screen.getByRole('form', { name: 'Clerk sign-in' })).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Readiness' })).toBeNull()
  })

  it("draws Clerk's form in Vouch's colour tokens, so it follows light and dark", () => {
    render(<ClerkApp publishableKey="pk_test_x" />)
    const variables = clerk.signInProps.appearance?.variables ?? {}
    expect(variables.colorBackground).toBe('var(--sheet)')
    expect(variables.colorForeground).toBe('var(--ink)')
    expect(variables.colorPrimary).toBe('var(--ink)')
    const colours = Object.entries(variables).filter(([name]) => name.startsWith('color'))
    expect(colours.length).toBeGreaterThan(5)
    for (const [, value] of colours) expect(value).toMatch(/^var\(--[a-z-]+\)$/)
  })

  it('sends no session token', async () => {
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect(await authHeaders()).toEqual({})
  })

  it('says so while Clerk loads, and when it cannot load', () => {
    clerk.auth = { ...clerk.auth, isLoaded: false }
    const { rerender } = render(<ClerkApp publishableKey="pk_test_x" />)
    expect(screen.getByText('Opening Vouch…')).toBeTruthy()
    clerk.failed = true
    rerender(<ClerkApp publishableKey="pk_test_x" />)
    expect(screen.getByRole('alert').textContent).toMatch(/sign-in could not be loaded/i)
    expect(screen.queryByRole('region', { name: 'Readiness' })).toBeNull()
  })
})

describe('signed in', () => {
  beforeEach(() => {
    clerk.auth = { ...clerk.auth, isSignedIn: true }
  })

  it('shows the app, and every API call carries a fresh session token', async () => {
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect(screen.getByRole('region', { name: 'Readiness' })).toBeTruthy()
    expect(screen.queryByText('Access is by invitation')).toBeNull()
    expect(await authHeaders()).toEqual({ Authorization: 'Bearer session-token' })
  })

  it('signs out from the top bar', async () => {
    render(<ClerkApp publishableKey="pk_test_x" />)
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(clerk.signOut).toHaveBeenCalledOnce()
  })

  it('stops sending the token once it leaves', async () => {
    const { unmount } = render(<ClerkApp publishableKey="pk_test_x" />)
    unmount()
    expect(await authHeaders()).toEqual({})
  })

  it('signs out, back to the sign-in, when the API says the session is no longer valid', () => {
    const { unmount } = render(<ClerkApp publishableKey="pk_test_x" />)
    signedOut()
    expect(clerk.signOut).toHaveBeenCalledOnce()
    unmount()
    signedOut() // gone: nothing left to sign out
    expect(clerk.signOut).toHaveBeenCalledOnce()
  })
})
