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
import { fetchProfiles, type Profiles, type ProfileSummary, startAfresh, withoutProfile, withProfile } from './profiles.ts'
import { authHeaders, setOnSignedOut, setProfile, setTokenSource } from './session.ts'

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

describe('authHeaders', () => {
  it('names the company the app opened under on every call, and none before one is', async () => {
    expect(await authHeaders()).toEqual({})
    setProfile('testwerk')
    expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'testwerk' })
  })

  it('names the company set when the call started, even if the app reopens while the token is fetched', async () => {
    let giveToken: (token: string) => void = () => {}
    setTokenSource(() => new Promise((resolve) => { giveToken = resolve }))
    setProfile('nordlicht')
    const headers = authHeaders()
    setProfile('testwerk')
    giveToken('t0ken')
    expect(await headers).toEqual({ Authorization: 'Bearer t0ken', 'X-Vouch-Profile': 'nordlicht' })
  })
})

/** A list as GET /api/profiles answers it: Nordlicht the default while listed, `current` the current company. */
const listOf = (profiles: ProfileSummary[], current: string | null = 'nordlicht'): Profiles => ({
  profiles, default: profiles.some((p) => p.id === 'nordlicht') ? 'nordlicht' : null, current,
})

describe('withProfile, issue 409', () => {
  it('adds a company made in setup at the end of the list held, once', () => {
    const held = listOf([NORDLICHT])
    expect(withProfile(held, MUSTERFIRMA)).toEqual(listOf([NORDLICHT, MUSTERFIRMA]))
    expect(withProfile(listOf([NORDLICHT, MUSTERFIRMA]), MUSTERFIRMA)).toEqual(listOf([NORDLICHT, MUSTERFIRMA]))
  })

  it('with nothing held, is that company alone', () => {
    expect(withProfile(null, MUSTERFIRMA)).toEqual({ profiles: [MUSTERFIRMA], default: null, current: null })
  })
})

describe('withoutProfile, issue 418', () => {
  it('drops the company from the list held, and from its default and current when it was either', () => {
    const held = listOf([NORDLICHT, MUSTERFIRMA])
    expect(withoutProfile(held, 'musterfirma-gmbh')).toEqual(listOf([NORDLICHT]))
    expect(withoutProfile(held, 'nobody')).toEqual(held)
    expect(withoutProfile(listOf([NORDLICHT, MUSTERFIRMA], 'musterfirma-gmbh'), 'musterfirma-gmbh'))
      .toEqual(listOf([NORDLICHT], null))
  })

  it('is none when nothing is held', () => {
    expect(withoutProfile(null, 'musterfirma-gmbh')).toBeNull()
  })
})

describe('fetchProfiles', () => {
  it('reads the list with its current company, or null when it cannot', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json(listOf([NORDLICHT]))))
    expect(await fetchProfiles()).toEqual(listOf([NORDLICHT]))
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

/** An overview with nothing assessed, for `company`. */
const overviewOf = (company: string): Overview => ({
  company, country: 'Somewhere', protection_level: 'normal', answer_language: 'en', goal: 'audit-readiness',
  requirements: {}, rounds: { current: { name: 'First round', opened_at: null }, comparison: null },
  readiness: { controls: 1, accepted: 0, by_verdict: { evidenced: 0, 'partly-evidenced': 0, 'not-evidenced': 0,
    'not-applicable': 0 }, open: 1, remediation_items: 0, remediation_approved: 0 },
  by_status: { 'not-assessed': 1, 'waiting-for-review': 0, accepted: 0, rejected: 0, 'not-applicable': 0 },
  next_step: { control_id: '1.1.1', action: 'assess' },
  controls: [{ id: '1.1.1', question: 'Question?', iso_27001: 'A.5.1', status: 'not-assessed', verdict: null }],
  quotes: [],
})

/** The API with these profiles and `current` the current company: each overview is the company of the profile the
 * call names (the current one without); a Make current answers the developer settings. */
function serve(profiles: ProfileSummary[], current: string | null = 'nordlicht') {
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    const named = new Headers(init?.headers).get('X-Vouch-Profile') ?? current
    if (url === '/api/profiles') return Promise.resolve(Response.json(listOf(profiles, current)))
    if (url === '/api/overview') {
      const profile = profiles.find((p) => p.id === named)
      return Promise.resolve(profile ? Response.json(overviewOf(profile.company)) : new Response(null, { status: 500 }))
    }
    if (url === '/api/developer/current-company') return Promise.resolve(Response.json(DEVELOPER_SETTINGS))
    return new Promise<Response>(() => {})
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const overviewCalls = (fetch: ReturnType<typeof serve>) =>
  fetch.mock.calls.filter(([url]) => url === '/api/overview')
    .map(([, init]) => new Headers(init?.headers).get('X-Vouch-Profile'))

/** The company the top bar names (ticket 495), or null when it names none. */
const barName = () => screen.getByRole('banner').querySelector('.top-name')?.textContent ?? null

/** The Dossier under `company`: the top bar names it, and its overview is read (the hero is shown). */
async function dossierOf(company: string) {
  await waitFor(() => {
    expect(barName()).toBe(company)
    expect(document.querySelector('.hero')).not.toBeNull()
  })
}

const currentChanges = (fetch: ReturnType<typeof serve>) =>
  fetch.mock.calls.filter(([url]) => url === '/api/developer/current-company')
    .map(([, init]) => JSON.parse(String(init?.body)) as { company: string })

describe('ProfiledApp, issue 480', () => {
  it('opens under the current company, named in the top bar with no switcher (ticket 495), and keeps nothing in the browser',
    async () => {
    const fetch = serve([NORDLICHT, TESTWERK], 'testwerk')
    const stored = vi.spyOn(Storage.prototype, 'setItem')
    render(<ProfiledApp />)
    await dossierOf('Testwerk Prüfstand GmbH')
    expect(overviewCalls(fetch)).toEqual(['testwerk'])
    expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'testwerk' })
    const banner = screen.getByRole('banner')
    expect(within(banner).queryByRole('button', { name: /^Profile/ })).toBeNull()
    expect(within(banner).getByText('Somewhere · EN')).toBeTruthy() // the overview's, once read (this fixture's country)
    expect(within(banner).getByText('Testwerk Prüfstand GmbH').closest('button, a')).toBeNull()
    expect(stored.mock.calls.filter(([key]) => key === 'vouch.profile')).toEqual([])
    expect(window.localStorage.getItem('vouch.profile')).toBeNull()
  })

  it('ignores a profile an earlier version kept in the browser', async () => {
    window.localStorage.setItem('vouch.profile', 'testwerk')
    const fetch = serve([NORDLICHT, TESTWERK])
    render(<ProfiledApp />)
    await dossierOf('Nordlicht Komponenten GmbH')
    expect(overviewCalls(fetch)).toEqual(['nordlicht'])
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
    await screen.findByRole('navigation', { name: 'Main' })
    expect(overviewCalls(fetch)).toEqual([]) // the report reads no overview
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))

    await userEvent.click(await screen.findByRole('menuitem', { name: `Start ${NORDLICHT.company} afresh…` }))

    await dossierOf('Nordlicht Komponenten GmbH')
    expect(window.location.pathname).toBe('/')
    expect(overviewCalls(fetch)).toEqual(['nordlicht'])
    const freshStart = fetch.mock.calls.filter(([url]) => url === '/api/progress/fresh-start')
    expect(freshStart.map(([, init]) => new Headers(init?.headers).get('X-Vouch-Profile'))).toEqual(['nordlicht'])
    expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page')
  })

  it('without the list, names no company, so the API\'s current company answers', async () => {
    vi.stubGlobal('fetch', (url: string) => url === '/api/overview'
      ? Promise.resolve(Response.json(overviewOf('Nordlicht Komponenten GmbH')))
      : Promise.resolve(new Response(null, { status: 500 })))
    render(<ProfiledApp />)
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeTruthy()
    await dossierOf('Nordlicht Komponenten GmbH')
    expect(await authHeaders()).toEqual({})
  })

  it("without the list, the top bar names the overview's company once it's read, and none before (ticket 495)", async () => {
    let answer: (response: Response) => void = () => {}
    vi.stubGlobal('fetch', (url: string) => url === '/api/overview'
      ? new Promise<Response>((resolve) => { answer = resolve })
      : Promise.resolve(new Response(null, { status: 500 })))
    render(<ProfiledApp />)
    await screen.findByRole('navigation', { name: 'Main' })
    const banner = screen.getByRole('banner')
    expect(banner.querySelector('.top-company')).toBeNull()
    expect(banner.querySelector('.top-rule')).toBeNull()
    answer(Response.json(overviewOf('Nordlicht Komponenten GmbH')))
    await dossierOf('Nordlicht Komponenten GmbH')
    expect(within(banner).getByText('Somewhere · EN')).toBeTruthy() // a country with no ISO code by its name
    // The user menu's fresh start names the company the bar names.
    await userEvent.click(within(banner).getByRole('button', { name: 'Your menu' }))
    expect(screen.getByRole('menuitem', { name: 'Start Nordlicht Komponenten GmbH afresh…' })).toBeTruthy()
  })

  it('without the list, names no company on a page that reads no overview (ticket 495)', async () => {
    window.history.replaceState(null, '', '/report')
    vi.stubGlobal('fetch', (url: string) => url === '/api/overview'
      ? Promise.resolve(Response.json(overviewOf('Nordlicht Komponenten GmbH')))
      : url === '/api/profiles' ? Promise.resolve(new Response(null, { status: 500 })) : new Promise<Response>(() => {}))
    render(<ProfiledApp />)
    await screen.findByRole('navigation', { name: 'Main' })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.getByRole('banner').querySelector('.top-company')).toBeNull()
  })

  it('names the current company in the top bar on a page that reads no overview, from the list (ticket 495)', async () => {
    window.history.replaceState(null, '', '/report') // the Company page reads the overview since #505
    const fetch = serve([NORDLICHT, TESTWERK], 'testwerk')
    render(<ProfiledApp />)
    await waitFor(() => expect(barName()).toBe('Testwerk Prüfstand GmbH'))
    expect(within(screen.getByRole('banner')).getByText('AT · DE')).toBeTruthy()
    expect(overviewCalls(fetch)).toEqual([])
  })
})

const SETTINGS = DEVELOPER_SETTINGS

describe('Make current on the developer page, issue 480', () => {
  it('opens the app afresh under the new current company, on the developer page, saying so', async () => {
    window.history.replaceState(null, '', '/developer')
    const fetch = serve([NORDLICHT, TESTWERK])
    const answered = fetch.getMockImplementation()!
    const listed = [...SETTINGS.listed_companies, TESTWERK]
    fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/developer/settings') return Promise.resolve(Response.json({ ...SETTINGS, listed_companies: listed }))
      if (url === '/api/developer/current-company')
        return Promise.resolve(Response.json({ ...SETTINGS, listed_companies: listed, current_company: 'testwerk' }))
      return answered(url, init)
    })
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Make Testwerk Prüfstand GmbH current' }))

    const note = await within(await screen.findByRole('region', { name: 'Companies' })).findByRole('status')
    expect(note.textContent).toBe('Testwerk Prüfstand GmbH is now the current company.')
    await waitFor(async () => expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'testwerk' }))
    expect(window.location.pathname).toBe('/developer')
    expect(currentChanges(fetch)).toEqual([{ company: 'testwerk' }])
    // Opened afresh: the developer settings are read again by the new app.
    expect(fetch.mock.calls.filter(([url]) => url === '/api/developer/settings')).toHaveLength(2)
  })
})

describe('a company made in company setup, issue 409', () => {
  /** The API as `serve` answers it, for a developer, the list read again answering `relisted` (null: a 500) with the new
   * company current, and the Make current answering `madeCurrent`. */
  function serveDeveloper(relisted: ProfileSummary[] | null, madeCurrent: () => Response = () =>
    Response.json(SETTINGS)) {
    const fetch = serve([NORDLICHT, TESTWERK, MUSTERFIRMA])
    const answered = fetch.getMockImplementation()!
    let listings = 0
    fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/developer/settings') return Promise.resolve(Response.json(SETTINGS))
      if (url === '/api/developer/current-company') return Promise.resolve(madeCurrent())
      if (url === '/api/profiles' && ++listings > 1)
        return Promise.resolve(relisted ? Response.json(listOf(relisted, 'musterfirma-gmbh'))
          : new Response(null, { status: 500 }))
      if (url === '/api/profiles') return Promise.resolve(Response.json(listOf([NORDLICHT, TESTWERK])))
      return answered(url, init)
    })
    return fetch
  }

  it('makes it current, reads the list again and opens under it, on the Dossier', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    const fetch = serveDeveloper([NORDLICHT, TESTWERK, MUSTERFIRMA])
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))

    await dossierOf('Musterfirma GmbH')
    expect(window.location.pathname).toBe('/')
    expect(currentChanges(fetch)).toEqual([{ company: 'musterfirma-gmbh' }])
    const order = fetch.mock.calls.map(([url]) => url).filter((url) =>
      url === '/api/profiles' || url === '/api/developer/current-company')
    expect(order).toEqual(['/api/profiles', '/api/developer/current-company', '/api/profiles'])
    expect(overviewCalls(fetch)).toEqual(['nordlicht', 'musterfirma-gmbh']) // the setup page's, then the new company's
  })

  it('when the list can\'t be read again, still opens under the new company, which the user menu names', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    const fetch = serveDeveloper(null)
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))

    await dossierOf('Musterfirma GmbH')
    expect(overviewCalls(fetch)).toEqual(['nordlicht', 'musterfirma-gmbh'])
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    expect(screen.getByRole('menuitem', { name: `Start ${MUSTERFIRMA.company} afresh…` })).toBeTruthy()
  })

  it('a Make current answering 200 with an unreadable body still opens under the new company', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    const fetch = serveDeveloper([NORDLICHT, TESTWERK, MUSTERFIRMA], () => new Response('<html>proxy</html>'))
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))

    await dossierOf('Musterfirma GmbH')
    expect(window.location.pathname).toBe('/')
    expect(currentChanges(fetch)).toEqual([{ company: 'musterfirma-gmbh' }])
  })

  it('lands focus on the Dossier\'s main heading', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    serveDeveloper([NORDLICHT, TESTWERK, MUSTERFIRMA])
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))
    await dossierOf('Musterfirma GmbH')
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1 })))
  })

  it('not made current (a 503): opens the developer page under the current company, saying so, the company listed '
    + 'once', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    const fetch = serveDeveloper([NORDLICHT, TESTWERK, MUSTERFIRMA],
      () => Response.json({ detail: 'The database is unavailable just now.', unavailable: 'database' }, { status: 503 }))
    const answered = fetch.getMockImplementation()!
    let listings = 0
    fetch.mockImplementation((url: string, init?: RequestInit) => url === '/api/profiles' && ++listings > 1
      ? Promise.resolve(Response.json(listOf([NORDLICHT, TESTWERK, MUSTERFIRMA]))) // Nordlicht still current
      : answered(url, init))
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))

    const note = await within(await screen.findByRole('region', { name: 'Companies' })).findByRole('status')
    expect(note.textContent)
      .toBe('Musterfirma GmbH was made but could not be made current. Use Make current to try again.')
    expect(window.location.pathname).toBe('/developer')
    expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'nordlicht' })
    expect(currentChanges(fetch)).toEqual([{ company: 'musterfirma-gmbh' }]) // once: nothing made twice
    expect(fetch.mock.calls.filter(([url, init]) => url === '/api/developer/profiles' && init?.method === 'POST'))
      .toEqual([])
    expect(screen.queryByText('Goal: Internal improvement')).toBeNull()
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

  it('stays on the setup address until the list answers again, and after closing opens nothing', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    const release = heldRelisting()
    const { unmount } = render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))
    await waitFor(() => expect(reads).toHaveLength(2))
    expect(window.location.pathname).toBe('/developer/setup')
    unmount()
    release(Response.json(listOf([NORDLICHT, TESTWERK, MUSTERFIRMA], 'musterfirma-gmbh')))
    await reads[1]
    expect(await authHeaders()).toEqual({})
    expect(window.location.pathname).toBe('/developer/setup')
  })

  it('a 401 on the list read again signs out, and opens nothing', async () => {
    window.history.replaceState(null, '', '/developer/setup')
    const release = heldRelisting()
    const signOut = vi.fn()
    setOnSignedOut(signOut) // as ClerkApp's: the sign-in replaces the app on its next render, after this answer
    render(<ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: 'Created' }))
    await waitFor(() => expect(reads).toHaveLength(2))
    release(new Response(null, { status: 401 }))
    await reads[1]
    expect(signOut).toHaveBeenCalledOnce()
    expect(window.location.pathname).toBe('/developer/setup')
    expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'nordlicht' })
  })
})

describe('a company removed on the developer page, issues 418 and 480', () => {
  const MADE = { id: 'musterfirma-gmbh', company: 'Musterfirma GmbH', created_at: '2026-11-09T10:15:00+00:00' }
  const WITH_MADE = { ...SETTINGS, companies: [MADE], listed_companies: [...SETTINGS.listed_companies, MUSTERFIRMA] }

  /** The API, for a developer on the developer page with Musterfirma made in setup and `current` the current company:
   * the DELETE answers the settings without it (Nordlicht current), and the list read again answers `relisted` (null:
   * a 500) with Nordlicht current, or is held until `release` answers it when `hold` is set. */
  function serveRemoval(relisted: ProfileSummary[] | null, current = 'nordlicht', hold = false,
    listed: ProfileSummary[] = [NORDLICHT, TESTWERK, MUSTERFIRMA], relistedCurrent = 'nordlicht') {
    window.history.replaceState(null, '', '/developer')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetch = serve(listed, current)
    const answered = fetch.getMockImplementation()!
    let listings = 0
    let release: (answer: Response) => void = () => {}
    const settings: DeveloperSettings = { ...WITH_MADE, current_company: current }
    fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/developer/settings') return Promise.resolve(Response.json(settings))
      if (url === '/api/developer/profiles/musterfirma-gmbh' && init?.method === 'DELETE')
        return Promise.resolve(Response.json(SETTINGS))
      if (url === '/api/profiles' && ++listings > 1) {
        if (hold) return new Promise<Response>((resolve) => { release = resolve })
        return Promise.resolve(relisted ? Response.json(listOf(relisted, relistedCurrent))
          : new Response(null, { status: 500 }))
      }
      return answered(url, init)
    })
    return { fetch, release: (answer: Response) => release(answer) }
  }

  const listings = (fetch: ReturnType<typeof serve>) => fetch.mock.calls.filter(([url]) => url === '/api/profiles')
  const settingsReads = (fetch: ReturnType<typeof serve>) =>
    fetch.mock.calls.filter(([url]) => url === '/api/developer/settings')
  const companies = () => screen.findByRole('region', { name: 'Companies' })

  async function removeMusterfirma() {
    await userEvent.click(await within(await companies()).findByRole('button', { name: 'Remove Musterfirma GmbH' }))
  }

  it('one not current: the list is read again; the app stays under the current company, saying only Removed',
    async () => {
      const { fetch } = serveRemoval([NORDLICHT, TESTWERK])
      render(<ProfiledApp />)
      await removeMusterfirma()

      await waitFor(() => expect(listings(fetch)).toHaveLength(2))
      await reads[1]
      expect(within(await companies()).getByRole('status').textContent).toBe('Removed Musterfirma GmbH.')
      expect(window.location.pathname).toBe('/developer')
      expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'nordlicht' })
      expect(settingsReads(fetch)).toHaveLength(1) // not opened afresh
    })

  it("the current one: opens the API's current company afresh on the developer page, saying so", async () => {
    const { fetch } = serveRemoval([NORDLICHT, TESTWERK], 'musterfirma-gmbh')
    render(<ProfiledApp />)
    await waitFor(async () => expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'musterfirma-gmbh' }))
    await removeMusterfirma()

    await waitFor(async () => expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'nordlicht' }))
    await waitFor(() => expect(settingsReads(fetch)).toHaveLength(2)) // opened afresh
    expect(window.location.pathname).toBe('/developer')
    // The note is carried through the app opened afresh (#418 review), and focus goes to it, not the heading.
    const note = await within(await companies()).findByRole('status')
    expect(note.textContent).toBe('Removed Musterfirma GmbH. Nordlicht Komponenten GmbH is now the current company.')
    expect(document.activeElement).toBe(note)
  })

  it("the current one, the list not read again: opens afresh naming none, so the API's current company answers",
    async () => {
      const { fetch } = serveRemoval(null, 'musterfirma-gmbh')
      render(<ProfiledApp />)
      await waitFor(async () => expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'musterfirma-gmbh' }))
      await removeMusterfirma()

      await waitFor(() => expect(settingsReads(fetch)).toHaveLength(2)) // opened afresh
      expect(await authHeaders()).toEqual({}) // no fallback worked out here
      expect(window.location.pathname).toBe('/developer')
    })

  it('one not shown, while another developer changed the current company: keeps the page as it is', async () => {
    const { fetch } = serveRemoval([NORDLICHT, TESTWERK], 'nordlicht', false, undefined, 'testwerk')
    render(<ProfiledApp />)
    await removeMusterfirma()

    await waitFor(() => expect(listings(fetch)).toHaveLength(2))
    await reads[1]
    expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'nordlicht' })
    expect(settingsReads(fetch)).toHaveLength(1) // not reopened
  })

  it("the current one, and no other company can be had: opens with none named, not under the one removed", async () => {
    serveRemoval(null, 'musterfirma-gmbh', false, [MUSTERFIRMA])
    render(<ProfiledApp />)
    await waitFor(async () => expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'musterfirma-gmbh' }))
    await removeMusterfirma()

    await waitFor(async () => expect(await authHeaders()).toEqual({}))
    expect(await screen.findByRole('region', { name: 'Developer settings' })).toBeTruthy()
  })

  it('a list read again that answers after a sign-out changes nothing', async () => {
    const { release } = serveRemoval(null, 'musterfirma-gmbh', true)
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    render(<ProfiledApp />)
    await removeMusterfirma()
    await screen.findByText('Removed Musterfirma GmbH. Nordlicht Komponenten GmbH is now the current company.')
    await waitFor(() => expect(reads).toHaveLength(2))
    release(new Response(null, { status: 401 }))
    await reads[1] // ProfiledApp has handled the late answer by now
    expect(signOut).toHaveBeenCalledOnce()
    expect(await authHeaders()).toEqual({ 'X-Vouch-Profile': 'musterfirma-gmbh' })
  })
})

describe('the first pass on a company just made, issue 447', () => {
  const SETTINGS = { model: 'm', runs_on: 'fake', fake_reason: 'no_key', prompt_version: 'v', usage_url: '',
    kept_in: 'memory', daily_limit: 10 }
  const PASS_DONE = { assessments: [{ thread_id: 't1', control_id: '1.1.1', status: 'waiting-for-review',
    verdict: 'partly-evidenced' }], skipped: [] }
  const LIMIT = 'The daily limit of profile passes is reached. It resets at midnight UTC.'
  const NOT_SET = 'The company was created, but its Goal could not be set to Internal improvement. Use Change goal to '
    + 'set it.'
  const NEVER = () => new Promise<Response>(() => {})

  type Answer = () => Response | Promise<Response>
  type Setup = {
    /** PUT /api/goal's answer; a 2xx keeps the goal, so the overview shows it. */
    goalAnswer?: Answer
    /** POST /api/profile-pass's answer. */
    passAnswer?: Answer
    /** Each read of the new company's overview, answered as `overviewAnswer(goalSent)` says (`goalSent`: a Goal change
     * has answered), null for the usual; a 2xx answer stands for the usual one. */
    overviewAnswer?: (goalSent: boolean) => Promise<Response> | null
    /** The new company's Goal before any change. */
    startingGoal?: Goal
  }

  /** The API for a developer on the setup page: Nordlicht and Testwerk listed, Musterfirma too once made. */
  function serveSetup({ goalAnswer = () => Response.json({ goal: 'internal-improvement' }),
    passAnswer = () => Response.json(PASS_DONE), overviewAnswer = () => null, startingGoal = 'audit-readiness',
  }: Setup = {}) {
    const fetch = serve([NORDLICHT, TESTWERK, MUSTERFIRMA])
    const answered = fetch.getMockImplementation()!
    let listings = 0
    let goalSent = false
    let goal: Goal = startingGoal
    fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/developer/settings') return Promise.resolve(Response.json(SETTINGS))
      if (url === '/api/profiles') {
        const made = ++listings > 1 // made current by then
        return Promise.resolve(Response.json(made ? listOf([NORDLICHT, TESTWERK, MUSTERFIRMA], 'musterfirma-gmbh')
          : listOf([NORDLICHT, TESTWERK])))
      }
      if (url === '/api/goal' && init?.method === 'PUT') {
        return Promise.resolve(goalAnswer()).then((answer) => {
          if (answer.ok) goal = (JSON.parse(String(init.body)) as { goal: Goal }).goal
          goalSent = true
          return answer
        })
      }
      if (url === '/api/profile-pass' && init?.method === 'POST') return Promise.resolve(passAnswer())
      if (url === '/api/overview') {
        const named = new Headers(init?.headers).get('X-Vouch-Profile')
        const company = [NORDLICHT, TESTWERK, MUSTERFIRMA].find((p) => p.id === named)?.company ?? NORDLICHT.company
        const usual = () => Response.json({ ...overviewOf(company), goal: named === 'musterfirma-gmbh' ? goal
          : 'audit-readiness' })
        if (named !== 'musterfirma-gmbh') return Promise.resolve(usual())
        return overviewAnswer(goalSent)?.then((answer) => answer.ok ? usual() : answer) ?? Promise.resolve(usual())
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
  const goalButton = () => screen.getByRole<HTMLButtonElement>('button', { name: 'Change goal' })

  async function createFromSetup(button = 'Created', strict = true) {
    window.history.replaceState(null, '', '/developer/setup')
    render(strict ? <StrictMode><ProfiledApp /></StrictMode> : <ProfiledApp />)
    await userEvent.click(await screen.findByRole('button', { name: button }))
  }
  /** As createFromSetup, then waits for the new company's Dossier. */
  async function createAndOpen(button = 'Created') {
    await createFromSetup(button)
    await dossierOf('Musterfirma GmbH')
  }

  it('sets Internal improvement under the new company, then runs one pass, once under StrictMode', async () => {
    const fetch = serveSetup()
    await createAndOpen()
    expect(await screen.findByText('1 assessment waiting for your review.')).toBeTruthy()
    expect(firstPassCalls(fetch)).toEqual([
      ['/api/goal', 'musterfirma-gmbh', JSON.stringify({ goal: 'internal-improvement' })],
      ['/api/profile-pass', 'musterfirma-gmbh', null],
    ])
    expect(await screen.findByText('Goal: Internal improvement')).toBeTruthy()
  })

  it('shows the running state on the hero while the pass runs', async () => {
    serveSetup({ passAnswer: NEVER })
    await createAndOpen()
    expect(await screen.findByRole('button', { name: 'Running the profile pass…' })).toBeTruthy()
    expect(screen.getByText('Vouch is assessing every open control in one run. This can take a few minutes.'))
      .toBeTruthy()
  })

  it('waits for the first overview read before changing the Goal, so that read is shown, not dropped', async () => {
    let release: () => void = () => {}
    const first = new Promise<Response>((resolve) => { release = () => resolve(new Response(null)) })
    let released = false
    let sentWhileHeld = false
    const fetch = serveSetup({ overviewAnswer: () => released ? null : first, passAnswer: NEVER, goalAnswer: () => {
      sentWhileHeld ||= !released
      return Response.json({ goal: 'internal-improvement' })
    } })
    await createFromSetup()
    await waitFor(() => expect(overviewCalls(fetch)).toContain('musterfirma-gmbh'))
    released = true
    release()
    expect(await screen.findByText('Goal: Internal improvement')).toBeTruthy()
    await screen.findByRole('button', { name: 'Running the profile pass…' })
    expect(firstPassCalls(fetch).map(([url]) => url)).toEqual(['/api/goal', '/api/profile-pass'])
    expect(sentWhileHeld).toBe(false) // the Goal change waited for the first read
  })

  it("when the read after the Goal change fails, shows the overview with the new Goal, not 'Opening' for ever",
    async () => {
    let failed = false // the one read after the change fails
    serveSetup({ passAnswer: NEVER, overviewAnswer: (goalSent) => goalSent && !failed && (failed = true)
      ? Promise.resolve(new Response(null, { status: 503 })) : null })
    await createAndOpen()
    expect(await screen.findByText('Goal: Internal improvement')).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Running the profile pass…' })).toBeTruthy()
    expect(screen.queryByText('Opening the dossier…')).toBeNull()
  })

  it('with Audit readiness chosen, sends no goal change and still runs the pass', async () => {
    const fetch = serveSetup()
    await createAndOpen('Created for audit')
    await screen.findByText('1 assessment waiting for your review.')
    expect(firstPassCalls(fetch)).toEqual([['/api/profile-pass', 'musterfirma-gmbh', null]])
    expect(screen.getByText('Goal: Audit readiness')).toBeTruthy()
  })

  it("sends no change for a Goal the overview already has", async () => {
    const fetch = serveSetup({ startingGoal: 'internal-improvement' })
    await createAndOpen()
    await screen.findByText('1 assessment waiting for your review.')
    expect(firstPassCalls(fetch)).toEqual([['/api/profile-pass', 'musterfirma-gmbh', null]])
  })

  it("sends Audit readiness when the overview has another Goal", async () => {
    const changed = serveSetup({ startingGoal: 'internal-improvement' })
    await createAndOpen('Created for audit')
    await screen.findByText('1 assessment waiting for your review.')
    expect(firstPassCalls(changed)).toEqual([
      ['/api/goal', 'musterfirma-gmbh', JSON.stringify({ goal: 'audit-readiness' })],
      ['/api/profile-pass', 'musterfirma-gmbh', null],
    ])
    expect(await screen.findByText('Goal: Audit readiness')).toBeTruthy()
  })

  it('while the Goal change is out, Change goal and Run a profile pass wait', async () => {
    let answer: () => void = () => {}
    const held = new Promise<Response>((resolve) => {
      answer = () => resolve(Response.json({ goal: 'internal-improvement' }))
    })
    const fetch = serveSetup({ goalAnswer: () => held, passAnswer: NEVER })
    await createAndOpen()
    await waitFor(() => expect(firstPassCalls(fetch).map(([url]) => url)).toEqual(['/api/goal']))
    expect(goalButton().disabled).toBe(true)
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Run a profile pass' }).disabled).toBe(true)
    answer()
    await screen.findByRole('button', { name: 'Running the profile pass…' })
    expect(passes(fetch)).toHaveLength(1)
    expect(goalButton().disabled).toBe(false)
  })

  it('a fresh start starts no pass again', async () => {
    const fetch = serveSetup()
    vi.stubGlobal('confirm', () => true)
    const answered = fetch.getMockImplementation()!
    fetch.mockImplementation((url: string, init?: RequestInit) => url === '/api/progress/fresh-start'
      ? Promise.resolve(Response.json({ fresh_start: '2026-11-10T10:00:00+00:00' }))
      : answered(url, init))
    await createAndOpen()
    await screen.findByText('1 assessment waiting for your review.')
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
    const fetch = serveSetup()
    fetch.mockImplementation(((answered) => (url: string, init?: RequestInit) => url === '/api/profiles'
      ? Promise.resolve(Response.json(listOf([NORDLICHT, TESTWERK, MUSTERFIRMA], 'musterfirma-gmbh')))
      : answered(url, init))(fetch.getMockImplementation()!))
    render(<StrictMode><ProfiledApp /></StrictMode>)
    await dossierOf('Musterfirma GmbH')
    await screen.findByRole('button', { name: 'Run a profile pass' })
    expect(firstPassCalls(fetch)).toEqual([])
  })

  it('a failed goal change says so under the Goal line, and the pass still runs', async () => {
    const fetch = serveSetup({ goalAnswer: () => new Response(null, { status: 503 }) })
    await createAndOpen()
    const alert = await screen.findByText(NOT_SET)
    expect(alert.getAttribute('role')).toBe('alert')
    expect(screen.getByText('Goal: Audit readiness')).toBeTruthy()
    await screen.findByText('1 assessment waiting for your review.')
    expect(firstPassCalls(fetch).map(([url]) => url)).toEqual(['/api/goal', '/api/profile-pass'])
  })

  it('the note that the Goal was not set ends with the next Goal change that goes through, for good', async () => {
    let changes = 0
    serveSetup({ goalAnswer: () => ++changes === 1 ? new Response(null, { status: 503 })
      : Response.json({ goal: 'internal-improvement' }) })
    await createAndOpen()
    await screen.findByText(NOT_SET)
    await screen.findByText('1 assessment waiting for your review.')
    await userEvent.click(goalButton())
    await userEvent.click(goalButton()) // in the in-app ask (#441)
    await screen.findByText('Goal: Internal improvement')
    expect(screen.queryByText(NOT_SET)).toBeNull()
    // A person's next click comes after a double-click's would: within 500ms of a confirm, the ask presses nothing (#527).
    await new Promise((done) => setTimeout(done, 500))
    await userEvent.click(goalButton()) // back to Audit readiness: the note stays gone
    await userEvent.click(goalButton())
    await screen.findByText('Goal: Audit readiness')
    expect(screen.queryByText(NOT_SET)).toBeNull()
  })

  it('a goal change answering 401 signs out and starts no pass', async () => {
    const fetch = serveSetup({ goalAnswer: () => new Response(null, { status: 401 }) })
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    await createAndOpen()
    await waitFor(() => expect(signOut).toHaveBeenCalled())
    // The sequence is over once the hero offers the pass again; starting one would have shown it running.
    await waitFor(() => expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Run a profile pass' }).disabled)
      .toBe(false))
    expect(passes(fetch)).toHaveLength(0)
  })

  it("the pass's daily limit leaves the company open, with the limit sentence and the per-control way on", async () => {
    serveSetup({ passAnswer: () => Response.json({ detail: LIMIT, limit: 'profile-pass' }, { status: 429 }) })
    await createAndOpen()
    expect(await screen.findByText(LIMIT)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Assess 1.1.1 on its own' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Run a profile pass' })).toBeTruthy()
    expect(barName()).toBe('Musterfirma GmbH')
  })
})
