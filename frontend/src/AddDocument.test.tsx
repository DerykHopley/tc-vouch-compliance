import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AddDocument, { ADD_FAILED, INVENTED_ONLY } from './AddDocument.tsx'
import { type ProfileDocument, prefill, versionFor } from './documents.ts'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const ADDED: ProfileDocument = {
  id: 'visitor-badge-policy', source: 'policies', title: 'Visitor badge policy', kind: 'documented', version: '2',
  approval_date: '2026-10-01', review_cycle: 'yearly', stale_because: null, stale: false, language: null,
  cited_by: [], origin: 'added',
}
const BUILT_IN: ProfileDocument = { ...ADDED, id: 'backup-policy', title: 'Backup policy', version: '1.0',
  origin: 'built-in' }

const FRONT = `---
title: Clean desk policy
kind: implemented
approval_date: 2026-09-14
review_cycle: half-yearly
---
# Clean desk
`

const file = (text: string, name = 'desk.md') => new File([text], name, { type: 'text/plain' })
const json = (body: unknown, status = 200) => Promise.resolve(Response.json(body, { status }))

function serve(...answers: (() => Promise<Response>)[]) {
  const fetch = vi.fn((_url: string, _init?: RequestInit) => {
    const answer = answers.shift()
    return answer ? answer() : new Promise<Response>(() => {})
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function sent(fetch: ReturnType<typeof serve>, call = 0) {
  const [url, init] = fetch.mock.calls[call]
  return { url, method: init?.method, body: JSON.parse(String(init?.body)) }
}

function shown(props: Partial<Parameters<typeof AddDocument>[0]> = {}) {
  const onAdded = vi.fn()
  const onCancel = vi.fn()
  render(<AddDocument documents={[ADDED, BUILT_IN]} onAdded={onAdded} onCancel={onCancel} {...props} />)
  return { onAdded, onCancel, form: screen.getByRole('form', { name: props.title ? 'Add new version' : 'Add document' }) }
}

async function choose(text: string, name?: string) {
  await userEvent.upload(screen.getByLabelText('File (.md or .txt)'), file(text, name))
}

describe('prefill and versionFor', () => {
  it('reads title, kind, approval date and review cycle from front matter', () => {
    expect(prefill(FRONT)).toEqual({ title: 'Clean desk policy', kind: 'record', approval_date: '2026-09-14',
      review_cycle: 'half-yearly' })
    expect(prefill('---\nkind: policy\napproval_date: "2026-01-02"\nreview_cycle: weekly\n---\n'))
      .toEqual({ kind: 'policy', approval_date: '2026-01-02' })
    expect(prefill('# No front matter\n')).toEqual({})
  })

  it('is the next version of an added document with the same title, ignoring case, else 1', () => {
    expect(versionFor([ADDED, BUILT_IN], 'visitor BADGE policy ')).toBe(3)
    expect(versionFor([ADDED, BUILT_IN], 'Backup policy')).toBe(1)
    expect(versionFor([ADDED], 'Something new')).toBe(1)
  })
})

describe('the Add document form (ticket 585)', () => {
  it('prefills its fields from the chosen file and shows the version it will be, with the invented-data note', async () => {
    const { form } = shown()
    expect(within(form).getByText(INVENTED_ONLY)).toBeTruthy()
    expect(within(form).getByText('Vouch will add this as version 1.')).toBeTruthy()

    await choose(FRONT)

    await waitFor(() => expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('Clean desk policy'))
    expect((screen.getByLabelText('Kind') as HTMLSelectElement).value).toBe('record')
    expect((screen.getByLabelText('Approval date') as HTMLInputElement).value).toBe('2026-09-14')
    expect((screen.getByLabelText('Review cycle') as HTMLSelectElement).value).toBe('half-yearly')
    await userEvent.clear(screen.getByLabelText('Title'))
    await userEvent.type(screen.getByLabelText('Title'), 'Visitor badge policy')
    expect(within(form).getByText('Vouch will add this as version 3.')).toBeTruthy()
  })

  it('for a new version, keeps the title fixed and prefills the rest from the newest version', async () => {
    const { form } = shown({ title: 'Visitor badge policy' })
    const title = screen.getByLabelText('Title') as HTMLInputElement
    expect(title.value).toBe('Visitor badge policy')
    expect(title.readOnly).toBe(true)
    expect(within(form).getByText('Vouch will add this as version 3.')).toBeTruthy()

    await choose(FRONT)

    await waitFor(() => expect((screen.getByLabelText('Kind') as HTMLSelectElement).value).toBe('record'))
    expect(title.value).toBe('Visitor badge policy')
  })

  it('prefills a new version from the added document whose title differs only in case or spaces', () => {
    shown({ title: ' visitor BADGE policy' })
    expect((screen.getByLabelText('Approval date') as HTMLInputElement).value).toBe('2026-10-01')
  })

  it('sends the file in base64 with the fields; busy and disabled while sending; then reports it added', async () => {
    let answer: (r: Response) => void = () => {}
    const fetch = serve(() => new Promise<Response>((resolve) => { answer = resolve }))
    const { onAdded } = shown()
    await choose(FRONT)
    await waitFor(() => expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('Clean desk policy'))

    await userEvent.click(screen.getByRole('button', { name: 'Add document' }))

    const add = screen.getByRole('button', { name: 'Add document' })
    expect(add.getAttribute('aria-busy')).toBe('true')
    expect((add as HTMLButtonElement).disabled).toBe(true)
    const { url, method, body } = sent(fetch)
    expect([url, method]).toEqual(['/api/documents', 'POST'])
    expect(body).toEqual({ filename: 'desk.md', content: btoa(FRONT), title: 'Clean desk policy', kind: 'record',
      approval_date: '2026-09-14', review_cycle: 'half-yearly', add_anyway: false })
    answer(Response.json({ document: { id: 'clean-desk-policy', version: 1 } }, { status: 201 }))
    await waitFor(() => expect(onAdded).toHaveBeenCalledOnce())
  })

  it("shows a refusal's sentence as a field note alert, and nothing is reported added", async () => {
    serve(() => json({ detail: 'Only Markdown (.md) and plain-text (.txt) files can be added.' }, 400))
    const { onAdded } = shown()
    await choose('# x\n', 'x.md')
    await userEvent.type(screen.getByLabelText('Title'), 'X')
    await userEvent.click(screen.getByRole('button', { name: 'Add document' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe('Only Markdown (.md) and plain-text (.txt) files can be added.')
    expect(alert.classList.contains('field-note')).toBe(true)
    expect(onAdded).not.toHaveBeenCalled()
  })

  it('says it could not be added when the request fails', async () => {
    serve(() => Promise.reject(new TypeError('offline')))
    shown()
    await choose('# x\n', 'x.md')
    await userEvent.type(screen.getByLabelText('Title'), 'X')
    await userEvent.click(screen.getByRole('button', { name: 'Add document' }))

    expect((await screen.findByRole('alert')).textContent).toBe(ADD_FAILED)
  })

  it('asks before choosing no file', async () => {
    const fetch = serve()
    shown()
    await userEvent.type(screen.getByLabelText('Title'), 'X')
    await userEvent.click(screen.getByRole('button', { name: 'Add document' }))

    expect((await screen.findByRole('alert')).textContent).toBe('Choose a .md or .txt file first.')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('shows the warning with Add anyway and Cancel; Add anyway sends it again with add_anyway', async () => {
    const warning = 'This document holds text that reads like an instruction to the assistant. Vouch treats it as data. Add it anyway?'
    const fetch = serve(() => json({ detail: warning, warning: 'instruction-like' }, 409),
      () => json({ document: { id: 'x', version: 1 } }, 201))
    const { onAdded } = shown()
    await choose('# x\n', 'x.md')
    await userEvent.type(screen.getByLabelText('Title'), 'X')
    await userEvent.click(screen.getByRole('button', { name: 'Add document' }))

    expect((await screen.findByRole('alert')).textContent).toBe(warning)
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Add anyway' }))

    await waitFor(() => expect(onAdded).toHaveBeenCalledOnce())
    expect(sent(fetch, 1).body.add_anyway).toBe(true)
  })

  it("the warning's Cancel closes the warning and sends nothing more", async () => {
    const fetch = serve(() => json({ detail: 'Add it anyway?', warning: 'instruction-like' }, 409))
    shown()
    await choose('# x\n', 'x.md')
    await userEvent.type(screen.getByLabelText('Title'), 'X')
    await userEvent.click(screen.getByRole('button', { name: 'Add document' }))
    await screen.findByRole('button', { name: 'Add anyway' })

    await userEvent.click(screen.getAllByRole('button', { name: 'Cancel' })[0])

    expect(screen.queryByRole('button', { name: 'Add anyway' })).toBeNull()
    expect(fetch).toHaveBeenCalledOnce()
  })
})
