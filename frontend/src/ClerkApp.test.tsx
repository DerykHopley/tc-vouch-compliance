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
  useUser: () => ({ user: { fullName: 'Anna Berg', username: null, primaryEmailAddress: null } }),
}))

const ONE_PROFILE = {
  profiles: [{ id: 'nordlicht', company: 'Nordlicht Komponenten GmbH', country: 'Germany', answer_language: 'en' }],
  default: 'nordlicht',
}

/** GET /api/access answers `status` with `body`, and GET /api/profiles one profile (#47: the app
 *  opens once they're read); any other call never answers (only the access check matters here). */
function access(status: number, body: unknown = {}) {
  const asked: string[] = []
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    asked.push(url)
    if (url === '/api/profiles') return Promise.resolve(Response.json(ONE_PROFILE))
    if (url !== '/api/access') return new Promise(() => {})
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer session-token')
    return Promise.resolve(new Response(JSON.stringify(body), { status }))
  })
  return asked
}

beforeEach(() => {
  window.matchMedia = (query: string) => ({ matches: false, media: query }) as MediaQueryList
  clerk.auth = { isLoaded: true, isSignedIn: false, getToken: async () => 'session-token' }
  clerk.failed = false
  clerk.signOut.mockReset()
})

afterEach(() => {
  cleanup()
  setTokenSource(null)
  vi.unstubAllGlobals()
})

describe('signed out', () => {
  it('shows the invitation page with Clerk sign-in, and not the app', () => {
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect(clerk.provider.publishableKey).toBe('pk_test_x')
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Access is by invitation')
    expect(screen.getByText(/available to invited users only/i)).toBeTruthy()
    expect(screen.getByText(/sign in with the Discord account that uses the e-mail address it was sent to/i)).toBeTruthy()
    expect(screen.getByRole('form', { name: 'Clerk sign-in' })).toBeTruthy()
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
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

  it("with the course's Discord server as the gate, says it is open to the server's members (ticket 152)", () => {
    render(<ClerkApp publishableKey="pk_test_x" gate="discord-server" />)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe("Open to the course's Discord server")
    expect(screen.getByText(/open to members of the course's Discord server/i)).toBeTruthy()
    expect(screen.getByText(/sign in with the Discord account you use there/i)).toBeTruthy()
    expect(screen.queryByText('Access is by invitation')).toBeNull()
    expect(screen.getByRole('form', { name: 'Clerk sign-in' })).toBeTruthy()
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
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
  })
})

describe('signed in', () => {
  beforeEach(() => {
    clerk.auth = { ...clerk.auth, isSignedIn: true }
    access(200, { access: 'allowed' })
  })

  it('shows the app, and every API call carries a fresh session token', async () => {
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeTruthy() // once the profiles are read
    expect(screen.queryByText('Access is by invitation')).toBeNull()
    expect((await authHeaders()).Authorization).toBe('Bearer session-token') // beside the profile (#47)
  })

  it("signs out from the top bar's user menu, which names the user (issue 222)", async () => {
    render(<ClerkApp publishableKey="pk_test_x" />)
    const user = await screen.findByRole('button', { name: 'Anna Berg: your menu' })
    expect(user.textContent).toBe('AB')
    await userEvent.click(user)
    expect(screen.getByText('Signed in as Anna Berg')).toBeTruthy()
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent))
      .toEqual(['Start Nordlicht Komponenten GmbH afresh…', 'Dark theme', 'Sign out']) // the fresh start, #285
    await userEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }))
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

describe('signed in, before the app: may this user use Vouch? (ticket 152)', () => {
  beforeEach(() => {
    clerk.auth = { ...clerk.auth, isSignedIn: true }
  })

  it('asks the API first, and shows nothing of the app until it answers', async () => {
    const asked = access(200, { access: 'allowed' })
    vi.stubGlobal('fetch', (url: string) => {
      asked.push(url)
      return new Promise(() => {})
    })
    render(<ClerkApp publishableKey="pk_test_x" />)
    await vi.waitFor(() => expect(asked).toEqual(['/api/access']))
    expect(screen.getByText('Opening Vouch…')).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Readiness' })).toBeNull()
  })

  it('outside the course server: says Vouch is open to its members, with a sign-out button', async () => {
    access(403, { detail: 'not in it', reason: 'not-a-member' })
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Open to course members')
    expect(screen.getByText(/open to members of the course's Discord server/i)).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Readiness' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(clerk.signOut).toHaveBeenCalledOnce()
  })

  it('a Discord token Vouch cannot use: asks to sign in with Discord again', async () => {
    access(403, { detail: 'again', reason: 'sign-in-again' })
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Please sign in again')
    expect(screen.getByText(/which Discord servers you are in/i)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Sign out and sign in again' }))
    expect(clerk.signOut).toHaveBeenCalledOnce()
  })

  it("on an invitation-only Vouch, the page before the app keeps the invitation's pill", async () => {
    access(503, {})
    render(<ClerkApp publishableKey="pk_test_x" gate="clerk" />)
    await screen.findByRole('alert')
    expect(screen.getByText('By invitation')).toBeTruthy()
    expect(screen.queryByText('Course members')).toBeNull()
  })

  it('the check failing: says so, never the app, and can try again', async () => {
    access(503, { detail: 'Vouch cannot check your sign-in just now.' })
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect((await screen.findByRole('alert')).textContent).toMatch(/cannot check your sign-in just now/i)
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
    access(200, { access: 'allowed' })
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeTruthy() // the app, once the profiles are read
  })

  it('an answer it does not know, or none at all, counts as the check failing', async () => {
    access(403, { detail: 'Developers only' })
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect((await screen.findByRole('alert')).textContent).toMatch(/cannot check your sign-in/i)
    cleanup()
    vi.stubGlobal('fetch', () => Promise.reject(new Error('offline')))
    render(<ClerkApp publishableKey="pk_test_x" />)
    expect((await screen.findByRole('alert')).textContent).toMatch(/cannot check your sign-in/i)
  })

  it('a 401 signs out, back to the sign-in', async () => {
    access(401, { detail: 'Sign in to use Vouch.' })
    render(<ClerkApp publishableKey="pk_test_x" />)
    await vi.waitFor(() => expect(clerk.signOut).toHaveBeenCalledOnce())
  })
})
