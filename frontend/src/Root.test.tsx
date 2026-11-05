import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Root from './Root.tsx'

// Clerk's chunk fails to load (a deploy replaced it, the network dropped): its import rejects.
vi.mock('./ClerkApp.tsx', () => {
  throw new Error('Failed to fetch dynamically imported module')
})

beforeEach(() => {
  window.matchMedia = (query: string) => ({ matches: false, media: query }) as MediaQueryList
  vi.spyOn(console, 'error').mockImplementation(() => {}) // React reports the caught error
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Root', () => {
  it('shows the app in local mode', async () => {
    render(<Root auth={{ mode: 'local' }} />)
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeTruthy() // once the profiles are read
  })

  it('says Vouch could not be reached, and never shows the app, when health could not be read', () => {
    render(<Root auth={{ mode: 'unreachable' }} />)
    expect(screen.getByRole('alert').textContent).toBe('Vouch could not be reached. Please try again shortly.')
    expect(screen.getByRole('banner').textContent).toMatch(/Vouch/)
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
  })

  it('shows the invitation page with "Vouch could not be loaded" when Clerk\'s chunk fails', async () => {
    render(<Root auth={{ mode: 'clerk', publishableKey: 'pk_test_x', gate: 'clerk' }} />)
    // Not "sign-in could not be loaded": the boundary also catches the signed-in app's errors
    expect((await screen.findByRole('alert')).textContent).toBe('Vouch could not be loaded. Please reload the page.')
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Access is by invitation')
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
  })
})
