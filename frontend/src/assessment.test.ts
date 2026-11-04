import { afterEach, describe, expect, it, vi } from 'vitest'
import { assess, readEvents } from './assessment.ts'
import { setSessionToken } from './session.ts'

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(bytes.encode(chunk))
      controller.close()
    },
  })
}

async function all(body: ReadableStream<Uint8Array>) {
  const events = []
  for await (const event of readEvents(body)) events.push(event)
  return events
}

describe('readEvents', () => {
  it('reads named events with JSON data, in order', async () => {
    const body = streamOf(
      'event: step\ndata: {"node":"gather","text":"Searched"}\n\n',
      'event: result\ndata: {"assessment":{"verdict":"evidenced"}}\n\n',
    )
    expect(await all(body)).toEqual([
      { event: 'step', data: { node: 'gather', text: 'Searched' } },
      { event: 'result', data: { assessment: { verdict: 'evidenced' } } },
    ])
  })

  it('joins an event split across chunks, even inside a multi-byte character', async () => {
    const whole = 'event: step\r\ndata: {"node":"cite","text":"Zitat über"}\r\n\r\n'
    const bytes = new TextEncoder().encode(whole)
    const cut = whole.indexOf('ü') + 1 // inside the two bytes of ü
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut))
        controller.enqueue(bytes.slice(cut))
        controller.close()
      },
    })
    expect(await all(body)).toEqual([{ event: 'step', data: { node: 'cite', text: 'Zitat über' } }])
  })

  it('reads a \\r\\n line end cut between chunks as one line end', async () => {
    const body = streamOf('event: step\r', '\ndata: {"node":"judge","text":"Judged"}\r\n\r', '\n')
    expect(await all(body)).toEqual([{ event: 'step', data: { node: 'judge', text: 'Judged' } }])
  })

  it('skips comments and keep-alive pings', async () => {
    const body = streamOf(': ping\n\n', 'event: error\ndata: {"message":"No."}\n\n')
    expect(await all(body)).toEqual([{ event: 'error', data: { message: 'No.' } }])
  })
})

describe('assess, signed in', () => {
  const result =
    'event: result\ndata: {"assessment":{"control_id":"NK-ENC-01","verdict":"evidenced","answer":"Yes."}}\n\n'
  const sentHeaders = async () => {
    const calls: RequestInit[] = []
    vi.stubGlobal('fetch', (_url: string, init: RequestInit) => {
      calls.push(init)
      return Promise.resolve(new Response(streamOf(result), { status: 200 }))
    })
    for await (const item of assess('NK-ENC-01')) void item
    return calls[0].headers as Record<string, string>
  }

  afterEach(() => {
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  it('sends a fresh session token from the token source on each call', async () => {
    let n = 0
    setSessionToken(async () => `token-${++n}`)
    expect((await sentHeaders()).Authorization).toBe('Bearer token-1')
    expect((await sentHeaders()).Authorization).toBe('Bearer token-2')
  })

  it('sends no Authorization header without a token source (local mode) or a token', async () => {
    expect(await sentHeaders()).not.toHaveProperty('Authorization')
    setSessionToken(async () => null)
    expect(await sentHeaders()).not.toHaveProperty('Authorization')
  })
})
