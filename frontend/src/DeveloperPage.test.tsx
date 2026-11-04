import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import type { DeveloperSettings } from './developer.ts'
import DeveloperPage from './DeveloperPage.tsx'

const SETTINGS: DeveloperSettings = {
  model: 'anthropic/claude-haiku-5.5',
  runs_on: 'openrouter',
  prompt_version: '1',
  usage_url: 'https://openrouter.ai/activity',
  kept_in: 'database',
}

/** The API as a developer (or, given 403, a compliance lead) sees it; assessments never answer. */
function api(settingsStatus = 200, put?: (body: { model: string }) => [number, unknown]) {
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/developer/settings')
      return Promise.resolve(new Response(JSON.stringify(SETTINGS), { status: settingsStatus }))
    if (url === '/api/developer/model' && put) {
      const [status, body] = put(JSON.parse(init!.body as string))
      return Promise.resolve(new Response(JSON.stringify(body), { status }))
    }
    return new Promise<Response>(() => {})
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
  vi.unstubAllGlobals()
})

describe('the developer page', () => {
  const page = () => screen.queryByRole('region', { name: 'Developer settings' })

  it('shows the model, the prompt version and the usage link', () => {
    render(<DeveloperPage settings={SETTINGS} onChange={() => {}} />)
    const region = page()!
    expect(within(region).getByText('anthropic/claude-haiku-5.5')).toBeTruthy()
    expect(within(region).getByText('1')).toBeTruthy()
    const usage = within(region).getByRole('link', { name: /usage and costs/i })
    expect(usage.getAttribute('href')).toBe('https://openrouter.ai/activity')
    expect(within(region).queryByText(/fake model/i)).toBeNull()
  })

  it('says when assessments run on the fake model, and when the choice lasts only until a restart', () => {
    render(<DeveloperPage settings={{ ...SETTINGS, runs_on: 'fake', kept_in: 'memory' }} onChange={() => {}} />)
    expect(screen.getByText(/no OpenRouter key is set: assessments run on the fake model/i)).toBeTruthy()
    expect(screen.getByText(/until the server restarts/i)).toBeTruthy()
  })

  it('changes the model, and says the next assessment runs on it', async () => {
    api(200, ({ model }) => [200, { ...SETTINGS, model }])
    const onChange = vi.fn()
    render(<DeveloperPage settings={SETTINGS} onChange={onChange} />)
    const field = screen.getByRole('textbox', { name: 'Model id' })
    await userEvent.clear(field)
    await userEvent.type(field, 'openai/gpt-5.5')
    await userEvent.click(screen.getByRole('button', { name: 'Change model' }))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...SETTINGS, model: 'openai/gpt-5.5' }))
    expect(screen.getByRole('status').textContent).toBe('The next assessment runs on openai/gpt-5.5.')
  })

  it("shows the API's refusal in place, and keeps what was typed", async () => {
    const detail = 'OpenRouter has no model “x/y”. Check the id on openrouter.ai/models.'
    api(200, () => [422, { detail }])
    const onChange = vi.fn()
    render(<DeveloperPage settings={SETTINGS} onChange={onChange} />)
    const field = screen.getByRole('textbox', { name: 'Model id' })
    await userEvent.clear(field)
    await userEvent.type(field, 'x/y')
    await userEvent.click(screen.getByRole('button', { name: 'Change model' }))
    expect((await screen.findByRole('alert')).textContent).toBe(detail)
    expect((field as HTMLInputElement).value).toBe('x/y')
    expect(field.getAttribute('aria-invalid')).toBe('true')
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('the Developer link', () => {
  it('shows for a developer and opens the page at /developer', async () => {
    api(200)
    render(<App />)
    const link = await screen.findByRole('link', { name: 'Developer' })
    await userEvent.click(link)
    expect(window.location.pathname).toBe('/developer')
    expect(screen.getByRole('region', { name: 'Developer settings' })).toBeTruthy()
    expect(link.getAttribute('aria-current')).toBe('page')
  })

  it('is not there for a compliance lead (the API answers 403)', async () => {
    const fetch = api(403)
    render(<App />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/developer/settings', expect.anything()))
    expect(screen.queryByRole('link', { name: 'Developer' })).toBeNull()
  })

  it("keeps a compliance lead at /developer on the Dossier, never the page", async () => {
    window.history.replaceState(null, '', '/developer')
    const fetch = api(403)
    render(<App />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByRole('region', { name: 'Developer settings' })).toBeNull()
    expect(screen.getByRole('heading', { level: 1 }).textContent).toMatch(/prepared for audit/i)
  })

  it('opens straight at /developer for a developer, as after a refresh', async () => {
    window.history.replaceState(null, '', '/developer')
    api(200)
    render(<App />)
    expect(await screen.findByRole('region', { name: 'Developer settings' })).toBeTruthy()
  })
})
