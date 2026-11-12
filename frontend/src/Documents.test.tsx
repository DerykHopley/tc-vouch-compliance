import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Documents from './Documents.tsx'
import { CHANGE_REFUSED_NOTE, DOCUMENTS_FAILED, type DocumentWords, WITHDRAW_FAILED, withdrawDocument, type ProfileDocument, type ProfileDocuments, useDocuments }
  from './documents.ts'
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
    status: 'Status', cited_by: 'Cited by', origin: 'Origin' },
  kinds: { documented: 'Documented', implemented: 'Implemented' },
  cycles: { quarterly: 'Quarterly', 'half-yearly': 'Half-yearly', yearly: 'Yearly', 'two-yearly': 'Two-yearly' },
  stale: 'Stale',
  stale_reasons: { no_approval_date: 'no approval date', no_review_cycle: 'no review cycle',
    unknown_cycle: 'unknown review cycle' },
  current: 'Current', no_version: 'no version', no_date: 'no date', no_cycle: 'none',
  not_cited: 'Not cited yet', no_documents: 'This profile has no evidence documents.',
  origins: { 'built-in': 'Built in', added: 'Added' }, withdrawn: 'Withdrawn',
}

const AUTHENTICATION: ProfileDocument = {
  id: 'authentication-standard', source: 'policies', title: 'Authentication standard', kind: 'documented',
  version: '3.1', approval_date: '2026-04-08', review_cycle: 'two-yearly', stale_because: null, stale: false,
  language: 'en', cited_by: ['1.1.1', '4.1.2'], origin: 'built-in',
}
const BACKUP: ProfileDocument = { ...BACKUP_POLICY, cited_by: [], origin: 'built-in' }
const RECORD: ProfileDocument = {
  id: 'mfa-rollout-record', source: 'records', title: 'MFA roll-out record', kind: 'implemented', version: '2026-Q3',
  approval_date: '2026-10-01', review_cycle: 'yearly', stale_because: null, stale: false, language: 'en',
  cited_by: ['4.1.2'], origin: 'built-in',
}
const ADDED: ProfileDocument = {
  id: 'visitor-badge-policy', source: 'policies', title: 'Visitor badge policy', kind: 'documented', version: '2',
  approval_date: '2026-10-01', review_cycle: 'yearly', stale_because: null, stale: false, language: null,
  cited_by: [], origin: 'added',
}
const NORDLICHT: ProfileDocuments = { documents: [AUTHENTICATION, BACKUP, RECORD], words: ENGLISH, words_language: 'en',
  can_change: true }

/** The panel as the Company page shows it: read with useDocuments, then shown. */
function Shown() {
  const { state, tryAgain } = useDocuments()
  return <Documents state={state} tryAgain={tryAgain} onChanged={() => {}} />
}

const json = (body: unknown, status = 200) => Promise.resolve(Response.json(body, { status }))

function serve(answer: () => Promise<Response>) {
  const fetch = vi.fn((url: string) => url === '/api/documents' ? answer() : new Promise<Response>(() => {}))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('the Documents panel of the Company page (ticket 505: its title an h2)', () => {
  it('says it is loading, then shows the sheet in the words language: one table per source, with Cited by', async () => {
    serve(() => json(NORDLICHT))
    render(<Shown />)
    expect(screen.getByRole('status').textContent).toBe('Loading the documents…')

    const sheet = await screen.findByRole('article', { name: 'Documents' })
    expect(sheet.getAttribute('lang')).toBe('en')
    expect(within(sheet).getByRole('heading', { level: 2 }).textContent).toBe('Documents')
    const tables = within(sheet).getAllByRole('table')
    expect(tables.map((t) => t.querySelector('caption')?.textContent)).toEqual(['Policies', 'Records'])
    const [policies] = tables
    expect(within(policies).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['Document', 'Kind', 'Version', 'Approved', 'Review', 'Status', 'Cited by', 'Origin'])
    const [authentication, backup] = within(policies).getAllByRole('row').slice(1)
    const cells = (row: HTMLElement) => within(row).getAllByRole('cell').map((c) => c.textContent)
    expect(cells(authentication)).toEqual(['Documented', '3.1', '2026-04-08', 'Two-yearly', 'Current', '1.1.1, 4.1.2',
      'Built in'])
    expect(cells(backup).slice(3)).toEqual(['Yearly', 'Stale', 'Not cited yet', 'Built in'])
    expect(within(backup).getByText('Not cited yet').className).toBe('report-muted')
  })

  it("names a source it has no word for as written, even one named like an object's own member", async () => {
    serve(() => json({ ...NORDLICHT, documents: [{ ...RECORD, source: 'constructor' }, { ...BACKUP, source: 'toString' }] }))
    render(<Shown />)

    const sheet = await screen.findByRole('article', { name: 'Documents' })
    expect(within(sheet).getAllByRole('table').map((t) => t.querySelector('caption')?.textContent))
      .toEqual(['constructor', 'toString'])
  })

  it('with no documents, shows the no_documents sentence and no table', async () => {
    serve(() => json({ ...NORDLICHT, documents: [] }))
    render(<Shown />)

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
    render(<Shown />)

    expect((await screen.findByRole('alert')).textContent).toBe(DOCUMENTS_FAILED)
    expect(screen.queryByRole('article')).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('article', { name: 'Documents' })).toBeTruthy()
    expect(fetch.mock.calls.filter(([url]) => url === '/api/documents')).toHaveLength(2)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('adding documents on the Documents panel (ticket 585)', () => {
  it('labels built-in documents "Built in" with no action, and offers Add new version on an added one', async () => {
    serve(() => json({ ...NORDLICHT, documents: [AUTHENTICATION, ADDED] }))
    render(<Shown />)

    const sheet = await screen.findByRole('article', { name: 'Documents' })
    const [authentication, added] = within(sheet).getAllByRole('row').slice(1)
    expect(within(authentication).getByText('Built in')).toBeTruthy()
    expect(within(authentication).queryAllByRole('button')).toHaveLength(0)
    expect(within(added).getByText('Added')).toBeTruthy()
    expect(within(added).getByRole('button', { name: 'Add new version of Visitor badge policy' })).toBeTruthy()
  })

  it('Add document opens the form, Cancel closes it; Add new version opens it with the title fixed', async () => {
    serve(() => json({ ...NORDLICHT, documents: [ADDED] }))
    render(<Shown />)

    await userEvent.click(await screen.findByRole('button', { name: 'Add document' }))
    expect(screen.getByRole('form', { name: 'Add document' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('form')).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Add new version of Visitor badge policy' }))
    const form = screen.getByRole('form', { name: 'Add new version' })
    expect((within(form).getByLabelText('Title') as HTMLInputElement).value).toBe('Visitor badge policy')
    expect(within(form).getByText('Vouch will add this as version 3.')).toBeTruthy()
  })

  it('after an add, closes the form and asks the page to read the documents again', async () => {
    const fetch = vi.fn((url: string) => url === '/api/documents' && fetch.mock.calls.length > 1
      ? json({ document: { id: 'x', version: 1 } }, 201) : json(NORDLICHT))
    vi.stubGlobal('fetch', fetch)
    const onChanged = vi.fn()
    function Page() {
      const { state, tryAgain } = useDocuments()
      return <Documents state={state} tryAgain={tryAgain} onChanged={onChanged} />
    }
    render(<Page />)
    await userEvent.click(await screen.findByRole('button', { name: 'Add document' }))
    await userEvent.upload(screen.getByLabelText('File (.md or .txt)'), new File(['# x'], 'x.md'))
    await userEvent.type(screen.getByLabelText('Title'), 'X')

    await userEvent.click(within(screen.getByRole('form')).getByRole('button', { name: 'Add document' }))

    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce())
    expect(screen.queryByRole('form')).toBeNull()
  })
})

describe('who may change the documents (ticket 624)', () => {
  it('shows a user who may not change them no Add document, Add new version or Withdraw, and says who can', async () => {
    serve(() => json({ ...NORDLICHT, documents: [AUTHENTICATION, ADDED], can_change: false }))
    render(<Shown />)

    const sheet = await screen.findByRole('article', { name: 'Documents' })
    expect(within(sheet).queryAllByRole('button')).toHaveLength(0)
    expect(within(sheet).getByText(CHANGE_REFUSED_NOTE)).toBeTruthy()
  })

  it("shows a refused withdraw's 403 in the server's words, not Try again", async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ detail: CHANGE_REFUSED_NOTE }, 403)))
    expect(await withdrawDocument('visitor-badge-policy')).toEqual({ ok: false, message: CHANGE_REFUSED_NOTE })
  })

  it('shows one who may change them the actions and no note', async () => {
    serve(() => json({ ...NORDLICHT, documents: [ADDED] }))
    render(<Shown />)

    const sheet = await screen.findByRole('article', { name: 'Documents' })
    expect(within(sheet).getByRole('button', { name: 'Add document' })).toBeTruthy()
    expect(within(sheet).queryByText(CHANGE_REFUSED_NOTE)).toBeNull()
  })
})

describe('withdrawing an added document (ticket 586)', () => {
  const ASK = 'Withdraw "Visitor badge policy"? It is no longer searched or cited. Its versions are kept, so the '
    + 'audit trail stays true.'
  const WITHDRAWN: ProfileDocument = { ...ADDED, withdrawn: true }
  const WORDS = { ...ENGLISH, withdrawn: 'Withdrawn' }

  /** The panel on a server answering the documents read with `documents`, and the withdraw with `answer`. */
  function withdrawing(answer: () => Promise<Response>, documents: ProfileDocument[] = [AUTHENTICATION, ADDED]) {
    const fetch = vi.fn((url: string) => url.endsWith('/withdraw') ? answer()
      : json({ ...NORDLICHT, words: WORDS, documents }))
    vi.stubGlobal('fetch', fetch)
    const onChanged = vi.fn()
    function Page() {
      const { state, tryAgain } = useDocuments()
      return <Documents state={state} tryAgain={tryAgain} onChanged={onChanged} />
    }
    render(<Page />)
    return { fetch, onChanged }
  }
  const press = async (name: string) => userEvent.click(await screen.findByRole('button', { name }))
  const confirm = () => userEvent.click(within(screen.getByRole('alert').parentElement!)
    .getByRole('button', { name: 'Withdraw' }))

  it('lists a withdrawn document with the Withdrawn status and no actions; a built-in one has no Withdraw', async () => {
    withdrawing(() => json({}), [AUTHENTICATION, WITHDRAWN])

    const sheet = await screen.findByRole('article', { name: 'Documents' })
    const [authentication, withdrawn] = within(sheet).getAllByRole('row').slice(1)
    expect(within(withdrawn).getAllByRole('cell')[4].textContent).toBe('Withdrawn')
    expect(within(withdrawn).queryAllByRole('button')).toHaveLength(0)
    expect(within(authentication).queryByRole('button', { name: /Withdraw/ })).toBeNull()
  })

  it('asks first in the app; Cancel withdraws nothing', async () => {
    const { fetch } = withdrawing(() => json({}))
    await press('Withdraw Visitor badge policy')

    expect(screen.getByRole('alert').textContent).toBe(ASK)
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(fetch.mock.calls.some(([url]) => url.endsWith('/withdraw'))).toBe(false)
  })

  it('withdraws on Withdraw: busy and disabled while it runs, then reads the documents again', async () => {
    let finish: (answer: Response) => void = () => {}
    const { fetch, onChanged } = withdrawing(() => new Promise<Response>((done) => { finish = done }))
    await press('Withdraw Visitor badge policy')
    await confirm()

    const busy = screen.getByRole('button', { name: 'Withdrawing Visitor badge policy' })
    expect(busy.getAttribute('aria-busy')).toBe('true')
    expect(busy.getAttribute('aria-disabled')).toBe('true')
    expect(fetch).toHaveBeenCalledWith('/api/documents/visitor-badge-policy/withdraw',
      expect.objectContaining({ method: 'POST' }))
    finish(Response.json({ document: { id: 'visitor-badge-policy', withdrawn: true } }))
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce())
  })

  it.each([
    ['a 503', () => json({ detail: 'The database is unavailable.' }, 503)],
    ['a network error', () => Promise.reject(new TypeError('Failed to fetch'))],
  ])('on %s says it could not be withdrawn, and Withdraw may be pressed again', async (_, failing) => {
    const { onChanged } = withdrawing(failing)
    await press('Withdraw Visitor badge policy')
    await confirm()

    expect((await screen.findByText(WITHDRAW_FAILED)).getAttribute('role')).toBe('alert')
    expect(screen.getByRole('button', { name: 'Withdraw Visitor badge policy' })).toBeTruthy()
    expect(onChanged).not.toHaveBeenCalled()
  })
})
