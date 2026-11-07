import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import UserMenu from './UserMenu.tsx'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('UserMenu (issue 222)', () => {
  it("shows the user's initials, local mode's one user as LU", () => {
    render(<UserMenu name="Anna Berg" theme="light" onTheme={() => {}} onSignOut={() => {}} />)
    expect(screen.getByRole('button', { name: 'Anna Berg: your menu' }).textContent).toBe('AB')
    cleanup()
    render(<UserMenu theme="light" onTheme={() => {}} />)
    expect(screen.getByRole('button', { name: 'Your menu' }).textContent).toBe('LU')
  })

  it("shows an ellipsis, never local mode's LU, while Clerk has not read the user yet", () => {
    render(<UserMenu theme="light" onTheme={() => {}} onSignOut={() => {}} />)
    expect(screen.getByRole('button', { name: 'Your menu' }).textContent).toBe('…')
  })
})

describe('starting afresh (issue 285)', () => {
  const COMPANY = 'Nordlicht Komponenten GmbH'
  const QUESTION = `Start ${COMPANY} afresh? Your progress here starts from nothing; the audit trail keeps every entry.`

  /** The menu with the fresh start, opened; `answer` is the fresh start's response. */
  async function opened(answer: () => Promise<Response>, { company }: { company?: string } = { company: COMPANY }) {
    const fetch = vi.fn((_url: string, _init?: RequestInit) => answer())
    vi.stubGlobal('fetch', fetch)
    const started = vi.fn()
    render(<UserMenu theme="light" onTheme={() => {}} company={company} onStartedAfresh={started} />)
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    return { fetch, started }
  }

  it('names the company above the theme item, and only "Start afresh…" without one', async () => {
    await opened(() => new Promise(() => {}))
    const items = screen.getAllByRole('menuitem').map((item) => item.textContent)
    expect(items).toEqual([`Start ${COMPANY} afresh…`, 'Dark theme'])
    cleanup()
    await opened(() => new Promise(() => {}), {})
    expect(screen.getByRole('menuitem', { name: 'Start afresh…' })).toBeTruthy()
  })

  it('asks first, and Cancel sends no request', async () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    const { fetch, started } = await opened(() => Promise.resolve(Response.json({})))
    await userEvent.click(screen.getByRole('menuitem', { name: `Start ${COMPANY} afresh…` }))
    expect(confirm).toHaveBeenCalledWith(QUESTION)
    expect(fetch).not.toHaveBeenCalled()
    expect(started).not.toHaveBeenCalled()
  })

  it('OK sends the fresh start, then the app opens afresh', async () => {
    vi.stubGlobal('confirm', () => true)
    const { fetch, started } = await opened(
      () => Promise.resolve(Response.json({ fresh_start: '2026-11-07T10:00:00+00:00' })))
    await userEvent.click(screen.getByRole('menuitem', { name: `Start ${COMPANY} afresh…` }))
    await waitFor(() => expect(started).toHaveBeenCalledOnce())
    expect(fetch).toHaveBeenCalledWith('/api/progress/fresh-start', expect.objectContaining({ method: 'POST' }))
  })

  it.each([
    ['a running assessment (409)', () => Promise.resolve(new Response(null, { status: 409 })),
      'An assessment is running: start afresh once it has finished.'],
    ['the database down (503)', () => Promise.resolve(new Response(null, { status: 503 })),
      'Vouch could not start afresh just now. Try again.'],
    ['no answer', () => Promise.reject(new TypeError('offline')),
      'Vouch could not start afresh just now. Try again.'],
  ])('on %s, says why in an alert and the page stays as it was', async (_case, answer, words) => {
    vi.stubGlobal('confirm', () => true)
    const alert = vi.fn()
    vi.stubGlobal('alert', alert)
    const { started } = await opened(answer)
    await userEvent.click(screen.getByRole('menuitem', { name: `Start ${COMPANY} afresh…` }))
    await waitFor(() => expect(alert).toHaveBeenCalledWith(words))
    expect(started).not.toHaveBeenCalled()
  })

  it('on a 401, signs out with no alert', async () => {
    vi.stubGlobal('confirm', () => true)
    const alert = vi.fn()
    vi.stubGlobal('alert', alert)
    const { fetch, started } = await opened(() => Promise.resolve(new Response(null, { status: 401 })))
    await userEvent.click(screen.getByRole('menuitem', { name: `Start ${COMPANY} afresh…` }))
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce())
    await new Promise((resolve) => setTimeout(resolve, 0)) // the answer is handled
    expect(alert).not.toHaveBeenCalled()
    expect(started).not.toHaveBeenCalled()
  })
})
