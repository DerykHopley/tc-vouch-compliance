import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import type { DeveloperSettings } from './developer.ts'
import DeveloperPage from './DeveloperPage.tsx'

const NO_RUNS = { assessments: 0, input_tokens: 0, output_tokens: 0, reported_cost_usd: 0, estimated_cost_usd: 0, incomplete: 0 }

const SETTINGS: DeveloperSettings = {
  model: 'anthropic/claude-haiku-5.5',
  runs_on: 'openrouter',
  fake_reason: null,
  prompt_version: '1',
  usage_url: 'https://openrouter.ai/activity',
  kept_in: 'database',
  daily_limit: 30,
  usage: { openrouter: NO_RUNS, fake: NO_RUNS },
}

/** The API as a developer (or, given 403, a compliance lead) sees it; assessments never answer. */
function api(settingsStatus = 200, put?: (body: Record<string, unknown>) => [number, unknown], refusal?: unknown) {
  const body = settingsStatus === 200 ? SETTINGS : (refusal ?? { detail: 'This is available to developers only.' })
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/developer/settings')
      return Promise.resolve(new Response(JSON.stringify(body), { status: settingsStatus }))
    if ((url === '/api/developer/model' || url === '/api/developer/daily-limit') && put) {
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

  it("totals OpenRouter's tokens and cost, reported and estimated apart, and keeps the fake's apart", () => {
    const openrouter = { ...NO_RUNS, assessments: 3, input_tokens: 12_345, output_tokens: 678, reported_cost_usd: 0.0016, estimated_cost_usd: 0.0002 }
    const fake = { ...NO_RUNS, assessments: 1, input_tokens: 900, output_tokens: 40, estimated_cost_usd: 0.0001 }
    render(<DeveloperPage settings={{ ...SETTINGS, usage: { openrouter, fake } }} onChange={() => {}} />)
    const totals = screen.getByRole('region', { name: 'Usage' })
    expect(within(totals).getByText(
      'OpenRouter: 3 assessments · 12,345 tokens in · 678 tokens out · $0.0016 reported · $0.0002 estimated',
    )).toBeTruthy()
    expect(within(totals).getByText(
      'Fake model: 1 assessment · 900 tokens in · 40 tokens out · $0.0001 estimated, not spent',
    )).toBeTruthy()
    expect(within(totals).queryByText(/incomplete/)).toBeNull()
  })

  it('says how many assessments have an incomplete cost, and shows no fake line without fake runs', () => {
    const openrouter = { ...NO_RUNS, assessments: 2, input_tokens: 10, output_tokens: 2, reported_cost_usd: 0.5, incomplete: 1 }
    render(<DeveloperPage settings={{ ...SETTINGS, usage: { openrouter, fake: NO_RUNS } }} onChange={() => {}} />)
    expect(screen.getByText("1 of them has an incomplete cost: some calls' cost could not be known.")).toBeTruthy()
    expect(screen.queryByText(/Fake model:/)).toBeNull()
  })

  it('shows the daily limit, changes it, and says the new limit applies', async () => {
    const put = vi.fn((body: Record<string, unknown>) => [200, { ...SETTINGS, daily_limit: body.daily_limit }] as [number, unknown])
    api(200, put)
    const onChange = vi.fn()
    render(<DeveloperPage settings={SETTINGS} onChange={onChange} />)
    expect(screen.getByText('30 assessments per user per day (UTC)')).toBeTruthy()
    const field = screen.getByRole('textbox', { name: 'Assessments per day' })
    await userEvent.clear(field)
    await userEvent.type(field, '5')
    await userEvent.click(screen.getByRole('button', { name: 'Change limit' }))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...SETTINGS, daily_limit: 5 }))
    expect(put).toHaveBeenCalledWith({ daily_limit: 5 })
    expect(screen.getByRole('status').textContent).toBe('Each user may now start 5 assessments a day.')
  })

  it('shows a refused daily limit in place', async () => {
    api(200, () => [422, { detail: [{ msg: 'Input should be greater than or equal to 1' }] }])
    render(<DeveloperPage settings={SETTINGS} onChange={() => {}} />)
    const field = screen.getByRole('textbox', { name: 'Assessments per day' })
    await userEvent.clear(field)
    await userEvent.type(field, '0')
    await userEvent.click(screen.getByRole('button', { name: 'Change limit' }))
    expect((await screen.findByRole('alert')).textContent).toBe(
      'The daily limit is a whole number of assessments, from 1 to 10,000.',
    )
    expect(field.getAttribute('aria-invalid')).toBe('true')
  })

  it('says when no key makes assessments run on the fake model, and when the choice lasts only until a restart', () => {
    render(<DeveloperPage settings={{ ...SETTINGS, runs_on: 'fake', fake_reason: 'no_key', kept_in: 'memory' }}
      onChange={() => {}} />)
    expect(screen.getByText('No OpenRouter key is set: assessments run on the fake model.')).toBeTruthy()
    expect(screen.getByText(/until the server restarts/i)).toBeTruthy()
  })

  it('says a fake script, not a missing key, makes assessments run on the fake model', () => {
    render(<DeveloperPage settings={{ ...SETTINGS, runs_on: 'fake', fake_reason: 'fake_script' }} onChange={() => {}} />)
    expect(screen.getByText('ASSESS_FAKE_SCRIPT is set: assessments run on the scripted fake model.')).toBeTruthy()
    expect(screen.queryByText(/no OpenRouter key/i)).toBeNull()
  })

  it('says when the settings are unavailable, with nothing else to act on', () => {
    render(<DeveloperPage settings="unavailable" onChange={() => {}} />)
    expect(within(page()!).getByRole('alert').textContent).toBe(
      'The developer settings are unavailable just now. Please try again shortly.',
    )
    expect(screen.queryByRole('textbox')).toBeNull()
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

  it("stays, with the page saying the settings are unavailable, on the database's 503", async () => {
    window.history.replaceState(null, '', '/developer')
    api(503, undefined, { detail: 'The database is unavailable just now. Please try again shortly.', unavailable: 'database' })
    render(<App />)
    expect(await screen.findByRole('link', { name: 'Developer' })).toBeTruthy()
    const region = await screen.findByRole('region', { name: 'Developer settings' })
    expect(within(region).getByRole('alert').textContent).toMatch(/unavailable just now/)
  })

  it("is hidden on the role lookup's 503: a compliance lead never sees the page", async () => {
    window.history.replaceState(null, '', '/developer')
    const fetch = api(503, undefined, { detail: 'Vouch cannot check your sign-in just now. Please try again shortly.' })
    render(<App />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/developer/settings', expect.anything()))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole('link', { name: 'Developer' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Developer settings' })).toBeNull()
  })

  it('opens straight at /developer for a developer, as after a refresh', async () => {
    window.history.replaceState(null, '', '/developer')
    api(200)
    render(<App />)
    expect(await screen.findByRole('region', { name: 'Developer settings' })).toBeTruthy()
  })
})
