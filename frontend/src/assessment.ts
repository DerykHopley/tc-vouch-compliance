/** One control's assessment from `POST /api/assessments`, read as server-sent events. */

/** A requirement's or a control's verdict, as the API sends it (`vouch.graph.Verdict`). */
export type Verdict = 'evidenced' | 'partly-evidenced' | 'not-evidenced' | 'not-applicable'

export const VERDICT_WORDS: Record<Verdict, string> = {
  evidenced: 'Evidenced',
  'partly-evidenced': 'Partly evidenced',
  'not-evidenced': 'Not evidenced',
  'not-applicable': 'Not applicable',
}

/** The column a requirement comes from (`vouch.isa.Column`). */
export type Column = 'must' | 'should' | 'high' | 'very high' | 'overlay'

export type Control = { id: string; title: string; owner: string }

export type Step = { node: string; text: string }
/** One judged requirement, as its `requirement` event and the Assessment carry it. */
export type RequirementVerdict = { id: string; column: Column; text: string; verdict: Verdict; reason: string }
// The page reads only these of the Assessment's fields so far.
export type Assessment = { control_id: string; verdict: Verdict; answer: string }

export type AssessmentEvent =
  | { event: 'step'; data: Step }
  | { event: 'requirement'; data: { requirement: RequirementVerdict } }
  | { event: 'result'; data: { assessment: Assessment } }
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
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ control_id: controlId }),
  })
  if (response.status >= 500 || !response.body) throw new Error(`service answered ${response.status}`)
  if (!response.ok) {
    yield { event: 'error', data: { message: NOT_STARTED } }
    return
  }
  for await (const item of readEvents(response.body)) {
    yield item as AssessmentEvent
    if (item.event === 'result' || item.event === 'error') return
  }
  throw new Error('the stream ended before the assessment')
}
