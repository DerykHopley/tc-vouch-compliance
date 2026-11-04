import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

/** One control's assessment from `POST /api/assessments`, read as server-sent events. */

export type Verdict = 'evidenced' | 'partly-evidenced' | 'not-evidenced'

export const VERDICT_WORDS: Record<Verdict, string> = {
  evidenced: 'Evidenced',
  'partly-evidenced': 'Partly evidenced',
  'not-evidenced': 'Not evidenced',
}

export type Control = { id: string; title: string; owner: string }

export type Step = { node: string; text: string }
// The trial's assessment shape until M1 renames its fields (#20, #21, #26); the page reads only these.
export type Assessment = { control_id: string; verdict: Verdict; answer: string }

/** One assessment's tokens and cost (#35); `cost_usd` null when no price is known, `estimated` unless OpenRouter
 * reported it. */
export type Usage = {
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  estimated: boolean
}

const count = (n: number) => n.toLocaleString('en-US')
const dollars = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4 })

/** Tokens and cost in words, as the working paper and the developer page show them. */
export function usageWords({ input_tokens, output_tokens, cost_usd, estimated }: Omit<Usage, 'model'>): string {
  const cost = cost_usd === null ? 'cost unknown' : `${dollars(cost_usd)}${estimated ? ' (estimated)' : ''}`
  return `${count(input_tokens)} tokens in · ${count(output_tokens)} tokens out · ${cost}`
}

export type AssessmentEvent =
  | { event: 'step'; data: Step }
  | { event: 'result'; data: { assessment: Assessment; usage?: Usage } }
  | { event: 'error'; data: { message: string } }

/** Shown, with Retry, for any throw from `assess`. */
export const UNREACHABLE = 'Vouch could not reach its service. Try again shortly.'
const NOT_STARTED = 'The assessment could not be started. Please try again later.'

/** Each event in a stream of server-sent events, with its data parsed as JSON. Comments are skipped. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: unknown }> {
  const reader = body.getReader()
  const decoder = new TextDecoder() // `stream: true` keeps a character cut between chunks for the next one
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) return
    // Line ends to \n; a \r last in the chunk waits, as its \n may come in the next one.
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n|\r(?=.)/gs, '\n')
    let end
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      let event = 'message'
      const data: string[] = []
      for (const line of block.split('\n')) {
        const [field, ...rest] = line.split(':')
        const value = rest.join(':').replace(/^ /, '')
        if (field === 'event') event = value
        else if (field === 'data') data.push(value)
      }
      if (data.length) yield { event, data: JSON.parse(data.join('\n')) }
    }
  }
}

/**
 * Starts the assessment and yields its events until the `result` or `error`.
 * Throws when the service can't be reached, or the stream breaks off before either.
 */
export async function* assess(controlId: string): AsyncGenerator<AssessmentEvent> {
  const response = await fetch('/api/assessments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(await authHeaders()) },
    body: JSON.stringify({ control_id: controlId }),
  })
  if (response.status >= 500 || !response.body) throw new Error(`service answered ${response.status}`)
  if (sessionEnded(response)) {
    // No valid session (expired, or signed out elsewhere): back to the sign-in (session.ts).
    yield { event: 'error', data: { message: SESSION_ENDED } }
    return
  }
  if (!response.ok) {
    // A limit's 429 (the daily limit, with its reset time, or the request rate) says why; any other refusal doesn't.
    const detail = response.status === 429
      ? await response.json().then((body: { detail?: unknown }) => body.detail, () => undefined)
      : undefined
    yield { event: 'error', data: { message: typeof detail === 'string' ? detail : NOT_STARTED } }
    return
  }
  for await (const item of readEvents(response.body)) {
    yield item as AssessmentEvent
    if (item.event === 'result' || item.event === 'error') return
  }
  throw new Error('the stream ended before the assessment')
}
