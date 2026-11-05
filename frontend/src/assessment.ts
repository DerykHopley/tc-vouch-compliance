import { authHeaders, SESSION_ENDED, sessionEnded } from './session.ts'

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

export type Control = { id: string; title: string }

export type Step = { node: string; text: string }
/** Documented (a policy says so) or implemented (a record shows it done): `vouch.documents.EvidenceKind`. */
export type EvidenceKind = 'documented' | 'implemented'
/** A quote checked word for word in this version of the document (`vouch.citations.Citation`). */
export type Citation = {
  doc_id: string
  version: string
  section: string
  quote: string
  kind: EvidenceKind
  stale: boolean
  stale_because: string | null
}
/** One judged requirement, as its `requirement` event and the Assessment carry it. */
export type RequirementVerdict = {
  id: string
  column: Column
  text: string
  verdict: Verdict
  reason: string
  citations: Citation[]
}
/** An assessment's review state (`vouch.graph.Status`); the screen says Proposed for waiting-for-review. */
export type ReviewStatus = 'waiting-for-review' | 'accepted' | 'rejected'

export const REVIEW_WORDS: Record<ReviewStatus, string> = {
  'waiting-for-review': 'Proposed',
  accepted: 'Accepted',
  rejected: 'Rejected',
}

/** The lead's recorded decision on the control (`vouch.graph.Review`); its overrides keep the agent's verdicts. */
export type Review = {
  decision: 'accept' | 'reject'
  note: string
  overrides: { requirement_id: string; verdict: Verdict; reason: string; agent_verdict: Verdict; agent_reason: string }[]
}

// The page reads only these of the Assessment's fields so far; the working paper's rows come from `requirements`.
export type Assessment = {
  control_id: string
  verdict: Verdict
  answer: string
  status?: ReviewStatus
  requirements?: RequirementVerdict[]
  /** The thread to review it by (#38, #39). */
  thread_id?: string
  review?: Review | null
}

/**
 * One assessment's tokens and cost (#35): `cost_usd` is OpenRouter's reported part plus the part computed from a
 * price table (`estimated` when there is one); `cost_complete` is false when some call's cost could not be known.
 */
export type Usage = {
  model: string
  input_tokens: number
  output_tokens: number
  reported_cost_usd: number
  estimated_cost_usd: number
  cost_usd: number
  estimated: boolean
  cost_complete: boolean
}

export const count = (n: number) => n.toLocaleString('en-US')
export const plural = (n: number, noun: string) => `${count(n)} ${noun}${n === 1 ? '' : 's'}`
export const dollars = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4 })

type UsageShown = Pick<Usage, 'input_tokens' | 'output_tokens' | 'cost_usd' | 'estimated' | 'cost_complete'>

/** An assessment's tokens and cost in words: an incomplete cost is "at least" its known part, never $0. */
export function usageWords({ input_tokens, output_tokens, cost_usd, estimated, cost_complete }: UsageShown): string {
  const known = `${dollars(cost_usd)}${estimated ? ' (estimated)' : ''}`
  const cost = cost_complete ? known : cost_usd > 0 ? `at least ${known}` : 'cost unknown'
  return `${count(input_tokens)} tokens in · ${count(output_tokens)} tokens out · ${cost}`
}

export type AssessmentEvent =
  | { event: 'step'; data: Step }
  | { event: 'requirement'; data: { requirement: RequirementVerdict } }
  | { event: 'result'; data: { assessment: Assessment; usage?: Usage } }
  | { event: 'error'; data: { message: string } }

/**
 * The control's newest stored assessment, from #38's `GET /api/assessments` (newest first) and
 * `GET /api/assessments/{thread_id}`; null when it has none, or it can't be read just now.
 */
export async function newestAssessment(controlId: string): Promise<Assessment | null> {
  try {
    const list = await fetch('/api/assessments', { headers: await authHeaders() })
    if (sessionEnded(list) || !list.ok) return null
    const { assessments } = (await list.json()) as { assessments: { thread_id: string; control_id: string }[] }
    const newest = assessments.find((a) => a.control_id === controlId)
    return newest ? await storedAssessment(newest.thread_id) : null
  } catch {
    return null
  }
}

/** One stored assessment, by its thread (`GET /api/assessments/{thread_id}`); null when it can't be read just now. */
export async function storedAssessment(threadId: string): Promise<Assessment | null> {
  try {
    const one = await fetch(`/api/assessments/${encodeURIComponent(threadId)}`, { headers: await authHeaders() })
    return one.ok ? ((await one.json()) as { assessment: Assessment }).assessment : null
  } catch {
    return null
  }
}

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
 * POSTs `body` as JSON to a server-sent event stream and yields its events until one named in `last` (the `error`
 * event always ends it). A refusal is one `error`: the session ended, a limit's 429 with its own detail, or
 * `refused` for any other. Throws when the service can't be reached, or the stream breaks off before its last event.
 */
export async function* postEvents<E extends { event: string; data: unknown }>(
  url: string, body: unknown, last: E['event'][], refused: string,
): AsyncGenerator<E | { event: 'error'; data: { message: string } }> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(await authHeaders()) },
    body: JSON.stringify(body),
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
      ? await response.json().then((answer: { detail?: unknown }) => answer.detail, () => undefined)
      : undefined
    yield { event: 'error', data: { message: typeof detail === 'string' ? detail : refused } }
    return
  }
  for await (const item of readEvents(response.body)) {
    yield item as E
    if (item.event === 'error' || last.includes(item.event)) return
  }
  throw new Error(`the stream from ${url} ended before its last event`)
}

/**
 * Starts the assessment and yields its events until the `result` or `error`; `note` is the lead's note from a
 * rejected review, for the control assessed again (#42).
 * Throws when the service can't be reached, or the stream breaks off before either.
 */
export function assess(controlId: string, note?: string): AsyncGenerator<AssessmentEvent> {
  const body = note ? { control_id: controlId, note } : { control_id: controlId }
  return postEvents<AssessmentEvent>('/api/assessments', body, ['result'], NOT_STARTED)
}
