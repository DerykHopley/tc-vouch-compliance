import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CompanyFacts } from './company.ts'
import CompanyPage from './CompanyPage.tsx'
import type { Overview, OverviewState } from './overview.ts'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const REASON = 'Nordlicht allows no remote access to its company network.'

/** Nordlicht's facts, as `GET /api/company` sends them. */
const NORDLICHT: CompanyFacts = {
  company: 'Nordlicht Komponenten GmbH', country: 'Germany', answer_language: 'en', base_framework: 'VDA ISA 2027.1',
  protection_level: 'normal', overlays: [],
  scope: { level: 'normal', requirements: { must: 37, should: 48 }, total: 85, controls: 12, added: 0,
    added_controls: [] },
  not_applicable: [{ control_id: '5.1.2', requirement_id: 'S3', reason: REASON }],
  defined_in: 'files',
}

/** Alpenwerk's facts: German answers, high protection and one overlay. */
const ALPENWERK: CompanyFacts = {
  ...NORDLICHT, company: 'Alpenwerk Präzision GmbH', country: 'Austria', answer_language: 'de',
  protection_level: 'high', overlays: [{ name: 'AT NIS2 (illustrativ)', requirements: 1 }],
  scope: { level: 'high', requirements: { must: 37, should: 48, high: 14 }, total: 99, controls: 12, added: 14,
    added_controls: ['1.6.2'] },
  not_applicable: [{ control_id: '1.1.1', requirement_id: null, reason: 'Nicht unseres.' }],
}

const OVERVIEW = {
  company: NORDLICHT.company, country: 'Germany', protection_level: 'normal', answer_language: 'en',
  goal: 'audit-readiness', rounds: { current: { name: 'First round', opened_at: null }, comparison: null },
} as Overview
const READY: OverviewState = { status: 'ready', overview: OVERVIEW }

const DOCUMENTS = {
  documents: [{ id: 'backup-policy', source: 'policies', title: 'Backup policy', kind: 'documented', version: '1.0',
    approval_date: '2026-01-01', review_cycle: 'yearly', stale_because: null, stale: false, language: 'en',
    cited_by: [] }],
  words: { title: 'Documents', sources: { policies: 'Policies' }, columns: { document: 'Document', kind: 'Kind',
    version: 'Version', approved: 'Approved', review: 'Review', status: 'Status', cited_by: 'Cited by' },
  kinds: { documented: 'Documented', implemented: 'Implemented' }, cycles: { yearly: 'Yearly' }, stale: 'Stale',
  stale_reasons: {}, current: 'Current', no_version: 'no version', no_date: 'no date', no_cycle: 'none',
  not_cited: 'Not cited yet', no_documents: 'This profile has no evidence documents.' },
  words_language: 'en',
}

const json = (body: unknown, status = 200) => Promise.resolve(Response.json(body, { status }))

/** The API: the company's facts and the documents, each as given; nothing else answers. */
function serve(company: () => Promise<Response> = () => json(NORDLICHT),
  documents: () => Promise<Response> = () => json(DOCUMENTS)) {
  const fetch = vi.fn((url: string) => url === '/api/company' ? company()
    : url === '/api/documents' ? documents() : new Promise<Response>(() => {}))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function show(overview: OverviewState = READY, onRetry = () => {}) {
  return render(<CompanyPage overview={overview} onRetryOverview={onRetry} onGoalChanged={async () => true}
    onRoundChanged={async () => true} arriving={false} />)
}

const panel = (name: string) => screen.getByRole('region', { name })
const factsStrip = () => document.querySelector<HTMLElement>('dl.company-facts')!

describe('the Company page (ticket 505)', () => {
  it("shows the company's name as the only h1, and the five facts", async () => {
    serve()
    show()

    const heading = await screen.findByRole('heading', { level: 1 })
    expect(heading.textContent).toBe('Nordlicht Komponenten GmbH')
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    const facts = factsStrip()
    const terms = [...facts.querySelectorAll('dt')].map((t) => t.textContent)
    const values = [...facts.querySelectorAll('dd')].map((d) => d.textContent)
    expect(terms).toEqual(['Country', 'Answer language', 'Base framework', 'Protection level', 'Overlays'])
    expect(values).toEqual(['Germany', 'English', 'VDA ISA 2027.1', 'Normal', 'None'])
  })

  it("names an overlay in the facts in the answer language's lang", async () => {
    serve(() => json(ALPENWERK))
    show()

    await screen.findByRole('heading', { level: 1 })
    const facts = factsStrip()
    const overlay = within(facts).getByText('AT NIS2 (illustrativ)')
    expect(overlay.getAttribute('lang')).toBe('de')
    expect([...facts.querySelectorAll('dd')].map((d) => d.textContent)).toEqual(
      ['Austria', 'German', 'VDA ISA 2027.1', 'High', 'AT NIS2 (illustrativ)'])
  })

  it('says a company defined in files is read-only apart from the Goal', async () => {
    serve()
    show()

    expect(await screen.findByText(
      "This company is defined in files: its details can't be changed here, apart from the Goal.")).toBeTruthy()
  })

  it('says a company made in Company setup is read-only apart from the Goal', async () => {
    serve(() => json({ ...NORDLICHT, defined_in: 'setup' }))
    show()

    expect(await screen.findByText(
      "This company was made in Company setup: its details can't be changed here, apart from the Goal.")).toBeTruthy()
    expect(screen.queryByText(/defined in files/)).toBeNull()
  })

  it('the Scope panel says what the level brings in, each column with its count, and no overlay', async () => {
    serve()
    show()

    await screen.findByRole('heading', { level: 1 })
    const lines = [...panel('Scope').querySelectorAll('p, li')].map((l) => l.textContent)
    expect(lines).toEqual([
      'Normal protection brings in 85 requirements of VDA ISA 2027.1, in 12 controls.',
      'Must: 37', 'Should: 48',
      'No country overlay applies.',
    ])
  })

  it("the Scope panel names each overlay with what it adds, in the answer language's lang", async () => {
    serve(() => json(ALPENWERK))
    show()

    await screen.findByRole('heading', { level: 1 })
    const scope = panel('Scope')
    expect([...scope.querySelectorAll('p, li')].map((l) => l.textContent)).toEqual([
      'High protection brings in 99 requirements of VDA ISA 2027.1, in 12 controls.',
      'Must: 37', 'Should: 48', 'High protection: 14',
      'AT NIS2 (illustrativ) adds 1 requirement.',
    ])
    expect(within(scope).getByText('AT NIS2 (illustrativ)').getAttribute('lang')).toBe('de')
  })

  it('the Not applicable panel lists each entry in mono with its reason in the answer language', async () => {
    serve()
    show()

    await screen.findByRole('heading', { level: 1 })
    const listed = panel('Not applicable')
    const id = within(listed).getByText('5.1.2 S3')
    expect(id.className).toContain('mono')
    expect(within(listed).getByText(REASON).getAttribute('lang')).toBe('en')
  })

  it('the Not applicable panel names a whole control as such', async () => {
    serve(() => json(ALPENWERK))
    show()

    await screen.findByRole('heading', { level: 1 })
    const listed = panel('Not applicable')
    expect(within(listed).getByText('1.1.1, the whole control')).toBeTruthy()
    expect(within(listed).getByText('Nicht unseres.').getAttribute('lang')).toBe('de')
  })

  it('the Not applicable panel says when nothing is', async () => {
    serve(() => json({ ...NORDLICHT, not_applicable: [] }))
    show()

    await screen.findByRole('heading', { level: 1 })
    expect(within(panel('Not applicable')).getByText('Nothing is marked not applicable.')).toBeTruthy()
  })

  it('the Upload documents panel is a placeholder with no control', async () => {
    serve()
    show()

    await screen.findByRole('heading', { level: 1 })
    const upload = panel('Upload documents')
    expect(within(upload).getByText(
      "Uploading documents isn't available yet. For now, Vouch reads the documents listed under Documents.")).toBeTruthy()
    expect(within(upload).queryAllByRole('button')).toHaveLength(0)
    expect(within(upload).queryAllByRole('link')).toHaveLength(0)
    expect(upload.querySelectorAll('input, select, textarea')).toHaveLength(0)
  })

  it('the Goal and Review rounds panels hold the Goal and round controls', async () => {
    serve()
    show()

    await screen.findByRole('heading', { level: 1 })
    expect(within(panel('Goal')).getByText('Goal: Audit readiness')).toBeTruthy()
    expect(within(panel('Goal')).getByRole('button', { name: 'Change goal' })).toBeTruthy()
    expect(within(panel('Review rounds')).getByRole('button', { name: 'Close round' })).toBeTruthy()
  })

  it('puts the panels before the documents in the page, so the reading order is the phone order', async () => {
    serve()
    show()

    await screen.findByRole('heading', { level: 1 })
    const names = screen.getAllByRole('region').map((r) => r.getAttribute('aria-label')
      ?? document.getElementById(r.getAttribute('aria-labelledby') ?? '')?.textContent)
    expect(names).toEqual(['Goal', 'Review rounds', 'Scope', 'Not applicable', 'Upload documents', 'Documents'])
  })

  it('shows the documents sheet in the Documents panel, its served title an h2', async () => {
    serve()
    show()

    const documents = await screen.findByRole('region', { name: 'Documents' })
    const sheet = await within(documents).findByRole('article', { name: 'Documents' })
    expect(within(sheet).getByRole('heading', { level: 2 }).textContent).toBe('Documents')
    expect(within(sheet).getByRole('table')).toBeTruthy()
  })

  it("keeps the page up when the documents can't be read, their notice and Try again in their panel", async () => {
    serve(undefined, () => json({}, 500))
    show()

    const documents = await screen.findByRole('region', { name: 'Documents' })
    expect((await within(documents).findByRole('alert')).textContent).toBe('The documents could not be loaded.')
    expect(within(documents).getByRole('button', { name: 'Try again' })).toBeTruthy()
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Nordlicht Komponenten GmbH')
    expect(panel('Scope')).toBeTruthy()
  })

  it('says it is loading until both the company and the overview are read', async () => {
    serve()
    const { rerender } = show({ status: 'loading' })

    expect(screen.getByRole('status').textContent).toBe('Loading the company…')
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => url === '/api/company')).toBe(true))
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull()

    rerender(<CompanyPage overview={READY} onRetryOverview={() => {}} onGoalChanged={async () => true}
      onRoundChanged={async () => true} arriving={false} />)
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Nordlicht Komponenten GmbH')
  })

  it("says the company couldn't be loaded, none of the page, and Try again reads it again", async () => {
    let fails = true
    const fetch = serve(() => fails ? json({ detail: 'The company could not be loaded.' }, 500) : json(NORDLICHT))
    show()

    expect((await screen.findByRole('alert')).textContent).toBe('The company could not be loaded.')
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Documents' })).toBeNull()

    fails = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Nordlicht Komponenten GmbH')
    expect(fetch.mock.calls.filter(([url]) => url === '/api/company')).toHaveLength(2)
  })

  it("says the company couldn't be loaded when the overview failed, and Try again reads that again too", async () => {
    serve()
    const retry = vi.fn()
    show({ status: 'failed', message: 'The overview could not be loaded just now. Please try again.' }, retry)

    expect((await screen.findByRole('alert')).textContent).toBe('The company could not be loaded.')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalledOnce()
  })
})
