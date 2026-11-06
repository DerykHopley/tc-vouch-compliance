import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Overview } from './overview.ts'
import ProfiledApp from './ProfiledApp.tsx'
import { fetchProfiles, type ProfileSummary, profileLabel, startingProfile } from './profiles.ts'
import ProfileSwitcher from './ProfileSwitcher.tsx'
import {
  authHeaders, rememberedProfile, rememberProfile, setOnSignedOut, setProfile, setTokenSource,
} from './session.ts'

const NORDLICHT: ProfileSummary = { id: 'nordlicht', company: 'Nordlicht Komponenten GmbH', country: 'Germany',
  answer_language: 'en' }
const TESTWERK: ProfileSummary = { id: 'testwerk', company: 'Testwerk Prüfstand GmbH', country: 'Austria',
  answer_language: 'de' }

beforeEach(() => {
  window.matchMedia = (query: string) => ({ matches: false, media: query }) as MediaQueryList
})

afterEach(() => {
  cleanup()
  setProfile(null)
  setTokenSource(null)
  setOnSignedOut(null)
  delete document.documentElement.dataset.theme
  window.history.replaceState(null, '', '/')
  window.localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('profileLabel', () => {
  it('names the company, the country and the answer language in English', () => {
    expect(profileLabel(NORDLICHT)).toBe('Nordlicht Komponenten GmbH · Germany · English')
    expect(profileLabel(TESTWERK)).toBe('Testwerk Prüfstand GmbH · Austria · German')
  })
})

describe('authHeaders', () => {
  it('names the chosen profile on every call, and none before one is chosen', async () => {
    expect(await authHeaders()).toEqual({})
    setProfile('testwerk')
    expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'testwerk' })
  })

  it('names the profile chosen when the call started, even if a switch lands while the token is fetched', async () => {
    let giveToken: (token: string) => void = () => {}
    setTokenSource(() => new Promise((resolve) => { giveToken = resolve }))
    setProfile('nordlicht')
    const headers = authHeaders()
    setProfile('testwerk')
    giveToken('t0ken')
    expect(await headers).toEqual({ Authorization: 'Bearer t0ken', 'X-Vouch-Profile': 'nordlicht' })
  })
})

describe('the remembered profile, issue 201', () => {
  it('is the one last remembered, and none before', () => {
    expect(rememberedProfile()).toBeNull()
    rememberProfile('testwerk')
    expect(rememberedProfile()).toBe('testwerk')
  })

  it('is none, without failing, where the browser keeps no storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('denied') })
    expect(() => rememberProfile('testwerk')).not.toThrow()
    expect(rememberedProfile()).toBeNull()
  })
})

describe('startingProfile', () => {
  const both = { profiles: [NORDLICHT, TESTWERK], default: 'nordlicht' }

  it('is the remembered profile while it is listed', () => {
    expect(startingProfile(both, 'testwerk')).toEqual({ id: 'testwerk', fellBack: false })
  })

  it('is the default, saying it fell back, when the remembered one is no longer listed', () => {
    expect(startingProfile(both, 'alpenwerk')).toEqual({ id: 'nordlicht', fellBack: true })
  })

  it('is the default, or the first listed, with none remembered', () => {
    expect(startingProfile(both, null)).toEqual({ id: 'nordlicht', fellBack: false })
    expect(startingProfile({ profiles: [TESTWERK], default: null }, null)).toEqual({ id: 'testwerk', fellBack: false })
  })

  it("is none without a list: the API's default answers", () => {
    expect(startingProfile(null, 'testwerk')).toEqual({ id: null, fellBack: false })
  })
})

describe('fetchProfiles', () => {
  it('reads the list, or null when it cannot', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json({ profiles: [NORDLICHT], default: 'nordlicht' })))
    expect(await fetchProfiles()).toEqual({ profiles: [NORDLICHT], default: 'nordlicht' })
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(null, { status: 500 })))
    expect(await fetchProfiles()).toBeNull()
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')))
    expect(await fetchProfiles()).toBeNull()
  })

  it('signs out on a 401, as the other API calls do', async () => {
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(null, { status: 401 })))
    expect(await fetchProfiles()).toBeNull()
    expect(signOut).toHaveBeenCalledOnce()
  })
})

describe('ProfileSwitcher', () => {
  it('opens the app only once the list has answered, under the default profile', async () => {
    let answer: (response: Response) => void = () => {}
    const fetch = vi.fn((url: string, _init?: RequestInit) => {
      if (url === '/api/profiles') return new Promise<Response>((resolve) => { answer = resolve })
      if (url === '/api/overview') return Promise.resolve(Response.json(overviewOf(NORDLICHT.company)))
      return new Promise<Response>(() => {})
    })
    vi.stubGlobal('fetch', fetch)
    render(<ProfiledApp />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/profiles', expect.anything()))
    expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/profiles'])
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()

    answer(Response.json({ profiles: [NORDLICHT, TESTWERK], default: 'nordlicht' }))

    await screen.findByText('Nordlicht Komponenten GmbH · Somewhere', { selector: '.pill' })
    expect(overviewCalls(fetch)).toEqual(['nordlicht'])
  })

  it('with one profile, shows its name and no menu', () => {
    render(<ProfileSwitcher profiles={[NORDLICHT]} selected="nordlicht" onSwitch={() => {}} />)
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.getByText('Nordlicht Komponenten GmbH · Germany · English')).toBeTruthy()
  })

  it('with two, offers each in a menu and switches to the one chosen', async () => {
    const onSwitch = vi.fn()
    render(<ProfileSwitcher profiles={[NORDLICHT, TESTWERK]} selected="nordlicht" onSwitch={onSwitch} />)
    const menu = screen.getByRole('combobox', { name: 'Profile' })
    expect(within(menu).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Nordlicht Komponenten GmbH · Germany · English', 'Testwerk Prüfstand GmbH · Austria · German'])
    expect((menu as HTMLSelectElement).value).toBe('nordlicht')
    await userEvent.selectOptions(menu, 'testwerk')
    expect(onSwitch).toHaveBeenCalledWith('testwerk')
  })
})

/** An overview with nothing assessed, for `company`. */
const overviewOf = (company: string): Overview => ({
  company, country: 'Somewhere', protection_level: 'normal', answer_language: 'en', requirements: {},
  readiness: { controls: 1, accepted: 0, by_verdict: { evidenced: 0, 'partly-evidenced': 0, 'not-evidenced': 0,
    'not-applicable': 0 }, open: 1, remediation_items: 0 },
  by_status: { 'not-assessed': 1, 'waiting-for-review': 0, accepted: 0, rejected: 0 },
  next_step: { control_id: '1.1.1', action: 'assess' },
  controls: [{ id: '1.1.1', question: 'Question?', iso_27001: 'A.5.1', status: 'not-assessed', verdict: null }],
  quotes: [],
})

/** The API with these profiles: each overview is the company of the profile the call names (the default without). */
function serve(profiles: ProfileSummary[], fallback: string | null = 'nordlicht') {
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    const named = new Headers(init?.headers).get('X-Vouch-Profile') ?? fallback
    if (url === '/api/profiles') return Promise.resolve(Response.json({ profiles, default: fallback }))
    if (url === '/api/overview') {
      const profile = profiles.find((p) => p.id === named)
      return Promise.resolve(profile ? Response.json(overviewOf(profile.company)) : new Response(null, { status: 500 }))
    }
    return new Promise<Response>(() => {})
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const overviewCalls = (fetch: ReturnType<typeof serve>) =>
  fetch.mock.calls.filter(([url]) => url === '/api/overview')
    .map(([, init]) => new Headers(init?.headers).get('X-Vouch-Profile'))

describe('ProfiledApp', () => {
  it('switching loads the chosen profile\'s overview, and keeps the theme', async () => {
    const fetch = serve([NORDLICHT, TESTWERK])
    render(<ProfiledApp />)
    await screen.findByText('Nordlicht Komponenten GmbH · Somewhere', { selector: '.pill' })
    const menu = await screen.findByRole('combobox', { name: 'Profile' })
    await userEvent.click(screen.getByRole('button', { name: 'Dark theme' }))

    await userEvent.selectOptions(menu, 'testwerk')

    await screen.findByText('Testwerk Prüfstand GmbH · Somewhere', { selector: '.pill' })
    expect(overviewCalls(fetch)).toEqual(['nordlicht', 'testwerk'])
    expect((screen.getByRole('combobox', { name: 'Profile' }) as HTMLSelectElement).value).toBe('testwerk')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(screen.getByRole('button', { name: 'Light theme' })).toBeTruthy()
  })

  it('opens the profile chosen before a reload, and remembers each switch, issue 201', async () => {
    rememberProfile('testwerk')
    const fetch = serve([NORDLICHT, TESTWERK])
    render(<ProfiledApp />)
    await screen.findByText('Testwerk Prüfstand GmbH · Somewhere', { selector: '.pill' })
    expect(overviewCalls(fetch)).toEqual(['testwerk'])
    expect(within(screen.getByRole('banner')).queryByRole('status')).toBeNull()

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Profile' }), 'nordlicht')

    await screen.findByText('Nordlicht Komponenten GmbH · Somewhere', { selector: '.pill' })
    expect(rememberedProfile()).toBe('nordlicht')
  })

  it('a remembered profile no longer listed opens the default, and the top bar says so, issue 201', async () => {
    rememberProfile('alpenwerk')
    const fetch = serve([NORDLICHT, TESTWERK])
    render(<ProfiledApp />)
    await screen.findByText('Nordlicht Komponenten GmbH · Somewhere', { selector: '.pill' })
    expect(overviewCalls(fetch)).toEqual(['nordlicht'])
    const notice = within(screen.getByRole('banner')).getByRole('status')
    expect(notice.textContent).toBe('Your last profile is no longer available: showing Nordlicht Komponenten GmbH.')
    expect(rememberedProfile()).toBeNull() // said once: the next reload opens the default quietly

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Profile' }), 'testwerk')
    await screen.findByText('Testwerk Prüfstand GmbH · Somewhere', { selector: '.pill' })
    expect(within(screen.getByRole('banner')).queryByRole('status')).toBeNull()
  })

  it('with one profile, shows its name in the top bar and no menu', async () => {
    serve([NORDLICHT])
    render(<ProfiledApp />)
    await screen.findByText('Nordlicht Komponenten GmbH · Germany · English')
    expect(within(screen.getByRole('banner')).queryByRole('combobox')).toBeNull()
  })

  it('with the default profile left out, chooses the first listed', async () => {
    const fetch = serve([TESTWERK], null)
    render(<ProfiledApp />)
    await screen.findByText('Testwerk Prüfstand GmbH · Somewhere', { selector: '.pill' })
    expect(overviewCalls(fetch)).toEqual(['testwerk'])
  })

  it('without the list, shows no switcher and the app as before', async () => {
    vi.stubGlobal('fetch', (url: string) => url === '/api/overview'
      ? Promise.resolve(Response.json(overviewOf('Nordlicht Komponenten GmbH')))
      : Promise.resolve(new Response(null, { status: 500 })))
    render(<ProfiledApp />)
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeTruthy()
    await screen.findByText('Nordlicht Komponenten GmbH · Somewhere', { selector: '.pill' })
    expect(screen.queryByRole('combobox', { name: 'Profile' })).toBeNull()
  })
})

describe('switching profile on a working paper', () => {
  const judged = [{ id: 'M1', column: 'must', text: 'Transfers are encrypted.', verdict: 'partly-evidenced',
    reason: 'Only some.', citations: [] }]
  const proposed = { control_id: '5.1.2', verdict: 'partly-evidenced', answer: 'Partly.', status: 'waiting-for-review',
    thread_id: 't1', requirements: judged }
  const paperOverview = (company: string): Overview => ({
    ...overviewOf(company),
    controls: [{ id: '5.1.2', question: 'Question 5.1.2?', iso_27001: 'A.5.14', status: 'waiting-for-review',
      verdict: 'partly-evidenced' }],
    requirements: { '5.1.2': [{ id: 'M1', column: 'must', text: 'Transfers are encrypted.', source: 'VDA ISA 2027.1',
      not_applicable: null }] },
  })

  it("drops the paper's unsaved overrides, which belonged to the other profile's assessment", async () => {
    window.history.replaceState(null, '', '/controls/5.1.2')
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
      const named = new Headers(init?.headers).get('X-Vouch-Profile')
      const company = named === 'testwerk' ? TESTWERK.company : NORDLICHT.company
      if (url === '/api/profiles') {
        return Promise.resolve(Response.json({ profiles: [NORDLICHT, TESTWERK], default: 'nordlicht' }))
      }
      if (url === '/api/overview') return Promise.resolve(Response.json(paperOverview(company)))
      if (url === '/api/assessments') return Promise.resolve(Response.json({ assessments: [proposed] }))
      if (url === '/api/assessments/t1') return Promise.resolve(Response.json({ assessment: proposed }))
      return new Promise<Response>(() => {})
    })
    render(<ProfiledApp />)
    const row = async () => (await screen.findByText('M1', { selector: 'th .mono' })).closest('tr')!
    await userEvent.click(within(await row()).getByRole('button', { name: 'Change M1' }))
    const editor = screen.getByRole('form', { name: 'Override M1' })
    await userEvent.selectOptions(within(editor).getByLabelText('Verdict'), 'evidenced')
    await userEvent.type(within(editor).getByLabelText('Reason (required)'), 'The rollout record shows it.')
    await userEvent.click(within(editor).getByRole('button', { name: 'Save' }))
    expect(within(await row()).getByText('Overridden from Partly evidenced')).toBeTruthy()

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Profile' }), 'testwerk')

    await screen.findByText(/Working paper · Testwerk/)
    expect(within(await row()).queryByText(/Overridden from/)).toBeNull()
    expect(within(await row()).queryByText('The rollout record shows it.')).toBeNull()
  })
})
