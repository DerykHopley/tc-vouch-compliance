import { afterEach, describe, expect, it, vi } from 'vitest'
import { CHAT_UNREACHABLE, type ChatEvent, pageLabel, sendChat, suggestionLabel, suggestionMessage } from './chat.ts'

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
    const fetch = vi.fn(() => Promise.resolve(sse(['event: done\ndata: {"thread_id":"t1"}\n\n'])))
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

describe('pageLabel', () => {
  it('names the page, and the control on a working paper', () => {
    expect(pageLabel({ page: 'dossier', controlId: null })).toBe('on Dossier')
    expect(pageLabel({ page: 'developer', controlId: null })).toBe('on Developer')
    expect(pageLabel({ page: 'paper', controlId: '4.1.2' })).toBe('on 4.1.2 · Working paper')
  })
})
