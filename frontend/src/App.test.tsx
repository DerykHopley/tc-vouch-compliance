import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import { type Overview, OVERVIEW_FAILED } from './overview.ts'

/** The window's width and theme as media queries see them; `resizeTo` changes the width, telling each listener. */
let resizeTo: (width: number) => void = () => {}

function systemPrefers(dark: boolean | null, width = 1400) {
  let now = width
  const listeners = new Set<() => void>()
  // The page's width as layout sees it (no scrollbar in jsdom): what the pinned chat's width is checked against.
  Object.defineProperty(document.documentElement, 'clientWidth', { configurable: true, get: () => now })
  resizeTo = (next) => act(() => {
    now = next
    for (const listener of listeners) listener()
    window.dispatchEvent(new Event('resize'))
  })
  window.matchMedia = (query: string) => {
    const maxWidth = /^\(max-width: (\d+)px\)$/.exec(query)
    return {
      get matches() {
        return maxWidth ? now <= Number(maxWidth[1]) : dark === true && query === '(prefers-color-scheme: dark)'
      },
      media: query,
      addEventListener: (_: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
    } as unknown as MediaQueryList
  }
}

beforeEach(() => systemPrefers(null))

afterEach(() => {
  cleanup()
  delete document.documentElement.dataset.theme
  delete document.documentElement.dataset.chat
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
    remediation_items: 0, remediation_approved: 0 },
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
    expect(within(top).getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page')
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toMatch(/prepared for audit/i)
    expect(screen.getByRole('region', { name: 'Readiness' })).toBeTruthy()
    expect(screen.getAllByRole('article', { name: /^Control / })).toHaveLength(12)
  })

  it('gives each card one link, "Open <id>", and one Tab stop (ticket 231: the link covers the whole card)', async () => {
    serve()
    render(<App />)
    const cards = await screen.findAllByRole('article', { name: /^Control / })
    const links = cards.map((card) => {
      const inCard = within(card).getAllByRole('link')
      expect(inCard).toHaveLength(1)
      return inCard[0]
    })
    links.forEach((link, i) => expect(link.getAttribute('aria-label')).toBe(`Open ${IDS[i]}`))
    links[0].focus()
    for (const next of links.slice(1, 4)) {
      await userEvent.tab()
      expect(document.activeElement).toBe(next)
    }
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
    expect(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBeNull()
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
    await userEvent.click(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Overview' }))
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

  describe('reviewing a stored proposed assessment (#42; from #40\'s verify)', () => {
    const WAITING = { ...STORED, requirements: [{ id: 'M1', column: 'must', text: 'Keys.', verdict: 'partly-evidenced',
      reason: 'Policy only.', citations: [] }], review: null }
    const ACCEPTED = { ...WAITING, status: 'accepted', review: { decision: 'accept', note: '', overrides: [] } }

    /** The API with WAITING stored; the review route answers `review`; GET of the thread answers `reread`. */
    function serveReview(review: Response, reread = ACCEPTED) {
      const fetch = vi.fn((url: string, init?: RequestInit) =>
        url === '/api/overview' ? Promise.resolve(Response.json(overviewWith([STORED])))
          : url === '/api/assessments' ? Promise.resolve(Response.json({ assessments: [STORED] }))
            : url === '/api/assessments/t1/review' && init?.method === 'POST' ? Promise.resolve(review)
              : url === '/api/assessments/t1'
                ? Promise.resolve(Response.json({ assessment: fetch.mock.calls.some(([u]) => u.endsWith('/review'))
                  ? reread : WAITING }))
                : url.startsWith('/api/audit-trail') ? Promise.resolve(Response.json({ entries: [] }))
                  : new Promise<Response>(() => {}))
      vi.stubGlobal('fetch', fetch)
      return fetch
    }

    it('accepts it from its paper: the paper and the control index show it accepted', async () => {
      serveReview(Response.json({ assessment: ACCEPTED }))
      window.history.replaceState(null, '', '/controls/4.1.2')
      render(<App />)
      const bar = await screen.findByRole('region', { name: 'Review' })
      await userEvent.click(within(bar).getByRole('button', { name: 'Accept verdict' }))
      expect(await within(screen.getByRole('region', { name: 'Review' })).findByText('Accepted')).toBeTruthy()
      const index = screen.getByRole('navigation', { name: 'Controls' })
      expect(within(within(index).getByRole('link', { name: /^4\.1\.2/ })).getByText('Accepted')).toBeTruthy()
    })

    it('after "already reviewed", Refresh reads the assessment again and shows the decision made elsewhere', async () => {
      serveReview(Response.json({ detail: 'not waiting' }, { status: 409 }))
      window.history.replaceState(null, '', '/controls/4.1.2')
      render(<App />)
      const bar = await screen.findByRole('region', { name: 'Review' })
      await userEvent.click(within(bar).getByRole('button', { name: 'Accept verdict' }))
      await userEvent.click(await within(bar).findByRole('button', { name: 'Refresh' }))
      expect(await within(screen.getByRole('region', { name: 'Review' })).findByText('Accepted')).toBeTruthy()
    })

    describe('leaving with an override not yet recorded asks first (issue 182)', () => {
      const ONE = 'You have 1 override not yet recorded. Leave and drop it?'
      const m1 = () => screen.getByText('M1', { selector: 'th .mono' }).closest('tr')!
      const headVerdict = () => screen.getByText('Verdict', { selector: '.paper-meta span' }).parentElement!
      const indexLink = (id: string) =>
        within(screen.getByRole('navigation', { name: 'Controls' })).getByRole('link', { name: new RegExp(`^${id}`) })
      /** The browser's confirm, answering `answer`. */
      function asking(answer: boolean) {
        const confirm = vi.fn(() => answer)
        vi.stubGlobal('confirm', confirm)
        return confirm
      }
      /** 4.1.2's paper on WAITING, at its address. */
      async function openPaper() {
        serveReview(Response.json({ assessment: ACCEPTED }))
        window.history.replaceState(null, '', '/controls/4.1.2')
        render(<App />)
        await screen.findByRole('region', { name: 'Review' })
      }
      async function saveOverrideOfM1() {
        await userEvent.click(within(m1()).getByRole('button', { name: 'Change M1' }))
        const editor = screen.getByRole('form', { name: 'Override M1' })
        await userEvent.selectOptions(within(editor).getByLabelText('Verdict'), 'evidenced')
        await userEvent.type(within(editor).getByLabelText('Reason (required)'), 'The rollout record shows it.')
        await userEvent.click(within(editor).getByRole('button', { name: 'Save' }))
      }

      it('opening another control from the index asks; Cancel keeps the paper, its override and its verdict',
        async () => {
          await openPaper()
          await saveOverrideOfM1()
          const confirm = asking(false)

          await userEvent.click(indexLink('4.1.3'))

          expect(confirm).toHaveBeenCalledWith(ONE)
          expect(window.location.pathname).toBe('/controls/4.1.2')
          expect(screen.getByRole('heading', { name: /4\.1\.2 Question 4\.1\.2\?/ })).toBeTruthy()
          expect(within(m1()).getByText('Overridden from Partly evidenced')).toBeTruthy()
          expect(within(headVerdict()).getByText('Evidenced')).toBeTruthy()
        })

      it('OK opens the other control, and coming back shows no override (#42)', async () => {
        await openPaper()
        await saveOverrideOfM1()
        asking(true)

        await userEvent.click(indexLink('4.1.3'))

        expect(await screen.findByRole('heading', { name: /4\.1\.3 Question 4\.1\.3\?/ })).toBeTruthy()
        expect(window.location.pathname).toBe('/controls/4.1.3')
        await userEvent.click(indexLink('4.1.2'))
        await screen.findByRole('region', { name: 'Review' })
        expect(within(m1()).queryByText(/^Overridden/)).toBeNull()
        expect(within(headVerdict()).getByText('Partly evidenced')).toBeTruthy()
      })

      it('the breadcrumb "Overview" and the Report tab ask too; Cancel keeps the paper\'s address', async () => {
        await openPaper()
        await saveOverrideOfM1()
        const confirm = asking(false)

        await userEvent.click(within(screen.getByRole('navigation', { name: 'Breadcrumb' }))
          .getByRole('link', { name: 'Overview' }))
        await userEvent.click(within(screen.getByRole('navigation', { name: 'Main' }))
          .getByRole('link', { name: 'Report' }))

        expect(confirm).toHaveBeenCalledTimes(2)
        expect(confirm).toHaveBeenLastCalledWith(ONE)
        expect(window.location.pathname).toBe('/controls/4.1.2')
        expect(within(m1()).getByText('Overridden from Partly evidenced')).toBeTruthy()
      })

      it("Back asks; Cancel keeps the paper shown and puts its address back", async () => {
        window.history.replaceState(null, '', '/')
        window.history.pushState(null, '', '/controls/4.1.2')
        serveReview(Response.json({ assessment: ACCEPTED }))
        render(<App />)
        await screen.findByRole('region', { name: 'Review' })
        await saveOverrideOfM1()
        const confirm = asking(false)

        act(() => window.history.back())

        await waitFor(() => expect(confirm).toHaveBeenCalledWith(ONE))
        expect(window.location.pathname).toBe('/controls/4.1.2')
        expect(screen.getByRole('region', { name: 'Working paper' })).toBeTruthy()
        expect(within(m1()).getByText('Overridden from Partly evidenced')).toBeTruthy()
      })

      it('a reload or closing the tab is held back while an override is unsaved, and not otherwise', async () => {
        await openPaper()
        const unload = () => {
          const event = new Event('beforeunload', { cancelable: true })
          window.dispatchEvent(event)
          return event.defaultPrevented
        }
        expect(unload()).toBe(false)

        await saveOverrideOfM1()

        expect(unload()).toBe(true)
      })

      it('asks nothing with no override saved, or for the control already open', async () => {
        await openPaper()
        await saveOverrideOfM1()
        const confirm = asking(false)
        await userEvent.click(indexLink('4.1.2'))
        expect(confirm).not.toHaveBeenCalled()
        await userEvent.click(within(m1()).getByRole('button', { name: 'Undo the override of M1' }))

        await userEvent.click(indexLink('4.1.3'))

        expect(confirm).not.toHaveBeenCalled()
        expect(await screen.findByRole('heading', { name: /4\.1\.3 Question 4\.1\.3\?/ })).toBeTruthy()
      })

      it('asks nothing after Accept verdict records the decision', async () => {
        await openPaper()
        await saveOverrideOfM1()
        const confirm = asking(false)
        await userEvent.click(within(screen.getByRole('region', { name: 'Review' }))
          .getByRole('button', { name: 'Accept verdict' }))
        await within(screen.getByRole('region', { name: 'Review' })).findByText('Accepted')

        await userEvent.click(indexLink('4.1.3'))

        expect(confirm).not.toHaveBeenCalled()
        expect(await screen.findByRole('heading', { name: /4\.1\.3 Question 4\.1\.3\?/ })).toBeTruthy()
      })
    })
  })

  it('marks the Dossier current when the overview fails at a paper address, as the Dossier is what shows', async () => {
    vi.stubGlobal('fetch', (url: string) =>
      url === '/api/overview' ? Promise.resolve(new Response(null, { status: 503 })) : new Promise(() => {}))
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    await screen.findByRole('alert')
    expect(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page')
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
    await userEvent.click(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Overview' }))
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
    expect(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBeNull()
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
    await userEvent.click(within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByRole('link', { name: 'Overview' }))
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
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Dark theme' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Light theme' }))
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

  it('shows Close as a × glyph hidden from screen readers, keeping its name (#72)', async () => {
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
    const close = screen.getByRole('button', { name: 'Close the chat' })
    expect(close.textContent).toBe('×')
    expect(close.querySelector('[aria-hidden="true"]')?.textContent).toBe('×')
    expect(close.getAttribute('title')).toBe('Close the chat')
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
    for (let i = 0; i < 6; i++) {
      await userEvent.tab()
      reached.add(document.activeElement)
    }
    expect(reached.has(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Overview' }))).toBe(true)
    expect(reached.has(screen.getByRole('button', { name: 'Your menu' }))).toBe(true)
  })

  it('opens with Ctrl+K', async () => {
    render(<App />)
    await userEvent.keyboard('{Control>}k{/Control}')
    expect(bubble()).toBeTruthy()
  })

  it('opens with Cmd+K, ready to type', async () => {
    render(<App />)
    await userEvent.keyboard('{Meta>}k{/Meta}')
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Message to Vouch' }))
  })

  const open = () => userEvent.click(screen.getByRole('button', { name: /ask vouch/i }))
  const press = (name: string | RegExp) => userEvent.click(screen.getByRole('button', { name }))

  it('enlarges, pins, and goes back the same way', async () => {
    render(<App />)
    await open()
    expect(bubble()!.className).toBe('bubble')
    expect(screen.queryByRole('button', { name: 'Pin beside the page' })).toBeNull()
    await press('Make larger')
    expect(bubble()!.className).toBe('bubble enlarged')
    const pin = screen.getByRole('button', { name: 'Pin beside the page' })
    expect(pin.getAttribute('aria-pressed')).toBe('false')
    await userEvent.click(pin)
    expect(bubble()!.className).toBe('bubble pinned')
    expect(document.documentElement.dataset.chat).toBe('pinned')
    expect(screen.queryByRole('button', { name: 'Make smaller' })).toBeNull()
    // One label, its state in aria-pressed: pressed again, it unpins.
    expect(screen.getByRole('button', { name: 'Pin beside the page' }).getAttribute('aria-pressed')).toBe('true')
    await press('Pin beside the page')
    expect(bubble()!.className).toBe('bubble enlarged')
    expect(document.documentElement.dataset.chat).toBeUndefined()
    await press('Make smaller')
    expect(bubble()!.className).toBe('bubble')
  })

  it('offers no pin at 1000px or less: bubble and enlarged only', async () => {
    systemPrefers(null, 900)
    render(<App />)
    await open()
    await press('Make larger')
    expect(bubble()!.className).toBe('bubble enlarged')
    expect(screen.queryByRole('button', { name: 'Pin beside the page' })).toBeNull()
  })

  it('falls back from pinned to enlarged at 1000px or less, and to the sheet at 700px', async () => {
    render(<App />)
    await open()
    await press('Make larger')
    await press('Pin beside the page')
    resizeTo(900)
    expect(bubble()!.className).toBe('bubble enlarged')
    expect(document.documentElement.dataset.chat).toBeUndefined()
    expect(screen.queryByRole('button', { name: 'Pin beside the page' })).toBeNull()
    await userEvent.keyboard('{Escape}') // enlarged now: Esc closes it
    expect(bubble()).toBeNull()
  })

  it('as a full-screen sheet, closes on Esc even when it was pinned', async () => {
    render(<App />)
    await open()
    await press('Make larger')
    await press('Pin beside the page')
    resizeTo(400)
    await userEvent.keyboard('{Escape}')
    expect(bubble()).toBeNull()
  })

  it('closes an enlarged chat on Esc, but never a pinned one', async () => {
    render(<App />)
    await open()
    await press('Make larger')
    await userEvent.keyboard('{Escape}')
    expect(bubble()).toBeNull()
    await open()
    await press('Make larger')
    await press('Pin beside the page')
    await userEvent.keyboard('{Escape}')
    expect(bubble()).toBeTruthy()
    await press('Close the chat')
    expect(bubble()).toBeNull()
    expect(document.documentElement.dataset.chat).toBeUndefined()
  })

  describe('the pinned chat\'s width (ticket 231)', () => {
    const handle = () => screen.queryByRole('separator', { name: 'Chat width' })
    const pinned = async () => {
      await open()
      await press('Make larger')
      await press('Pin beside the page')
    }
    const width = () => document.documentElement.style.getPropertyValue('--chat-width')
    afterEach(() => {
      window.localStorage.clear()
      document.documentElement.style.removeProperty('--chat-width')
    })

    it('has a handle only when pinned: none on the bubble, the enlarged chat or the sheet', async () => {
      render(<App />)
      await open()
      expect(handle()).toBeNull()
      await press('Make larger')
      expect(handle()).toBeNull()
      await press('Pin beside the page')
      const found = handle()!
      expect(found.getAttribute('aria-orientation')).toBe('vertical')
      expect(found.tabIndex).toBe(0)
      resizeTo(400)
      expect(handle()).toBeNull()
    })

    it('opens 480px wide, and the arrows, Home and End move it with aria-valuenow following', async () => {
      render(<App />)
      await pinned()
      const found = handle()!
      expect(found.getAttribute('aria-valuenow')).toBe('480')
      expect(found.getAttribute('aria-valuemin')).toBe('360')
      expect(found.getAttribute('aria-valuemax')).toBe('720')
      expect(width()).toBe('480px')
      found.focus()
      await userEvent.keyboard('{ArrowLeft}')
      expect(found.getAttribute('aria-valuenow')).toBe('512')
      expect(width()).toBe('512px')
      await userEvent.keyboard('{ArrowRight}{ArrowRight}')
      expect(found.getAttribute('aria-valuenow')).toBe('448')
      await userEvent.keyboard('{End}')
      expect(found.getAttribute('aria-valuenow')).toBe('720')
      await userEvent.keyboard('{ArrowLeft}')
      expect(found.getAttribute('aria-valuenow')).toBe('720')
      await userEvent.keyboard('{Home}')
      expect(found.getAttribute('aria-valuenow')).toBe('360')
      expect(width()).toBe('360px')
    })

    it('opens at 480px when the stored width is not a number', async () => {
      window.localStorage.setItem('vouch.chatWidth', 'abc')
      render(<App />)
      await pinned()
      expect(handle()!.getAttribute('aria-valuenow')).toBe('480')
    })

    it('keeps a remembered 640px through a narrow window: shown at 540px at 1100px, 640px again at 1400px', async () => {
      window.localStorage.setItem('vouch.chatWidth', '640')
      systemPrefers(null, 1100)
      render(<App />)
      await pinned()
      expect(handle()!.getAttribute('aria-valuenow')).toBe('540')
      resizeTo(1400)
      expect(handle()!.getAttribute('aria-valuenow')).toBe('640')
      expect(width()).toBe('640px')
    })

    it('opens at the widest the window allows when the stored width is 5000', async () => {
      window.localStorage.setItem('vouch.chatWidth', '5000')
      render(<App />)
      await pinned()
      expect(handle()!.getAttribute('aria-valuenow')).toBe('720')
      resizeTo(1100)
      expect(handle()!.getAttribute('aria-valuenow')).toBe('540')
      expect(width()).toBe('540px')
    })

    it('remembers a chosen width across unpin and pin again', async () => {
      render(<App />)
      await pinned()
      handle()!.focus()
      await userEvent.keyboard('{ArrowLeft}')
      await press('Pin beside the page') // unpin
      expect(width()).toBe('')
      await press('Pin beside the page')
      expect(handle()!.getAttribute('aria-valuenow')).toBe('512')
      expect(window.localStorage.getItem('vouch.chatWidth')).toBe('512')
    })
  })

  it('names the page it is on in its header', async () => {
    serve()
    render(<App />)
    await open()
    expect(within(bubble()!).getByText('on Dossier')).toBeTruthy()
  })

  it("names the control on its working paper", async () => {
    serve()
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    await screen.findByRole('heading', { name: /Question 4\.1\.2/ })
    await open()
    expect(within(bubble()!).getByText('on 4.1.2 · Working paper')).toBeTruthy()
  })

  type ChatBody = { message?: string; page: string; control_id: string | null; thread_id: string | null }
  const sse = (blocks: string[]) => new Response(blocks.join(''), { headers: { 'Content-Type': 'text/event-stream' } })
  /** A greeting with nothing to say, on chat thread t0. */
  const QUIET = () => Promise.resolve(sse(['event: thread\ndata: {"thread_id":"t0"}\n\nevent: done\ndata: {}\n\n']))

  /** The API as `serve` has it, and `/api/chat` answering a message with `chat`, the greeting with `greeting`. */
  /** The kept conversation the chat opens on (#251): none, unless a test sets one. */
  let kept: { thread_id: string | null; lines: unknown[] } = { thread_id: null, lines: [] }
  const cleared: string[] = []

  function serveChat(chat: (body: ChatBody) => Promise<Response>, greeting: (body: ChatBody) => Promise<Response> = QUIET) {
    serve()
    const others = fetch
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
      if (url === '/api/chat/current') {
        if (init?.method === 'DELETE') cleared.push(url)
        return Promise.resolve(Response.json(init?.method === 'DELETE' ? { thread_id: null } : kept))
      }
      if (url !== '/api/chat') return others(url, init)
      const body = JSON.parse(init!.body as string) as ChatBody
      return body.message === undefined ? greeting(body) : chat(body)
    })
  }

  it('keeps the log on the newest message at its bottom, and leaves a lead who scrolled up to read where they are',
    async () => {
      let write: (block: string) => void = () => {}
      serveChat(() => Promise.resolve(new Response(new ReadableStream<Uint8Array>({
        start(controller) {
          const encoder = new TextEncoder()
          write = (block) => controller.enqueue(encoder.encode(block))
        },
      }), { headers: { 'Content-Type': 'text/event-stream' } })))
      render(<App />)
      await open()
      await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), 'Assess 4.1.2{Enter}')
      const log = within(bubble()!).getByRole('log')
      // jsdom lays nothing out: the log is 1000px of content in a 400px box.
      Object.defineProperty(log, 'scrollHeight', { configurable: true, get: () => 1000 })
      Object.defineProperty(log, 'clientHeight', { configurable: true, get: () => 400 })
      const step = (n: number) => act(async () =>
        write(`event: step\ndata: {"node":"gather","text":"Step ${n}."}\n\n`))

      await step(1)
      await within(log).findByText('Step 1.')
      expect(log.scrollTop).toBe(1000) // followed to the newest

      log.scrollTop = 100 // the lead scrolls up to read
      fireEvent.scroll(log)
      await step(2)
      await within(log).findByText('Step 2.')
      expect(log.scrollTop).toBe(100) // left where they are

      log.scrollTop = 580 // back near the bottom
      fireEvent.scroll(log)
      await step(3)
      await within(log).findByText('Step 3.')
      expect(log.scrollTop).toBe(1000)
    })

  it('marks the log busy while the reply streams, so a screen reader announces the finished message', async () => {
    let write: (block: string) => void = () => {}
    let end: () => void = () => {}
    serveChat(() => Promise.resolve(new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder()
        write = (block) => controller.enqueue(encoder.encode(block))
        end = () => controller.close()
      },
    }), { headers: { 'Content-Type': 'text/event-stream' } })))
    render(<App />)
    await open()
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), 'Hello{Enter}')
    const log = within(bubble()!).getByRole('log')
    await act(async () => write('event: token\ndata: {"text":"Thank"}\n\n'))
    await within(log).findByText('Thank')
    expect(log.getAttribute('aria-busy')).toBe('true')
    await act(async () => {
      write('event: token\ndata: {"text":" you."}\n\nevent: done\ndata: {}\n\n')
      end()
    })
    await within(log).findByText('Thank you.')
    await waitFor(() => expect(log.getAttribute('aria-busy')).toBe('false'))
  })

  it('keeps Send disabled while the message is empty', async () => {
    render(<App />)
    await open()
    const send = screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement
    expect(send.disabled).toBe(true)
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), '   ')
    expect(send.disabled).toBe(true)
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), 'Hello')
    expect(send.disabled).toBe(false)
  })

  it("sends the message with the page, and shows Vouch's reply as it streams", async () => {
    const asked: unknown[] = []
    serveChat((body) => {
      asked.push(body)
      return Promise.resolve(sse(['event: token\ndata: {"text":"Thank"}\n\n', 'event: token\ndata: {"text":" you."}\n\n',
        'event: done\ndata: {}\n\n']))
    })
    render(<App />)
    await open()
    const field = screen.getByRole('textbox', { name: 'Message to Vouch' })
    await userEvent.type(field, 'Where do I stand?{Enter}')
    const log = within(bubble()!).getByRole('log')
    await within(log).findByText('Thank you.')
    expect(within(log).getByText('Where do I stand?')).toBeTruthy()
    expect((field as HTMLInputElement).value).toBe('')
    expect(asked).toEqual([{ message: 'Where do I stand?', page: 'dossier', control_id: null, thread_id: 't0' }])
  })

  it('greets the lead once, on first opening, with where they stand and the next step as a chip', async () => {
    const greetings: ChatBody[] = []
    const asked: ChatBody[] = []
    serveChat((body) => {
      asked.push(body)
      return Promise.resolve(sse(['event: thread\ndata: {"thread_id":"t1"}\n\nevent: done\ndata: {}\n\n']))
    }, (body) => {
      greetings.push(body)
      return Promise.resolve(sse(['event: suggestion\ndata: {"control_id":"1.1.1","action":"assess"}\n\n',
        'event: token\ndata: {"text":"You have not assessed any of the 12 controls yet."}\n\n',
        'event: thread\ndata: {"thread_id":"t1"}\n\nevent: done\ndata: {}\n\n']))
    })
    render(<App />)
    await open()
    const log = within(bubble()!).getByRole('log')
    await within(log).findByText('You have not assessed any of the 12 controls yet.')
    expect(greetings).toEqual([{ page: 'dossier', control_id: null, thread_id: null }])

    await userEvent.click(within(log).getByRole('button', { name: 'Assess 1.1.1' }))

    await waitFor(() => expect(asked).toEqual([{ message: 'Assess 1.1.1', page: 'dossier', control_id: null, thread_id: 't1' }]))
    expect(within(log).getByText('Assess 1.1.1')).toBeTruthy() // the chip's message, as the lead's
    expect(within(log).queryByRole('button', { name: 'Assess 1.1.1' })).toBeNull()
    await press('Close the chat')
    await open()
    expect(greetings).toHaveLength(1)
  })

  it('opens on the conversation kept on the server, as it was, without a greeting (issue 251)', async () => {
    kept = { thread_id: 'k'.repeat(32), lines: [
      { from: 'lead', text: 'Where do I stand?' },
      { from: 'vouch', text: 'You have **12** controls to go.' },
    ] }
    const asked: ChatBody[] = []
    try {
      serveChat((body) => { asked.push(body); return QUIET() }, (body) => { asked.push(body); return QUIET() })
      render(<App />)
      await screen.findByRole('heading', { level: 1 })
      await open()
      const log = within(bubble()!).getByRole('log')
      await within(log).findByText('Where do I stand?')
      expect(within(log).getByText('12').tagName).toBe('STRONG') // through Prose, as when it was live
      expect(asked).toEqual([]) // no greeting over a kept conversation

      await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), 'And now?{Enter}')
      await waitFor(() => expect(asked).toHaveLength(1))
      expect(asked[0].thread_id).toBe('k'.repeat(32)) // it goes on with the kept thread
    } finally {
      kept = { thread_id: null, lines: [] }
    }
  })

  it('"New conversation" empties the chat, and the next message starts a new thread (issue 251)', async () => {
    kept = { thread_id: 'k'.repeat(32), lines: [{ from: 'lead', text: 'An old question' }] }
    const asked: ChatBody[] = []
    try {
      serveChat((body) => { asked.push(body); return QUIET() })
      render(<App />)
      await screen.findByRole('heading', { level: 1 })
      await open()
      const log = within(bubble()!).getByRole('log')
      await within(log).findByText('An old question')

      await userEvent.click(within(bubble()!).getByRole('button', { name: 'New conversation' }))
      expect(within(log).queryByText('An old question')).toBeNull()
      await waitFor(() => expect(cleared).toHaveLength(1))

      await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), 'Fresh{Enter}')
      await waitFor(() => expect(asked).toHaveLength(1))
      expect(asked[0].thread_id).toBeNull()
    } finally {
      kept = { thread_id: null, lines: [] }
      cleared.length = 0
    }
  })

  it('offers a waiting review as a link to its working paper', async () => {
    serveChat(QUIET, () => Promise.resolve(sse(['event: suggestion\ndata: {"control_id":"4.1.2","action":"review"}\n\n',
      'event: thread\ndata: {"thread_id":"t1"}\n\nevent: done\ndata: {}\n\n'])))
    render(<App />)
    await screen.findByRole('heading', { level: 1 })
    await open()

    await userEvent.click(await within(bubble()!).findByRole('link', { name: 'Review 4.1.2' }))

    expect(window.location.pathname).toBe('/controls/4.1.2')
    await screen.findByRole('heading', { name: /Question 4\.1\.2/ })
  })

  it("links each control a cross-control answer names to its working paper, from the tool's list only (issue 217)",
    async () => {
      serveChat(() => Promise.resolve(sse([
        'event: step\ndata: {"node":"assessed_controls","text":"Read the newest assessments of 1 control."}\n\n',
        'event: papers\ndata: {"control_ids":["1.1.1","4.1.2"]}\n\n',
        'event: token\ndata: {"text":"Documented evidence only: **4.1.2**. Not assessed yet: 1.1.1. '
          + 'See [9.9.9](javascript:alert(1)) and <a href=\\"/x\\">5.1.1</a>."}\n\n',
        'event: done\ndata: {}\n\n'])))
      render(<App />)
      await screen.findByRole('heading', { level: 1 })
      await open()
      await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }),
        'Which controls have documented evidence only?{Enter}')
      const log = within(bubble()!).getByRole('log')
      await within(log).findByText(/Not assessed yet/)

      const links = within(log).getAllByRole('link')
      expect(links.map((a) => [a.textContent, a.getAttribute('href')])).toEqual(
        [['1.1.1', '/controls/1.1.1'], ['4.1.2', '/controls/4.1.2']])
      expect(within(log).getByRole('link', { name: 'Working paper 4.1.2' })).toBeTruthy()
      expect(log.querySelector('a[href^="javascript"], a[href="/x"]')).toBeNull() // model text stays text

      await userEvent.click(within(log).getByRole('link', { name: 'Working paper 4.1.2' }))
      expect(window.location.pathname).toBe('/controls/4.1.2')
      await screen.findByRole('heading', { name: /Question 4\.1\.2/ })
    })

  it("shows an assessment's steps, then its verdict with a link that opens its working paper", async () => {
    serveChat(() => Promise.resolve(sse([
      'event: step\ndata: {"node":"load_control","text":"Loaded control 4.1.2."}\n\n',
      'event: step\ndata: {"node":"guard","text":"Removed an email address from the step."}\n\n',
      'event: step\ndata: {"node":"judge","text":"Judged 1 of 2 requirements evidenced."}\n\n',
      'event: assessment\ndata: {"control_id":"4.1.2","verdict":"partly-evidenced","status":"waiting-for-review","thread_id":"a1"}\n\n',
      'event: token\ndata: {"text":"The assessment of control 4.1.2 has finished."}\n\n',
      'event: done\ndata: {}\n\n'])))
    render(<App />)
    await screen.findByRole('heading', { level: 1 })
    await open()
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), 'Assess 4.1.2{Enter}')
    const log = within(bubble()!).getByRole('log')

    await within(log).findByText('The assessment of control 4.1.2 has finished.')
    // Each step as the trace shows it: its node, then what it did; a guard step marked apart.
    const steps = [...log.querySelectorAll('.msg-step')]
    expect(steps.map((step) => step.textContent)).toEqual(['load_control Loaded control 4.1.2.',
      'guard Removed an email address from the step.', 'judge Judged 1 of 2 requirements evidenced.'])
    expect(steps.map((step) => step.classList.contains('guard'))).toEqual([false, true, false])
    expect(within(log).getByText('Partly evidenced').className).toBe('chip v-partly-evidenced')
    expect(within(log).getByText('Proposed').className).toBe('tag tag-waiting-for-review') // its review state tag
    await userEvent.click(within(log).getByRole('link', { name: 'Open the working paper' }))

    expect(window.location.pathname).toBe('/controls/4.1.2')
    await screen.findByRole('heading', { name: /Question 4\.1\.2/ })
    expect(bubble()).toBeTruthy() // the chat stays open beside the paper
  })

  it("shows a German profile's chat verdict in German words, marked as German (ticket 200)", async () => {
    serveChat(() => Promise.resolve(sse([
      'event: assessment\ndata: {"control_id":"4.1.2","verdict":"partly-evidenced","status":"waiting-for-review","thread_id":"a1","answer_language":"de"}\n\n',
      'event: token\ndata: {"text":"Die Bewertung ist abgeschlossen."}\n\n',
      'event: done\ndata: {}\n\n'])))
    render(<App />)
    await screen.findByRole('heading', { level: 1 })
    await open()
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), 'Assess 4.1.2{Enter}')
    const log = within(bubble()!).getByRole('log')

    const chip = await within(log).findByText('Teilweise nachgewiesen')
    expect(chip.className).toBe('chip v-partly-evidenced')
    expect(chip.getAttribute('lang')).toBe('de')
    expect(within(log).getByText('Proposed').getAttribute('lang')).toBeNull() // the interface stays English
  })

  it("shows an English profile's chat verdict with no lang mark (ticket 200)", async () => {
    serveChat(() => Promise.resolve(sse([
      'event: assessment\ndata: {"control_id":"4.1.2","verdict":"evidenced","status":"waiting-for-review","thread_id":"a1","answer_language":"en"}\n\n',
      'event: done\ndata: {}\n\n'])))
    render(<App />)
    await screen.findByRole('heading', { level: 1 })
    await open()
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), 'Assess 4.1.2{Enter}')

    const chip = await within(within(bubble()!).getByRole('log')).findByText('Evidenced')
    expect(chip.getAttribute('lang')).toBeNull()
  })

  it('goes on with the thread a failed first turn named, and asks for a failed greeting again on the next opening',
    async () => {
      const greetings: ChatBody[] = []
      const asked: ChatBody[] = []
      serveChat((body) => {
        asked.push(body)
        return Promise.resolve(sse(asked.length === 1
          ? ['event: thread\ndata: {"thread_id":"t9"}\n\n', 'event: error\ndata: {"message":"Vouch could not answer."}\n\n']
          : ['event: done\ndata: {}\n\n']))
      }, (body) => {
        greetings.push(body)
        return Promise.reject(new TypeError('Failed to fetch'))
      })
      render(<App />)
      await open()
      await within(bubble()!).findByText('Vouch could not be reached just now. Please try again shortly.')
      expect(greetings).toHaveLength(1) // not asked for over and over while open
      const field = screen.getByRole('textbox', { name: 'Message to Vouch' })
      await userEvent.type(field, 'Hello{Enter}')
      await within(bubble()!).findByText('Vouch could not answer.')
      await userEvent.clear(field)
      await userEvent.type(field, 'Again{Enter}')
      await waitFor(() => expect(asked).toHaveLength(2))
      expect(asked[1].thread_id).toBe('t9')

      await press('Close the chat')
      await open()
      await waitFor(() => expect(greetings).toHaveLength(2))
    })

  it("at the daily limit, the greeting shows the limit's message", async () => {
    const limit = "You have reached today's limit of 0 assessments. It resets at 00:00 UTC on 6 November 2026."
    serveChat(QUIET, () => Promise.resolve(Response.json({ detail: limit, limit: 'daily' }, { status: 429 })))
    render(<App />)
    await open()

    expect(await within(bubble()!).findByText(limit)).toBeTruthy()
    expect((screen.getByRole('textbox', { name: 'Message to Vouch' }) as HTMLInputElement).value).toBe('')
  })

  it('with the chat endpoint down, says so and keeps the typed message', async () => {
    serveChat(() => Promise.reject(new TypeError('Failed to fetch')))
    render(<App />)
    await open()
    const field = screen.getByRole('textbox', { name: 'Message to Vouch' }) as HTMLInputElement
    await userEvent.type(field, 'Where do I stand?')
    await press('Send')
    await within(bubble()!).findByText(/Vouch could not be reached/)
    expect(field.value).toBe('Where do I stand?')
  })
})

describe('local mode', () => {
  it('has no sign-out: nobody is signed in, so the user menu offers only the theme (issue 222)', async () => {
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Your menu' }))
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Dark theme'])
    expect(screen.queryByText(/Signed in as/)).toBeNull()
  })
})

describe('remediation items', () => {
  const draft = (id: string) => ({ requirement_id: id, missing: `What ${id} lacks.`, owner_role: 'CISO',
    due_date: '2027-02-01', status: 'draft' })
  const ACCEPTED = { ...STORED, status: 'accepted', verdict: 'not-evidenced',
    requirements: ['M1', 'M2'].map((id) => ({ id, column: 'must', text: `${id}.`, verdict: 'not-evidenced',
      reason: 'Nothing on file.', citations: [] })),
    remediation: [draft('M1'), draft('M2')] }

  it('keeps both of two decisions in flight, whichever lands first', async () => {
    const answers: Record<string, (r: Response) => void> = {}
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const decided = /\/remediation\/(M\d)$/.exec(url)
      if (decided) return new Promise<Response>((resolve) => { answers[decided[1]] = resolve })
      return url === '/api/overview' ? Promise.resolve(Response.json(overviewWith([STORED])))
        : url === '/api/assessments' ? Promise.resolve(Response.json({ assessments: [ACCEPTED] }))
          : url.startsWith('/api/assessments/') ? Promise.resolve(Response.json({ assessment: ACCEPTED }))
            : new Promise<Response>(() => {})
    }))
    window.history.replaceState(null, '', '/controls/4.1.2')
    render(<App />)
    const item = (id: string) => screen.getByRole('region', { name: `Remediation item ${id}` })
    await screen.findByRole('region', { name: 'Remediation item M1' })

    await userEvent.click(within(item('M1')).getByRole('button', { name: 'Approve M1' }))
    await userEvent.click(within(item('M2')).getByRole('button', { name: 'Discard M2' }))
    await act(async () => answers.M1(Response.json({ item: { ...draft('M1'), status: 'approved' } })))
    await act(async () => answers.M2(Response.json({ item: { ...draft('M2'), status: 'discarded' } })))

    expect(within(item('M1')).getByText('Approved')).toBeTruthy()
    expect(within(item('M2')).getByText('Discarded')).toBeTruthy()
    expect(within(item('M1')).queryByRole('button')).toBeNull()
  })
})
