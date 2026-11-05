import { afterEach, describe, expect, it, vi } from 'vitest'
import { CHAT_UNREACHABLE, type ChatEvent, pageLabel, sendChat } from './chat.ts'

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
    expect(JSON.parse(init.body as string)).toEqual({ message: 'What is missing?', page: 'paper', control_id: '4.1.2' })
  })

  it('sends no control off a working paper', async () => {
    const fetch = vi.fn(() => Promise.resolve(sse(['event: done\ndata: {}\n\n'])))
    vi.stubGlobal('fetch', fetch)

    await all(sendChat('Hello', { page: 'dossier', controlId: null }))

    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ message: 'Hello', page: 'dossier', control_id: null })
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

describe('pageLabel', () => {
  it('names the page, and the control on a working paper', () => {
    expect(pageLabel({ page: 'dossier', controlId: null })).toBe('on Dossier')
    expect(pageLabel({ page: 'developer', controlId: null })).toBe('on Developer')
    expect(pageLabel({ page: 'paper', controlId: '4.1.2' })).toBe('on 4.1.2 · Working paper')
  })
})
