import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Overview } from './overview.ts'
import ProfiledApp from './ProfiledApp.tsx'

// Company setup stands in as one button that reports the company made (#409): its steps are tested on their own.
vi.mock('./CompanySetupWizard.tsx', () => ({
  default: ({ onCreated }: { onCreated: (profile: ProfileSummary) => void }) => (
    <button type="button" onClick={() => onCreated({ id: 'musterfirma-gmbh', company: 'Musterfirma GmbH',
      country: 'Germany', answer_language: 'de' })}>Created</button>
  ),
}))
import {
  fetchProfiles, type ProfileSummary, profileLabel, startAfresh, startingProfile, withProfile,
} from './profiles.ts'
import ProfileSwitcher from './ProfileSwitcher.tsx'
import {
  authHeaders, rememberedProfile, rememberProfile, setOnSignedOut, setProfile, setTokenSource,
} from './session.ts'

const NORDLICHT: ProfileSummary = { id: 'nordlicht', company: 'Nordlicht Komponenten GmbH', country: 'Germany',
  answer_language: 'en' }
const MUSTERFIRMA: ProfileSummary = { id: 'musterfirma-gmbh', company: 'Musterfirma GmbH', country: 'Germany',
  answer_language: 'de' }
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
    expect(startingProfile(both, '')).toEqual({ id: 'nordlicht', fellBack: false })
    expect(startingProfile({ profiles: [TESTWERK], default: null }, null)).toEqual({ id: 'testwerk', fellBack: false })
  })

  it("is none without a list: the API's default answers", () => {
    expect(startingProfile(null, 'testwerk')).toEqual({ id: null, fellBack: false })
  })
})

describe('withProfile, issue 409', () => {
  it('adds the profile to the list held, unless it is listed already', () => {
    const held = { profiles: [NORDLICHT], default: 'nordlicht' }
    expect(withProfile(held, MUSTERFIRMA)).toEqual({ profiles: [NORDLICHT, MUSTERFIRMA], default: 'nordlicht' })
    expect(withProfile({ profiles: [NORDLICHT, MUSTERFIRMA], default: 'nordlicht' }, MUSTERFIRMA))
      .toEqual({ profiles: [NORDLICHT, MUSTERFIRMA], default: 'nordlicht' })
  })

  it('with no list held, is a list of that profile alone, with no default', () => {
    expect(withProfile(null, MUSTERFIRMA)).toEqual({ profiles: [MUSTERFIRMA], default: null })
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

describe('startAfresh, issue 285', () => {
  it('says how the fresh start went: started, running (409), or failed', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json({ fresh_start: '2026-11-07T10:00:00+00:00' })))
    expect(await startAfresh()).toBe('started')
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(null, { status: 409 })))
    expect(await startAfresh()).toBe('running')
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(null, { status: 503 })))
    expect(await startAfresh()).toBe('failed')
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')))
    expect(await startAfresh()).toBe('failed')
  })

  it('signs out on a 401 and says so, not that it failed', async () => {
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(null, { status: 401 })))
    expect(await startAfresh()).toBe('signed-out')
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

  it('with one profile, shows its name and tag and no menu', () => {
    render(<ProfileSwitcher profiles={[NORDLICHT]} selected="nordlicht" onSwitch={() => {}} />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByLabelText('Profile: Nordlicht Komponenten GmbH · Germany · English').textContent)
      .toBe('Nordlicht Komponenten GmbH' + 'DE · EN')
  })

  it('with two, is a dropdown button naming the company and its tag, and switches to the one chosen (issue 222)',
    async () => {
      const onSwitch = vi.fn()
      render(<ProfileSwitcher profiles={[NORDLICHT, TESTWERK]} selected="nordlicht" onSwitch={onSwitch} />)
      const button = screen.getByRole('button', { name: 'Profile: Nordlicht Komponenten GmbH · Germany · English' })
      expect(button.getAttribute('aria-haspopup')).toBe('menu')
      expect(button.textContent).toBe('Nordlicht Komponenten GmbH' + 'DE · EN' + '▾')
      await userEvent.click(button)
      const choices = screen.getAllByRole('menuitemradio')
      expect(choices.map((o) => [o.textContent, o.getAttribute('aria-checked')])).toEqual([
        ['Nordlicht Komponenten GmbH · Germany · English', 'true'],
        ['Testwerk Prüfstand GmbH · Austria · German', 'false']])
      await userEvent.click(choices[1])
      expect(onSwitch).toHaveBeenCalledWith('testwerk')
    })
})

/** An overview with nothing assessed, for `company`. */
const overviewOf = (company: string): Overview => ({
  company, country: 'Somewhere', protection_level: 'normal', answer_language: 'en', requirements: {},
  readiness: { controls: 1, accepted: 0, by_verdict: { evidenced: 0, 'partly-evidenced': 0, 'not-evidenced': 0,
    'not-applicable': 0 }, open: 1, remediation_items: 0, remediation_approved: 0 },
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

/** Switches profile in the top bar's dropdown (#222): opens it, and chooses the profile whose id is `id`. */
async function switchTo(id: string) {
  await userEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /^Profile: / }))
  const label = { nordlicht: NORDLICHT, testwerk: TESTWERK }[id as 'nordlicht' | 'testwerk']
  await userEvent.click(screen.getByRole('menuitemradio', { name: profileLabel(label) }))
}

describe('ProfiledApp', () => {
  it('switching loads the chosen profile\'s overview, and keeps the theme', async () => {
    const fetch = serve([NORDLICHT, TESTWERK])
    render(<ProfiledApp />)
    await screen.findByText('Nordlicht Komponenten GmbH · Somewhere', { selector: '.pill' })
    await screen.findByRole('button', { name: /^Profile: / })
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Dark theme' }))

    await switchTo('testwerk')

    await screen.findByText('Testwerk Prüfstand GmbH · Somewhere', { selector: '.pill' })
    expect(overviewCalls(fetch)).toEqual(['nordlicht', 'testwerk'])
    expect(screen.getByRole('button', { name: /^Profile: Testwerk/ })).toBeTruthy()
    expect(document.documentElement.dataset.theme).toBe('dark')
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    expect(screen.getByRole('menuitem', { name: 'Light theme' })).toBeTruthy()
  })

  it('opens the profile chosen before a reload, and remembers each switch, issue 201', async () => {
    rememberProfile('testwerk')
    const fetch = serve([NORDLICHT, TESTWERK])
    render(<ProfiledApp />)
    await screen.findByText('Testwerk Prüfstand GmbH · Somewhere', { selector: '.pill' })
    expect(overviewCalls(fetch)).toEqual(['testwerk'])
    expect(within(screen.getByRole('banner')).queryByRole('status')).toBeNull()

    await switchTo('nordlicht')

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

    await switchTo('testwerk')
    await screen.findByText('Testwerk Prüfstand GmbH · Somewhere', { selector: '.pill' })
    expect(within(screen.getByRole('banner')).queryByRole('status')).toBeNull()
  })

  it('with one profile, shows its name in the top bar and no menu', async () => {
    serve([NORDLICHT])
    render(<ProfiledApp />)
    await screen.findByLabelText('Profile: Nordlicht Komponenten GmbH · Germany · English')
    expect(within(screen.getByRole('banner')).queryByRole('button', { name: /^Profile: / })).toBeNull()
  })

  it('with the default profile left out, chooses the first listed', async () => {
    const fetch = serve([TESTWERK], null)
    render(<ProfiledApp />)
    await screen.findByText('Testwerk Prüfstand GmbH · Somewhere', { selector: '.pill' })
    expect(overviewCalls(fetch)).toEqual(['testwerk'])
  })

  it('starting afresh from the user menu opens the Overview afresh, at its address, issue 285', async () => {
    window.history.replaceState(null, '', '/report')
    const fetch = serve([NORDLICHT, TESTWERK])
    const answered = fetch.getMockImplementation()!
    fetch.mockImplementation((url: string, init?: RequestInit) => url === '/api/progress/fresh-start'
      ? Promise.resolve(Response.json({ fresh_start: '2026-11-07T10:00:00+00:00' }))
      : answered(url, init))
    vi.stubGlobal('confirm', () => true)
    render(<ProfiledApp />)
    await screen.findByRole('button', { name: /^Profile: / })
    expect(overviewCalls(fetch)).toEqual([]) // the report reads no overview
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))

    await userEvent.click(screen.getByRole('menuitem', { name: `Start ${NORDLICHT.company} afresh…` }))

    await screen.findByText('Nordlicht Komponenten GmbH · Somewhere', { selector: '.pill' })
    expect(window.location.pathname).toBe('/')
    expect(overviewCalls(fetch)).toEqual(['nordlicht'])
    const freshStart = fetch.mock.calls.filter(([url]) => url === '/api/progress/fresh-start')
    expect(freshStart.map(([, init]) => new Headers(init?.headers).get('X-Vouch-Profile'))).toEqual(['nordlicht'])
    expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page')
  })

  it('without the list, shows no switcher and the app as before', async () => {
    vi.stubGlobal('fetch', (url: string) => url === '/api/overview'
      ? Promise.resolve(Response.json(overviewOf('Nordlicht Komponenten GmbH')))
      : Promise.resolve(new Response(null, { status: 500 })))
    render(<ProfiledApp />)
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeTruthy()
    await screen.findByText('Nordlicht Komponenten GmbH · Somewhere', { selector: '.pill' })
    expect(screen.queryByRole('button', { name: /^Profile: / })).toBeNull()
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

  function serveTwo() {
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
  }
  const row = async () => (await screen.findByText('M1', { selector: 'th .mono' })).closest('tr')!
  /** Nordlicht's 5.1.2 paper, with M1 overridden and saved, not yet recorded. */
  async function overriddenPaper() {
    window.history.replaceState(null, '', '/controls/5.1.2')
    serveTwo()
    render(<ProfiledApp />)
    await userEvent.click(within(await row()).getByRole('button', { name: 'Change M1' }))
    const editor = screen.getByRole('form', { name: 'Override M1' })
    await userEvent.selectOptions(within(editor).getByLabelText('Verdict'), 'evidenced')
    await userEvent.type(within(editor).getByLabelText('Reason (required)'), 'The rollout record shows it.')
    await userEvent.click(within(editor).getByRole('button', { name: 'Save' }))
    expect(within(await row()).getByText('Overridden from Partly evidenced')).toBeTruthy()
  }

  it('asks first, and Cancel keeps the profile and the override (issue 182)', async () => {
    await overriddenPaper()
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)

    await switchTo('testwerk')

    expect(confirm).toHaveBeenCalledWith('You have 1 override not yet recorded. Leave and drop it?')
    expect(screen.getByRole('button', { name: /^Profile: Nordlicht/ })).toBeTruthy()
    expect(within(await row()).getByText('Overridden from Partly evidenced')).toBeTruthy()
    expect(rememberedProfile()).toBeNull()
  })

  it("drops the paper's unsaved overrides, which belonged to the other profile's assessment", async () => {
    await overriddenPaper()
    vi.stubGlobal('confirm', () => true) // the lead leaves (#182)

    await switchTo('testwerk')

    await screen.findByText(/Working paper · Testwerk/)
    expect(within(await row()).queryByText(/Overridden from/)).toBeNull()
    expect(within(await row()).queryByText('The rollout record shows it.')).toBeNull()
  })
})

describe('a company made in company setup, issue 409', () => {
  const SETTINGS = { model: 'm', runs_on: 'fake', fake_reason: 'no_key', prompt_version: 'v', usage_url: '',
    kept_in: 'memory', daily_limit: 10 }

  /** The API as `serve` answers it, for a developer, the list read again answering `relisted` (null: a 500). */
  function serveDeveloper(relisted: ProfileSummary[] | null) {
    const fetch = serve([NORDLICHT, TESTWERK, MUSTERFIRMA])
    const answered = fetch.getMockImplementation()!
    let listings = 0
    fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/developer/settings') return Promise.resolve(Response.json(SETTINGS))
      if (url === '/api/profiles' && ++listings > 1)
        return Promise.resolve(relisted ? Response.json({ profiles: relisted, default: 'nordlicht' })
          : new Response(null, { status: 500 }))
      if (url === '/api/profiles') return Promise.resolve(Response.json({ profiles: [NORDLICHT, TESTWERK], default: 'nordlicht' }))
      return answered(url, init)
    })
    return fetch
  }

  it('reads the list again and opens under the new company, on the Dossier, remembered for the next reload', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    const fetch = serveDeveloper([NORDLICHT, TESTWERK, MUSTERFIRMA])
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))

    await screen.findByText('Musterfirma GmbH · Somewhere', { selector: '.pill' })
    expect(window.location.pathname).toBe('/')
    expect(fetch.mock.calls.filter(([url]) => url === '/api/profiles')).toHaveLength(2)
    expect(overviewCalls(fetch)).toEqual(['nordlicht', 'musterfirma-gmbh']) // the setup page's, then the new company's
    expect(rememberedProfile()).toBe('musterfirma-gmbh')
    expect(screen.getByRole('button', { name: /^Profile: Musterfirma GmbH/ })).toBeTruthy()
    await userEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /^Profile: / }))
    expect(screen.getAllByRole('menuitemradio').map((m) => m.getAttribute('aria-checked')))
      .toEqual(['false', 'false', 'true'])
  })

  it('when the list can\'t be read again, still opens under the new company, which the switcher names', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    const fetch = serveDeveloper(null)
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))

    await screen.findByText('Musterfirma GmbH · Somewhere', { selector: '.pill' })
    expect(overviewCalls(fetch)).toEqual(['nordlicht', 'musterfirma-gmbh']) // the setup page's, then the new company's
    expect(rememberedProfile()).toBe('musterfirma-gmbh')
    await userEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /^Profile: Musterfirma/ }))
    expect(screen.getAllByRole('menuitemradio').map((m) => m.textContent?.startsWith('Musterfirma GmbH')))
      .toEqual([false, false, true])
  })

  it('lands focus on the Dossier\'s main heading, not the profile dropdown', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    serveDeveloper([NORDLICHT, TESTWERK, MUSTERFIRMA])
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))
    await screen.findByText('Musterfirma GmbH · Somewhere', { selector: '.pill' })
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1 })))
  })

  /** serveDeveloper, the list read again held until the returned function answers it. */
  function heldRelisting() {
    const fetch = serveDeveloper([NORDLICHT, TESTWERK, MUSTERFIRMA])
    const answered = fetch.getMockImplementation()!
    let release: (answer: Response) => void = () => {}
    let listings = 0
    fetch.mockImplementation((url: string, init?: RequestInit) => url === '/api/profiles' && ++listings > 1
      ? new Promise<Response>((resolve) => { release = resolve })
      : answered(url, init))
    return (answer: Response) => release(answer)
  }

  it('stays on the setup address until the list answers again, and after closing neither opens nor remembers it',
    async () => {
      window.history.replaceState(null, '', '/developer/setup')
      const release = heldRelisting()
      const { unmount } = render(<ProfiledApp />)
      await userEvent.click(await screen.findByRole('button', { name: 'Created' }))
      expect(window.location.pathname).toBe('/developer/setup')
      unmount()
      release(Response.json({ profiles: [NORDLICHT, TESTWERK, MUSTERFIRMA], default: 'nordlicht' }))
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(rememberedProfile()).toBeNull()
      expect(await authHeaders()).toEqual({})
      expect(window.location.pathname).toBe('/developer/setup')
    })

  it('a 401 on the list read again signs out, and neither opens nor remembers the new company', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    const release = heldRelisting()
    const signOut = vi.fn()
    setOnSignedOut(signOut) // as ClerkApp's: the sign-in replaces the app on its next render, after this answer
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))
    release(new Response(null, { status: 401 }))
    await waitFor(() => expect(signOut).toHaveBeenCalledOnce())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(rememberedProfile()).toBeNull()
    expect(window.location.pathname).toBe('/developer/setup')
    expect(screen.queryByRole('button', { name: /^Profile: Musterfirma/ })).toBeNull()
  })
})
