import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Assessment, RequirementVerdict } from './assessment.ts'
import type { ControlCard } from './overview.ts'
import type { PaperRequirement } from './paper.ts'
import { withOverrides } from './review.ts'
import type { Run } from './useAssessment.ts'
import WorkingPaper, { ControlNotFound } from './WorkingPaper.tsx'

const QUESTION = 'To what extent is information protected during transfer?'

/** 5.1.2's requirements as the overview lists them: one from each column, one not applicable. */
const LISTED: PaperRequirement[] = [
  { id: 'M1', column: 'must', text: 'Transfers are encrypted.', source: 'VDA ISA 2027.1', not_applicable: null },
  { id: 'S1', column: 'should', text: 'Keys are rotated.', source: 'VDA ISA 2027.1', not_applicable: null },
  { id: 'H1', column: 'high', text: 'Transfers are logged.', source: 'VDA ISA 2027.1', not_applicable: null },
  { id: 'O1', column: 'overlay', text: 'An early warning goes out.', source: 'AT NIS2 (illustrative)',
    not_applicable: null },
  { id: 'S3', column: 'should', text: 'Remote access is protected.', source: 'VDA ISA 2027.1',
    not_applicable: 'Nordlicht allows no remote access.' },
]

const judged: RequirementVerdict[] = [
  { id: 'M1', column: 'must', text: 'Transfers are encrypted.', verdict: 'evidenced', reason: 'TLS everywhere.',
    citations: [{ doc_id: 'network-transfer-standard', version: '2.0', section: 'Encryption', quote: 'All transfers use TLS 1.3.',
      kind: 'implemented', stale: false, stale_because: null }] },
  { id: 'S1', column: 'should', text: 'Keys are rotated.', verdict: 'partly-evidenced', reason: 'The standard is old.',
    citations: [{ doc_id: 'encryption-standard', version: '1.0', section: 'Keys', quote: 'Keys are rotated yearly.',
      kind: 'documented', stale: true, stale_because: 'approved 2022-03-14, yearly review' }] },
  { id: 'H1', column: 'high', text: 'Transfers are logged.', verdict: 'not-evidenced', reason: 'Nothing on file.',
    citations: [] },
  { id: 'O1', column: 'overlay', text: 'An early warning goes out.', verdict: 'not-evidenced', reason: 'No procedure.',
    citations: [] },
  { id: 'S3', column: 'should', text: 'Remote access is protected.', verdict: 'not-applicable',
    reason: 'Nordlicht allows no remote access.', citations: [] },
]

const ASSESSED: Assessment = { control_id: '5.1.2', verdict: 'partly-evidenced', answer: 'Transfers are encrypted.',
  status: 'waiting-for-review', requirements: judged }

const IDS = ['1.1.1', '1.6.1', '2.1.3', '4.1.2', '5.1.1', '5.1.2']
const CONTROLS: ControlCard[] = IDS.map((id) => ({ id, question: `Question ${id}?`,
  iso_27001: id === '5.1.2' ? 'ISO 27001:2022: A.5.14' : 'ISO 27001:2022: A.5.1',
  status: id === '1.1.1' ? 'accepted' : 'not-assessed', verdict: id === '1.1.1' ? 'evidenced' : null }))
const OVERVIEW = { company: 'Nordlicht Komponenten GmbH', protection_level: 'high', answer_language: 'en',
  requirements: { '5.1.2': LISTED }, controls: CONTROLS } as const

type Shown = {
  run?: Run
  running?: string | null
  checking?: boolean
  onAssess?: (note?: string) => void
  onOpen?: (id: string) => void
  onOverview?: () => void
  onReviewed?: (assessment: Assessment) => void
  onRefresh?: () => void
}

function show({ run = { status: 'idle', steps: [] }, running = null, checking = false, onAssess = () => {},
  onOpen = () => {}, onOverview = () => {}, onReviewed = () => {}, onRefresh = () => {} }: Shown = {}) {
  const card = { ...CONTROLS.find((c) => c.id === '5.1.2')!, question: QUESTION }
  render(<WorkingPaper overview={OVERVIEW} card={card} running={running} run={run} checking={checking}
    onAssess={onAssess} onOpen={onOpen} onOverview={onOverview} onReviewed={onReviewed} onRefresh={onRefresh} />)
}

/** The requirement's row, by the id in its header cell. */
const row = (id: string) => screen.getByText(id, { selector: 'th .mono' }).closest('tr')!

afterEach(cleanup)

describe('the working paper of an assessed control', () => {
  beforeEach(() => show({ run: { status: 'done', steps: [], assessment: ASSESSED } }))

  it('shows the head: question, ISO reference, protection level and answer language', () => {
    const paper = screen.getByRole('region', { name: 'Working paper' })
    expect(within(paper).getByRole('heading', { name: /5\.1\.2 To what extent is information protected/ })).toBeTruthy()
    expect(within(paper).getByText('ISO A.5.14')).toBeTruthy()
    expect(within(paper).getByText('Protection level: high')).toBeTruthy()
    expect(within(paper).getByText('Answer language: English')).toBeTruthy()
  })

  it('shows each requirement with its column, verdict, evidence kind, stale flag, reason and exact quotes', () => {
    const m1 = row('M1')
    expect(within(m1).getByText('Must')).toBeTruthy()
    expect(within(m1).getByText('Evidenced')).toBeTruthy()
    expect(within(m1).getAllByText('implemented')).toHaveLength(2) // the row's evidence, and the quote's own
    expect(within(m1).getByText('Current')).toBeTruthy()
    expect(within(m1).getByText('TLS everywhere.')).toBeTruthy()
    expect(within(m1).getByText('All transfers use TLS 1.3.').closest('blockquote')).toBeTruthy()
    expect(within(m1).getByText('network-transfer-standard v2.0 · Encryption')).toBeTruthy()

    const s1 = row('S1')
    expect(within(s1).getByText('Should')).toBeTruthy()
    expect(within(s1).getByText('Partly evidenced')).toBeTruthy()
    expect(within(s1).getAllByText('documented')).toHaveLength(2)
    expect(within(s1).getByText('Stale')).toBeTruthy()
    expect(within(s1).getByText('Stale: approved 2022-03-14, yearly review')).toBeTruthy()

    expect(within(row('H1')).getByText('High protection')).toBeTruthy()
    expect(within(row('H1')).getByText('No evidence')).toBeTruthy()
    expect(within(row('O1')).getByText('Country overlay')).toBeTruthy()
    expect(within(row('O1')).getByText('AT NIS2 (illustrative)')).toBeTruthy()
  })

  it('heads the text column "Requirement": an overlay requirement is not VDA ISA text', () => {
    const table = screen.getByRole('table', { name: 'Requirements' })
    expect(within(table).getByRole('columnheader', { name: 'Requirement' })).toBeTruthy()
  })

  it('shows a not-applicable requirement with its reason', () => {
    expect(within(row('S3')).getByText('Not applicable')).toBeTruthy()
    expect(within(row('S3')).getByText('Nordlicht allows no remote access.')).toBeTruthy()
  })
})

describe('the working paper of a control not yet assessed', () => {
  it('lists its requirements, not-applicable ones with their reason, and an Assess button', async () => {
    const onAssess = vi.fn()
    show({ onAssess })
    expect(screen.getByText('Not assessed yet. 4 requirements apply in this profile.')).toBeTruthy()
    expect(screen.getAllByRole('rowheader').map((h) => h.querySelector('.mono')?.textContent)).toEqual(
      ['M1', 'S1', 'H1', 'O1', 'S3'])
    expect(within(row('S3')).getByText('Not applicable: Nordlicht allows no remote access.')).toBeTruthy()
    expect(within(row('O1')).getByText('AT NIS2 (illustrative)')).toBeTruthy()
    expect(within(row('M1')).queryByText('Evidenced')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Assess 5.1.2' }))
    expect(onAssess).toHaveBeenCalledOnce()
  })

  it("offers no Assess while another control's run is in progress, and says which, with a link to it", async () => {
    const onOpen = vi.fn()
    show({ running: '4.1.2', onOpen })
    const assess = screen.getByRole('button', { name: 'Assess 5.1.2' }) as HTMLButtonElement
    expect(assess.disabled).toBe(true)
    const why = screen.getByText(/^Vouch is assessing/)
    expect(why.textContent).toBe('Vouch is assessing 4.1.2. One assessment runs at a time.')
    const link = within(why).getByRole('link', { name: '4.1.2' })
    expect(link.getAttribute('href')).toBe('/controls/4.1.2')
    await userEvent.click(link)
    expect(onOpen).toHaveBeenCalledWith('4.1.2')
  })
})

describe('the working paper while its stored assessment is read', () => {
  it('shows a neutral loading state: no verdict, no Assess, no requirement table', () => {
    show({ checking: true })
    const paper = screen.getByRole('region', { name: 'Working paper' })
    expect(within(paper).getByRole('status').textContent).toBe('Reading the working paper…')
    expect(within(paper).queryByText('Not assessed')).toBeNull()
    expect(within(paper).queryByRole('button', { name: /Assess/ })).toBeNull()
    expect(within(paper).queryByRole('table')).toBeNull()
    expect(within(paper).getByText('Protection level: high')).toBeTruthy() // the head is known already
  })
})

describe('the control index', () => {
  it('lists the controls by area, each with its verdict and state, the open one marked', () => {
    show()
    const index = screen.getByRole('navigation', { name: 'Controls' })
    expect(within(index).getAllByRole('heading').map((h) => h.textContent)).toEqual(
      ['Policies and incidents', 'People', 'Access', 'IT security'])
    const current = within(index).getByRole('link', { name: /^5\.1\.2/ })
    expect(current.getAttribute('aria-current')).toBe('page')
    const accepted = within(index).getByRole('link', { name: /^1\.1\.1/ })
    expect(within(accepted).getByText('Evidenced')).toBeTruthy()
    expect(within(accepted).getByText('Accepted')).toBeTruthy()
    expect(within(index).getByRole('link', { name: /^2\.1\.3/ }).textContent).toContain('Not assessed')
  })

  it("shows the open control's own run: assessing, then its verdict and state", () => {
    show({ run: { status: 'running', steps: [] } })
    const index = screen.getByRole('navigation', { name: 'Controls' })
    expect(within(within(index).getByRole('link', { name: /^5\.1\.2/ })).getByText('Assessing')).toBeTruthy()
    cleanup()
    show({ run: { status: 'done', steps: [], assessment: ASSESSED } })
    const done = within(screen.getByRole('navigation', { name: 'Controls' })).getByRole('link', { name: /^5\.1\.2/ })
    expect(within(done).getByText('Partly evidenced')).toBeTruthy()
    expect(within(done).getByText('Proposed')).toBeTruthy()
  })

  it('opens another control, and "Overview" returns', async () => {
    const onOpen = vi.fn()
    const onOverview = vi.fn()
    show({ onOpen, onOverview })
    const index = screen.getByRole('navigation', { name: 'Controls' })
    await userEvent.click(within(index).getByRole('link', { name: /^4\.1\.2/ }))
    expect(onOpen).toHaveBeenCalledWith('4.1.2')
    await userEvent.click(screen.getByRole('link', { name: '← Overview' }))
    expect(onOverview).toHaveBeenCalledOnce()
  })
})

describe('a control that does not exist', () => {
  it('says so, with a way back to the overview', async () => {
    const onOverview = vi.fn()
    render(<ControlNotFound controlId="9.9.9" onOverview={onOverview} />)
    expect(screen.getByRole('heading').textContent).toBe('No control 9.9.9')
    const back = screen.getByRole('link', { name: '← Overview' })
    expect(back.getAttribute('href')).toBe('/')
    await userEvent.click(back)
    expect(onOverview).toHaveBeenCalledOnce()
  })
})

/** 4.1.2 as the demo script judges it: M1 partly evidenced, M2 evidenced, so the control is partly evidenced. */
const DEMO: RequirementVerdict[] = [
  { id: 'M1', column: 'must', text: 'Access is decided by risk.', verdict: 'partly-evidenced', reason: 'Policy only.',
    citations: [] },
  { id: 'M2', column: 'must', text: 'Administrators use keys.', verdict: 'evidenced', reason: 'Keys issued.',
    citations: [] },
  { id: 'S1', column: 'should', text: 'Access is reviewed.', verdict: 'not-evidenced', reason: 'Nothing.', citations: [] },
]
const PROPOSED: Assessment = { control_id: '5.1.2', verdict: 'partly-evidenced', answer: 'Keys for administrators.',
  status: 'waiting-for-review', requirements: DEMO, thread_id: 't1', review: null }
const REASON = 'The 2025 MFA rollout record shows it.'

function review({ assessment = PROPOSED, ...rest }: Shown & { assessment?: Assessment } = {}) {
  show({ ...rest, run: { status: 'done', steps: [], assessment } })
}

/** The API: the review route answering `status` with `body`, and the audit trail; the reviews it got. */
function reviewRoute(status = 200, body: unknown = {}) {
  const sent: { url: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/audit-trail?limit=500') return Promise.resolve(Response.json({ entries: [
      { thread_id: 't1', action: 'reject', user_id: 'user_1', reviewer_name: 'Jana Weber', at: '2026-11-04T08:30:00Z' },
    ] }))
    sent.push({ url, body: JSON.parse(init!.body as string) })
    return Promise.resolve(Response.json(body, { status }))
  }))
  return sent
}

/** The paper head's verdict line: the control's verdict chip and its review state. */
const headVerdict = () => screen.getByText('Verdict', { selector: '.paper-meta span' }).parentElement!

describe('the review of a proposed control', () => {
  afterEach(() => vi.unstubAllGlobals())

  it("overrides a requirement's verdict with a reason, and the control's verdict recalculates on screen", async () => {
    review()
    expect(within(headVerdict()).getByText('Partly evidenced')).toBeTruthy()
    await userEvent.click(within(row('M1')).getByRole('button', { name: 'Change M1' }))
    const editor = screen.getByRole('form', { name: 'Change M1' })
    await userEvent.selectOptions(within(editor).getByLabelText('Verdict'), 'evidenced')
    await userEvent.type(within(editor).getByLabelText('Reason (required)'), REASON)
    await userEvent.click(within(editor).getByRole('button', { name: 'Save' }))

    expect(screen.queryByRole('form', { name: 'Change M1' })).toBeNull()
    expect(within(row('M1')).getByText('Evidenced')).toBeTruthy()
    expect(within(row('M1')).getByText(REASON)).toBeTruthy()
    expect(within(row('M1')).getByText('Changed from Partly evidenced')).toBeTruthy()
    expect(within(headVerdict()).getByText('Evidenced')).toBeTruthy()
  })

  it("can't save an override without a reason, and says why", async () => {
    review()
    await userEvent.click(within(row('M1')).getByRole('button', { name: 'Change M1' }))
    const editor = screen.getByRole('form', { name: 'Change M1' })
    const save = within(editor).getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    expect(document.getElementById(save.getAttribute('aria-describedby')!)!.textContent)
      .toBe('Give a reason to save the change.')
    await userEvent.type(within(editor).getByLabelText('Reason (required)'), '   ')
    expect(save.disabled).toBe(true)
    await userEvent.type(within(editor).getByLabelText('Reason (required)'), 'Seen.')
    expect(save.disabled).toBe(false)
  })

  it('accepts with the note and the overrides, through the review route, and hands on the reviewed assessment', async () => {
    const accepted = { ...PROPOSED, status: 'accepted', verdict: 'evidenced' }
    const sent = reviewRoute(200, { assessment: accepted })
    const onReviewed = vi.fn()
    review({ onReviewed })
    await userEvent.click(within(row('M1')).getByRole('button', { name: 'Change M1' }))
    await userEvent.selectOptions(screen.getByLabelText('Verdict'), 'evidenced')
    await userEvent.type(screen.getByLabelText('Reason (required)'), REASON)
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    const bar = screen.getByRole('region', { name: 'Review' })
    await userEvent.type(within(bar).getByLabelText('Your note (kept in the audit trail)'), 'Checked.')
    await userEvent.click(within(bar).getByRole('button', { name: 'Accept verdict' }))

    expect(sent).toEqual([{ url: '/api/assessments/t1/review', body: { decision: 'accept', note: 'Checked.',
      overrides: [{ requirement_id: 'M1', verdict: 'evidenced', reason: REASON }] } }])
    expect(onReviewed).toHaveBeenCalledWith(accepted)
  })

  it('shows a failed review as not made, with a message and Retry, which sends it again', async () => {
    const sent = reviewRoute(503)
    const onReviewed = vi.fn()
    review({ onReviewed })
    const bar = screen.getByRole('region', { name: 'Review' })
    await userEvent.click(within(bar).getByRole('button', { name: 'Reject verdict' }))
    expect((await within(bar).findByRole('alert')).textContent).toBe(
      'Your decision could not be recorded just now. Nothing was changed; please try again.')
    expect(onReviewed).not.toHaveBeenCalled()
    expect(within(headVerdict()).getByText('Proposed')).toBeTruthy()
    await userEvent.click(within(bar).getByRole('button', { name: 'Retry' }))
    expect(sent.map((s) => (s.body as { decision: string }).decision)).toEqual(['reject', 'reject'])
  })

  it('shows a 409 as already reviewed, with Refresh', async () => {
    reviewRoute(409, { detail: 'not waiting' })
    const onRefresh = vi.fn()
    review({ onRefresh })
    const bar = screen.getByRole('region', { name: 'Review' })
    await userEvent.click(within(bar).getByRole('button', { name: 'Accept verdict' }))
    expect((await within(bar).findByRole('alert')).textContent).toContain('already been reviewed')
    await userEvent.click(within(bar).getByRole('button', { name: 'Refresh' }))
    expect(onRefresh).toHaveBeenCalledOnce()
  })
})

describe('a reviewed control', () => {
  afterEach(() => vi.unstubAllGlobals())

  const REJECTED: Assessment = { ...PROPOSED, status: 'rejected',
    review: { decision: 'reject', note: 'See the MFA record.', overrides: [] } }

  it('shows who rejected it and when, the note, and Assess again, which carries the note', async () => {
    reviewRoute()
    const onAssess = vi.fn()
    review({ assessment: REJECTED, onAssess })
    const bar = screen.getByRole('region', { name: 'Review' })
    expect(await within(bar).findByText(/^by Jana Weber on /)).toBeTruthy()
    expect(within(bar).getByText('See the MFA record.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Change M1' })).toBeNull()
    expect(screen.getAllByRole('button', { name: 'Assess again 5.1.2' })).toHaveLength(1)
    await userEvent.click(within(bar).getByRole('button', { name: 'Assess again 5.1.2' }))
    expect(onAssess).toHaveBeenCalledWith('See the MFA record.')
  })

  it("shows an override the lead made, with the agent's verdict it replaced", () => {
    reviewRoute()
    review({ assessment: { ...PROPOSED, status: 'accepted',
      requirements: withOverrides(DEMO, [{ requirement_id: 'M1', verdict: 'evidenced', reason: REASON }]),
      review: { decision: 'accept', note: '', overrides: [{ requirement_id: 'M1', verdict: 'evidenced', reason: REASON,
        agent_verdict: 'partly-evidenced', agent_reason: 'Policy only.' }] } } })
    expect(within(row('M1')).getByText('Changed from Partly evidenced')).toBeTruthy()
    expect(within(screen.getByRole('region', { name: 'Review' })).getByText('Accepted')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Change M1' })).toBeNull()
  })
})
