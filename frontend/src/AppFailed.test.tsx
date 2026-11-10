import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ClerkApp from './ClerkApp.tsx'
import Root from './Root.tsx'

// Issue 455: a render error inside the signed-in app (the Developer page reading a field a change's answer lacked) was
// caught by Root's boundary for Clerk's chunk, which showed the Discord gate and "Vouch could not be loaded" to a
// developer who was signed in with access. The app now has a boundary of its own: its own error, and Try again.
const app = vi.hoisted(() => ({ throws: true }))

vi.mock('./ProfiledApp.tsx', () => ({
  default: () => {
    if (app.throws) throw new Error("Cannot read properties of undefined (reading 'length')")
    return <main aria-label="The app">Vouch</main>
  },
}))

vi.mock('@clerk/react', () => ({
  ClerkProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  ClerkLoading: () => null,
  ClerkLoaded: ({ children }: { children: ReactNode }) => <>{children}</>,
  ClerkFailed: () => null,
  SignIn: () => <form aria-label="Clerk sign-in" />,
  useAuth: () => ({ isLoaded: true, isSignedIn: true, getToken: async () => 'session-token' }),
  useClerk: () => ({ signOut: vi.fn() }),
  useUser: () => ({ user: { fullName: 'Anna Berg', username: null, primaryEmailAddress: null } }),
}))

beforeEach(() => {
  app.throws = true
  vi.spyOn(console, 'error').mockImplementation(() => {}) // React logs each caught render error
  vi.stubGlobal('fetch', (url: string) =>
    url === '/api/access' ? Promise.resolve(Response.json({ access: 'allowed' })) : new Promise(() => {}))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const GATE_COPY = [/Discord server/i, /could not be loaded/i, /invitation/i]

describe('a render error inside the app', () => {
  it('signed in with access: shows its own error and Try again, never the Discord gate', async () => {
    render(<ClerkApp publishableKey="pk_test_x" gate="discord-server" />)

    expect((await screen.findByRole('alert')).textContent).toBe('Vouch could not show this page. Please try again.')
    for (const copy of GATE_COPY) expect(screen.queryByText(copy)).toBeNull()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy() // a way out if it keeps failing

    app.throws = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(screen.getByRole('main', { name: 'The app' })).toBeTruthy()
  })

  it('in local mode: the same, not a blank page', async () => {
    render(<Root auth={{ mode: 'local' }} />)

    expect((await screen.findByRole('alert')).textContent).toBe('Vouch could not show this page. Please try again.')
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull() // no sign-in to leave
    app.throws = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(screen.getByRole('main', { name: 'The app' })).toBeTruthy()
  })
})
