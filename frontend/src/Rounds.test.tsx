import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Rounds from './Rounds.tsx'
import { type ListedRound, type RoundList, ROUNDS_FAILED, roundPath, useRounds } from './rounds.ts'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const FIRST: ListedRound = { number: 1, name: 'First round', opened_at: null, closed_at: '2026-09-30T10:00:00+00:00',
  before_fresh_start: true }
const Q3: ListedRound = { number: 2, name: 'Q3 2026', opened_at: '2026-10-01T09:00:00+00:00',
  closed_at: '2026-10-31T16:00:00+00:00', before_fresh_start: false }
const SPRINT: ListedRound = { number: 3, name: 'Sprint review', opened_at: '2026-11-01T09:00:00+00:00',
  closed_at: '2026-11-08T16:00:00+00:00', before_fresh_start: false }
const Q4: ListedRound = { number: 4, name: 'Q4 2026', opened_at: '2026-11-09T09:00:00+00:00', closed_at: null,
  before_fresh_start: false }
/** As GET /api/rounds serves them: oldest first, the open one last, and the default choice. */
const SERVED: RoundList = { rounds: [FIRST, Q3, SPRINT, Q4], default: 3 }

/** The section as the Company page shows it: read with useRounds, then shown. */
function Shown() {
  const { state, tryAgain } = useRounds()
  return <Rounds state={state} tryAgain={tryAgain} />
}

const json = (body: unknown, status = 200) => Promise.resolve(Response.json(body, { status }))

function serve(answer: () => Promise<Response>) {
  const fetch = vi.fn((url: string) => url === '/api/rounds' ? answer() : new Promise<Response>(() => {}))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const items = () => screen.getAllByRole('listitem')

describe('the Rounds section of the Company page (ticket 572)', () => {
  it('lists every round newest first with its times, the last closed one marked as compared by default', async () => {
    serve(() => json(SERVED))
    render(<Shown />)

    await screen.findByRole('list')
    expect(items().map((i) => i.textContent)).toEqual([
      'Q4 2026Open since 9 November 2026',
      'Sprint reviewOpened 1 November 2026, closed 8 November 2026Compared by default',
      'Q3 2026Opened 1 October 2026, closed 31 October 2026',
      'First roundOpened at the start, closed 30 September 2026Closed before the fresh start',
    ])
    const [open, sprint, q3] = items()
    expect(within(open).queryByRole('link')).toBeNull()
    expect(within(sprint).getByRole('link', { name: /Sprint review/ }).getAttribute('href')).toBe('/company/rounds/3')
    expect(within(q3).getByRole('link', { name: /Q3 2026/ }).getAttribute('href')).toBe('/company/rounds/2')
    expect(screen.queryByText('Close a review round to compare with it later.')).toBeNull()
  })

  it('notes a round closed before the fresh start, and does not make it choosable', async () => {
    serve(() => json(SERVED))
    render(<Shown />)

    await screen.findByRole('list')
    const first = items()[3]
    expect(within(first).getByText('Closed before the fresh start')).toBeTruthy()
    expect(within(first).queryByRole('link')).toBeNull()
    expect(screen.getAllByRole('link')).toHaveLength(2)
  })

  it('with no closed round, lists the open round and says to close one to compare with it later', async () => {
    serve(() => json({ rounds: [{ ...FIRST, closed_at: null, before_fresh_start: false }], default: null }))
    render(<Shown />)

    await screen.findByRole('list')
    expect(items().map((i) => i.textContent)).toEqual(['First roundOpen since the start'])
    expect(screen.getByText('Close a review round to compare with it later.')).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('says to close a round when the only closed ones are from before the fresh start', async () => {
    serve(() => json({ rounds: [FIRST, Q4], default: null }))
    render(<Shown />)

    await screen.findByRole('list')
    expect(screen.getByText('Close a review round to compare with it later.')).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('says it is loading, then, when the rounds cannot be read, a notice with Try again that reads them again', async () => {
    let fails = true
    const fetch = serve(() => fails ? json({ detail: 'no' }, 500) : json(SERVED))
    render(<Shown />)
    expect(screen.getByRole('status').textContent).toBe('Loading the review rounds…')

    expect((await screen.findByRole('alert')).textContent).toBe(ROUNDS_FAILED)
    expect(ROUNDS_FAILED).toBe('The review rounds could not be loaded.')
    fails = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await screen.findByRole('list')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(fetch.mock.calls.filter(([url]) => url === '/api/rounds')).toHaveLength(2)
  })

  it('says it could not be loaded when the network fails', async () => {
    serve(() => Promise.reject(new TypeError('offline')))
    render(<Shown />)

    expect((await screen.findByRole('alert')).textContent).toBe(ROUNDS_FAILED)
  })

  it("gives a round's comparison its address", () => {
    expect(roundPath(7)).toBe('/company/rounds/7')
  })
})
