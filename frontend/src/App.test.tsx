import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'

function systemPrefers(dark: boolean | null, width = 1400) {
  window.matchMedia = (query: string) => {
    const maxWidth = /^\(max-width: (\d+)px\)$/.exec(query)
    const matches = maxWidth ? width <= Number(maxWidth[1]) : dark === true && query === '(prefers-color-scheme: dark)'
    return { matches, media: query } as MediaQueryList
  }
}

beforeEach(() => systemPrefers(null))

afterEach(() => {
  cleanup()
  delete document.documentElement.dataset.theme
  window.history.replaceState(null, '', '/')
  vi.unstubAllGlobals()
})

describe('Dossier landing', () => {
  it('shows the top bar, headline, readiness band and one control card', () => {
    render(<App />)
    const top = screen.getByRole('banner')
    expect(within(top).getByText('Vouch')).toBeTruthy()
    expect(within(top).getByRole('link', { name: 'Dossier' }).getAttribute('aria-current')).toBe('page')
    expect(screen.getByRole('heading', { level: 1 }).textContent).toMatch(/prepared for audit/i)
    expect(screen.getByRole('region', { name: 'Readiness' })).toBeTruthy()
    const cards = screen.getAllByRole('article')
    expect(cards).toHaveLength(1)
    expect(within(cards[0]).getByText('NK-ENC-01')).toBeTruthy()
    expect(within(cards[0]).getByText('Not assessed')).toBeTruthy()
  })
})

describe('the working paper', () => {
  const paper = () => screen.queryByRole('region', { name: 'Working paper' })

  it('opens straight at its address, as after a refresh', () => {
    window.history.replaceState(null, '', '/controls/NK-ENC-01')
    render(<App />)
    expect(within(paper()!).getByRole('heading', { name: /NK-ENC-01/ })).toBeTruthy()
    expect(within(paper()!).getByRole('button', { name: 'Assess NK-ENC-01' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Dossier' }).getAttribute('aria-current')).toBeNull()
  })

  it('gets its address when assessed from the card, and Back returns to the Dossier', async () => {
    vi.stubGlobal('fetch', () => new Promise(() => {})) // never answers: only the address matters here
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Assess NK-ENC-01' }))
    expect(window.location.pathname).toBe('/controls/NK-ENC-01')
    expect(paper()).toBeTruthy()

    window.history.back()
    await waitFor(() => expect(paper()).toBeNull())
    expect(window.location.pathname).toBe('/')
  })

  it('leaves for the Dossier at /', async () => {
    window.history.replaceState(null, '', '/controls/NK-ENC-01')
    render(<App />)
    await userEvent.click(screen.getByRole('link', { name: 'Dossier' }))
    expect(window.location.pathname).toBe('/')
    expect(paper()).toBeNull()
  })

  it('shows the Dossier for an address it does not know', () => {
    window.history.replaceState(null, '', '/controls/NK-XXX-99')
    render(<App />)
    expect(paper()).toBeNull()
    expect(screen.getAllByRole('article')).toHaveLength(1)
  })
})

describe('theme', () => {
  it('starts dark when the system prefers dark', () => {
    systemPrefers(true)
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('starts light when the system has no preference', () => {
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('flips with the switch', async () => {
    systemPrefers(false)
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Dark theme' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    await userEvent.click(screen.getByRole('button', { name: 'Light theme' }))
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})

describe('Ask Vouch', () => {
  const bubble = () => screen.queryByRole('complementary', { name: 'Chat with Vouch' })

  it('opens an empty bubble and closes it again', async () => {
    render(<App />)
    expect(bubble()).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    expect(bubble()).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Close the chat' }))
    expect(bubble()).toBeNull()
  })

  it('closes on Esc and gives focus back to the button', async () => {
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    await userEvent.keyboard('{Escape}')
    expect(bubble()).toBeNull()
    expect(document.activeElement?.textContent).toMatch(/ask vouch/i)
  })

  it('keeps Tab and Shift+Tab inside the full-screen sheet at 400px', async () => {
    systemPrefers(null, 400)
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    const sheet = bubble()!
    for (const shift of [false, true]) {
      for (let i = 0; i < 4; i++) {
        await userEvent.tab({ shift })
        expect(sheet.contains(document.activeElement)).toBe(true)
      }
    }
  })

  it('leaves the page reachable by Tab beside the bubble at 1400px', async () => {
    systemPrefers(null, 1400)
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    const reached = new Set<Element | null>()
    for (let i = 0; i < 4; i++) {
      await userEvent.tab()
      reached.add(document.activeElement)
    }
    expect(reached.has(screen.getByRole('link', { name: 'Dossier' }))).toBe(true)
    expect(reached.has(screen.getByRole('button', { name: /theme/i }))).toBe(true)
  })

  it('opens with Ctrl+K', async () => {
    render(<App />)
    await userEvent.keyboard('{Control>}k{/Control}')
    expect(bubble()).toBeTruthy()
  })
})
