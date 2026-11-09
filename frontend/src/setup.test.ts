import { afterEach, describe, expect, it, vi } from 'vitest'
import { BACKUP_POLICY } from './libraryFixtures.ts'
import { profileTag, type ProfileSummary } from './profiles.ts'
import { setOnSignedOut } from './session.ts'
import {
  type AnswerLanguage, COUNTRIES, type CompanySetup, createCompany, frameworkControls, LANGUAGES, type LibraryDocument,
  newlyListed, notApplicableProblems, protectionLevels, type ProtectionScope, readyToCite, sampleLibrary, scopeWords,
} from './setup.ts'

afterEach(() => {
  vi.unstubAllGlobals()
  setOnSignedOut(null)
})

describe('the countries company setup offers', () => {
  it('are the EU, Iceland, Liechtenstein, Norway, Switzerland and the UK', () => {
    expect(COUNTRIES).toHaveLength(32)
    const names = COUNTRIES.map((c) => c.name)
    for (const name of ['Germany', 'Iceland', 'Liechtenstein', 'Norway', 'Switzerland', 'United Kingdom', 'Austria'])
      expect(names).toContain(name)
  })

  it("each name maps back to its ISO code through Intl's English region names, as the profile tag reads it", () => {
    const regions = new Intl.DisplayNames(['en'], { type: 'region' })
    for (const { code, name } of COUNTRIES) {
      expect(regions.of(code)).toBe(name)
      expect(profileTag({ id: 'x', company: 'X', country: name, answer_language: 'en' })).toBe(`${code} · EN`)
    }
  })
})

const HIGH: ProtectionScope = {
  level: 'high', requirements: { must: 37, should: 48, high: 14 }, total: 99, controls: 12, added: 14,
  added_controls: ['1.6.2', '4.1.2'],
}

describe('the protection levels', () => {
  it("read the route's levels", async () => {
    const fetch = vi.fn((_url: string) => Promise.resolve(new Response(JSON.stringify({ levels: [HIGH] }))))
    vi.stubGlobal('fetch', fetch)
    expect(await protectionLevels()).toEqual([HIGH])
    expect(fetch.mock.calls[0][0]).toBe('/api/developer/protection-levels')
  })

  it('are null when refused or unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 500 }))))
    expect(await protectionLevels()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    expect(await protectionLevels()).toBeNull()
  })

  it('say what normal brings in, and what a higher level adds', () => {
    const normal = { ...HIGH, level: 'normal' as const, total: 85, added: 0, added_controls: [] }
    expect(scopeWords(normal)).toBe('Must and should requirements: 85 in 12 controls.')
    expect(scopeWords(HIGH)).toBe('Adds 14 high-protection requirements, in 2 controls (1.6.2, 4.1.2): 99 in all.')
    expect(scopeWords({ ...HIGH, level: 'very high', added: 10, total: 109 }))
      .toBe('Adds 10 very-high-protection requirements, in 2 controls (1.6.2, 4.1.2): 109 in all.')
    expect(scopeWords({ ...HIGH, added: 1, added_controls: ['1.6.2'] }))
      .toBe('Adds 1 high-protection requirement, in 1 control (1.6.2): 99 in all.')
    expect(scopeWords({ ...normal, controls: 1 })).toBe('Must and should requirements: 85 in 1 control.')
  })
})

describe("the framework's controls", () => {
  const CONTROLS = [{ id: '1.1.1', question: 'To what extent are policies available?' }]

  it("read the route's controls", async () => {
    const fetch = vi.fn((_url: string) => Promise.resolve(new Response(JSON.stringify({ controls: CONTROLS }))))
    vi.stubGlobal('fetch', fetch)
    expect(await frameworkControls()).toEqual(CONTROLS)
    expect(fetch.mock.calls[0][0]).toBe('/api/developer/controls')
  })

  it('are null when refused or unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 500 }))))
    expect(await frameworkControls()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    expect(await frameworkControls()).toBeNull()
  })
})

describe("step 5's checks on the controls marked not applicable", () => {
  const CONTROLS = ['1.1.1', '1.6.1', '5.1.1'].map((id) => ({ id, question: `${id}?` }))

  it('find nothing wrong with none marked, or with each marked one given a reason', () => {
    expect(notApplicableProblems([], CONTROLS)).toEqual({ blank: [], allMarked: false })
    expect(notApplicableProblems([{ control: '5.1.1', reason: ' No crypto ' }], CONTROLS))
      .toEqual({ blank: [], allMarked: false })
  })

  it("name each marked control whose reason is empty or only spaces, in the framework's order", () => {
    const marked = [{ control: '5.1.1', reason: '  ' }, { control: '1.6.1', reason: 'Fine' }, { control: '1.1.1', reason: '' }]
    expect(notApplicableProblems(marked, CONTROLS).blank).toEqual(['1.1.1', '5.1.1'])
  })

  it('say when every control is marked, so none would apply', () => {
    const marked = CONTROLS.map((c) => ({ control: c.id, reason: 'Not here' }))
    expect(notApplicableProblems(marked, CONTROLS)).toEqual({ blank: [], allMarked: true })
    expect(notApplicableProblems(marked.slice(1), CONTROLS).allMarked).toBe(false)
  })

  it('find nothing to check while the controls are unknown', () => {
    expect(notApplicableProblems([{ control: '1.1.1', reason: '' }], [])).toEqual({ blank: [], allMarked: false })
  })
})

const DOCUMENT: LibraryDocument = BACKUP_POLICY

describe('the sample library', () => {
  it("reads the route's documents", async () => {
    const fetch = vi.fn((_url: string) => Promise.resolve(new Response(JSON.stringify({ documents: [DOCUMENT] }))))
    vi.stubGlobal('fetch', fetch)
    expect(await sampleLibrary()).toEqual([DOCUMENT])
    expect(fetch.mock.calls[0][0]).toBe('/api/developer/sample-library')
  })

  it('is null when refused or unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 500 }))))
    expect(await sampleLibrary()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    expect(await sampleLibrary()).toBeNull()
  })
})

describe("step 6's ready-to-cite line", () => {
  const current = { ...DOCUMENT, stale: false, stale_because: null }
  const record = { ...current, id: 'training-record', source: 'records' as const, kind: 'implemented' as const }

  it('says when nothing is chosen', () => {
    expect(readyToCite([])).toBe('No documents chosen yet.')
  })

  it('counts the chosen documents by kind, and the stale ones only when there are some', () => {
    expect(readyToCite([DOCUMENT, current, record])).toBe('Ready to cite: 3 documents (2 documented, 1 implemented, 1 stale).')
    expect(readyToCite([current])).toBe('Ready to cite: 1 document (1 documented, 0 implemented).')
  })
})

const SETUP: CompanySetup = {
  company: 'Musterfirma GmbH', country: 'Germany', answer_language: 'de', protection_level: 'high',
  not_applicable: [{ control: '1.6.1', reason: '  Remote only  ' }],
  evidence: { policies: ['backup-policy'], records: [] },
}
const CREATED = { id: 'musterfirma-gmbh', company: 'Musterfirma GmbH', country: 'Germany', answer_language: 'de' }

/** fetch for a create: GET /api/profiles answers `listings` in turn (null: a 500), the POST answers `post`. */
function createApi(post: Response | 'offline', ...listings: (ProfileSummary[] | null)[]) {
  const fetch = vi.fn((url: string, _init?: RequestInit) => {
    if (url === '/api/developer/profiles')
      return post === 'offline' ? Promise.reject(new TypeError('offline')) : Promise.resolve(post)
    const listed = listings.shift()
    return Promise.resolve(listed ? Response.json({ profiles: listed, default: 'nordlicht' })
      : new Response(null, { status: 500 }))
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
const NORDLICHT = { id: 'nordlicht', company: 'Nordlicht Komponenten GmbH', country: 'Germany', answer_language: 'en' }

describe('creating a company, issue 409', () => {
  it('posts the setup to the developer route, the name and each reason trimmed, and answers the created profile',
    async () => {
      const fetch = createApi(Response.json(CREATED, { status: 201 }), [NORDLICHT])
      expect(await createCompany({ ...SETUP, company: '  Musterfirma GmbH ' })).toEqual({ status: 'created', profile: CREATED })
      const [url, init] = fetch.mock.calls.find(([u]) => u === '/api/developer/profiles')!
      expect(url).toBe('/api/developer/profiles')
      expect(init?.method).toBe('POST')
      expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
      expect(JSON.parse(String(init?.body))).toEqual({
        ...SETUP, not_applicable: [{ control: '1.6.1', reason: 'Remote only' }],
      })
    })

  it('says why a refused setup was refused, from a string detail or a list', async () => {
    createApi(Response.json({ detail: 'evidence: no document x' }, { status: 422 }))
    expect(await createCompany(SETUP)).toEqual({ status: 'refused', problem: 'evidence: no document x' })
    createApi(Response.json(
      { detail: [{ loc: ['body', 'company'], msg: 'too long' }, { loc: ['body', 'country'], msg: 'empty' }] },
      { status: 422 }))
    expect(await createCompany(SETUP)).toEqual({ status: 'refused', problem: 'too long; empty' })
  })

  it('is unavailable on a 503, and failed on a 4xx: nothing was made', async () => {
    createApi(Response.json({ detail: 'x', unavailable: 'database' }, { status: 503 }), [NORDLICHT], [NORDLICHT, CREATED])
    expect(await createCompany(SETUP)).toEqual({ status: 'unavailable' })
    createApi(new Response('{}', { status: 403 }), [NORDLICHT], [NORDLICHT, CREATED])
    expect(await createCompany(SETUP)).toEqual({ status: 'failed' })
  })

  it.each([
    ['a 500', new Response('{}', { status: 500 }) as Response | 'offline'],
    ['a 502', new Response('{}', { status: 502 }) as Response | 'offline'],
    ['an unreachable server', 'offline' as const],
    ['a 201 whose body won\'t parse', new Response('<html>', { status: 201 }) as Response | 'offline'],
  ])('after %s, reads the list again: a new company of that name listed now was created', async (_, post) => {
    const fetch = createApi(post, [NORDLICHT], [NORDLICHT, CREATED])
    expect(await createCompany({ ...SETUP, company: ' Musterfirma GmbH ' })).toEqual({ status: 'created', profile: CREATED })
    expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/profiles', '/api/developer/profiles', '/api/profiles'])
  })

  it.each([
    ['nothing new is listed', [NORDLICHT, CREATED], [NORDLICHT, CREATED]],
    ['the list could not be read before', null, [NORDLICHT, CREATED]],
    ['the list could not be read after', [NORDLICHT], null],
    ['only another name is new', [NORDLICHT], [NORDLICHT, { ...CREATED, id: 'x', company: 'Other GmbH' }]],
  ])('after a lost answer, it may not have been created when %s', async (_, before, after) => {
    createApi(new Response('{}', { status: 500 }), before, after)
    expect(await createCompany(SETUP)).toEqual({ status: 'unknown' })
  })

  it('signs out on a 401', async () => {
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    createApi(new Response(null, { status: 401 }), [NORDLICHT])
    expect(await createCompany(SETUP)).toEqual({ status: 'signed-out' })
    expect(signOut).toHaveBeenCalledOnce()
  })
})

describe('newlyListed', () => {
  const before = { profiles: [NORDLICHT, CREATED], default: 'nordlicht' }
  const twin = { ...CREATED, id: 'musterfirma-gmbh-2' }

  it('is the profile of that name whose id was not listed before', () => {
    expect(newlyListed(before, { ...before, profiles: [...before.profiles, twin] }, 'Musterfirma GmbH')).toEqual(twin)
  })

  it('is none when either list is missing or nothing of that name is new', () => {
    expect(newlyListed(null, before, 'Musterfirma GmbH')).toBeNull()
    expect(newlyListed(before, null, 'Musterfirma GmbH')).toBeNull()
    expect(newlyListed(before, before, 'Musterfirma GmbH')).toBeNull()
  })
})

describe('the answer languages, issue 409', () => {
  it('are English and German, by code', () => {
    const codes: AnswerLanguage[] = LANGUAGES.map((l) => l.code)
    expect(codes).toEqual(['en', 'de'])
    expect(LANGUAGES.map((l) => l.name)).toEqual(['English', 'German'])
  })
})
