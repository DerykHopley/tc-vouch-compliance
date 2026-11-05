import { afterEach, describe, expect, it, vi } from 'vitest'
import { assess, count, plural, readEvents, usageWords, verdictWord } from './assessment.ts'
import { setOnSignedOut, setTokenSource } from './session.ts'

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
    setTokenSource(null)
    vi.unstubAllGlobals()
  })

  it('sends a fresh session token from the token source on each call', async () => {
    let n = 0
    setTokenSource(async () => `token-${++n}`)
    expect((await sentHeaders()).Authorization).toBe('Bearer token-1')
    expect((await sentHeaders()).Authorization).toBe('Bearer token-2')
  })

  it('sends no Authorization header without a token source (local mode) or a token', async () => {
    expect(await sentHeaders()).not.toHaveProperty('Authorization')
    setTokenSource(async () => null)
    expect(await sentHeaders()).not.toHaveProperty('Authorization')
  })
})

describe('usageWords', () => {
  const usage = { input_tokens: 10, output_tokens: 5, cost_usd: 1.5, estimated: false, cost_complete: true }

  it('puts the tokens and the cost in words, saying when the cost is estimated', () => {
    expect(usageWords({ ...usage, input_tokens: 1234, output_tokens: 56, cost_usd: 0.000325, estimated: true })).toBe(
      '1,234 tokens in · 56 tokens out · $0.0003 (estimated)',
    )
    expect(usageWords(usage)).toBe('10 tokens in · 5 tokens out · $1.50')
  })

  it('says an incomplete cost is at least its known part, and unknown with none known', () => {
    expect(usageWords({ ...usage, cost_complete: false })).toBe('10 tokens in · 5 tokens out · at least $1.50')
    expect(usageWords({ ...usage, cost_usd: 0, cost_complete: false })).toBe('10 tokens in · 5 tokens out · cost unknown')
  })

  it('counts and plurals in words', () => {
    expect(count(12345)).toBe('12,345')
    expect(plural(1, 'assessment')).toBe('1 assessment')
    expect(plural(1200, 'assessment')).toBe('1,200 assessments')
  })
})

describe('assess, when a limit refuses it (429)', () => {
  afterEach(() => vi.unstubAllGlobals())

  const events = async (body: string) => {
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(body, { status: 429 })))
    const seen = []
    for await (const item of assess('NK-ENC-01')) seen.push(item)
    return seen
  }

  it("says the daily limit's message, with its reset time", async () => {
    const detail = "You have reached today's limit of 30 assessments. It resets at 00:00 UTC on 5 November 2026."
    expect(await events(JSON.stringify({ detail, limit: 'daily' }))).toEqual([{ event: 'error', data: { message: detail } }])
  })

  it('falls back to the plain refusal when the 429 has no message', async () => {
    expect(await events('not json')).toEqual([
      { event: 'error', data: { message: 'The assessment could not be started. Please try again later.' } },
    ])
  })
})

describe('assess, when the API says signed out (401)', () => {
  const refuse = () =>
    vi.stubGlobal('fetch', () =>
      Promise.resolve(new Response(JSON.stringify({ detail: 'Sign in to use Vouch.' }), { status: 401 })),
    )
  const events = async () => {
    const seen = []
    for await (const item of assess('NK-ENC-01')) seen.push(item)
    return seen
  }

  afterEach(() => {
    setOnSignedOut(null)
    vi.unstubAllGlobals()
  })

  it('sends the user back to sign-in, and says why', async () => {
    const signedOut = vi.fn()
    setOnSignedOut(signedOut)
    refuse()
    expect(await events()).toEqual([
      { event: 'error', data: { message: 'Your sign-in has ended. Please sign in again.' } },
    ])
    expect(signedOut).toHaveBeenCalledOnce()
  })

  it('still says why with nothing to send back to (local mode)', async () => {
    refuse()
    expect(await events()).toEqual([
      { event: 'error', data: { message: 'Your sign-in has ended. Please sign in again.' } },
    ])
  })
})

describe('verdictWord', () => {
  it('words a verdict in the answer language: German for de (#51)', () => {
    expect(verdictWord('evidenced', 'de')).toBe('Nachgewiesen')
    expect(verdictWord('partly-evidenced', 'de')).toBe('Teilweise nachgewiesen')
    expect(verdictWord('not-evidenced', 'de')).toBe('Nicht nachgewiesen')
    expect(verdictWord('not-applicable', 'de')).toBe('Nicht anwendbar')
  })

  it('words it in English for en, and for a language it has no words in', () => {
    expect(verdictWord('partly-evidenced', 'en')).toBe('Partly evidenced')
    expect(verdictWord('not-evidenced', 'lt')).toBe('Not evidenced')
  })
})
