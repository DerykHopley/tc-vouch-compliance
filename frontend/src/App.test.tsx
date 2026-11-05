import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import { type Overview, OVERVIEW_FAILED } from './overview.ts'

function systemPrefers(dark: boolean | null, width = 1400) {
  window.matchMedia = (query: string) => {
    const maxWidth = /^\(max-width: (\d+)px\)$/.exec(query)
    const matches = maxWidth ? width <= Number(maxWidth[1]) : dark === true && query === '(prefers-color-scheme: dark)'
    return { matches, media: query } as MediaQueryList
  }
}

beforeEach(() => systemPrefers(null))

afterEach(() => {
  cleanup()
  delete document.documentElement.dataset.theme
  window.history.replaceState(null, '', '/')
  vi.unstubAllGlobals()
})

const IDS = ['1.1.1', '1.6.1', '1.6.2', '2.1.3', '4.1.2', '4.1.3', '4.2.1', '5.1.1', '5.1.2', '5.2.4', '5.2.5', '5.2.9']

/** The overview with nothing assessed, as `GET /api/overview` sends it. */
const OVERVIEW: Overview = {
  company: 'Nordlicht Komponenten GmbH', country: 'Germany', protection_level: 'normal', answer_language: 'en',
  requirements: {},
  readiness: { controls: 12, accepted: 0,
    by_verdict: { evidenced: 0, 'partly-evidenced': 0, 'not-evidenced': 0, 'not-applicable': 0 }, open: 12,
    remediation_items: 0 },
  by_status: { 'not-assessed': 12, 'waiting-for-review': 0, accepted: 0, rejected: 0 },
  next_step: { control_id: '1.1.1', action: 'assess' },
  controls: IDS.map((id) => ({ id, question: `Question ${id}?`, iso_27001: 'ISO 27001:2022: A.5.1', status: 'not-assessed',
    verdict: null })),
  quotes: [],
}

/** A stored assessment as `GET /api/assessments/{thread_id}` sends it (#38). */
const STORED = { control_id: '4.1.2', verdict: 'partly-evidenced', answer: 'Keys for administrators.',
  status: 'waiting-for-review', thread_id: 't1' }

/** The overview with each stored assessment's control on its card, as the API would send it. */
const overviewWith = (stored: (typeof STORED)[]): Overview => ({
  ...OVERVIEW,
  controls: OVERVIEW.controls.map((c) => {
    const a = stored.find((s) => s.control_id === c.id)
    return a ? { ...c, status: 'waiting-for-review', verdict: 'partly-evidenced' } : c
  }),
})

/**
 * The API: the overview (each stored assessment on its card), the assessment's stream when given (POST), the stored
 * assessments (GET: none unless given), and nothing else ever answers. Returns the fetch, to count its calls.
 */
function serve(assessment?: () => Promise<Response>, stored: (typeof STORED)[] = []) {
  const fetch = vi.fn((url: string, init?: RequestInit) =>
    url === '/api/overview' ? Promise.resolve(Response.json(overviewWith(stored)))
      : url === '/api/assessments' && init?.method === 'POST' ? (assessment ? assessment() : new Promise<Response>(() => {}))
        : url === '/api/assessments' ? Promise.resolve(Response.json({ assessments: stored }))
          : url.startsWith('/api/assessments/') && stored.length
            ? Promise.resolve(Response.json({ assessment: stored.find((a) => url.endsWith(a.thread_id)) }))
            : new Promise<Response>(() => {}))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

/** How many times the page asked for the stored assessments. */
const storedReads = (fetch: ReturnType<typeof serve>) =>
  fetch.mock.calls.filter(([url, init]) => url === '/api/assessments' && init?.method !== 'POST').length

describe('Dossier landing', () => {
  it('shows the top bar, headline, readiness band and the 12 controls from the API', async () => {
    serve()
    render(<App />)
    const top = screen.getByRole('banner')
    expect(within(top).getByText('Vouch')).toBeTruthy()
    expect(within(top).getByRole('link', { name: 'Dossier' }).getAttribute('aria-current')).toBe('page')
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toMatch(/prepared for audit/i)
    expect(screen.getByRole('region', { name: 'Readiness' })).toBeTruthy()
    expect(screen.getAllByRole('article', { name: /^Control / })).toHaveLength(12)
  })

  it('says so with Retry when the overview cannot be read, and shows it once it can', async () => {
    let answers = 0
    vi.stubGlobal('fetch', (url: string) => url !== '/api/overview' ? new Promise(() => {})
      : ++answers === 1 ? Promise.resolve(new Response(null, { status: 503 })) : Promise.resolve(Response.json(OVERVIEW)))
    render(<App />)
    expect((await screen.findByRole('alert')).textContent).toBe(OVERVIEW_FAILED)
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findAllByRole('article', { name: /^Control / })).toHaveLength(12)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('the working paper', () => {
  const paper = () => screen.queryByRole('region', { name: 'Working paper' })

  it('opens straight at its address, as after a refresh', async () => {
    serve()
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    expect(await screen.findByRole('heading', { name: /4.1.2 Question 4.1.2\?/ })).toBeTruthy()
    expect(within(paper()!).getByRole('button', { name: 'Assess 4.1.2' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Dossier' }).getAttribute('aria-current')).toBeNull()
  })

  it('opens from its card with its address, and Back returns to the Dossier', async () => {
    serve()
    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Open 4.1.2' }))
    expect(window.location.pathname).toBe('/controls/4.1.2')
    expect(paper()).toBeTruthy()

    window.history.back()
    await waitFor(() => expect(paper()).toBeNull())
    expect(window.location.pathname).toBe('/')
  })

  it("starts no second run when Back and Forward return to a paper opened by the hero's Assess", async () => {
    const assessed = vi.fn(() => new Promise<Response>(() => {}))
    serve(assessed)
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Assess 1.1.1' }))
    act(() => window.history.back())
    await waitFor(() => expect(paper()).toBeNull())
    act(() => window.history.forward())
    await waitFor(() => expect(paper()).toBeTruthy())
    expect(assessed).toHaveBeenCalledTimes(1)
  })

  it('keeps a run going while the lead leaves its paper, and starts no second one on return', async () => {
    const assessed = vi.fn(() => new Promise<Response>(() => {}))
    serve(assessed)
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Assess 4.1.2' }))
    await userEvent.click(screen.getByRole('link', { name: 'Dossier' }))
    const card = await screen.findByRole('article', { name: 'Control 4.1.2' })
    expect(within(card).getByText('Assessing')).toBeTruthy()
    await userEvent.click(within(card).getByRole('link', { name: 'Open 4.1.2' }))
    const busy = await within(paper()!).findByRole('button', { name: 'Assessing 4.1.2' })
    expect((busy as HTMLButtonElement).disabled).toBe(true)
    expect(assessed).toHaveBeenCalledTimes(1)
  })

  it("shows a control's newest stored assessment, proposed, when its paper opens", async () => {
    serve(undefined, [STORED])
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    const result = await screen.findByRole('region', { name: 'Verdict and draft answer' })
    expect(within(result).getByText('Partly evidenced')).toBeTruthy()
    expect(within(result).getByText('Keys for administrators.')).toBeTruthy()
    expect(within(paper()!).getByText('Proposed')).toBeTruthy()
  })

  it('marks the Dossier current when the overview fails at a paper address, as the Dossier is what shows', async () => {
    vi.stubGlobal('fetch', (url: string) =>
      url === '/api/overview' ? Promise.resolve(new Response(null, { status: 503 })) : new Promise(() => {}))
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    await screen.findByRole('alert')
    expect(screen.getByRole('link', { name: 'Dossier' }).getAttribute('aria-current')).toBe('page')
  })

  it("starts the assessment from the Dossier's next step", async () => {
    const assessed = vi.fn(() => new Promise<Response>(() => {}))
    serve(assessed)
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Assess 1.1.1' }))
    expect(window.location.pathname).toBe('/controls/1.1.1')
    expect(within(paper()!).getByRole('button', { name: 'Assessing 1.1.1' })).toBeTruthy()
    expect(assessed).toHaveBeenCalledTimes(1)
  })

  /** A stream handing out one event per read; `askedForMore` settles when the reader asks past
   * the given events, i.e. once it has handled the last of them. `finish` sends the rest. */
  function heldStream(events: string[]) {
    const encode = (text: string) => new TextEncoder().encode(text)
    let askedForMore = () => {}
    const asked = new Promise<void>((resolve) => { askedForMore = resolve })
    let finish = (_rest: string[]) => {}
    const finished = new Promise<string[]>((resolve) => { finish = resolve })
    const queue = [...events]
    const body = new ReadableStream<Uint8Array>({
      async pull(controller) {
        if (queue.length) return controller.enqueue(encode(queue.shift()!))
        askedForMore()
        for (const rest of await finished) controller.enqueue(encode(rest))
        controller.close()
      },
    }, { highWaterMark: 0 })
    return { body, asked, finish }
  }

  const sse = (event: string, data: object) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

  async function assessWith(stream: ReturnType<typeof heldStream>) {
    // The assessment gets the stream; the page's other requests (the developer API's) wait.
    serve(() => Promise.resolve(new Response(stream.body, { headers: { 'Content-Type': 'text/event-stream' } })))
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Assess 4.1.2' }))
  }

  it('keeps running through requirement events, then shows the verdict and the answer', async () => {
    const stream = heldStream([
      sse('step', { node: 'judge', text: 'Judged 4 of 5.' }),
      sse('requirement', { requirement: { id: 'M1', column: 'must', text: 'T', verdict: 'partly-evidenced', reason: 'R' } }),
    ])
    await assessWith(stream)
    await stream.asked // the requirement event has been handled
    await act(async () => {}) // and anything it set is rendered
    expect(within(paper()!).queryByRole('alert')).toBeNull()
    expect(within(paper()!).getByRole('button', { name: 'Assessing 4.1.2' })).toBeTruthy()

    stream.finish([sse('result', { assessment: { control_id: '4.1.2', verdict: 'partly-evidenced', answer: 'Sign-in follows exposure.' } })])
    const result = await within(paper()!).findByRole('region', { name: 'Verdict and draft answer' })
    expect(within(result).getByText('Partly evidenced')).toBeTruthy()
    expect(within(result).getByText('Sign-in follows exposure.')).toBeTruthy()
  })

  it('shows a not applicable control with its own chip and words', async () => {
    const stream = heldStream([])
    await assessWith(stream)
    stream.finish([sse('result', { assessment: { control_id: '4.1.2', verdict: 'not-applicable', answer: 'Excluded.' } })])
    const result = await within(paper()!).findByRole('region', { name: 'Verdict and draft answer' })
    const chip = within(result).getByText('Not applicable')
    expect(chip.className).toBe('chip v-not-applicable')
  })

  it('leaves for the Dossier at /', async () => {
    serve()
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    await userEvent.click(screen.getByRole('link', { name: 'Dossier' }))
    expect(window.location.pathname).toBe('/')
    expect(paper()).toBeNull()
  })

  it("shows the assessment's tokens and cost beside its answer", async () => {
    const result = {
      assessment: { control_id: '4.1.2', verdict: 'evidenced', answer: 'Sign-in follows exposure.' },
      usage: { model: 'fake', input_tokens: 1530, output_tokens: 98, reported_cost_usd: 0, estimated_cost_usd: 0.000202,
        cost_usd: 0.000202, estimated: true, cost_complete: true },
    }
    const stream = `event: result\ndata: ${JSON.stringify(result)}\n\n`
    serve(() => Promise.resolve(new Response(stream, { status: 200 })))
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Assess 4.1.2' }))
    const answer = await screen.findByRole('region', { name: 'Verdict and draft answer' })
    expect(within(answer).getByRole('region', { name: 'Usage' }).textContent).toBe(
      '1,530 tokens in · 98 tokens out · $0.0002 (estimated)',
    )
  })

  it('says a control the overview does not list is not found, and "Overview" returns to the Dossier', async () => {
    serve()
    window.history.replaceState(null, '', '/controls/9.9.9')
    render(<App />)
    const notFound = await screen.findByRole('region', { name: 'Control not found' })
    expect(within(notFound).getByRole('heading').textContent).toBe('No control 9.9.9')
    expect(paper()).toBeNull()
    expect(screen.getByRole('link', { name: 'Dossier' }).getAttribute('aria-current')).toBeNull()
    await userEvent.click(within(notFound).getByRole('link', { name: '← Overview' }))
    expect(await screen.findAllByRole('article', { name: /^Control / })).toHaveLength(12)
    expect(window.location.pathname).toBe('/')
  })

  it('says an address with a broken escape is not found, rather than failing blank', async () => {
    serve()
    window.history.replaceState(null, '', '/controls/%E0')
    render(<App />)
    const notFound = await screen.findByRole('region', { name: 'Control not found' })
    expect(within(notFound).getByRole('heading').textContent).toBe('No control %E0')
  })

  it('reads no stored assessment for a control its card says is not assessed, and one for a control it says is', async () => {
    const fetch = serve(undefined, [STORED])
    window.history.replaceState(null, '', '/controls/5.2.4')
    render(<App />)
    expect(await within(await screen.findByRole('region', { name: 'Working paper' })).findByRole('button',
      { name: 'Assess 5.2.4' })).toBeTruthy()
    expect(storedReads(fetch)).toBe(0)
    await userEvent.click(within(screen.getByRole('navigation', { name: 'Controls' })).getByRole('link', { name: /^4\.1\.2/ }))
    expect(await screen.findByRole('region', { name: 'Verdict and draft answer' })).toBeTruthy()
    expect(storedReads(fetch)).toBe(1)
  })

  it("keeps a finished run's control current in the index after the lead opens another paper", async () => {
    const result = { assessment: { control_id: '4.1.2', verdict: 'partly-evidenced', answer: 'Keys.',
      status: 'waiting-for-review' } }
    const fetch = serve(() => Promise.resolve(new Response(`event: result\ndata: ${JSON.stringify(result)}\n\n`)))
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Assess 4.1.2' }))
    await screen.findByRole('region', { name: 'Verdict and draft answer' })
    const overviewReads = fetch.mock.calls.filter(([url]) => url === '/api/overview').length
    await userEvent.click(within(screen.getByRole('navigation', { name: 'Controls' })).getByRole('link', { name: /^5\.2\.4/ }))
    await screen.findByRole('heading', { name: /5.2.4 Question 5.2.4\?/ })
    const entry = within(screen.getByRole('navigation', { name: 'Controls' })).getByRole('link', { name: /^4\.1\.2/ })
    expect(within(entry).getByText('Partly evidenced')).toBeTruthy()
    expect(within(entry).getByText('Proposed')).toBeTruthy()
    expect(fetch.mock.calls.filter(([url]) => url === '/api/overview').length).toBe(overviewReads) // no read for it
  })

  it('opens another control from the index, and "Overview" returns to the Dossier', async () => {
    serve()
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    const index = await screen.findByRole('navigation', { name: 'Controls' })
    await userEvent.click(within(index).getByRole('link', { name: /^5\.2\.4/ }))
    expect(window.location.pathname).toBe('/controls/5.2.4')
    expect(await screen.findByRole('heading', { name: /5.2.4 Question 5.2.4\?/ })).toBeTruthy()
    await userEvent.click(screen.getByRole('link', { name: '← Overview' }))
    expect(window.location.pathname).toBe('/')
    expect(paper()).toBeNull()
  })
})

describe('theme', () => {
  it('starts dark when the system prefers dark', () => {
    systemPrefers(true)
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('starts light when the system has no preference', () => {
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('flips with the switch', async () => {
    systemPrefers(false)
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Dark theme' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    await userEvent.click(screen.getByRole('button', { name: 'Light theme' }))
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})

describe('Ask Vouch', () => {
  const bubble = () => screen.queryByRole('complementary', { name: 'Chat with Vouch' })

  it('opens an empty bubble and closes it again', async () => {
    render(<App />)
    expect(bubble()).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    expect(bubble()).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Close the chat' }))
    expect(bubble()).toBeNull()
  })

  it('closes on Esc and gives focus back to the button', async () => {
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    await userEvent.keyboard('{Escape}')
    expect(bubble()).toBeNull()
    expect(document.activeElement?.textContent).toMatch(/ask vouch/i)
  })

  it('keeps Tab and Shift+Tab inside the full-screen sheet at 400px', async () => {
    systemPrefers(null, 400)
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    const sheet = bubble()!
    for (const shift of [false, true]) {
      for (let i = 0; i < 4; i++) {
        await userEvent.tab({ shift })
        expect(sheet.contains(document.activeElement)).toBe(true)
      }
    }
  })

  it('makes the page behind the full-screen sheet inert at 400px, and gives it back on close', async () => {
    systemPrefers(null, 400)
    render(<App />)
    const page = [screen.getByRole('banner'), screen.getByRole('main')]
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    for (const el of page) expect(el.hasAttribute('inert')).toBe(true)
    expect(bubble()!.hasAttribute('inert')).toBe(false)
    await userEvent.keyboard('{Escape}')
    for (const el of page) expect(el.hasAttribute('inert')).toBe(false)
  })

  it('leaves the page live beside the bubble at 1400px', async () => {
    systemPrefers(null, 1400)
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    expect(screen.getByRole('banner').hasAttribute('inert')).toBe(false)
    expect(screen.getByRole('main').hasAttribute('inert')).toBe(false)
  })

  it('leaves the page reachable by Tab beside the bubble at 1400px', async () => {
    systemPrefers(null, 1400)
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    const reached = new Set<Element | null>()
    for (let i = 0; i < 4; i++) {
      await userEvent.tab()
      reached.add(document.activeElement)
    }
    expect(reached.has(screen.getByRole('link', { name: 'Dossier' }))).toBe(true)
    expect(reached.has(screen.getByRole('button', { name: /theme/i }))).toBe(true)
  })

  it('opens with Ctrl+K', async () => {
    render(<App />)
    await userEvent.keyboard('{Control>}k{/Control}')
    expect(bubble()).toBeTruthy()
  })
})

describe('local mode', () => {
  it('has no sign-out: nobody is signed in', () => {
    render(<App />)
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull()
  })
})
