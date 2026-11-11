import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setBuild } from './build.ts'
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

  it('names the company, last in the menu (ticket 495), and only "Start afresh…" without one', async () => {
    await opened(() => new Promise(() => {}))
    const items = screen.getAllByRole('menuitem').map((item) => item.textContent)
    expect(items).toEqual(['Dark theme', `Start ${COMPANY} afresh…`])
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

describe('the user menu in the Letterhead order (ticket 495)', () => {
  const DEV = 'Vouch dev' // the running build, last (issue 580)
  const COMPANY = 'Nordlicht Komponenten GmbH'
  const developerTools = { onPage: () => {}, onSetup: () => {} }
  /** The open menu's contents in order: each item's words, the note's, and '—' for a separator. */
  const contents = () => [...screen.getByRole('menu').children].map((el) =>
    el.getAttribute('role') === 'separator' ? '—' : el.textContent)

  it('for a developer in local mode: the theme, Developer page, Set up a company, then the reset', async () => {
    render(<UserMenu theme="light" onTheme={() => {}} company={COMPANY} onStartedAfresh={() => {}}
      developerTools={developerTools} />)
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    expect(contents()).toEqual(['Dark theme', '—', 'Developer page', 'Set up a company', '—',
      `Start ${COMPANY} afresh…`, '—', DEV])
  })

  it('in clerk mode: "Signed in as", the theme, the developer items, Sign out, then the reset', async () => {
    render(<UserMenu name="Anna Berg" theme="dark" onTheme={() => {}} onSignOut={() => {}} company={COMPANY}
      onStartedAfresh={() => {}} developerTools={developerTools} />)
    await userEvent.click(screen.getByRole('button', { name: 'Anna Berg: your menu' }))
    expect(contents()).toEqual(['Signed in as Anna Berg', 'Light theme', '—', 'Developer page', 'Set up a company',
      '—', 'Sign out', '—', `Start ${COMPANY} afresh…`, '—', DEV])
  })

  it("for a lead, no developer items and no empty group's separator", async () => {
    render(<UserMenu theme="light" onTheme={() => {}} company={COMPANY} onStartedAfresh={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    expect(contents()).toEqual(['Dark theme', '—', `Start ${COMPANY} afresh…`, '—', DEV])
    cleanup()
    render(<UserMenu theme="light" onTheme={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    expect(contents()).toEqual(['Dark theme', '—', DEV])
  })

  it('opens the developer page and company setup, closing the menu', async () => {
    const onPage = vi.fn()
    const onSetup = vi.fn()
    render(<UserMenu theme="light" onTheme={() => {}} developerTools={{ onPage, onSetup }} />)
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Developer page' }))
    expect(onPage).toHaveBeenCalledOnce()
    expect(screen.queryByRole('menu')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Set up a company' }))
    expect(onSetup).toHaveBeenCalledOnce()
  })

  it('the arrow keys move between the items and skip the separators', async () => {
    render(<UserMenu theme="light" onTheme={() => {}} company={COMPANY} onStartedAfresh={() => {}}
      developerTools={developerTools} />)
    screen.getByRole('button', { name: 'Your menu' }).focus()
    await userEvent.keyboard('{ArrowDown}')
    const visited = [document.activeElement?.textContent]
    for (let i = 0; i < 4; i++) {
      await userEvent.keyboard('{ArrowDown}')
      visited.push(document.activeElement?.textContent)
    }
    expect(visited).toEqual(['Dark theme', 'Developer page', 'Set up a company', `Start ${COMPANY} afresh…`,
      'Dark theme'])
  })

  it('keeps focus on the reset when the developer tools arrive while the menu is open', async () => {
    const props = { name: 'Anna Berg', theme: 'light' as const, onTheme: () => {}, onSignOut: () => {},
      company: COMPANY, onStartedAfresh: () => {} }
    const { rerender } = render(<UserMenu {...props} />)
    await userEvent.click(screen.getByRole('button', { name: 'Anna Berg: your menu' }))
    const reset = screen.getByRole('menuitem', { name: `Start ${COMPANY} afresh…` })
    reset.focus()
    rerender(<UserMenu {...props} developerTools={developerTools} />)
    expect(document.activeElement).toBe(reset)
    expect(document.activeElement?.textContent).toBe(`Start ${COMPANY} afresh…`)
    expect(contents()).toEqual(['Signed in as Anna Berg', 'Dark theme', '—', 'Developer page', 'Set up a company',
      '—', 'Sign out', '—', `Start ${COMPANY} afresh…`, '—', DEV])
  })
})

describe("the running build, the menu's muted last line (issue 580)", () => {
  afterEach(() => setBuild(undefined))

  /** The open menu's last element. */
  async function lastLine() {
    render(<UserMenu theme="light" onTheme={() => {}} onStartedAfresh={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    const last = screen.getByRole('menu').lastElementChild!
    expect(last.previousElementSibling?.getAttribute('role')).toBe('separator')
    expect(last.className).toBe('menu-note') // muted, as "Signed in as" is
    return last.textContent
  }

  it('names the commit and the Cloud Run revision the API serves', async () => {
    setBuild({ commit: '1a2b3c4', revision: 'vouch-00029' })
    expect(await lastLine()).toBe('Vouch 1a2b3c4 · vouch-00029')
  })

  it('names the commit alone with no revision', async () => {
    setBuild({ commit: '1a2b3c4', revision: null })
    expect(await lastLine()).toBe('Vouch 1a2b3c4')
  })

  it('reads "Vouch dev" locally, and when the API serves no build it can read', async () => {
    setBuild({ commit: 'dev', revision: null })
    expect(await lastLine()).toBe('Vouch dev')
    cleanup()
    setBuild(undefined)
    expect(await lastLine()).toBe('Vouch dev')
    cleanup()
    setBuild({ commit: 42, revision: ['x'] })
    expect(await lastLine()).toBe('Vouch dev')
  })
})
