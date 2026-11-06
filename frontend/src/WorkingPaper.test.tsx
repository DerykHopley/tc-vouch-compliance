import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Assessment, RequirementVerdict } from './assessment.ts'
import { NO_OWNER, type RemediationItem } from './remediation.ts'
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
  onRefresh?: () => Promise<boolean>
  onItemDecided?: (threadId: string, item: RemediationItem) => void
  language?: string
}

function paper({ run = { status: 'idle', steps: [] }, running = null, checking = false, onAssess = () => {},
  onOpen = () => {}, onOverview = () => {}, onReviewed = () => {}, onRefresh = () => Promise.resolve(true),
  onItemDecided = () => {}, language = 'en' }: Shown) {
  const card = { ...CONTROLS.find((c) => c.id === '5.1.2')!, question: QUESTION }
  return <WorkingPaper overview={{ ...OVERVIEW, answer_language: language }} card={card} running={running} run={run} checking={checking}
    onAssess={onAssess} onOpen={onOpen} onOverview={onOverview} onReviewed={onReviewed} onRefresh={onRefresh}
    onItemDecided={onItemDecided} />
}

/** Shows the paper; the function returned shows it again with other props, as App does when they change. */
function show(props: Shown = {}) {
  const { rerender } = render(paper(props))
  return (next: Shown) => rerender(paper({ ...props, ...next }))
}

/** The requirement's row, by the id in its header cell. */
const row = (id: string) => screen.getByText(id, { selector: 'th .mono' }).closest('tr')!

afterEach(cleanup)

describe("the roll-up's explanation, issue 212", () => {
  /** 5.1.2 judged with these verdicts per requirement, the control's verdict rolled up from them. */
  const judgedAs = (verdicts: Record<string, RequirementVerdict['verdict']>, verdict: Assessment['verdict']): Run =>
    ({ status: 'done', steps: [], assessment: { ...ASSESSED, verdict,
      requirements: judged.map((r) => (r.id in verdicts ? { ...r, verdict: verdicts[r.id] } : r)) } })
  const explanation = () => document.querySelector('.roll-up')

  it("says why partly evidenced rows roll up to not evidenced, next to the control's verdict", () => {
    show({ run: judgedAs({ M1: 'partly-evidenced', H1: 'partly-evidenced', O1: 'partly-evidenced' }, 'not-evidenced') })
    expect(explanation()?.textContent).toBe('Not evidenced: no must, protection-level or overlay requirement is '
      + 'fully evidenced (3 of 3 partly evidenced).')
    expect(explanation()?.closest('.paper-head')).not.toBeNull()
  })

  it('says nothing when every row matches the verdict', () => {
    show({ run: judgedAs({ M1: 'not-evidenced', S1: 'not-evidenced' }, 'not-evidenced') })
    expect(explanation()).toBeNull()
  })

  it('says it in German for a German profile', () => {
    show({ run: judgedAs({ M1: 'partly-evidenced', H1: 'partly-evidenced', O1: 'partly-evidenced' }, 'not-evidenced'),
      language: 'de' })
    expect(explanation()?.textContent).toBe('Nicht nachgewiesen: keine Muss-, Schutzbedarfs- oder '
      + 'Overlay-Anforderung ist vollständig nachgewiesen (3 von 3 teilweise nachgewiesen).')
  })
})

describe('the working paper of an assessed control', () => {
  beforeEach(() => { show({ run: { status: 'done', steps: [], assessment: ASSESSED } }) })

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

const done = (assessment: Assessment): Run => ({ status: 'done', steps: [], assessment })

/** Shows the paper on `assessment`; the function returned shows it again with other props, as App would. */
function review({ assessment = PROPOSED, ...rest }: Shown & { assessment?: Assessment } = {}) {
  return show({ ...rest, run: done(assessment) })
}

/** The API's review route answering `status` with `body` (or `answer`'s promise); the reviews it got. */
function reviewRoute(status = 200, body: unknown = {}, answer?: () => Promise<Response>) {
  const sent: { url: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    sent.push({ url, body: JSON.parse(init!.body as string) })
    return answer ? answer() : Promise.resolve(Response.json(body, { status }))
  }))
  return sent
}

/** The paper head's verdict line: the control's verdict chip and its review state. */
const headVerdict = () => screen.getByText('Verdict', { selector: '.paper-meta span' }).parentElement!
const bar = () => screen.getByRole('region', { name: 'Review' })

/** Opens M1's override and fills it in; the editor, not yet saved. */
async function overrideM1(verdict = 'evidenced', reason = REASON) {
  await userEvent.click(within(row('M1')).getByRole('button', { name: 'Change M1' }))
  const editor = screen.getByRole('form', { name: 'Override M1' })
  await userEvent.selectOptions(within(editor).getByLabelText('Verdict'), verdict)
  if (reason) await userEvent.type(within(editor).getByLabelText('Reason (required)'), reason)
  return editor
}

async function saveOverrideOfM1() {
  await userEvent.click(within(await overrideM1()).getByRole('button', { name: 'Save' }))
}

describe('the review of a proposed control', () => {
  afterEach(() => vi.unstubAllGlobals())

  it("overrides a requirement's verdict with a reason, and the control's verdict recalculates on screen", async () => {
    review()
    expect(within(headVerdict()).getByText('Partly evidenced')).toBeTruthy()
    await saveOverrideOfM1()

    expect(screen.queryByRole('form', { name: 'Override M1' })).toBeNull()
    expect(within(row('M1')).getByText('Evidenced')).toBeTruthy()
    expect(within(row('M1')).getByText(REASON)).toBeTruthy()
    expect(within(row('M1')).getByText('Overridden from Partly evidenced')).toBeTruthy()
    expect(within(headVerdict()).getByText('Evidenced')).toBeTruthy()
  })

  it("can't save an override without a reason, and says why", async () => {
    review()
    const editor = await overrideM1('evidenced', '')
    const save = within(editor).getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    expect(document.getElementById(save.getAttribute('aria-describedby')!)!.textContent)
      .toBe('Give a reason to save the override.')
    await userEvent.type(within(editor).getByLabelText('Reason (required)'), '   ')
    expect(save.disabled).toBe(true)
    await userEvent.type(within(editor).getByLabelText('Reason (required)'), 'Seen.')
    expect(save.disabled).toBe(false)
  })

  it("can't save an override that gives the agent's own verdict, and says why", async () => {
    review()
    const editor = await overrideM1('partly-evidenced')
    const save = within(editor).getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    expect(document.getElementById(save.getAttribute('aria-describedby')!)!.textContent)
      .toBe("Choose a verdict other than Vouch's to override it.")
  })

  it('opens a saved override again at its verdict and reason', async () => {
    review()
    await saveOverrideOfM1()
    await userEvent.click(within(row('M1')).getByRole('button', { name: 'Change M1' }))
    const editor = screen.getByRole('form', { name: 'Override M1' })
    expect((within(editor).getByLabelText('Verdict') as HTMLSelectElement).value).toBe('evidenced')
    expect((within(editor).getByLabelText('Reason (required)') as HTMLTextAreaElement).value).toBe(REASON)
  })

  it('accepts with the note and the overrides, through the review route, and hands on the reviewed assessment', async () => {
    const accepted = { ...PROPOSED, status: 'accepted', verdict: 'evidenced' }
    const sent = reviewRoute(200, { assessment: accepted })
    const onReviewed = vi.fn()
    review({ onReviewed })
    await saveOverrideOfM1()
    await userEvent.type(within(bar()).getByLabelText('Your note (kept in the audit trail)'), 'Checked.')
    await userEvent.click(within(bar()).getByRole('button', { name: 'Accept verdict' }))

    expect(sent).toEqual([{ url: '/api/assessments/t1/review', body: { decision: 'accept', note: 'Checked.',
      overrides: [{ requirement_id: 'M1', verdict: 'evidenced', reason: REASON }] } }])
    expect(onReviewed).toHaveBeenCalledWith(accepted)
  })

  it('blocks Accept and Reject while an override is open, saying why', async () => {
    review()
    await overrideM1()
    for (const name of ['Accept verdict', 'Reject verdict']) {
      expect((within(bar()).getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
    }
    expect(within(bar()).getByText('Save or cancel the override of M1 first.')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect((within(bar()).getByRole('button', { name: 'Accept verdict' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('shows a failed review as not made, with a message and Retry, which sends it again', async () => {
    const sent = reviewRoute(503)
    const onReviewed = vi.fn()
    review({ onReviewed })
    await userEvent.click(within(bar()).getByRole('button', { name: 'Reject verdict' }))
    expect((await within(bar()).findByRole('alert')).textContent).toBe(
      'Your decision could not be recorded just now. Nothing was changed; please try again.')
    expect(onReviewed).not.toHaveBeenCalled()
    expect(within(headVerdict()).getByText('Proposed')).toBeTruthy()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Retry' }))
    expect(sent.map((s) => (s.body as { decision: string }).decision)).toEqual(['reject', 'reject'])
  })

  it("shows a 422 as refused, with the server's reason, and no Retry", async () => {
    reviewRoute(422, { detail: 'override names M9' })
    review()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Accept verdict' }))
    expect((await within(bar()).findByRole('alert')).textContent).toContain('override names M9')
    expect(within(bar()).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('shows a 409 as already reviewed, with Refresh; a Refresh that fails says so', async () => {
    reviewRoute(409, { detail: 'not waiting' })
    const onRefresh = vi.fn(() => Promise.resolve(false))
    review({ onRefresh })
    await userEvent.click(within(bar()).getByRole('button', { name: 'Accept verdict' }))
    expect((await within(bar()).findByRole('alert')).textContent).toContain('already been reviewed')
    await userEvent.click(within(bar()).getByRole('button', { name: 'Refresh' }))
    expect(onRefresh).toHaveBeenCalledOnce()
    expect(await within(bar()).findByText('The assessment could not be read again just now. Please try again.'))
      .toBeTruthy()
  })
})

describe('overrides belong to the assessment shown', () => {
  afterEach(() => vi.unstubAllGlobals())

  const OTHER: Assessment = { ...PROPOSED, control_id: '4.1.2', thread_id: 't2' }

  it("are dropped when another assessment is shown: another control's, or a new run's", async () => {
    const again = review()
    await saveOverrideOfM1()
    again({ run: done(OTHER) })
    expect(within(row('M1')).getByText('Partly evidenced')).toBeTruthy()
    expect(within(row('M1')).queryByText(/^Overridden/)).toBeNull()
    expect(within(headVerdict()).getByText('Partly evidenced')).toBeTruthy()
  })

  it('are dropped, with an "already reviewed" message, when the same assessment is read again', async () => {
    reviewRoute(409)
    const again = review()
    await saveOverrideOfM1()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Accept verdict' }))
    await within(bar()).findByRole('alert')
    again({ run: done({ ...PROPOSED }) }) // re-read: still waiting, a new object
    expect(within(row('M1')).queryByText(/^Overridden/)).toBeNull()
    expect(within(bar()).queryByRole('alert')).toBeNull()
  })

  it("aren't cleared by a decision on another assessment that lands after the switch", async () => {
    let land: (response: Response) => void = () => {}
    reviewRoute(200, {}, () => new Promise<Response>((resolve) => { land = resolve }))
    const again = review()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Accept verdict' }))
    again({ run: done(OTHER) })
    await saveOverrideOfM1()
    land(Response.json({ assessment: { ...PROPOSED, status: 'accepted' } }))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(within(row('M1')).getByText('Overridden from Partly evidenced')).toBeTruthy()
  })
})

describe('a reviewed control', () => {
  afterEach(() => vi.unstubAllGlobals())

  const REJECTED: Assessment = { ...PROPOSED, status: 'rejected', review: { decision: 'reject',
    note: 'See the MFA record.', overrides: [], reviewer_id: 'user_1', reviewer_name: 'Jana Weber',
    reviewed_at: '2026-11-04T08:30:00+00:00' } }

  it('shows who rejected it and when, the note, and Assess again, which starts a new run (the server carries the note)', async () => {
    const onAssess = vi.fn()
    review({ assessment: REJECTED, onAssess })
    expect(within(bar()).getByText(/^by Jana Weber on \d+ Nov 2026, \d\d:\d\d$/)).toBeTruthy()
    expect(within(bar()).getByText('See the MFA record.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Change M1' })).toBeNull()
    expect(screen.getAllByRole('button', { name: 'Assess again 5.1.2' })).toHaveLength(1)
    await userEvent.click(within(bar()).getByRole('button', { name: 'Assess again 5.1.2' }))
    expect(onAssess).toHaveBeenCalledOnce()
  })

  it("says why Assess again is held while another control's run is in progress", () => {
    review({ assessment: REJECTED, running: '4.1.2' })
    expect((within(bar()).getByRole('button', { name: 'Assess again 5.1.2' }) as HTMLButtonElement).disabled)
      .toBe(true)
    expect(within(bar()).getByText(/^Vouch is assessing/).textContent)
      .toBe('Vouch is assessing 4.1.2. One assessment runs at a time.')
  })

  it("shows an override the lead made, with the agent's verdict it replaced, and offers no override", () => {
    review({ assessment: { ...PROPOSED, status: 'accepted',
      requirements: withOverrides(DEMO, [{ requirement_id: 'M1', verdict: 'evidenced', reason: REASON }]),
      review: { decision: 'accept', note: '', reviewer_id: '', reviewer_name: '', reviewed_at: '',
        overrides: [{ requirement_id: 'M1', verdict: 'evidenced', reason: REASON, agent_verdict: 'partly-evidenced',
          agent_reason: 'Policy only.' }] } } })
    expect(within(row('M1')).getByText('Overridden from Partly evidenced')).toBeTruthy()
    expect(within(bar()).getByText('Accepted')).toBeTruthy()
    expect(within(bar()).queryByText(/^by /)).toBeNull() // a review kept before who and when were
    expect(screen.queryByRole('button', { name: 'Change M1' })).toBeNull()
  })

  it('closes an open override when the decision lands, so nothing unsaved is left to save', async () => {
    const again = review()
    await overrideM1()
    again({ run: done({ ...PROPOSED, status: 'accepted', review: REJECTED.review }) })
    expect(screen.queryByRole('form', { name: 'Override M1' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
  })
})

describe("a not-evidenced requirement's remediation item (#43)", () => {
  afterEach(() => vi.unstubAllGlobals())

  const DRAFT: RemediationItem = { requirement_id: 'S1', missing: 'A record of access reviews.',
    owner_role: 'IT operations lead', due_date: '2027-02-01', status: 'draft' }
  const ACCEPTED: Assessment = { ...PROPOSED, status: 'accepted', remediation: [DRAFT] }
  const item = () => screen.getByRole('region', { name: 'Remediation item S1' })

  /** The remediation route answering `status` with `body`; the decisions it got. */
  function itemRoute(status = 200, body: unknown = {}) {
    const sent: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      sent.push({ url, body: JSON.parse(init!.body as string) })
      return Promise.resolve(Response.json(body, { status }))
    }))
    return sent
  }

  it("shows the draft in its requirement's row: what's missing, the owner role and the due date", () => {
    review({ assessment: ACCEPTED })
    expect(within(row('S1')).getByRole('region', { name: 'Remediation item S1' })).toBeTruthy()
    expect(within(item()).getByText('A record of access reviews.')).toBeTruthy()
    expect((within(item()).getByLabelText('Owner role') as HTMLInputElement).value).toBe('IT operations lead')
    expect((within(item()).getByLabelText('Due date') as HTMLInputElement).value).toBe('2027-02-01')
    expect(within(item()).getByText('Draft')).toBeTruthy()
  })

  it("approves with the lead's owner role and due date, and hands on the item as kept", async () => {
    const kept = { ...DRAFT, owner_role: 'CISO', due_date: '2027-03-01', status: 'approved' }
    const sent = itemRoute(200, { item: kept })
    const onItemDecided = vi.fn()
    review({ assessment: ACCEPTED, onItemDecided })
    await userEvent.clear(within(item()).getByLabelText('Owner role'))
    await userEvent.type(within(item()).getByLabelText('Owner role'), 'CISO')
    await userEvent.clear(within(item()).getByLabelText('Due date'))
    await userEvent.type(within(item()).getByLabelText('Due date'), '2027-03-01')
    await userEvent.click(within(item()).getByRole('button', { name: 'Approve S1' }))
    expect(sent).toEqual([{ url: '/api/assessments/t1/remediation/S1',
      body: { decision: 'approve', owner_role: 'CISO', due_date: '2027-03-01' } }])
    expect(onItemDecided).toHaveBeenCalledWith('t1', kept)
  })

  it('discards, sending only the decision', async () => {
    const sent = itemRoute(200, { item: { ...DRAFT, status: 'discarded' } })
    review({ assessment: ACCEPTED })
    await userEvent.click(within(item()).getByRole('button', { name: 'Discard S1' }))
    expect(sent.map((s) => s.body)).toEqual([{ decision: 'discard' }])
  })

  it('sends one decision for a double click', async () => {
    const sent = itemRoute(200, { item: { ...DRAFT, status: 'approved' } })
    review({ assessment: ACCEPTED })
    await userEvent.dblClick(within(item()).getByRole('button', { name: 'Approve S1' }))
    expect(sent).toHaveLength(1)
  })

  it("shows a refused approval with the server's hint, and keeps the draft", async () => {
    const hint = 'The due date 2026-11-01 has passed. Choose today or a later date.'
    itemRoute(422, { detail: hint })
    const onItemDecided = vi.fn()
    review({ assessment: ACCEPTED, onItemDecided })
    await userEvent.click(within(item()).getByRole('button', { name: 'Approve S1' }))
    expect(within(item()).getByRole('alert').textContent).toBe(hint)
    expect(within(item()).getByLabelText('Due date').getAttribute('aria-invalid')).toBe('true')
    expect(onItemDecided).not.toHaveBeenCalled()
  })

  it("asks for an owner role where Vouch suggested none: Approve waits for one, saying why", async () => {
    review({ assessment: { ...ACCEPTED, remediation: [{ ...DRAFT, owner_role: NO_OWNER }] } })
    const owner = within(item()).getByLabelText('Owner role') as HTMLInputElement
    const approve = within(item()).getByRole('button', { name: 'Approve S1' }) as HTMLButtonElement
    expect(owner.value).toBe('')
    expect(approve.disabled).toBe(true)
    expect(within(item()).getByText('Name the role that owns the fix to approve it.')).toBeTruthy()
    await userEvent.type(owner, 'CISO')
    expect(approve.disabled).toBe(false)
    expect(within(item()).queryByText('Name the role that owns the fix to approve it.')).toBeNull()
  })

  it("shows the item's own values again when it changes, dropping unsent edits", async () => {
    const again = review({ assessment: ACCEPTED })
    await userEvent.type(within(item()).getByLabelText('Owner role'), ' and CISO')
    again({ run: done({ ...ACCEPTED, remediation: [{ ...DRAFT, owner_role: 'Head of IT', due_date: '2027-04-01' }] }) })
    expect((within(item()).getByLabelText('Owner role') as HTMLInputElement).value).toBe('Head of IT')
    expect((within(item()).getByLabelText('Due date') as HTMLInputElement).value).toBe('2027-04-01')
  })

  it('shows an approved item with its owner role and due date, and nothing to decide', () => {
    review({ assessment: { ...ACCEPTED, remediation: [{ ...DRAFT, status: 'approved' }] } })
    expect(within(item()).getByText('Approved')).toBeTruthy()
    expect(within(item()).getByText('Owner: IT operations lead · Due 2027-02-01')).toBeTruthy()
    expect(within(item()).queryByRole('button')).toBeNull()
  })

  it('shows a discarded item as discarded', () => {
    review({ assessment: { ...ACCEPTED, remediation: [{ ...DRAFT, status: 'discarded' }] } })
    expect(within(item()).getByText('Discarded')).toBeTruthy()
    expect(within(item()).queryByRole('button')).toBeNull()
  })

  it('hides a partly evidenced draft once its requirement is overridden to evidenced on screen (issue 212)',
    async () => {
      review({ assessment: { ...PROPOSED, remediation: [{ ...DRAFT, requirement_id: 'M1' }] } })
      expect(screen.getByRole('region', { name: 'Remediation item M1' })).toBeTruthy()
      await saveOverrideOfM1()
      expect(screen.queryByRole('region', { name: 'Remediation item M1' })).toBeNull()
    })

  it("offers no decision on a proposed control's draft, saying when it can be made", () => {
    review({ assessment: { ...PROPOSED, remediation: [DRAFT] } })
    expect(within(item()).queryByRole('button')).toBeNull()
    expect(within(item()).getByText('Approve or discard it once the verdict is accepted.')).toBeTruthy()
  })
})
