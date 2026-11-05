import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import { type Overview, OVERVIEW_FAILED } from './overview.ts'

/** The window's width and theme as media queries see them; `resizeTo` changes the width, telling each listener. */
let resizeTo: (width: number) => void = () => {}

function systemPrefers(dark: boolean | null, width = 1400) {
  let now = width
  const listeners = new Set<() => void>()
  resizeTo = (next) => act(() => {
    now = next
    for (const listener of listeners) listener()
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
  company: 'Nordlicht Komponenten GmbH', country: 'Germany',
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

/**
 * The API: the overview, the assessment's stream when given (POST), the stored assessments (GET: none unless given),
 * and nothing else ever answers.
 */
function serve(assessment?: () => Promise<Response>, stored: (typeof STORED)[] = []) {
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) =>
    url === '/api/overview' ? Promise.resolve(Response.json(OVERVIEW))
      : url === '/api/assessments' && init?.method === 'POST' ? (assessment ? assessment() : new Promise(() => {}))
        : url === '/api/assessments' ? Promise.resolve(Response.json({ assessments: stored }))
          : url.startsWith('/api/assessments/') && stored.length
            ? Promise.resolve(Response.json({ assessment: stored.find((a) => url.endsWith(a.thread_id)) }))
            : new Promise(() => {}))
}

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

  it('shows the Dossier for a control the overview does not list', async () => {
    serve()
    window.history.replaceState(null, '', '/controls/9.9.9')
    render(<App />)
    expect(await screen.findAllByRole('article', { name: /^Control / })).toHaveLength(12)
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
  const QUIET = () => Promise.resolve(sse(['event: done\ndata: {"thread_id":"t0"}\n\n']))

  /** The API as `serve` has it, and `/api/chat` answering a message with `chat`, the greeting with `greeting`. */
  function serveChat(chat: (body: ChatBody) => Promise<Response>, greeting: (body: ChatBody) => Promise<Response> = QUIET) {
    serve()
    const others = fetch
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
      if (url !== '/api/chat') return others(url, init)
      const body = JSON.parse(init!.body as string) as ChatBody
      return body.message === undefined ? greeting(body) : chat(body)
    })
  }

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
      return Promise.resolve(sse(['event: done\ndata: {"thread_id":"t1"}\n\n']))
    }, (body) => {
      greetings.push(body)
      return Promise.resolve(sse(['event: suggestion\ndata: {"control_id":"1.1.1","action":"assess"}\n\n',
        'event: token\ndata: {"text":"You have not assessed any of the 12 controls yet."}\n\n',
        'event: done\ndata: {"thread_id":"t1"}\n\n']))
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

  it('offers a waiting review as a link to its working paper', async () => {
    serveChat(QUIET, () => Promise.resolve(sse(['event: suggestion\ndata: {"control_id":"4.1.2","action":"review"}\n\n',
      'event: done\ndata: {"thread_id":"t1"}\n\n'])))
    render(<App />)
    await screen.findByRole('heading', { level: 1 })
    await open()

    await userEvent.click(await within(bubble()!).findByRole('link', { name: 'Review 4.1.2' }))

    expect(window.location.pathname).toBe('/controls/4.1.2')
    await screen.findByRole('heading', { name: /Question 4\.1\.2/ })
  })

  it("shows an assessment's steps, then its verdict with a link that opens its working paper", async () => {
    serveChat(() => Promise.resolve(sse([
      'event: step\ndata: {"node":"load_control","text":"Loaded control 4.1.2."}\n\n',
      'event: step\ndata: {"node":"judge","text":"Judged 1 of 2 requirements evidenced."}\n\n',
      'event: assessment\ndata: {"control_id":"4.1.2","verdict":"partly-evidenced","status":"waiting-for-review","thread_id":"a1"}\n\n',
      'event: token\ndata: {"text":"The assessment of control 4.1.2 has finished."}\n\n',
      'event: done\ndata: {"thread_id":"t0"}\n\n'])))
    render(<App />)
    await screen.findByRole('heading', { level: 1 })
    await open()
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to Vouch' }), 'Assess 4.1.2{Enter}')
    const log = within(bubble()!).getByRole('log')

    await within(log).findByText('The assessment of control 4.1.2 has finished.')
    expect(within(log).getByText('Loaded control 4.1.2.')).toBeTruthy()
    expect(within(log).getByText('Judged 1 of 2 requirements evidenced.')).toBeTruthy()
    expect(within(log).getByText('Partly evidenced').className).toBe('chip v-partly-evidenced')
    await userEvent.click(within(log).getByRole('link', { name: 'Open the working paper' }))

    expect(window.location.pathname).toBe('/controls/4.1.2')
    await screen.findByRole('heading', { name: /Question 4\.1\.2/ })
    expect(bubble()).toBeTruthy() // the chat stays open beside the paper
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
  it('has no sign-out: nobody is signed in', () => {
    render(<App />)
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull()
  })
})
