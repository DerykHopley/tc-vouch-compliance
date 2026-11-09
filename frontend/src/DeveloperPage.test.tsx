import type { ComponentProps } from 'react'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import type { DeveloperSettings } from './developer.ts'
import DeveloperPage from './DeveloperPage.tsx'
import { DEVELOPER_SETTINGS, NO_RUNS } from './developerFixtures.ts'
import { setOnSignedOut } from './session.ts'

const SETTINGS: DeveloperSettings = DEVELOPER_SETTINGS

/** The developer page with SETTINGS and no-op handlers, but for those given. */
function renderPage(props: Partial<ComponentProps<typeof DeveloperPage>> = {}) {
  return render(<DeveloperPage settings={SETTINGS} onChange={() => {}} onSetup={() => {}} {...props} />)
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
  vi.restoreAllMocks()
  setOnSignedOut(null)
})

describe('the developer page', () => {
  const page = () => screen.queryByRole('region', { name: 'Developer settings' })

  it('shows the model, the prompt version and the usage link', () => {
    renderPage()
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
    renderPage({ settings: { ...SETTINGS, usage: { openrouter, fake } } })
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
    renderPage({ settings: { ...SETTINGS, usage: { openrouter, fake: NO_RUNS } } })
    expect(screen.getByText("1 of them has an incomplete cost: some calls' cost could not be known.")).toBeTruthy()
    expect(screen.queryByText(/Fake model:/)).toBeNull()
  })

  it('shows the daily limit, changes it, and says the new limit applies', async () => {
    const put = vi.fn((body: Record<string, unknown>) => [200, { ...SETTINGS, daily_limit: body.daily_limit }] as [number, unknown])
    api(200, put)
    const onChange = vi.fn()
    renderPage({ onChange })
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
    renderPage()
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
    renderPage({ settings: { ...SETTINGS, runs_on: 'fake', fake_reason: 'no_key', kept_in: 'memory' } })
    expect(screen.getByText('No OpenRouter key is set: assessments run on the fake model.')).toBeTruthy()
    expect(screen.getByText(/until the server restarts/i)).toBeTruthy()
  })

  it('names each compliance profile left out because it could not be loaded, with its problem (#47)', () => {
    renderPage()
    expect(screen.queryByRole('region', { name: 'Profiles left out' })).toBeNull()
    cleanup()
    const broken_profiles = [{ id: 'broken', problem: 'not valid YAML (ParserError)' }]
    renderPage({ settings: { ...SETTINGS, broken_profiles } })
    const note = screen.getByRole('region', { name: 'Profiles left out' })
    expect(within(note).getByText('broken')).toBeTruthy()
    expect(within(note).getByText('not valid YAML (ParserError)')).toBeTruthy()
  })

  it('says a fake script, not a missing key, makes assessments run on the fake model', () => {
    renderPage({ settings: { ...SETTINGS, runs_on: 'fake', fake_reason: 'fake_script' } })
    expect(screen.getByText('ASSESS_FAKE_SCRIPT is set: assessments run on the scripted fake model.')).toBeTruthy()
    expect(screen.queryByText(/no OpenRouter key/i)).toBeNull()
  })

  it('says when the settings are unavailable, with nothing else to act on', () => {
    renderPage({ settings: 'unavailable' })
    expect(within(page()!).getByRole('alert').textContent).toBe(
      'The developer settings are unavailable just now. Please try again shortly.',
    )
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Set up a company' })).toBeNull()
  })

  it('opens company setup from its sheet', async () => {
    const onSetup = vi.fn()
    renderPage({ onSetup })
    const sheet = screen.getByRole('region', { name: 'Company setup' })
    await userEvent.click(within(sheet).getByRole('button', { name: 'Set up a company' }))
    expect(onSetup).toHaveBeenCalledOnce()
  })

  it('changes the model, and says the next assessment runs on it', async () => {
    api(200, ({ model }) => [200, { ...SETTINGS, model }])
    const onChange = vi.fn()
    renderPage({ onChange })
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
    renderPage({ onChange })
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

describe('the companies made in setup, issue 418', () => {
  const MUSTERFIRMA = { id: 'musterfirma-sud-gmbh', company: 'Musterfirma Süd GmbH', created_at: '2026-11-09T10:15:00+00:00' }
  const WITH_ONE: DeveloperSettings = { ...SETTINGS, companies: [MUSTERFIRMA] }
  const QUESTION = 'Remove Musterfirma Süd GmbH? It leaves the profile switcher for everyone and can\'t be opened again. '
    + 'Its audit trail is kept.'

  /** The API answering the DELETE with `status` and `body`, or held until the returned function answers it. */
  function deleting(status?: number, body: unknown = WITH_ONE) {
    let release: (answer: Response) => void = () => {}
    const fetch = vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method !== 'DELETE') return new Promise<Response>(() => {})
      if (status === undefined) return new Promise<Response>((resolve) => { release = resolve })
      return Promise.resolve(new Response(JSON.stringify(body), { status }))
    })
    vi.stubGlobal('fetch', fetch)
    return { fetch, release: (answer: Response) => release(answer) }
  }

  const sheet = () => screen.getByRole('region', { name: 'Company setup' })
  const removeButton = () => within(sheet()).getByRole('button', { name: 'Remove Musterfirma Süd GmbH' })

  it('lists each company with its id and the date it was made, under Set up a company', () => {
    renderPage({ settings: WITH_ONE })
    const item = within(sheet()).getByRole('listitem')
    expect(within(item).getByText('Musterfirma Süd GmbH')).toBeTruthy()
    expect(within(item).getByText('musterfirma-sud-gmbh').className).toContain('mono')
    expect(item.querySelector('time')?.getAttribute('dateTime')).toBe('2026-11-09T10:15:00+00:00')
    expect(removeButton().className).toContain('btn')
  })

  it('says when none was made, and when they cannot be read', () => {
    renderPage()
    expect(within(sheet()).getByText('No companies made in setup yet.')).toBeTruthy()
    cleanup()
    renderPage({ settings: { ...SETTINGS, companies: null } })
    expect(within(sheet()).getByText("The companies made in setup can't be read just now.")).toBeTruthy()
  })

  it('asks first, and Cancel sends nothing and keeps the company listed', async () => {
    const { fetch } = deleting(200)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage({ settings: WITH_ONE })
    await userEvent.click(removeButton())
    expect(confirm).toHaveBeenCalledWith(QUESTION)
    expect(fetch).not.toHaveBeenCalled()
    expect(removeButton()).toBeTruthy()
  })

  it('removes once on a double press, saying Removing… while the request is out', async () => {
    const { fetch, release } = deleting()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onChange = vi.fn()
    const onRemoved = vi.fn()
    renderPage({ settings: WITH_ONE, onChange, onRemoved })
    await userEvent.dblClick(removeButton())
    const busy = within(sheet()).getByRole('button', { name: 'Removing…' })
    expect((busy as HTMLButtonElement).disabled).toBe(true)
    expect(fetch).toHaveBeenCalledOnce()
    release(new Response(JSON.stringify(SETTINGS), { status: 200 }))
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith('musterfirma-sud-gmbh'))
    expect(onChange).toHaveBeenCalledWith(SETTINGS)
    expect(within(sheet()).getByRole('status').textContent).toBe('Removed Musterfirma Süd GmbH.')
  })

  it.each([
    [409, { detail: 'Only a company made in company setup can be removed.' },
      'Vouch could not remove this company: Only a company made in company setup can be removed.'],
    [503, { detail: 'The database is unavailable just now.', unavailable: 'database' },
      'The database is unavailable just now. Nothing was removed; try again in a moment.'],
    [500, { detail: 'Internal Server Error' }, 'The company could not be removed. Nothing changed.'],
  ])('on a %i, says why under the list, keeps it, and Remove can be pressed again', async (status, body, message) => {
    deleting(status, body)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onRemoved = vi.fn()
    renderPage({ settings: WITH_ONE, onRemoved })
    await userEvent.click(removeButton())
    expect((await within(sheet()).findByRole('alert')).textContent).toBe(message)
    expect((removeButton() as HTMLButtonElement).disabled).toBe(false)
    expect(onRemoved).not.toHaveBeenCalled()
  })

  it('on a 404, removed already elsewhere: reads the settings again, says so, and has the list read again', async () => {
    const fetch = vi.fn((url: string, init?: RequestInit) => init?.method === 'DELETE'
      ? Promise.resolve(Response.json({ detail: 'There is no such compliance profile.' }, { status: 404 }))
      : url === '/api/developer/settings' ? Promise.resolve(Response.json(SETTINGS))
      : new Promise<Response>(() => {}))
    vi.stubGlobal('fetch', fetch)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onChange = vi.fn()
    const onRemoved = vi.fn()
    renderPage({ settings: WITH_ONE, onChange, onRemoved })
    await userEvent.click(removeButton())
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(SETTINGS))
    expect(onRemoved).toHaveBeenCalledWith('musterfirma-sud-gmbh')
    expect(within(sheet()).getByRole('status').textContent).toBe('Musterfirma Süd GmbH was already removed.')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it("on a 404 whose settings can't be read again, drops the company from the list held", async () => {
    vi.stubGlobal('fetch', vi.fn((_url: string, init?: RequestInit) => Promise.resolve(init?.method === 'DELETE'
      ? Response.json({ detail: 'There is no such compliance profile.' }, { status: 404 })
      : new Response(null, { status: 500 }))))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onChange = vi.fn()
    renderPage({ settings: WITH_ONE, onChange })
    await userEvent.click(removeButton())
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...WITH_ONE, companies: [] }))
  })

  it("on a 200 whose settings couldn't be read after, says removed and drops it from the list held", async () => {
    deleting(200, { settings_unavailable: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onChange = vi.fn()
    const onRemoved = vi.fn()
    renderPage({ settings: WITH_ONE, onChange, onRemoved })
    await userEvent.click(removeButton())
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith('musterfirma-sud-gmbh'))
    expect(onChange).toHaveBeenCalledWith({ ...WITH_ONE, companies: [] })
    expect(within(sheet()).getByRole('status').textContent).toBe('Removed Musterfirma Süd GmbH.')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('opened afresh after removing the company shown, says what was removed, once', () => {
    const onRemovedShown = vi.fn()
    renderPage({ removedCompany: 'Musterfirma Süd GmbH', onRemovedShown })
    expect(within(sheet()).getByRole('status').textContent).toBe('Removed Musterfirma Süd GmbH.')
    expect(onRemovedShown).toHaveBeenCalledOnce()
  })

  it('on a 401, signs out with no alert', async () => {
    deleting(401, { detail: 'Sign in to use Vouch.' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const signedOut = vi.fn()
    setOnSignedOut(signedOut)
    renderPage({ settings: WITH_ONE })
    await userEvent.click(removeButton())
    await waitFor(() => expect(signedOut).toHaveBeenCalledOnce())
    expect(screen.queryByRole('alert')).toBeNull()
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
    expect(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page')
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

  it("shows the unavailable notice, not company setup, at /developer/setup on the database's 503", async () => {
    window.history.replaceState(null, '', '/developer/setup')
    api(503, undefined, { detail: 'The database is unavailable just now. Please try again shortly.', unavailable: 'database' })
    render(<App />)
    const region = await screen.findByRole('region', { name: 'Developer settings' })
    expect(within(region).getByRole('alert').textContent).toMatch(/unavailable just now/)
    expect(screen.queryByRole('region', { name: 'Company setup' })).toBeNull()
  })

  it('opens straight at /developer for a developer, as after a refresh', async () => {
    window.history.replaceState(null, '', '/developer')
    api(200)
    render(<App />)
    expect(await screen.findByRole('region', { name: 'Developer settings' })).toBeTruthy()
  })
})
