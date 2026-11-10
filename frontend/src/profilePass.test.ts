import { afterEach, describe, expect, it, vi } from 'vitest'
import { UNREACHABLE } from './assessment.ts'
import { PASS_NOT_STARTED, runProfilePass } from './profilePass.ts'
import { SESSION_ENDED, setOnSignedOut } from './session.ts'

afterEach(() => {
  vi.unstubAllGlobals()
  setOnSignedOut(null)
})

/** The API answering the pass with this status and JSON body. */
const answers = (status: number, body: unknown) => {
  const fetch = vi.fn(() => Promise.resolve(Response.json(body, { status })))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const ASSESSED = { thread_id: 't1', control_id: '1.1.1', status: 'waiting-for-review', verdict: 'partly-evidenced' }

describe('runProfilePass', () => {
  it('POSTs the pass and reads a 200 as done, with its assessments and the skipped controls with why', async () => {
    const skipped = [{ control_id: '1.1.1', reason: 'accepted' }, { control_id: '4.1.2', reason: 'in-review' }]
    const fetch = answers(200, { assessments: [ASSESSED], skipped, usage: {} })
    expect(await runProfilePass()).toEqual({ kind: 'done', assessments: [ASSESSED], skipped })
    expect(fetch).toHaveBeenCalledWith('/api/profile-pass', expect.objectContaining({ method: 'POST' }))
  })

  it('reads a 422 offering the per-control agent as refused, in the API\'s sentence', async () => {
    answers(422, { detail: 'The profile pass can\'t run: its prompt is too long.', fallback: 'per-control' })
    expect(await runProfilePass()).toEqual(
      { kind: 'refused', message: 'The profile pass can\'t run: its prompt is too long.' })
  })

  it('reads a 429 naming the pass\'s own limit as limit, in the API\'s sentence', async () => {
    answers(429, { detail: 'You have run 3 profile passes today.', limit: 'profile-pass', resets_at: 'x' })
    expect(await runProfilePass()).toEqual({ kind: 'limit', message: 'You have run 3 profile passes today.' })
  })

  it.each([
    [409, { detail: 'An assessment is already running.' }],
    [502, { detail: 'The profile pass could not be completed.' }],
    [429, { detail: 'Too many requests.', limit: 'daily' }],
    [503, { detail: 'The database is unavailable.', unavailable: 'database' }],
    [422, { detail: 'Not like that.' }], // no fallback offered
  ])('reads a %i as failed, in the API\'s detail', async (status, body) => {
    answers(status, body)
    expect(await runProfilePass()).toEqual({ kind: 'failed', message: body.detail })
  })

  it.each([[502, {}], [409, { detail: [{ msg: 'x' }] }], [500, 'oops']])(
    'reads a %i without a string detail as failed, not started', async (status, body) => {
      answers(status, body)
      expect(await runProfilePass()).toEqual({ kind: 'failed', message: PASS_NOT_STARTED })
    })

  it('reads a 401 as the session ended, signing out', async () => {
    const out = vi.fn()
    setOnSignedOut(out)
    answers(401, { detail: 'Not signed in.' })
    expect(await runProfilePass()).toEqual({ kind: 'failed', message: SESSION_ENDED })
    expect(out).toHaveBeenCalled()
  })

  it('reads a throw as unreachable', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')))
    expect(await runProfilePass()).toEqual({ kind: 'unreachable', message: UNREACHABLE })
  })

  it('says the not-started sentence plainly', () => {
    expect(PASS_NOT_STARTED).toBe('The profile pass could not be started. Please try again later.')
  })
})
