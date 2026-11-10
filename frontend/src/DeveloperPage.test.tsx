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
/** Musterfirma, made in setup, as GET /api/profiles lists it. */
const LISTED_MUSTERFIRMA = { id: 'musterfirma-sud-gmbh', company: 'Musterfirma Süd GmbH', country: 'Germany',
  answer_language: 'en' }

/** The Companies section's row naming `company`. */
function rowOf(company: string): HTMLElement {
  const sheet = screen.getByRole('region', { name: 'Companies' })
  return within(sheet).getAllByRole('listitem').find((li) => within(li).queryByText(company, { selector: '.company-name' }))!
}

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
    expect(screen.getByText(/until the server restarts/i).textContent).toContain('the current company') // #480
  })

  it('names each compliance profile left out because it could not be loaded, with its problem (#47)', () => {
    renderPage()
    expect(screen.queryByRole('region', { name: 'Profiles left out' })).toBeNull()
    cleanup()
    const broken_profiles = [{ id: 'broken', problem: 'not valid YAML (ParserError)' }]
    renderPage({ settings: { ...SETTINGS, broken_profiles } })
    const note = screen.getByRole('region', { name: 'Profiles left out' })
    expect(within(note).getByText(
      'These compliance profiles could not be loaded, so the Companies list leaves them out and none can be made current:',
    )).toBeTruthy()
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

  it('opens company setup from the Companies section', async () => {
    const onSetup = vi.fn()
    renderPage({ onSetup })
    const sheet = screen.getByRole('region', { name: 'Companies' })
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
  const WITH_ONE: DeveloperSettings = { ...SETTINGS, companies: [MUSTERFIRMA],
    listed_companies: [...SETTINGS.listed_companies, LISTED_MUSTERFIRMA] }
  const QUESTION = 'Remove Musterfirma Süd GmbH? It leaves the Companies list for everyone and can\'t be opened or '
    + 'made current again. Its audit trail is kept.'

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

  const sheet = () => screen.getByRole('region', { name: 'Companies' })
  const removeButton = () => within(sheet()).getByRole('button', { name: 'Remove Musterfirma Süd GmbH' })

  it('lists each company made in setup with its id and the date it was made', () => {
    renderPage({ settings: WITH_ONE })
    const item = rowOf('Musterfirma Süd GmbH')
    expect(within(item).getByText('musterfirma-sud-gmbh').className).toContain('mono')
    expect(item.querySelector('time')?.getAttribute('dateTime')).toBe('2026-11-09T10:15:00+00:00')
    expect(removeButton().className).toContain('btn')
  })

  it('says when they cannot be read, and still lists the file companies', () => {
    renderPage({ settings: { ...SETTINGS, companies: null } })
    expect(within(sheet()).getByText("The companies made in setup can't be read just now.")).toBeTruthy()
    expect(within(sheet()).getAllByRole('listitem')).toHaveLength(2)
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
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith('musterfirma-sud-gmbh', 'Removed Musterfirma Süd GmbH.'))
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
    expect(onRemoved).toHaveBeenCalledWith('musterfirma-sud-gmbh', 'Musterfirma Süd GmbH was already removed.')
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
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...WITH_ONE, companies: [], listed_companies: SETTINGS.listed_companies }))
  })

  it("on a 200 whose settings couldn't be read after, says removed and drops it from the list held", async () => {
    deleting(200, { settings_unavailable: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onChange = vi.fn()
    const onRemoved = vi.fn()
    renderPage({ settings: WITH_ONE, onChange, onRemoved })
    await userEvent.click(removeButton())
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith('musterfirma-sud-gmbh', 'Removed Musterfirma Süd GmbH.'))
    expect(onChange).toHaveBeenCalledWith({ ...WITH_ONE, companies: [], listed_companies: SETTINGS.listed_companies })
    expect(within(sheet()).getByRole('status').textContent).toBe('Removed Musterfirma Süd GmbH.')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('opened afresh with a note for it, says it, once', () => {
    const onNoteShown = vi.fn()
    renderPage({ developerNote: 'Removed Musterfirma Süd GmbH.', onNoteShown })
    expect(within(sheet()).getByRole('status').textContent).toBe('Removed Musterfirma Süd GmbH.')
    expect(onNoteShown).toHaveBeenCalledOnce()
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

  it('removing the current company says the company now current, the one the API answers (issue 480, decision 8)',
    async () => {
      const current = { ...WITH_ONE, current_company: 'musterfirma-sud-gmbh' }
      deleting(200, { ...SETTINGS }) // Nordlicht current again
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const onRemoved = vi.fn()
      renderPage({ settings: current, onRemoved })
      await userEvent.click(removeButton())
      const note = 'Removed Musterfirma Süd GmbH. Nordlicht Komponenten GmbH is now the current company.'
      await waitFor(() => expect(onRemoved).toHaveBeenCalledWith('musterfirma-sud-gmbh', note))
      expect(within(sheet()).getByRole('status').textContent).toBe(note)
    })

  /** The API answering the DELETE with `removal`, the settings read again with `again` (null: a 500), and the profile
   * list with `current` the current company (null: a 500). */
  function serving(removal: Response, again: DeveloperSettings | null, current: string | null) {
    const listed = [...SETTINGS.listed_companies, { id: 'alpenwerk-2', company: 'Alpenwerk Zwei GmbH',
      country: 'Austria', answer_language: 'de' }]
    const fetch = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') return Promise.resolve(removal)
      if (url === '/api/developer/settings')
        return Promise.resolve(again ? Response.json(again) : new Response(null, { status: 500 }))
      if (url === '/api/profiles') return Promise.resolve(current
        ? Response.json({ profiles: listed, default: 'nordlicht', current }) : new Response(null, { status: 500 }))
      return new Promise<Response>(() => {})
    })
    vi.stubGlobal('fetch', fetch)
    return fetch
  }

  it("removing the current company, the settings unreadable after, names the API's current company (issue 480)",
    async () => {
      const current = { ...WITH_ONE, current_company: 'musterfirma-sud-gmbh' }
      // The API's answer, not a rule copied here: whatever company it says is current.
      serving(Response.json({ settings_unavailable: true }), null, 'alpenwerk-2')
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      renderPage({ settings: current })
      await userEvent.click(removeButton())
      expect((await within(sheet()).findByRole('status')).textContent)
        .toBe('Removed Musterfirma Süd GmbH. Alpenwerk Zwei GmbH is now the current company.')
    })

  it("removing the current company, nothing readable after, says only that it was removed", async () => {
    serving(Response.json({ settings_unavailable: true }), null, null)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPage({ settings: { ...WITH_ONE, current_company: 'musterfirma-sud-gmbh' } })
    await userEvent.click(removeButton())
    expect((await within(sheet()).findByRole('status')).textContent).toBe('Removed Musterfirma Süd GmbH.')
  })

  it('removed elsewhere already (404) while current: the note names the company now current (decision 8)',
    async () => {
      serving(Response.json({ detail: 'There is no such compliance profile.' }, { status: 404 }), SETTINGS, 'nordlicht')
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const onRemoved = vi.fn()
      renderPage({ settings: { ...WITH_ONE, current_company: 'musterfirma-sud-gmbh' }, onRemoved })
      await userEvent.click(removeButton())
      const note = 'Musterfirma Süd GmbH was already removed. Nordlicht Komponenten GmbH is now the current company.'
      await waitFor(() => expect(onRemoved).toHaveBeenCalledWith('musterfirma-sud-gmbh', note))
      expect(within(sheet()).getByRole('status').textContent).toBe(note)
    })

  it('a removal after the current company changed elsewhere names the company now current', async () => {
    // Held: Nordlicht current; meanwhile another developer made Alpenwerk current.
    deleting(200, { ...SETTINGS, current_company: 'alpenwerk' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPage({ settings: WITH_ONE })
    await userEvent.click(removeButton())
    expect((await within(sheet()).findByRole('status')).textContent)
      .toBe('Removed Musterfirma Süd GmbH. Alpenwerk Präzision GmbH is now the current company.')
  })
})

describe('the Companies section, issue 480', () => {
  const MADE = { id: 'musterfirma-sud-gmbh', company: 'Musterfirma Süd GmbH', created_at: '2026-11-09T10:15:00+00:00' }
  const WITH_ONE: DeveloperSettings = { ...SETTINGS, companies: [MADE],
    listed_companies: [...SETTINGS.listed_companies, LISTED_MUSTERFIRMA] }
  const sheet = () => screen.getByRole('region', { name: 'Companies' })
  const buttonsOf = (company: string) => within(rowOf(company)).queryAllByRole('button').map((b) => b.textContent)
  const makeButton = (company: string) => within(sheet()).getByRole('button', { name: `Make ${company} current` })

  /** The API answering the PUT with `status` and `body` (held until the returned function answers, when none), and
   * the settings read again with `again`. */
  function putting(status?: number, body: unknown = WITH_ONE, again: DeveloperSettings = WITH_ONE) {
    let release: (answer: Response) => void = () => {}
    const fetch = vi.fn((url: string, init?: RequestInit) => {
      if (url === '/api/developer/settings') return Promise.resolve(Response.json(again))
      if (url !== '/api/developer/current-company' || init?.method !== 'PUT') return new Promise<Response>(() => {})
      if (status === undefined) return new Promise<Response>((resolve) => { release = resolve })
      return Promise.resolve(new Response(JSON.stringify(body), { status }))
    })
    vi.stubGlobal('fetch', fetch)
    return { fetch, release: (answer: Response) => release(answer) }
  }
  const puts = (fetch: ReturnType<typeof putting>['fetch']) =>
    fetch.mock.calls.filter(([url]) => url === '/api/developer/current-company')

  it('lists every listed company, the current one marked Current with no button, the others Make current, and Remove '
    + 'only on those made in setup, under Set up a company', () => {
    renderPage({ settings: WITH_ONE })
    const label = within(sheet()).getByText('Companies', { selector: '.lbl' })
    const setUp = within(sheet()).getByRole('button', { name: 'Set up a company' })
    const list = within(sheet()).getByRole('list')
    expect(label.compareDocumentPosition(setUp) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(setUp.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(list).getAllByRole('listitem').map((li) => li.querySelector('.company-name')?.textContent))
      .toEqual(['Alpenwerk Präzision GmbH', 'Nordlicht Komponenten GmbH', 'Musterfirma Süd GmbH'])
    expect(within(rowOf('Alpenwerk Präzision GmbH')).getByText('alpenwerk').className).toContain('mono')
    expect(buttonsOf('Alpenwerk Präzision GmbH')).toEqual(['Make current'])
    expect(buttonsOf('Nordlicht Komponenten GmbH')).toEqual([])
    expect(within(rowOf('Nordlicht Komponenten GmbH')).getByText('Current')).toBeTruthy()
    expect(buttonsOf('Musterfirma Süd GmbH')).toEqual(['Make current', 'Remove'])
    expect(rowOf('Alpenwerk Präzision GmbH').querySelector('time')).toBeNull()
  })

  it.each([
    ['broken', { id: 'musterfirma-sud-gmbh', problem: 'chosen document retired-policy is not in the library' }],
    ['shadowed', { id: 'nordlicht', problem: "its id is now a file profile's" }],
  ])('a company made in setup that is %s is listed with Remove only', (_, left) => {
    const made = { ...MADE, id: left.id }
    renderPage({ settings: { ...SETTINGS, companies: [made], broken_profiles: [left] } })
    const rows = within(sheet()).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    const last = rows[2]
    expect(within(last).getByText('Musterfirma Süd GmbH')).toBeTruthy()
    expect(within(last).getAllByRole('button').map((b) => b.textContent)).toEqual(['Remove'])
    expect(within(last).queryByText('Current')).toBeNull()
  })

  it('Make current pressed twice quickly sends one request, then opens the app afresh under it with the note',
    async () => {
      const made = { ...WITH_ONE, current_company: 'alpenwerk' }
      const { fetch, release } = putting()
      const onChange = vi.fn()
      const onMadeCurrent = vi.fn()
      renderPage({ settings: WITH_ONE, onChange, onMadeCurrent })
      await userEvent.dblClick(makeButton('Alpenwerk Präzision GmbH'))
      const busy = within(sheet()).getByRole('button', { name: 'Making current…' })
      expect((busy as HTMLButtonElement).disabled).toBe(true)
      expect((within(sheet()).getByRole('button', { name: 'Remove Musterfirma Süd GmbH' }) as HTMLButtonElement).disabled)
        .toBe(true)
      expect(puts(fetch)).toHaveLength(1)
      expect(JSON.parse(String(puts(fetch)[0][1]?.body))).toEqual({ company: 'alpenwerk' })
      release(Response.json(made))
      const note = 'Alpenwerk Präzision GmbH is now the current company.'
      await waitFor(() => expect(onMadeCurrent).toHaveBeenCalledWith('alpenwerk', note))
      expect(onChange).toHaveBeenCalledWith(made)
      expect(within(sheet()).getByRole('status').textContent).toBe(note)
      expect(puts(fetch)).toHaveLength(1)
    })

  it("refused (404): reads the settings again and says the API's reason", async () => {
    const again = { ...SETTINGS } // Musterfirma removed meanwhile
    putting(404, { detail: 'There is no such compliance profile.' }, again)
    const onChange = vi.fn()
    const onMadeCurrent = vi.fn()
    renderPage({ settings: WITH_ONE, onChange, onMadeCurrent })
    await userEvent.click(makeButton('Musterfirma Süd GmbH'))
    expect((await within(sheet()).findByRole('alert')).textContent)
      .toBe('Vouch could not make this company current: There is no such compliance profile.')
    expect(onChange).toHaveBeenCalledWith(again)
    expect(onMadeCurrent).not.toHaveBeenCalled()
  })

  it("a 200 whose answer can't be read is still a change: reads the settings again, never 'Nothing changed'",
    async () => {
      const again = { ...WITH_ONE, current_company: 'alpenwerk' }
      const { fetch } = putting(undefined, WITH_ONE, again)
      const answered = fetch.getMockImplementation()!
      fetch.mockImplementation((url: string, init?: RequestInit) => url === '/api/developer/current-company'
        ? Promise.resolve(new Response('<html>proxy page</html>', { status: 200 })) : answered(url, init))
      const onChange = vi.fn()
      const onMadeCurrent = vi.fn()
      renderPage({ settings: WITH_ONE, onChange, onMadeCurrent })
      await userEvent.click(makeButton('Alpenwerk Präzision GmbH'))
      const note = 'Alpenwerk Präzision GmbH is now the current company.'
      await waitFor(() => expect(onMadeCurrent).toHaveBeenCalledWith('alpenwerk', note))
      expect(onChange).toHaveBeenCalledWith(again)
      expect(within(sheet()).getByRole('status').textContent).toBe(note)
      expect(screen.queryByRole('alert')).toBeNull()
    })

  it('with the database down, says nothing changed, and Make current can be pressed again', async () => {
    putting(503, { detail: 'The database is unavailable just now.', unavailable: 'database' })
    const onMadeCurrent = vi.fn()
    renderPage({ settings: WITH_ONE, onMadeCurrent })
    await userEvent.click(makeButton('Alpenwerk Präzision GmbH'))
    expect((await within(sheet()).findByRole('alert')).textContent)
      .toBe('The database is unavailable just now. Nothing changed; try again in a moment.')
    expect((makeButton('Alpenwerk Präzision GmbH') as HTMLButtonElement).disabled).toBe(false)
    expect(onMadeCurrent).not.toHaveBeenCalled()
  })

  it('on a 401, signs out with no alert', async () => {
    putting(401, { detail: 'Sign in to use Vouch.' })
    const signedOut = vi.fn()
    setOnSignedOut(signedOut)
    renderPage({ settings: WITH_ONE })
    await userEvent.click(makeButton('Alpenwerk Präzision GmbH'))
    await waitFor(() => expect(signedOut).toHaveBeenCalledOnce())
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

/** The Main tabs' names. */
const tabNames = () =>
  within(screen.getByRole('navigation', { name: 'Main' })).getAllByRole('link').map((l) => l.textContent)

/** Opens the user menu (local mode) and gives its items' words. */
async function menuItems() {
  await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
  return screen.getAllByRole('menuitem').map((item) => item.textContent)
}

describe('the developer page in the user menu (ticket 495, M9 decision 4)', () => {
  it('shows for a developer, beside no Developer or Documents tab, and opens the page at /developer', async () => {
    api(200)
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Your menu' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Developer page' }))
    expect(window.location.pathname).toBe('/developer')
    expect(screen.getByRole('region', { name: 'Developer settings' })).toBeTruthy()
    expect(tabNames()).toEqual(['Overview', 'Report', 'Company'])
    expect(screen.queryByRole('link', { name: 'Developer' })).toBeNull()
    for (const name of tabNames()) {
      const tab = within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: name! })
      expect(tab.getAttribute('aria-current')).toBeNull()
    }
  })

  it('opens company setup at /developer/setup from Set up a company', async () => {
    api(200)
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Your menu' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Set up a company' }))
    expect(window.location.pathname).toBe('/developer/setup')
    expect(screen.getByRole('region', { name: 'Company setup' })).toBeTruthy()
  })

  it('is not there for a compliance lead (the API answers 403)', async () => {
    const fetch = api(403)
    render(<App />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/developer/settings', expect.anything()))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(await menuItems()).toEqual(['Dark theme'])
    expect(tabNames()).toEqual(['Overview', 'Report', 'Company'])
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
    const region = await screen.findByRole('region', { name: 'Developer settings' })
    expect(within(region).getByRole('alert').textContent).toMatch(/unavailable just now/)
  })

  it("is hidden on the role lookup's 503: a compliance lead never sees the page", async () => {
    window.history.replaceState(null, '', '/developer')
    const fetch = api(503, undefined, { detail: 'Vouch cannot check your sign-in just now. Please try again shortly.' })
    render(<App />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/developer/settings', expect.anything()))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole('region', { name: 'Developer settings' })).toBeNull()
    expect(await menuItems()).toEqual(['Dark theme'])
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
