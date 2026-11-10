import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { StrictMode } from 'react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Overview } from './overview.ts'
import ProfiledApp from './ProfiledApp.tsx'
import type { Goal } from './report.ts'

// Company setup stands in as buttons that report the company made (#409) with the Goal chosen on step 4 (#447): its
// steps are tested on their own.
vi.mock('./CompanySetupWizard.tsx', () => {
  const made = { id: 'musterfirma-gmbh', company: 'Musterfirma GmbH', country: 'Germany', answer_language: 'de' }
  return {
    default: ({ onCreated }: { onCreated: (profile: ProfileSummary, goal: Goal) => void }) => (
      <>
        <button type="button" onClick={() => onCreated(made, 'internal-improvement')}>Created</button>
        <button type="button" onClick={() => onCreated(made, 'audit-readiness')}>Created for audit</button>
      </>
    ),
  }
})
// Each list read is kept, so a test can wait on a late one's answer having been handled (#418 review): ProfiledApp's
// own `.then` on it runs before a test's `await` on the same promise.
const reads = vi.hoisted(() => [] as Promise<unknown>[])
vi.mock('./profiles.ts', async (original) => {
  const actual = await original<typeof import('./profiles.ts')>()
  return { ...actual, fetchProfiles: () => {
    const read = actual.fetchProfiles()
    reads.push(read)
    return read
  } }
})
import type { DeveloperSettings } from './developer.ts'
import { DEVELOPER_SETTINGS } from './developerFixtures.ts'
import {
  fetchProfiles, type ProfileSummary, profileLabel, startAfresh, startingProfile, withoutProfile, withProfile,
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
  reads.length = 0
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

describe('withoutProfile, issue 418', () => {
  it('leaves the profile out of the list held, keeping its default', () => {
    const held = { profiles: [NORDLICHT, MUSTERFIRMA], default: 'nordlicht' }
    expect(withoutProfile(held, 'musterfirma-gmbh')).toEqual({ profiles: [NORDLICHT], default: 'nordlicht' })
    expect(withoutProfile(held, 'nobody')).toEqual(held)
  })

  it('with the profile as the default, leaves no default, so the first listed opens (review of issue 418)', () => {
    const held = { profiles: [MUSTERFIRMA, NORDLICHT], default: 'musterfirma-gmbh' }
    expect(withoutProfile(held, 'musterfirma-gmbh')).toEqual({ profiles: [NORDLICHT], default: null })
  })

  it('with no list held, is none', () => {
    expect(withoutProfile(null, 'musterfirma-gmbh')).toBeNull()
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
  company, country: 'Somewhere', protection_level: 'normal', answer_language: 'en', goal: 'audit-readiness',
  requirements: {}, rounds: { current: { name: 'First round', opened_at: null }, comparison: null },
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

describe('a company removed on the developer page, issue 418', () => {
  const MADE = { id: 'musterfirma-gmbh', company: 'Musterfirma GmbH', created_at: '2026-11-09T10:15:00+00:00' }
  const SETTINGS = { ...DEVELOPER_SETTINGS, companies: [MADE] } satisfies DeveloperSettings

  /** The API as `serve` answers it, for a developer on the developer page with Musterfirma made in setup: the
   * DELETE answers the settings without it, and the list read again answers `relisted` (null: a 500), or is held
   * until `release` answers it when `hold` is set. */
  function serveRemoval(relisted: ProfileSummary[] | null, hold = false,
    listed: ProfileSummary[] = [NORDLICHT, TESTWERK, MUSTERFIRMA], fallback = 'nordlicht') {
    window.history.replaceState(null, '', '/developer')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetch = serve(listed, fallback)
    const answered = fetch.getMockImplementation()!
    let listings = 0
    let release: (answer: Response) => void = () => {}
    fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/developer/settings') return Promise.resolve(Response.json(SETTINGS))
      if (url === '/api/developer/profiles/musterfirma-gmbh' && init?.method === 'DELETE')
        return Promise.resolve(Response.json({ ...SETTINGS, companies: [] }))
      if (url === '/api/profiles' && ++listings > 1) {
        if (hold) return new Promise<Response>((resolve) => { release = resolve })
        return Promise.resolve(relisted ? Response.json({ profiles: relisted, default: 'nordlicht' })
          : new Response(null, { status: 500 }))
      }
      return answered(url, init)
    })
    return { fetch, release: (answer: Response) => release(answer) }
  }

  const listings = (fetch: ReturnType<typeof serve>) => fetch.mock.calls.filter(([url]) => url === '/api/profiles')

  async function removeMusterfirma() {
    const sheet = await screen.findByRole('region', { name: 'Company setup' })
    await userEvent.click(await within(sheet).findByRole('button', { name: 'Remove Musterfirma GmbH' }))
  }

  async function switcherCompanies() {
    await userEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /^Profile: / }))
    const names = screen.getAllByRole('menuitemradio').map((m) => m.textContent?.split(' · ')[0])
    await userEvent.keyboard('{Escape}')
    return names
  }

  it('one not shown: the list is read again and the switcher drops it; the page and the profile stay', async () => {
    const { fetch } = serveRemoval([NORDLICHT, TESTWERK])
    render(<ProfiledApp />)
    await removeMusterfirma()

    await waitFor(() => expect(listings(fetch)).toHaveLength(2))
    await waitFor(async () => expect(await switcherCompanies()).toEqual(['Nordlicht Komponenten GmbH',
      'Testwerk Prüfstand GmbH']))
    expect(screen.getByRole('status').textContent).toBe('Removed Musterfirma GmbH.')
    expect(window.location.pathname).toBe('/developer')
    expect(screen.getByRole('button', { name: /^Profile: Nordlicht/ })).toBeTruthy()
    expect(within(screen.getByRole('banner')).queryByRole('status')).toBeNull()
  })

  it.each([
    ['read again', [NORDLICHT, TESTWERK]],
    ["held, less it, when the list can't be read again", null],
  ])('the one shown: opens the default on the developer page with the fallback note, forgotten (%s)',
    async (_, relisted) => {
      rememberProfile('musterfirma-gmbh')
      serveRemoval(relisted)
      render(<ProfiledApp />)
      await screen.findByRole('button', { name: /^Profile: Musterfirma/ })
      await removeMusterfirma()

      await screen.findByRole('button', { name: /^Profile: Nordlicht/ })
      expect(within(screen.getByRole('banner')).getByRole('status').textContent)
        .toBe('Your last profile is no longer available: showing Nordlicht Komponenten GmbH.')
      expect(rememberedProfile()).toBeNull()
      expect(window.location.pathname).toBe('/developer')
      expect(await screen.findByRole('region', { name: 'Developer settings' })).toBeTruthy()
      // The note is carried through the app opened afresh (#418 review), and focus goes to it, not the heading.
      const note = await within(screen.getByRole('region', { name: 'Company setup' })).findByRole('status')
      expect(note.textContent).toBe('Removed Musterfirma GmbH.')
      expect(document.activeElement).not.toBe(screen.queryByRole('heading', { level: 1 }))
      expect(await switcherCompanies()).toEqual(['Nordlicht Komponenten GmbH', 'Testwerk Prüfstand GmbH'])
    })

  it("the one shown, the default, with no list read again: leaves it for the profile the held list has left",
    async () => {
      serveRemoval(null, false, [MUSTERFIRMA, NORDLICHT], 'musterfirma-gmbh')
      render(<ProfiledApp />)
      await screen.findByRole('button', { name: /^Profile: Musterfirma/ })
      await removeMusterfirma()

      await waitFor(async () => expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'nordlicht' }))
    })

  it("the one shown, and no other profile can be had: opens with none named, not under the one removed", async () => {
    serveRemoval(null, false, [MUSTERFIRMA], 'musterfirma-gmbh')
    render(<ProfiledApp />)
    await screen.findByRole('button', { name: 'Remove Musterfirma GmbH' })
    expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'musterfirma-gmbh' })
    await removeMusterfirma()

    await waitFor(async () => expect(await authHeaders()).toEqual({}))
    expect(await screen.findByRole('region', { name: 'Developer settings' })).toBeTruthy()
  })

  it('a list read again that answers after a sign-out changes nothing', async () => {
    rememberProfile('musterfirma-gmbh')
    const { release } = serveRemoval(null, true)
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    render(<ProfiledApp />)
    await screen.findByRole('button', { name: /^Profile: Musterfirma/ })
    await removeMusterfirma()
    await screen.findByText('Removed Musterfirma GmbH.')
    await waitFor(() => expect(reads).toHaveLength(2))
    release(new Response(null, { status: 401 }))
    await reads[1] // ProfiledApp has handled the late answer by now
    expect(signOut).toHaveBeenCalledOnce()
    expect(rememberedProfile()).toBe('musterfirma-gmbh')
    expect(screen.getByRole('button', { name: /^Profile: Musterfirma/ })).toBeTruthy()
    expect(within(screen.getByRole('banner')).queryByRole('status')).toBeNull()
  })
})

describe('the first pass on a company just made, issue 447', () => {
  const SETTINGS = { model: 'm', runs_on: 'fake', fake_reason: 'no_key', prompt_version: 'v', usage_url: '',
    kept_in: 'memory', daily_limit: 10 }
  const PASS_DONE = { assessments: [{ thread_id: 't1', control_id: '1.1.1', status: 'waiting-for-review',
    verdict: 'partly-evidenced' }], skipped: [] }
  const LIMIT = 'The daily limit of profile passes is reached. It resets at midnight UTC.'

  /** The API for a developer on the setup page: Nordlicht and Testwerk listed, Musterfirma too once made. PUT
   * /api/goal answers `goalAnswer` (and keeps the goal on a 2xx, so the overview shows it), the pass `passAnswer`. */
  function serveSetup(goalAnswer: () => Response = () => Response.json({ goal: 'internal-improvement' }),
    passAnswer: () => Response = () => Response.json(PASS_DONE)) {
    const fetch = serve([NORDLICHT, TESTWERK, MUSTERFIRMA])
    const answered = fetch.getMockImplementation()!
    let listings = 0
    let goal: Goal = 'audit-readiness'
    fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/developer/settings') return Promise.resolve(Response.json(SETTINGS))
      if (url === '/api/profiles') {
        const profiles = ++listings > 1 ? [NORDLICHT, TESTWERK, MUSTERFIRMA] : [NORDLICHT, TESTWERK]
        return Promise.resolve(Response.json({ profiles, default: 'nordlicht' }))
      }
      if (url === '/api/goal' && init?.method === 'PUT') {
        const answer = goalAnswer()
        if (answer.ok) goal = (JSON.parse(String(init.body)) as { goal: Goal }).goal
        return Promise.resolve(answer)
      }
      if (url === '/api/profile-pass' && init?.method === 'POST') return Promise.resolve(passAnswer())
      if (url === '/api/overview') {
        const named = new Headers(init?.headers).get('X-Vouch-Profile')
        const company = [NORDLICHT, TESTWERK, MUSTERFIRMA].find((p) => p.id === named)?.company ?? NORDLICHT.company
        return Promise.resolve(Response.json({ ...overviewOf(company), goal: named === 'musterfirma-gmbh' ? goal
          : 'audit-readiness' }))
      }
      return answered(url, init)
    })
    return fetch
  }
  /** Each goal change and pass sent, in order, with the profile it named. */
  const firstPassCalls = (fetch: ReturnType<typeof serve>) => fetch.mock.calls
    .filter(([url, init]) => (url === '/api/goal' && init?.method === 'PUT') || url === '/api/profile-pass')
    .map(([url, init]) => [url, new Headers(init?.headers).get('X-Vouch-Profile'), init?.body ?? null])
  const passes = (fetch: ReturnType<typeof serve>) => fetch.mock.calls.filter(([url]) => url === '/api/profile-pass')

  async function createFromSetup(button = 'Created', strict = true) {
    window.history.replaceState(null, '', '/developer/setup')
    render(strict ? <StrictMode><ProfiledApp /></StrictMode> : <ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: button }))
    await screen.findByText('Musterfirma GmbH · Somewhere', { selector: '.pill' })
  }

  it('sets Internal improvement under the new company, then runs one pass, once under StrictMode', async () => {
    const fetch = serveSetup()
    await createFromSetup()
    expect(await screen.findByText('1 assessment waiting for your review.')).toBeTruthy()
    expect(firstPassCalls(fetch)).toEqual([
      ['/api/goal', 'musterfirma-gmbh', JSON.stringify({ goal: 'internal-improvement' })],
      ['/api/profile-pass', 'musterfirma-gmbh', null],
    ])
    expect(await screen.findByText('Goal: Internal improvement')).toBeTruthy()
  })

  it('shows the running state on the hero while the pass runs', async () => {
    serveSetup(undefined, () => { throw new Error('held') })
    const fetch = vi.mocked(globalThis.fetch)
    const answered = fetch.getMockImplementation()!
    fetch.mockImplementation((url, init) => url === '/api/profile-pass' ? new Promise<Response>(() => {})
      : answered(url, init))
    await createFromSetup()
    expect(await screen.findByRole('button', { name: 'Running the profile pass…' })).toBeTruthy()
    expect(screen.getByText('Vouch is assessing every open control in one run. This can take a few minutes.'))
      .toBeTruthy()
  })

  it('with Audit readiness chosen, sends no goal change and still runs the pass', async () => {
    const fetch = serveSetup()
    await createFromSetup('Created for audit')
    await screen.findByText('1 assessment waiting for your review.')
    expect(firstPassCalls(fetch)).toEqual([['/api/profile-pass', 'musterfirma-gmbh', null]])
    expect(screen.getByText('Goal: Audit readiness')).toBeTruthy()
  })

  it('a switch away and back, then a fresh start, start no pass again', async () => {
    const fetch = serveSetup()
    vi.stubGlobal('confirm', () => true)
    const answered = fetch.getMockImplementation()!
    fetch.mockImplementation((url: string, init?: RequestInit) => url === '/api/progress/fresh-start'
      ? Promise.resolve(Response.json({ fresh_start: '2026-11-10T10:00:00+00:00' }))
      : answered(url, init))
    await createFromSetup()
    await screen.findByText('1 assessment waiting for your review.')
    await switchTo('nordlicht')
    await screen.findByText('Nordlicht Komponenten GmbH · Somewhere', { selector: '.pill' })
    await userEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /^Profile: / }))
    await userEvent.click(screen.getByRole('menuitemradio', { name: profileLabel(MUSTERFIRMA) }))
    await screen.findByText('Musterfirma GmbH · Somewhere', { selector: '.pill' })
    const before = overviewCalls(fetch).length
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    await userEvent.click(screen.getByRole('menuitem', { name: `Start ${MUSTERFIRMA.company} afresh…` }))
    await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url === '/api/progress/fresh-start')).toHaveLength(1))
    await waitFor(() => expect(overviewCalls(fetch).length).toBeGreaterThan(before)) // the app opened afresh
    await screen.findByRole('button', { name: 'Run a profile pass' })
    expect(passes(fetch)).toHaveLength(1)
    expect(screen.queryByText('Running the profile pass…')).toBeNull()
  })

  it('a fresh mount (a reload) under the new company starts no pass', async () => {
    rememberProfile('musterfirma-gmbh')
    const fetch = serveSetup()
    fetch.mockImplementation(((answered) => (url: string, init?: RequestInit) => url === '/api/profiles'
      ? Promise.resolve(Response.json({ profiles: [NORDLICHT, TESTWERK, MUSTERFIRMA], default: 'nordlicht' }))
      : answered(url, init))(fetch.getMockImplementation()!))
    render(<StrictMode><ProfiledApp /></StrictMode>)
    await screen.findByText('Musterfirma GmbH · Somewhere', { selector: '.pill' })
    await screen.findByRole('button', { name: 'Run a profile pass' })
    expect(firstPassCalls(fetch)).toEqual([])
  })

  it('a failed goal change says so under the Goal line, and the pass still runs', async () => {
    const fetch = serveSetup(() => new Response(null, { status: 503 }))
    await createFromSetup()
    const alert = await screen.findByText('The company was created, but its Goal could not be set to Internal '
      + 'improvement. Use Change goal to set it.')
    expect(alert.getAttribute('role')).toBe('alert')
    expect(screen.getByText('Goal: Audit readiness')).toBeTruthy()
    await screen.findByText('1 assessment waiting for your review.')
    expect(firstPassCalls(fetch).map(([url]) => url)).toEqual(['/api/goal', '/api/profile-pass'])
  })

  it('a goal change answering 401 signs out and starts no pass', async () => {
    const fetch = serveSetup(() => new Response(null, { status: 401 }))
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    await createFromSetup()
    await waitFor(() => expect(signOut).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(passes(fetch)).toHaveLength(0)
  })

  it("the pass's daily limit leaves the company open, with the limit sentence and the per-control way on", async () => {
    serveSetup(undefined, () => Response.json({ detail: LIMIT, limit: 'profile-pass' }, { status: 429 }))
    await createFromSetup()
    expect(await screen.findByText(LIMIT)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Assess 1.1.1 on its own' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Run a profile pass' })).toBeTruthy()
    expect(screen.getByText('Musterfirma GmbH · Somewhere', { selector: '.pill' })).toBeTruthy()
  })
})
