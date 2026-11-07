import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  CHAT_UNREACHABLE, type ChatEvent, fetchConversation, HISTORY_UNREACHABLE, namedControls, newConversation, restoredMessages, pageLabel, sendChat, suggestionLabel, suggestionMessage,
} from './chat.ts'

afterEach(() => vi.unstubAllGlobals())

const sse = (blocks: string[], status = 200) =>
  new Response(blocks.join(''), { status, headers: { 'Content-Type': 'text/event-stream' } })

async function all(events: AsyncGenerator<ChatEvent>) {
  const seen: ChatEvent[] = []
  for await (const e of events) seen.push(e)
  return seen
}

describe('sendChat', () => {
  it('posts the message with the page and control, and yields the reply in pieces, then done', async () => {
    const fetch = vi.fn(() => Promise.resolve(sse([
      'event: token\ndata: {"text":"Thank"}\n\n', 'event: token\ndata: {"text":" you."}\n\n', 'event: done\ndata: {}\n\n'])))
    vi.stubGlobal('fetch', fetch)

    const seen = await all(sendChat('What is missing?', { page: 'paper', controlId: '4.1.2' }))

    expect(seen).toEqual([
      { event: 'token', data: { text: 'Thank' } }, { event: 'token', data: { text: ' you.' } }, { event: 'done', data: {} }])
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/chat')
    expect(JSON.parse(init.body as string)).toEqual({ message: 'What is missing?', page: 'paper', control_id: '4.1.2', thread_id: null })
  })

  it('sends no control off a working paper', async () => {
    const fetch = vi.fn(() => Promise.resolve(sse(['event: done\ndata: {}\n\n'])))
    vi.stubGlobal('fetch', fetch)

    await all(sendChat('Hello', { page: 'dossier', controlId: null }))

    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ message: 'Hello', page: 'dossier', control_id: null, thread_id: null })
  })

  it('asks for the greeting with no message, and goes on with a chat thread when given one', async () => {
    const fetch = vi.fn(() => Promise.resolve(sse(['event: thread\ndata: {"thread_id":"t1"}\n\nevent: done\ndata: {}\n\n'])))
    vi.stubGlobal('fetch', fetch)

    await all(sendChat(null, { page: 'dossier', controlId: null }))
    await all(sendChat('Assess 5.1.1', { page: 'dossier', controlId: null }, 't1'))

    const bodies = fetch.mock.calls.map((call) => JSON.parse((call as unknown as [string, RequestInit])[1].body as string))
    expect(bodies).toEqual([
      { page: 'dossier', control_id: null, thread_id: null },
      { message: 'Assess 5.1.1', page: 'dossier', control_id: null, thread_id: 't1' },
    ])
  })

  it('throws when the service answers 5xx, so the chat shows it could not be reached', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(new Response('', { status: 502 })))

    await expect(all(sendChat('Hello', { page: 'dossier', controlId: null }))).rejects.toThrow()
  })

  it('throws when the stream ends before done', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(sse(['event: token\ndata: {"text":"Thank"}\n\n'])))

    await expect(all(sendChat('Hello', { page: 'dossier', controlId: null }))).rejects.toThrow()
  })

  it("yields a refusal's detail as an error: the rate limit says why", async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json({ detail: 'Too many requests.' }, { status: 429 })))

    expect(await all(sendChat('Hello', { page: 'dossier', controlId: null })))
      .toEqual([{ event: 'error', data: { message: 'Too many requests.' } }])
  })

  it('has a formal sentence for an unreachable service', () => {
    expect(CHAT_UNREACHABLE).toMatch(/^Vouch could not be reached/)
  })
})

describe('suggestion chips', () => {
  it('words the next step; an assessment chip sends "Assess <id>"', () => {
    expect(suggestionLabel({ control_id: '1.1.1', action: 'assess' })).toBe('Assess 1.1.1')
    expect(suggestionLabel({ control_id: '1.6.1', action: 'assess-again' })).toBe('Assess 1.6.1 again')
    expect(suggestionLabel({ control_id: '5.1.1', action: 'review' })).toBe('Review 5.1.1')
    expect(suggestionMessage({ control_id: '1.6.1', action: 'assess-again' })).toBe('Assess 1.6.1')
  })
})

describe('namedControls (issue 217)', () => {
  const ids = ['1.1.1', '5.1.1', '5.1.2', '5.1.10']

  it("gives the tool's controls the reply names, whole ids only, in the tool's order", () => {
    expect(namedControls('Documented only: **5.1.2** and 5.1.1. Not assessed: 1.1.1.', ids))
      .toEqual(['1.1.1', '5.1.1', '5.1.2'])
    expect(namedControls('Only 5.1.10 is stale.', ids)).toEqual(['5.1.10'])
    expect(namedControls('Version 15.1.1 and 5.1.1x are no ids.', ids)).toEqual([])
  })

  it("never gives an id the tool didn't list", () => {
    expect(namedControls('See 9.9.9 and 5.1.1.', ['5.1.1'])).toEqual(['5.1.1'])
  })
})

describe('pageLabel', () => {
  it('names the page, and the control on a working paper', () => {
    expect(pageLabel({ page: 'dossier', controlId: null })).toBe('on Dossier')
    expect(pageLabel({ page: 'developer', controlId: null })).toBe('on Developer')
    expect(pageLabel({ page: 'paper', controlId: '4.1.2' })).toBe('on 4.1.2 · Working paper')
  })

  it('names the readiness report by the top bar\'s word for it (issue 268)', () => {
    expect(pageLabel({ page: 'report', controlId: null })).toBe('on Report')
  })
})


describe('the kept conversation (issue 251)', () => {
  it('restores each kept line as the message the chat showed', () => {
    expect(restoredMessages([
      { from: 'lead', text: 'Assess 5.1.1' },
      { from: 'step', step: { node: 'judge', text: 'Judged 2 requirements.' } },
      { from: 'vouch', text: 'Documented: **5.1.1**.', papers: ['5.1.1'] },
      { from: 'vouch', text: 'Vouch could not answer just now.', failed: true },
      { from: 'verdict', control_id: '5.1.1', verdict: 'partly-evidenced', status: 'waiting-for-review',
        answer_language: 'de' },
    ])).toEqual([
      { from: 'lead', text: 'Assess 5.1.1' },
      { from: 'step', step: { node: 'judge', text: 'Judged 2 requirements.' } },
      { from: 'vouch', text: 'Documented: **5.1.1**.', papers: ['5.1.1'] },
      { from: 'vouch', text: 'Vouch could not answer just now.', failed: true },
      { from: 'verdict', controlId: '5.1.1', verdict: 'partly-evidenced', status: 'waiting-for-review',
        language: 'de' },
    ])
  })

  it('reads the current conversation, and a new one clears it', async () => {
    const calls: [string, string][] = []
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
      calls.push([url, init?.method ?? 'GET'])
      return Promise.resolve(url === '/api/chat/current' && (init?.method ?? 'GET') === 'GET'
        ? Response.json({ thread_id: 'a'.repeat(32), lines: [{ from: 'lead', text: 'Hi' }] })
        : Response.json({ thread_id: null }))
    })
    expect(await fetchConversation()).toEqual({ threadId: 'a'.repeat(32), messages: [{ from: 'lead', text: 'Hi' }],
      note: null })
    await newConversation()
    expect(calls).toEqual([['/api/chat/current', 'GET'], ['/api/chat/current', 'DELETE']])
  })

  it("opens empty with a note where the conversation can't be read, or the service isn't reached", async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json({ thread_id: null, lines: [], note: 'Not now.' })))
    expect(await fetchConversation()).toEqual({ threadId: null, messages: [], note: 'Not now.' })
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')))
    expect(await fetchConversation()).toEqual({ threadId: null, messages: [], note: HISTORY_UNREACHABLE })
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(null, { status: 503 })))
    expect(await fetchConversation()).toEqual({ threadId: null, messages: [], note: HISTORY_UNREACHABLE })
  })
})
