import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Documents from './Documents.tsx'
import { DOCUMENTS_FAILED, type DocumentWords, type ProfileDocument, type ProfileDocuments } from './documents.ts'
import { BACKUP_POLICY } from './libraryFixtures.ts'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** The English words, as vouch.documents_tab.DOCUMENT_WORDS sends them. */
const ENGLISH: DocumentWords = {
  title: 'Documents',
  sources: { policies: 'Policies', records: 'Records', patch: 'Patch' },
  columns: { document: 'Document', kind: 'Kind', version: 'Version', approved: 'Approved', review: 'Review',
    status: 'Status', cited_by: 'Cited by' },
  kinds: { documented: 'Documented', implemented: 'Implemented' },
  cycles: { quarterly: 'Quarterly', 'half-yearly': 'Half-yearly', yearly: 'Yearly', 'two-yearly': 'Two-yearly' },
  stale: 'Stale', current: 'Current', no_version: 'no version', no_date: 'no date', no_cycle: 'none',
  not_cited: 'Not cited yet', no_documents: 'This profile has no evidence documents.',
}

const AUTHENTICATION: ProfileDocument = {
  id: 'authentication-standard', source: 'policies', title: 'Authentication standard', kind: 'documented',
  version: '3.1', approval_date: '2026-04-08', review_cycle: 'two-yearly', stale_because: null, stale: false,
  language: 'en', cited_by: ['1.1.1', '4.1.2'],
}
const BACKUP: ProfileDocument = { ...BACKUP_POLICY, cited_by: [] }
const RECORD: ProfileDocument = {
  id: 'mfa-rollout-record', source: 'records', title: 'MFA roll-out record', kind: 'implemented', version: '2026-Q3',
  approval_date: '2026-10-01', review_cycle: 'yearly', stale_because: null, stale: false, language: 'en',
  cited_by: ['4.1.2'],
}
const NORDLICHT: ProfileDocuments = { documents: [AUTHENTICATION, BACKUP, RECORD], words: ENGLISH, words_language: 'en' }

const json = (body: unknown, status = 200) => Promise.resolve(Response.json(body, { status }))

function serve(answer: () => Promise<Response>) {
  const fetch = vi.fn((url: string) => url === '/api/documents' ? answer() : new Promise<Response>(() => {}))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('the Documents page', () => {
  it('says it is loading, then shows the sheet in the words language: one table per source, with Cited by', async () => {
    serve(() => json(NORDLICHT))
    render(<Documents />)
    expect(screen.getByRole('status').textContent).toBe('Loading the documents…')

    const sheet = await screen.findByRole('article', { name: 'Documents' })
    expect(sheet.getAttribute('lang')).toBe('en')
    expect(within(sheet).getByRole('heading', { level: 1 }).textContent).toBe('Documents')
    const tables = within(sheet).getAllByRole('table')
    expect(tables.map((t) => t.querySelector('caption')?.textContent)).toEqual(['Policies', 'Records'])
    const [policies] = tables
    expect(within(policies).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['Document', 'Kind', 'Version', 'Approved', 'Review', 'Status', 'Cited by'])
    const [authentication, backup] = within(policies).getAllByRole('row').slice(1)
    const cells = (row: HTMLElement) => within(row).getAllByRole('cell').map((c) => c.textContent)
    expect(cells(authentication)).toEqual(['Documented', '3.1', '2026-04-08', 'Two-yearly', 'Current', '1.1.1, 4.1.2'])
    expect(cells(backup).slice(3)).toEqual(['Yearly', 'Stale', 'Not cited yet'])
    expect(within(backup).getByText('Not cited yet').className).toContain('muted')
  })

  it('with no documents, shows the no_documents sentence and no table', async () => {
    serve(() => json({ ...NORDLICHT, documents: [] }))
    render(<Documents />)

    const sheet = await screen.findByRole('article', { name: 'Documents' })
    expect(within(sheet).getByText(ENGLISH.no_documents)).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it.each([
    ['a network error', () => Promise.reject(new TypeError('Failed to fetch'))],
    ['a 500', () => json({ detail: 'The documents could not be read.' }, 500)],
  ])('on %s shows the notice and Try again, none of the sheet, and Try again reads once more', async (_, failing) => {
    let reads = 0
    const fetch = serve(() => ++reads === 1 ? failing() : json(NORDLICHT))
    render(<Documents />)

    expect((await screen.findByRole('alert')).textContent).toBe(DOCUMENTS_FAILED)
    expect(screen.queryByRole('article')).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('article', { name: 'Documents' })).toBeTruthy()
    expect(fetch.mock.calls.filter(([url]) => url === '/api/documents')).toHaveLength(2)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
