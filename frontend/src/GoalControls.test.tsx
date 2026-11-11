import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GoalControl, RoundControl } from './GoalControls.tsx'
import type { Overview } from './overview.ts'
import type { Goal } from './report.ts'

/** The in-app ask on Change goal and Close round (#441): never the browser's own confirm. */

let confirmSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => {
    throw new Error('window.confirm was called')
  })
})

afterEach(() => {
  expect(confirmSpy).not.toHaveBeenCalled()
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** The API: `method` on `path` answers `answer`; nothing else is asked. */
function serve(path: string, method: string, answer: () => Promise<Response>) {
  const fetch = vi.fn((url: string, init?: RequestInit) =>
    url === path && init?.method === method ? answer() : new Promise<Response>(() => {}))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement

describe('Change goal asks in the app (ticket 441)', () => {
  const QUESTION = "Change this profile's goal to Internal improvement? The change is recorded in the audit trail."

  function shown(goal: Goal = 'audit-readiness') {
    const onChanged = vi.fn((_goal: Goal) => Promise.resolve(true))
    const view = render(<GoalControl overview={{ goal }} onChanged={onChanged} arriving={false} />)
    return { onChanged, rerender: (next: Goal) =>
      view.rerender(<GoalControl overview={{ goal: next }} onChanged={onChanged} arriving={false} />) }
  }

  it("opens the ask in the button's place: the question, Change goal and Cancel, focus on Cancel, nothing sent",
    async () => {
      const fetch = serve('/api/goal', 'PUT', () => Promise.resolve(new Response('{}')))
      shown()
      await userEvent.click(button('Change goal'))
      const alert = screen.getByRole('alert')
      expect(alert.textContent).toBe(QUESTION)
      expect(alert.className).toBe('field-note')
      expect(screen.getAllByRole('button', { name: 'Change goal' })).toHaveLength(1)
      expect(button('Change goal').className).toBe('btn btn-primary')
      expect(button('Cancel').className).toBe('btn btn-ghost')
      expect(document.activeElement).toBe(button('Cancel'))
      expect(fetch).not.toHaveBeenCalled()
    })

  it('on Cancel closes the ask, sends nothing and puts focus back on Change goal', async () => {
    const fetch = serve('/api/goal', 'PUT', () => Promise.resolve(new Response('{}')))
    const { onChanged } = shown()
    await userEvent.click(button('Change goal'))
    await userEvent.click(button('Cancel'))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
    expect(button('Change goal').className).toBe('btn btn-ghost')
    expect(document.activeElement).toBe(button('Change goal'))
    expect(fetch).not.toHaveBeenCalled()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('on Escape closes the ask, sends nothing and puts focus back on Change goal', async () => {
    const fetch = serve('/api/goal', 'PUT', () => Promise.resolve(new Response('{}')))
    shown()
    await userEvent.click(button('Change goal'))
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(document.activeElement).toBe(button('Change goal'))
    expect(fetch).not.toHaveBeenCalled()
  })

  it('on Change goal in the ask sends one PUT with the other goal, and the line then names it', async () => {
    const fetch = serve('/api/goal', 'PUT',
      () => Promise.resolve(new Response(JSON.stringify({ goal: 'internal-improvement' }))))
    const { onChanged, rerender } = shown()
    onChanged.mockImplementation(async (goal: Goal) => { rerender(goal); return true })
    await userEvent.click(button('Change goal'))
    await userEvent.click(button('Change goal'))
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith('internal-improvement'))
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('/api/goal')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({ goal: 'internal-improvement' })
    expect(screen.getByText(/^Goal: /).textContent).toBe('Goal: Internal improvement')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('keeps focus on Change goal while the change runs, busy, and after it', async () => {
    let answer: (r: Response) => void = () => {}
    serve('/api/goal', 'PUT', () => new Promise<Response>((resolve) => { answer = resolve }))
    shown()
    await userEvent.click(button('Change goal'))
    await userEvent.click(button('Change goal'))
    const trigger = button('Change goal')
    expect(trigger.className).toBe('btn btn-ghost')
    expect(trigger.getAttribute('aria-disabled')).toBe('true')
    expect(trigger.getAttribute('aria-busy')).toBe('true')
    expect(document.activeElement).toBe(trigger)
    answer(new Response(JSON.stringify({ goal: 'internal-improvement' })))
    await waitFor(() => expect(trigger.getAttribute('aria-disabled')).toBeNull())
    expect(document.activeElement).toBe(button('Change goal'))
  })

  it('closes the ask, focus back on Change goal, when the goal changes under it (another tab)', async () => {
    const fetch = serve('/api/goal', 'PUT', () => Promise.resolve(new Response('{}')))
    const { rerender } = shown()
    await userEvent.click(button('Change goal'))
    rerender('internal-improvement')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(document.activeElement).toBe(button('Change goal'))
    expect(fetch).not.toHaveBeenCalled()
  })

  it("can't be opened while the arrival sequence runs (ticket 447)", () => {
    render(<GoalControl overview={{ goal: 'audit-readiness' }} onChanged={() => Promise.resolve(true)} arriving />)
    expect(button('Change goal').disabled).toBe(true)
  })
})

describe('Close round asks in the app (ticket 441)', () => {
  const QUESTION = 'Close the review round "Q4 2026"? Its results are frozen, and the overview compares later work '
    + 'with them. The close is recorded in the audit trail.'
  const rounds: Overview['rounds'] = { current: { name: 'Q4 2026', opened_at: '2026-11-09T10:30:00Z' }, comparison: null }

  function shown() {
    const onChanged = vi.fn(() => Promise.resolve(true))
    const view = render(<RoundControl overview={{ rounds }} onChanged={onChanged} />)
    return { onChanged, rerender: (next: Overview['rounds']) =>
      view.rerender(<RoundControl overview={{ rounds: next }} onChanged={onChanged} />) }
  }

  it('opens the ask with its question, Close round and Cancel, focus on Cancel, nothing sent', async () => {
    const fetch = serve('/api/rounds/close', 'POST', () => Promise.resolve(new Response('{}')))
    shown()
    await userEvent.click(button('Close round'))
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toBe(QUESTION)
    expect(alert.className).toBe('field-note')
    expect(button('Close round').className).toBe('btn btn-primary')
    expect(button('Cancel').className).toBe('btn btn-ghost')
    expect(document.activeElement).toBe(button('Cancel'))
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each([
    ['Cancel', () => userEvent.click(button('Cancel'))],
    ['Escape', () => userEvent.keyboard('{Escape}')],
  ])('on %s closes the ask, sends nothing and puts focus back on Close round', async (_, dismiss) => {
    const fetch = serve('/api/rounds/close', 'POST', () => Promise.resolve(new Response('{}')))
    const { onChanged } = shown()
    await userEvent.click(button('Close round'))
    await dismiss()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(button('Close round').className).toBe('btn btn-ghost')
    expect(document.activeElement).toBe(button('Close round'))
    expect(fetch).not.toHaveBeenCalled()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('on Close round in the ask sends one POST to close it, then reads the overview again', async () => {
    const fetch = serve('/api/rounds/close', 'POST',
      () => Promise.resolve(new Response(JSON.stringify({ closed: { name: 'Q4 2026' } }))))
    const { onChanged } = shown()
    await userEvent.click(button('Close round'))
    await userEvent.click(button('Close round'))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/rounds/close')
    expect(fetch.mock.calls[0][1]?.method).toBe('POST')
  })

  it("keeps focus on Close round while it closes, then moves it to the next round's name field", async () => {
    let answer: (r: Response) => void = () => {}
    serve('/api/rounds/close', 'POST', () => new Promise<Response>((resolve) => { answer = resolve }))
    const { onChanged, rerender } = shown()
    onChanged.mockImplementation(async () => { rerender({ current: null, comparison: null }); return true })
    await userEvent.click(button('Close round'))
    await userEvent.click(button('Close round'))
    expect(button('Close round').getAttribute('aria-disabled')).toBe('true')
    expect(document.activeElement).toBe(button('Close round'))
    answer(new Response(JSON.stringify({ closed: { name: 'Q4 2026' } })))
    const field = await screen.findByRole('textbox', { name: 'Name of the next round' })
    await waitFor(() => expect(document.activeElement).toBe(field))
  })

  it('closes the ask, focus back on Close round, when the open round changes under it (another tab)', async () => {
    const fetch = serve('/api/rounds/close', 'POST', () => Promise.resolve(new Response('{}')))
    const { rerender } = shown()
    await userEvent.click(button('Close round'))
    rerender({ current: { name: 'Q4 2026', opened_at: '2026-11-10T08:00:00Z' }, comparison: null })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(document.activeElement).toBe(button('Close round'))
    expect(fetch).not.toHaveBeenCalled()
  })
})
