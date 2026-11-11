import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RoundComparisonView, { type ControlNames } from './RoundComparison.tsx'
import { COMPARISON_FAILED, type RoundComparison } from './roundComparison.ts'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** As GET /api/rounds/2/comparison serves it (#571): now against Q3 2026. */
const SERVED: RoundComparison = {
  round: { number: 2, name: 'Q3 2026', opened_at: '2026-10-01T09:00:00+00:00', closed_at: '2026-10-31T16:00:00+00:00',
    before_fresh_start: false },
  changes: { better: 1, worse: 1, new: 1, unchanged: 3 },
  readiness: {
    accepted: { then: 5, now: 4 },
    by_verdict: {
      evidenced: { then: 4, now: 2 },
      'partly-evidenced': { then: 1, now: 2 },
      'not-evidenced': { then: 0, now: 0 },
      'not-applicable': { then: 0, now: 0 },
    },
  },
  controls: [
    { control_id: '1.1.1', chapter: '1', change: 'better', before: 'partly-evidenced', after: 'evidenced', proposed: false },
    { control_id: '1.2.1', chapter: '1', change: 'unchanged', before: 'evidenced', after: 'evidenced', proposed: false },
    { control_id: '1.3.1', chapter: '1', change: 'unchanged', before: 'evidenced', after: 'evidenced', proposed: true },
    { control_id: '4.1.2', chapter: '4', change: 'worse', before: 'evidenced', after: null, proposed: false },
    { control_id: '4.1.3', chapter: '4', change: 'new', before: null, after: 'partly-evidenced', proposed: false },
    { control_id: '5.1.1', chapter: '5', change: 'new-since', before: null, after: null, proposed: false },
    { control_id: '5.1.2', chapter: '5', change: 'unchanged', before: null, after: null, proposed: false },
    { control_id: '5.2.1', chapter: '5', change: 'out-of-scope-since', before: 'evidenced', after: null, proposed: false },
  ],
  remediation_done: [{ control_id: '4.1.2', requirement_id: 'M2', at: '2026-11-02T10:00:00+00:00' }],
  questions_answered: [{ control_id: '1.3.1', question_id: 7, at: '2026-11-03T10:00:00+00:00' }],
}

const NAMES: ControlNames = {
  answer_language: 'en',
  controls: ['1.1.1', '1.2.1', '1.3.1', '4.1.2', '4.1.3', '5.1.1', '5.1.2']
    .map((id) => ({ id, question: `To what extent is control ${id} met?` })),
  requirements: { '4.1.2': [{ id: 'M2', text: 'Access rights are reviewed.' }] },
  questions: { '1.3.1': [{ id: 7, question: 'Who signs the policy?' }] },
}

const json = (body: unknown, status = 200) => Promise.resolve(Response.json(body, { status }))

function serve(answer: () => Promise<Response>) {
  const fetch = vi.fn((url: string) => url === '/api/rounds/2/comparison' ? answer()
    : new Promise<Response>(() => {}))
  vi.stubGlobal('fetch', fetch)
  vi.stubGlobal('scrollTo', vi.fn())
  return fetch
}

function show(onOpen = vi.fn(), onRounds = vi.fn()) {
  render(<RoundComparisonView number={2} names={NAMES} onOpen={onOpen} onRounds={onRounds} />)
  return { onOpen, onRounds }
}

const loaded = () => screen.findByRole('heading', { level: 1, name: 'Compared with Q3 2026' })
const row = (id: string) => screen.getByRole('row', { name: new RegExp(`^${id.replace(/\./g, '\\.')} `) })

describe('the round comparison view (ticket 573)', () => {
  it('sums up better, worse, new and unchanged, readiness then and now, and the scope changes apart', async () => {
    serve(() => json(SERVED))
    show()
    await loaded()

    const summary = screen.getByRole('region', { name: 'Summary' })
    expect(within(summary).getByRole('list', { name: 'Changes' }).textContent)
      .toBe('Better 1Worse 1New 1Unchanged 3')
    expect(within(summary).getByRole('list', { name: 'Scope changes' }).textContent)
      .toBe('New since 1Out of scope since 1')
    expect(within(summary).getByText('Accepted: 5 when Q3 2026 closed → 4 now')).toBeTruthy()
    expect(within(summary).getByRole('list', { name: 'Accepted by verdict' }).textContent)
      .toBe('Evidenced: 4 → 2Partly evidenced: 1 → 2Not evidenced: 0 → 0')
    expect(screen.queryByText(/progress/i)).toBeNull()
  })

  it('groups the controls by chapter, changes first, the unchanged ones folded', async () => {
    serve(() => json(SERVED))
    show()
    await loaded()

    const chapters = screen.getAllByRole('region').filter((r) => r.getAttribute('aria-label') !== 'Summary')
    expect(chapters.map((c) => within(c).getByRole('heading').textContent))
      .toEqual(['Policies and incidents', 'Access', 'IT security'])
    const [policies, access, it] = chapters
    const ids = (c: HTMLElement) => within(c).getAllByRole('row').map((r) => r.querySelector('.mono')?.textContent).filter(Boolean)
    expect(ids(policies)).toEqual(['1.1.1', '1.2.1', '1.3.1'])
    const fold = within(policies).getByText('2 unchanged').closest('details')!
    expect(fold.open).toBe(false)
    expect(within(fold).getAllByRole('row').map((r) => r.querySelector('.mono')?.textContent).filter(Boolean))
      .toEqual(['1.2.1', '1.3.1'])
    expect(within(access).queryByText(/unchanged/)).toBeNull()
    expect(ids(it)).toEqual(['5.1.1', '5.2.1', '5.1.2'])
    expect(within(it).getByText('1 unchanged')).toBeTruthy()
  })

  it('shows each control then and now with its change word, its name, and opens its working paper', async () => {
    serve(() => json(SERVED))
    const { onOpen } = show()
    await loaded()

    expect(row('1.1.1').textContent).toContain('Partly evidenced → Evidenced')
    expect(row('1.1.1').textContent).toContain('Better')
    expect(row('4.1.2').textContent).toContain('Evidenced → Not accepted')
    expect(row('4.1.2').textContent).toContain('Worse')
    expect(row('4.1.3').textContent).toContain('Not accepted → Partly evidenced')
    const link = within(row('1.1.1')).getByRole('link')
    expect(link.getAttribute('href')).toBe('/controls/1.1.1')
    expect(link.textContent).toBe('1.1.1 To what extent is control 1.1.1 met?')
    expect(link.querySelector('[lang="en"]')?.textContent).toBe('To what extent is control 1.1.1 met?')
    await userEvent.click(link)
    expect(onOpen).toHaveBeenCalledWith('1.1.1', 'Q3 2026')
  })

  it('labels proposed and scope changes, and shows done items and answered questions under their control', async () => {
    serve(() => json(SERVED))
    show()
    await loaded()

    expect(row('1.3.1').textContent).toContain('Proposed, waiting for review')
    expect(row('1.1.1').textContent).not.toContain('Proposed')
    expect(row('5.1.1').textContent).toContain('New since')
    expect(row('5.2.1').textContent).toContain('Out of scope since')
    expect(row('5.2.1').textContent).toContain('5.2.1') // no card: its id alone
    expect(row('4.1.2').textContent).toContain('Done since: M2 Access rights are reviewed.')
    expect(row('1.3.1').textContent).toContain('Answered since: Who signs the policy?')
    expect(row('1.1.1').textContent).not.toContain('since:')
  })

  it('comes back from a working paper with the folds and the scroll as they were', async () => {
    serve(() => json(SERVED))
    show()
    await loaded()
    await userEvent.click(screen.getByText('2 unchanged'))
    expect(screen.getByText('2 unchanged').closest('details')!.open).toBe(true)
    vi.stubGlobal('scrollY', 640)
    await userEvent.click(within(row('4.1.2')).getByRole('link'))
    cleanup()

    show()
    await loaded()
    expect(screen.getByText('2 unchanged').closest('details')!.open).toBe(true)
    expect(screen.getByText('1 unchanged').closest('details')!.open).toBe(false)
    expect(window.scrollTo).toHaveBeenCalledWith(0, 640)
    cleanup()

    show() // the scroll is used once: opened again from the Rounds section, the view starts at the top
    await loaded()
    expect(window.scrollTo).toHaveBeenCalledTimes(1)
  })

  it('says nothing has changed when no control changed', async () => {
    serve(() => json({
      ...SERVED, changes: { better: 0, worse: 0, new: 0, unchanged: 2 },
      controls: SERVED.controls.filter((c) => c.change === 'unchanged' && c.chapter === '1'),
    }))
    show()
    await loaded()

    expect(screen.getByText('Nothing has changed since Q3 2026 closed.')).toBeTruthy()
    expect(screen.queryByRole('list', { name: 'Scope changes' })).toBeNull()
  })

  it('says it is loading, then, when the comparison cannot be read, a notice with Try again that reads it again', async () => {
    let fails = true
    const fetch = serve(() => fails ? json({ detail: 'no' }, 500) : json(SERVED))
    show()
    expect(screen.getByRole('status').textContent).toBe('Loading the round comparison…')

    expect((await screen.findByRole('alert')).textContent).toBe(COMPARISON_FAILED)
    expect(COMPARISON_FAILED).toBe('The round comparison could not be loaded.')
    fails = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await loaded()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it.each([
    [404, 'No review round has that number.'],
    [409, 'Close this review round to compare with it.'],
    [409, "This review round closed before the fresh start, so it can't be compared."],
  ])('shows the served %i sentence with a link back to the Rounds section', async (status, detail) => {
    serve(() => json({ detail }, status))
    const { onRounds } = show()

    expect((await screen.findByRole('alert')).textContent).toBe(detail)
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    const back = screen.getByRole('link', { name: 'Back to the rounds' })
    expect(back.getAttribute('href')).toBe('/company')
    await userEvent.click(back)
    expect(onRounds).toHaveBeenCalled()
  })
})
