import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Assessment, RequirementVerdict } from './assessment.ts'
import { pastDoubleClick } from './doubleClickFixtures.ts'
import { NO_OWNER, type RemediationItem } from './remediation.ts'
import type { ControlCard } from './overview.ts'
import type { Goal } from './report.ts'
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
  /** The profile's goal (#369): the improvement plan shows in Internal improvement only (#410). */
  goal?: Goal
  /** The open card's improvement plan flags (#410). */
  flags?: Pick<ControlCard, 'reassess_suggested' | 'reassess_reasons' | 'closed'>
  /** The overview's titles of the added documents with a newer version, by control (#586). */
  newEvidence?: Record<string, string[]>
}

function paper({ run = { status: 'idle', steps: [] }, running = null, checking = false, onAssess = () => {},
  onOpen = () => {}, onOverview = () => {}, onReviewed = () => {}, onRefresh = () => Promise.resolve(true),
  onItemDecided = () => {}, language = 'en', goal = 'audit-readiness', flags, newEvidence }: Shown) {
  const card = { ...CONTROLS.find((c) => c.id === '5.1.2')!, question: QUESTION, ...flags }
  return <WorkingPaper overview={{ ...OVERVIEW, answer_language: language, goal, new_evidence: newEvidence }} card={card} running={running} run={run} checking={checking}
    onAssess={onAssess} onOpen={onOpen} onOverview={onOverview} onReviewed={onReviewed} onRefresh={onRefresh}
    onItemDecided={onItemDecided} onQuestionsChanged={() => {}} />
}

/** Shows the paper; the function returned shows it again with other props, as App does when they change. */
function show(props: Shown = {}) {
  const { rerender } = render(paper(props))
  return (next: Shown) => rerender(paper({ ...props, ...next }))
}

/** The requirement's row, by the id in its header cell. */
const row = (id: string) => screen.getByText(id, { selector: 'th .mono' }).closest('tr')!

afterEach(cleanup)

describe('the requirement text on a phone, issue 211', () => {
  it('clamps to 2 lines; "Show full requirement" expands it and "Show less" collapses it', async () => {
    show({ run: { status: 'done', steps: [], assessment: ASSESSED } })
    const text = within(row('M1')).getByText('Transfers are encrypted.')
    const toggle = within(row('M1')).getByRole('button', { name: 'Show full requirement' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.getAttribute('aria-controls')).toBe(text.id)
    expect(text.classList.contains('clamped')).toBe(true)

    await userEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(toggle.textContent).toBe('Show less')
    expect(text.classList.contains('clamped')).toBe(false)

    toggle.focus()
    await userEvent.keyboard('{Enter}')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(text.classList.contains('clamped')).toBe(true)
  })

  describe('measured again once the web fonts load, issue 516', () => {
    // A text that fits 2 lines in the first font, then needs 3 in the web font: its box stays 2 lines high (the
    // clamp), so no ResizeObserver fires. The page's FontFaceSet is a stand-in: jsdom has none.
    let lines = 2
    let resolveReady: () => void
    let fonts: EventTarget
    beforeEach(() => {
      lines = 2
      fonts = Object.assign(new EventTarget(), { ready: new Promise<void>((done) => { resolveReady = done }) })
      vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(() => 40)
      vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(() => lines * 20)
      Object.defineProperty(document, 'fonts', { configurable: true, value: fonts })
    })
    afterEach(() => {
      vi.restoreAllMocks()
      Reflect.deleteProperty(document, 'fonts')
    })

    it.each([
      ['a font loads', () => fonts.dispatchEvent(new Event('loadingdone'))],
      ['the fonts are ready', () => resolveReady()],
    ])('offers the toggle when, after the first measure, %s and takes the text past 2 lines', async (_, settle) => {
      show({ run: { status: 'done', steps: [], assessment: ASSESSED } })
      expect(within(row('M1')).queryByRole('button', { name: 'Show full requirement' })).toBeNull()

      lines = 3
      settle()
      expect(await within(row('M1')).findByRole('button', { name: 'Show full requirement' })).toBeTruthy()
    })
  })

  it('keeps one toggle per requirement, each its own', async () => {
    show({ run: { status: 'done', steps: [], assessment: ASSESSED } })
    await userEvent.click(within(row('M1')).getByRole('button', { name: 'Show full requirement' }))
    expect(within(row('S1')).getByRole('button', { name: 'Show full requirement' }).getAttribute('aria-expanded'))
      .toBe('false')
  })
})

describe('how Vouch reached this, and the draft answer, issue 232', () => {
  const STEPS = [{ node: 'load_control', text: 'Loaded control 5.1.2: transfer.' },
    { node: 'guard', text: 'Held back a claim of compliance.' },
    { node: 'overlay_branch', text: 'Added the AT NIS2 overlay.' }]

  it("shows each step's node name in a chip, then its text; only a guard step is marked guard", () => {
    show({ run: { status: 'done', steps: STEPS, assessment: ASSESSED } })
    const steps = within(screen.getByRole('list', { name: 'Agent steps' })).getAllByRole('listitem')
    expect(steps.map((step) => step.querySelector('.step-node')?.textContent))
      .toEqual(['load_control', 'guard', 'overlay_branch'])
    expect(steps.map((step) => step.querySelector('b'))).toEqual([null, null, null])
    expect(steps[0].textContent).toContain('Loaded control 5.1.2: transfer.')
    expect(steps.map((step) => step.classList.contains('guard'))).toEqual([false, true, false])
  })

  it('shows a heading in the draft answer as a heading', () => {
    show({ run: { status: 'done', steps: [], assessment: { ...ASSESSED, answer: '## Gaps\n- no record' } } })
    const answer = screen.getByRole('region', { name: 'Verdict and draft answer' })
    expect(within(answer).getByRole('heading', { level: 3, name: 'Gaps' }).classList.contains('prose-h')).toBe(true)
    expect(within(answer).getByRole('listitem').textContent).toBe('no record')
  })
})

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

describe("a stale citation's flag on a German profile, issue 359", () => {
  const GERMAN_REASON = 'freigegeben am 2022-03-14, jährlich zu prüfen, fällig am 2023-03-14' // as the API serves it
  const german = judged.map((r) => r.id !== 'S1' ? r
    : { ...r, citations: r.citations.map((c) => ({ ...c, stale_because: GERMAN_REASON })) })

  it('reads in German, the served stale word and reason, marked German', () => {
    const assessment = { ...ASSESSED, answer_language: 'de', stale_word: 'Prüfung überfällig', requirements: german }
    show({ run: { status: 'done', steps: [], assessment }, language: 'de' })
    const flag = within(row('S1')).getByText(`Prüfung überfällig: ${GERMAN_REASON}`)
    expect(flag.className).toBe('flag')
    expect(flag.getAttribute('lang')).toBe('de')
  })

  it('stays English on an English profile, and reads "Stale" from an API that serves no stale word', () => {
    show({ run: { status: 'done', steps: [], assessment: { ...ASSESSED, answer_language: 'en', stale_word: 'Stale' } } })
    expect(within(row('S1')).getByText('Stale: approved 2022-03-14, yearly review').getAttribute('lang')).toBe('en')
    cleanup()
    show({ run: { status: 'done', steps: [], assessment: ASSESSED } })
    expect(within(row('S1')).getByText('Stale: approved 2022-03-14, yearly review')).toBeTruthy()
  })
})

describe("the evidence column's labels on a German profile, issue 484", () => {
  const evidence = (id: string) => row(id).querySelector('.evidence-col')! as HTMLElement

  it('reads in German, the served stale and current words, marked German', () => {
    const assessment = { ...ASSESSED, answer_language: 'de', stale_word: 'Prüfung überfällig',
      current_word: 'Aktuell' }
    show({ run: { status: 'done', steps: [], assessment }, language: 'de' })
    const stale = within(evidence('S1')).getByText('Prüfung überfällig')
    expect(stale.className).toBe('flag')
    expect(stale.getAttribute('lang')).toBe('de')
    const current = within(evidence('M1')).getByText('Aktuell')
    expect(current.className).toBe('why')
    expect(current.getAttribute('lang')).toBe('de')
    expect(screen.queryByText(/^(Stale|Current)$/)).toBeNull()
  })

  it('reads "Stale" and "Current" from an API that serves no words', () => {
    show({ run: { status: 'done', steps: [], assessment: ASSESSED } })
    expect(within(evidence('S1')).getByText('Stale')).toBeTruthy()
    expect(within(evidence('M1')).getByText('Current')).toBeTruthy()
  })
})

describe("a lead's statement cited, issue 439", () => {
  const NOTE = "A lead's statement can't fully evidence a requirement: a document or record that shows it is needed "
    + 'to close it.'
  const statement = (section: string) => ({ doc_id: 'Q12', version: '2026-11-10', section,
    quote: 'Keys are rotated every year.', kind: 'documented' as const, stale: false, stale_because: null,
    lead_statement: true })
  const citedBy = (section: string) => judged.map((r) => r.id !== 'S1' ? r
    : { ...r, reason: 'The lead says so. At most partly evidenced: Q12 is the lead\'s statement, not a document.',
      citations: [statement(section)] })
  const quotes = (id: string) => row(id).querySelector('.quotes-cell')! as HTMLElement

  it('shows no kind chip on the statement, and says a document or record is needed to close it', () => {
    const assessment = { ...ASSESSED, answer_language: 'en', statement_note: NOTE,
      requirements: citedBy("Lead's statement") }
    show({ run: { status: 'done', steps: [], assessment } })
    expect(within(quotes('S1')).getByText("Q12 v2026-11-10 · Lead's statement")).toBeTruthy()
    expect(quotes('S1').querySelector('.kind')).toBeNull()
    expect(within(quotes('S1')).getByText(NOTE).getAttribute('lang')).toBe('en')
    // A document's citation keeps its kind chip, and a requirement not shown by a statement has no note.
    expect(within(quotes('M1')).getByText('implemented').className).toBe('kind kind-implemented')
    expect(within(quotes('M1')).queryByText(NOTE)).toBeNull()
  })

  it('reads in German on a German profile, the served sentence marked German', () => {
    const german = 'Eine Aussage der Compliance-Leitung kann eine Anforderung nicht vollständig nachweisen.'
    const assessment = { ...ASSESSED, answer_language: 'de', statement_note: german,
      requirements: citedBy('Aussage der Compliance-Leitung') }
    show({ run: { status: 'done', steps: [], assessment }, language: 'de' })
    expect(within(quotes('S1')).getByText(german).getAttribute('lang')).toBe('de')
    expect(within(quotes('S1')).queryByText(NOTE)).toBeNull()
  })

  it("says it in English's words from an API that serves none", () => {
    show({ run: { status: 'done', steps: [], assessment: { ...ASSESSED, requirements: citedBy("Lead's statement") } } })
    expect(within(quotes('S1')).getByText(NOTE)).toBeTruthy()
  })

  const evidence = (id: string) => row(id).querySelector('.evidence-col')! as HTMLElement

  it('labels the evidence column with the served statement word, not "documented", issue 498', () => {
    const assessment = { ...ASSESSED, answer_language: 'en', statement_word: "Lead's statement",
      requirements: citedBy("Lead's statement") }
    show({ run: { status: 'done', steps: [], assessment } })
    expect(evidence('S1').querySelector('.kind')).toBeNull()
    expect(within(evidence('S1')).queryByText('documented')).toBeNull()
    expect(within(evidence('S1')).queryByText('No evidence')).toBeNull()
    expect(within(evidence('S1')).queryByText('Current')).toBeNull() // a statement is no document to be current
    const word = within(evidence('S1')).getByText("Lead's statement")
    expect(word.className).toBe('why')
    expect(word.getAttribute('lang')).toBe('en')
    // A document's evidence reads as before.
    expect(within(evidence('M1')).getByText('implemented').className).toBe('kind kind-implemented')
    expect(within(evidence('M1')).getByText('Current')).toBeTruthy()
  })

  it('labels it in German on a German profile, the served word marked German, issue 498', () => {
    const assessment = { ...ASSESSED, answer_language: 'de', statement_word: 'Aussage der Compliance-Leitung',
      requirements: citedBy('Aussage der Compliance-Leitung') }
    show({ run: { status: 'done', steps: [], assessment }, language: 'de' })
    expect(within(evidence('S1')).queryByText('documented')).toBeNull()
    expect(within(evidence('S1')).getByText('Aussage der Compliance-Leitung').getAttribute('lang')).toBe('de')
    expect(within(evidence('S1')).queryByText("Lead's statement")).toBeNull()
  })

  it("labels it in English's words from an API that serves none, issue 498", () => {
    show({ run: { status: 'done', steps: [], assessment: { ...ASSESSED, requirements: citedBy("Lead's statement") } } })
    expect(within(evidence('S1')).getByText("Lead's statement")).toBeTruthy()
    expect(within(evidence('S1')).queryByText('documented')).toBeNull()
  })

  it('says nothing on a requirement judged evidenced, as nothing caps it', () => {
    const evidenced = citedBy("Lead's statement").map((r) => r.id === 'S1' ? { ...r, verdict: 'evidenced' as const } : r)
    show({ run: { status: 'done', steps: [], assessment: { ...ASSESSED, requirements: evidenced } } })
    expect(within(quotes('S1')).queryByText(NOTE)).toBeNull()
  })
})

describe('the working paper of a control not yet assessed', () => {
  it('lists its requirements, not-applicable ones with their reason, and an Assess button', async () => {
    const onAssess = vi.fn()
    show({ onAssess })
    expect(screen.getByText('Not assessed yet. 4 requirements apply in this profile.')).toBeTruthy()
    expect(screen.getAllByRole('rowheader').map((h) => h.querySelector('.mono')?.textContent)).toEqual(
      ['M1', 'S1', 'H1', 'O1', 'S3'])
    expect(row('S3').querySelector('.why')?.textContent).toBe('Not applicable: Nordlicht allows no remote access.')
    // The reason is the profile's words, marked in its language; the label is the interface's (#244).
    expect(within(row('S3')).getByText('Nordlicht allows no remote access.').getAttribute('lang')).toBe('en')
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

describe('the working paper of a control not applicable as a whole (ticket 454)', () => {
  it("shows the Not applicable chip and the profile's reason, and offers no assessment", () => {
    const reason = 'No transfers leave the company.'
    const excluded = LISTED.map((r) => ({ ...r, not_applicable: reason }))
    const card: ControlCard = { ...CONTROLS.find((c) => c.id === '5.1.2')!, question: QUESTION,
      status: 'not-applicable', verdict: 'not-applicable', not_applicable: reason }
    render(<WorkingPaper overview={{ ...OVERVIEW, requirements: { '5.1.2': excluded }, goal: 'audit-readiness' }}
      card={card} running={null} run={{ status: 'idle', steps: [] }} onAssess={() => {}} onOpen={() => {}}
      onOverview={() => {}} onReviewed={() => {}} onRefresh={() => Promise.resolve(true)} onItemDecided={() => {}}
      onQuestionsChanged={() => {}} />)
    const chip = screen.getByText('Not applicable', { selector: '.paper-meta .chip' })
    expect(chip.className).toBe('chip v-not-applicable')
    const why = screen.getByText(reason, { selector: '.paper-head p' })
    expect(why.className).toBe('field-note')
    expect(why.getAttribute('lang')).toBe('en')
    expect(screen.queryByText(/Not assessed yet/)).toBeNull()
    expect(screen.queryByRole('button', { name: /^Assess/ })).toBeNull()
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

  it("shows each row's question without the shared opening, in the answer language (issue 466)", () => {
    const german = 'Inwieweit werden Informationen während der Übertragung geschützt?'
    const controls = CONTROLS.map((c) => (c.id === '5.1.2' ? { ...c, question: german } : c))
    render(<WorkingPaper overview={{ ...OVERVIEW, answer_language: 'de', goal: 'audit-readiness', controls }}
      card={{ ...controls.find((c) => c.id === '5.1.2')!, question: german }} running={null}
      run={{ status: 'idle', steps: [] }} checking={false} onAssess={() => {}} onOpen={() => {}} onOverview={() => {}}
      onReviewed={() => {}} onRefresh={() => Promise.resolve(true)} onItemDecided={() => {}} onQuestionsChanged={() => {}} />)
    const index = screen.getByRole('navigation', { name: 'Controls' })
    const question = within(index).getByRole('link', { name: /^5\.1\.2/ }).querySelector('.wp-q')
    expect(question?.textContent).toBe('Werden Informationen während der Übertragung geschützt?')
    expect(question?.getAttribute('lang')).toBe('de')
  })

  it('brings the open control, then a focused row, into view in its own list (issue 466)', () => {
    const scrolled: [string | null, unknown][] = []
    const original = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function (this: Element, how?: unknown) {
      scrolled.push([this.querySelector('.mono')?.textContent ?? null, how])
    }
    try {
      show()
      expect(scrolled).toEqual([['5.1.2', { block: 'nearest' }]])
      const index = screen.getByRole('navigation', { name: 'Controls' })
      within(index).getByRole('link', { name: /^4\.1\.2/ }).focus()
      expect(scrolled.at(-1)).toEqual(['4.1.2', { block: 'nearest' }])
    } finally {
      Element.prototype.scrollIntoView = original
    }
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
    await userEvent.click(within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByRole('link', { name: 'Overview' }))
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
  status: 'waiting-for-review', requirements: DEMO, thread_id: 't1', review: null, review_step: 'confirm-findings' }
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
/** Presses a final decision and confirms it in its ask (#634): the confirm button bears the trigger's words. */
async function decide(name: 'Accept verdict' | 'Reject verdict') {
  await userEvent.click(within(bar()).getByRole('button', { name }))
  await userEvent.click(within(bar()).getByRole('button', { name }))
}

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

  it('confirms the findings with the overrides, through the findings route, and hands on the assessment', async () => {
    const confirmed = { ...PROPOSED, verdict: 'evidenced', review_step: 'plan-fixes' }
    const sent = reviewRoute(200, { assessment: confirmed })
    const onReviewed = vi.fn()
    review({ onReviewed })
    await saveOverrideOfM1()
    expect(within(bar()).getByText('1 override, recorded with the findings.')).toBeTruthy()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Confirm findings' }))

    expect(sent).toEqual([{ url: '/api/assessments/t1/findings',
      body: { overrides: [{ requirement_id: 'M1', verdict: 'evidenced', reason: REASON }] } }])
    expect(onReviewed).toHaveBeenCalledWith(confirmed)
  })

  it('blocks Confirm findings and Reject while an override is open, saying why', async () => {
    review()
    await overrideM1()
    for (const name of ['Confirm findings', 'Reject verdict']) {
      expect((within(bar()).getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
    }
    expect(within(bar()).getByText('Save or cancel the override of M1 first.')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect((within(bar()).getByRole('button', { name: 'Confirm findings' }) as HTMLButtonElement).disabled)
      .toBe(false)
  })

  it('shows a failed review as not made, with a message and Retry, which sends it again', async () => {
    const sent = reviewRoute(503)
    const onReviewed = vi.fn()
    review({ onReviewed })
    await decide('Reject verdict')
    expect((await within(bar()).findByRole('alert')).textContent).toBe(
      'This step could not be recorded just now. Nothing was changed; please try again.')
    expect(onReviewed).not.toHaveBeenCalled()
    expect(within(headVerdict()).getByText('Proposed')).toBeTruthy()
    await pastDoubleClick() // Retry is a new press after the confirm's click burst (#562), and asks nothing
    await userEvent.click(within(bar()).getByRole('button', { name: 'Retry' }))
    expect(sent.map((s) => (s.body as { decision: string }).decision)).toEqual(['reject', 'reject'])
  })

  it("shows a 422 as refused, with the server's reason, and no Retry", async () => {
    reviewRoute(422, { detail: 'override names M9' })
    review()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Confirm findings' }))
    expect((await within(bar()).findByRole('alert')).textContent).toContain('override names M9')
    expect(within(bar()).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('shows a 409 at Confirm findings as the review moved on, with Refresh; a Refresh that fails says so', async () => {
    reviewRoute(409, { detail: 'not waiting' })
    const onRefresh = vi.fn(() => Promise.resolve(false))
    review({ onRefresh })
    await userEvent.click(within(bar()).getByRole('button', { name: 'Confirm findings' }))
    expect((await within(bar()).findByRole('alert')).textContent)
      .toBe("This control's review has moved on, perhaps in another tab. Refresh to see where it stands.")
    expect(within(bar()).queryByRole('button', { name: 'Retry' })).toBeNull()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Refresh' }))
    expect(onRefresh).toHaveBeenCalledOnce()
    expect(await within(bar()).findByText('The assessment could not be read again just now. Please try again.'))
      .toBeTruthy()
  })
})

describe('the review in three steps (issue 372)', () => {
  afterEach(() => vi.unstubAllGlobals())

  const steps = () => within(screen.getByRole('list', { name: 'Review steps' })).getAllByRole('listitem')
  const current = () => steps().filter((s) => s.getAttribute('aria-current') === 'step').map((s) => s.textContent)
  const fix = (requirement_id: string, status: RemediationItem['status']): RemediationItem =>
    ({ requirement_id, missing: 'A record.', owner_role: 'CISO', due_date: '2027-02-01', status })
  /** Findings confirmed with M2 overridden to partly evidenced: M1, M2 and S1 each have an item, M1's decided. */
  const PLANNING: Assessment = { ...PROPOSED, review_step: 'plan-fixes',
    requirements: withOverrides(DEMO, [{ requirement_id: 'M2', verdict: 'partly-evidenced', reason: REASON }]),
    confirmed: [{ requirement_id: 'M2', verdict: 'partly-evidenced', reason: REASON, agent_verdict: 'evidenced',
      agent_reason: 'Keys issued.' }],
    remediation: [fix('M1', 'approved'), fix('M2', 'draft'), fix('S1', 'draft')] }
  const READY: Assessment = { ...PROPOSED, review_step: 'accept', confirmed: [], remediation: [] }

  it('names the three steps in order and marks Confirm findings current on a proposed control', () => {
    review()
    expect(steps().map((s) => s.textContent)).toEqual(['Confirm findings', 'Plan fixes', 'Accept'])
    expect(current()).toEqual(['Confirm findings'])
  })

  describe("the current step named in the paper's head (issue 467)", () => {
    const head = () => document.querySelector('.paper-head') as HTMLElement
    it.each([
      ['Confirm findings', PROPOSED], ['Plan fixes', PLANNING], ['Accept', READY],
    ] as const)('names %s in the head', (name, assessment) => {
      review({ assessment })
      expect(within(head()).getByText(`Review step: ${name}`)).toBeTruthy()
    })

    it('names no step once the control is reviewed', () => {
      review({ assessment: { ...READY, status: 'accepted', review_step: null } })
      expect(within(head()).queryByText(/Review step/)).toBeNull()
    })
  })

  it('marks Accept current and shows Plan fixes as skipped for a control with no remediation item', () => {
    review({ assessment: READY })
    expect(current()).toEqual(['Accept'])
    expect(steps()[1].textContent).toBe('Plan fixes: skipped, nothing to fix')
    expect((within(bar()).getByRole('button', { name: 'Accept verdict' }) as HTMLButtonElement).disabled).toBe(false)
    expect(within(bar()).queryByRole('button', { name: 'Confirm findings' })).toBeNull()
  })

  it('at Plan fixes holds Accept verdict, saying why and how many items are decided; the rows offer no Change', () => {
    review({ assessment: PLANNING })
    expect(current()).toEqual(['Plan fixes'])
    const accept = within(bar()).getByRole('button', { name: 'Accept verdict' }) as HTMLButtonElement
    expect(accept.disabled).toBe(true)
    expect(document.getElementById(accept.getAttribute('aria-describedby')!)!.textContent)
      .toBe('Approve or discard each remediation item to accept the verdict.')
    expect(within(bar()).getByText('1 of 3 remediation items decided.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Change / })).toBeNull()
    expect(within(row('M2')).getByText('Overridden from Evidenced')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approve M2' })).toBeTruthy()
  })

  it('counts an item decided on the page at once, and offers Accept once none is left a draft', () => {
    const again = review({ assessment: PLANNING })
    again({ run: done({ ...PLANNING, remediation: [fix('M1', 'approved'), fix('M2', 'discarded'),
      fix('S1', 'approved')] }) })
    expect(current()).toEqual(['Accept'])
    expect((within(bar()).getByRole('button', { name: 'Accept verdict' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('accepts with the note through the review route, and hands on the reviewed assessment', async () => {
    const accepted = { ...READY, status: 'accepted', review_step: null }
    const sent = reviewRoute(200, { assessment: accepted })
    const onReviewed = vi.fn()
    review({ assessment: READY, onReviewed })
    await userEvent.type(within(bar()).getByLabelText('Your note (kept in the audit trail)'), 'Checked.')
    await decide('Accept verdict')
    expect(sent).toEqual([{ url: '/api/assessments/t1/review', body: { decision: 'accept', note: 'Checked.' } }])
    expect(onReviewed).toHaveBeenCalledWith(accepted)
  })

  it('rejects at Plan fixes with the note', async () => {
    const sent = reviewRoute(200, { assessment: { ...PLANNING, status: 'rejected', review_step: null } })
    review({ assessment: PLANNING })
    await userEvent.type(within(bar()).getByLabelText('Your note (kept in the audit trail)'), 'Look again.')
    await decide('Reject verdict')
    expect(sent).toEqual([{ url: '/api/assessments/t1/review', body: { decision: 'reject', note: 'Look again.' } }])
  })

  it('shows a failed Confirm findings as not recorded, with Retry, which sends it again', async () => {
    const sent = reviewRoute(503)
    review()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Confirm findings' }))
    expect((await within(bar()).findByRole('alert')).textContent).toBe(
      'This step could not be recorded just now. Nothing was changed; please try again.')
    await userEvent.click(within(bar()).getByRole('button', { name: 'Retry' }))
    expect(sent.map((s) => s.url)).toEqual(['/api/assessments/t1/findings', '/api/assessments/t1/findings'])
  })
})

describe('the final decisions ask first (issue 634)', () => {
  afterEach(() => vi.unstubAllGlobals())

  const READY: Assessment = { ...PROPOSED, review_step: 'accept', confirmed: [], remediation: [] }
  const ask = () => within(bar()).getByRole('alert').textContent
  const note = () => within(bar()).getByLabelText('Your note (kept in the audit trail)')

  it('Accept verdict sends nothing on its first press: it asks, naming what is recorded; Cancel closes it', async () => {
    const sent = reviewRoute(200, { assessment: { ...READY, status: 'accepted', review_step: null } })
    review({ assessment: READY })
    await userEvent.click(within(bar()).getByRole('button', { name: 'Accept verdict' }))
    expect(sent).toEqual([])
    expect(ask()).toBe("Accept this control's verdict? Your decision is recorded in the audit trail, and the verdict "
      + 'counts towards readiness.')
    await userEvent.click(within(bar()).getByRole('button', { name: 'Cancel' }))
    expect(within(bar()).queryByRole('alert')).toBeNull()
    expect(sent).toEqual([])
  })

  it('names the note in the accept ask when one is written', async () => {
    review({ assessment: READY })
    await userEvent.type(note(), 'Checked.')
    await userEvent.click(within(bar()).getByRole('button', { name: 'Accept verdict' }))
    expect(ask()).toBe("Accept this control's verdict? Your decision and note are recorded in the audit trail, and "
      + 'the verdict counts towards readiness.')
  })

  it("Reject verdict with no note asks first, saying the note is the next assessment's only guidance", async () => {
    const sent = reviewRoute(200, { assessment: { ...PROPOSED, status: 'rejected', review_step: null } })
    review()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Reject verdict' }))
    expect(sent).toEqual([])
    expect(ask()).toBe("Reject this control's verdict with no note? Your decision is recorded in the audit trail. A "
      + 'note is the only guidance the next assessment of this control gets: write one in Your note first.')
  })

  it('a note written while the reject ask is open turns the ask to say the next assessment reads it', async () => {
    const sent = reviewRoute(200, { assessment: { ...PROPOSED, status: 'rejected', review_step: null } })
    review()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Reject verdict' }))
    await userEvent.type(note(), 'Look at the key register.')
    expect(ask()).toBe("Reject this control's verdict? Your decision and note are recorded in the audit trail, and "
      + 'the next assessment of this control reads your note.')
    await userEvent.click(within(bar()).getByRole('button', { name: 'Reject verdict' }))
    expect(sent).toEqual([{ url: '/api/assessments/t1/review',
      body: { decision: 'reject', note: 'Look at the key register.' } }])
  })
})

describe("the override form's keyboard focus (issue 226)", () => {
  const change = () => within(row('M1')).getByRole('button', { name: 'Change M1' })

  it('Enter on "Change M1" puts focus on the form\'s Verdict field', async () => {
    review()
    change().focus()
    await userEvent.keyboard('{Enter}')
    const editor = screen.getByRole('form', { name: 'Override M1' })
    expect(document.activeElement).toBe(within(editor).getByLabelText('Verdict'))
  })

  it('Cancel puts focus back on "Change M1"', async () => {
    review()
    change().focus()
    await userEvent.keyboard('{Enter}')
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(document.activeElement).toBe(change())
  })

  it('Save puts focus back on "Change M1"', async () => {
    review()
    await saveOverrideOfM1()
    expect(document.activeElement).toBe(change())
  })

  it('Undo, which goes once pressed, puts focus on "Change M1"', async () => {
    review()
    await saveOverrideOfM1()
    await userEvent.click(within(row('M1')).getByRole('button', { name: 'Undo the override of M1' }))
    expect(document.activeElement).toBe(change())
  })
})

describe("switching to another requirement's override keeps a typed reason unless the lead drops it (issue 559)", () => {
  const changeM2 = () => within(row('M2')).getByRole('button', { name: 'Change M2' })
  const QUESTION = 'You have an override of M1 not yet saved. Drop it and change M2?'

  it('asks first in the app while M1 holds a typed reason, and Keep editing M1 keeps it', async () => {
    review()
    await overrideM1()
    await userEvent.click(changeM2())
    expect(within(row('M2')).getByRole('alert').textContent).toBe(QUESTION)
    expect(screen.queryByRole('form', { name: 'Override M2' })).toBeNull()

    await userEvent.click(within(row('M2')).getByRole('button', { name: 'Keep editing M1' }))
    const editor = screen.getByRole('form', { name: 'Override M1' })
    expect((within(editor).getByLabelText('Reason (required)') as HTMLTextAreaElement).value).toBe(REASON)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it("Change M2 anyway drops M1's typed reason and opens M2", async () => {
    review()
    await overrideM1()
    await userEvent.click(changeM2())
    await userEvent.click(within(row('M2')).getByRole('button', { name: 'Change M2 anyway' }))
    expect(screen.queryByRole('form', { name: 'Override M1' })).toBeNull()
    expect(screen.getByRole('form', { name: 'Override M2' })).toBeTruthy()
    expect(within(row('M1')).queryByText(/^Overridden/)).toBeNull()
  })

  it('switches in one press when the open editor holds nothing typed', async () => {
    review()
    await userEvent.click(within(row('M1')).getByRole('button', { name: 'Change M1' }))
    await userEvent.click(changeM2())
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('form', { name: 'Override M1' })).toBeNull()
    expect(screen.getByRole('form', { name: 'Override M2' })).toBeTruthy()
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
    await userEvent.click(within(bar()).getByRole('button', { name: 'Confirm findings' }))
    await within(bar()).findByRole('alert')
    again({ run: done({ ...PROPOSED }) }) // re-read: still waiting, a new object
    expect(within(row('M1')).queryByText(/^Overridden/)).toBeNull()
    expect(within(bar()).queryByRole('alert')).toBeNull()
  })

  it("aren't cleared by a decision on another assessment that lands after the switch", async () => {
    let land: (response: Response) => void = () => {}
    reviewRoute(200, {}, () => new Promise<Response>((resolve) => { land = resolve }))
    const again = review()
    await userEvent.click(within(bar()).getByRole('button', { name: 'Confirm findings' }))
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

  const ACCEPTED_BEFORE: Assessment = { ...PROPOSED, status: 'accepted', before_three_step_review: true,
    review: { ...REJECTED.review!, decision: 'accept', note: '' } }
  const MARK = 'before the three-step review'

  it('marks a control accepted before the three-step review, beside who accepted it and when', () => {
    review({ assessment: ACCEPTED_BEFORE })
    expect(within(bar()).getByText('Accepted')).toBeTruthy()
    expect(within(bar()).getByText(/^by Jana Weber on /)).toBeTruthy()
    expect(within(bar()).getByText(MARK).className).toBe('field-note')
  })

  it('marks it also when its review names no reviewer, with no "by" line', () => {
    review({ assessment: { ...ACCEPTED_BEFORE,
      review: { ...ACCEPTED_BEFORE.review!, reviewer_id: '', reviewer_name: '', reviewed_at: '' } } })
    expect(within(bar()).getByText(MARK)).toBeTruthy()
    expect(within(bar()).queryByText(/^by /)).toBeNull()
  })

  it.each([
    ['accepted in the three steps', { ...ACCEPTED_BEFORE, before_three_step_review: false }],
    ['rejected', { ...REJECTED, before_three_step_review: false }],
    ['rejected before the three-step review',{ ...REJECTED, before_three_step_review: true }],
  ])('shows no mark on a control %s', (_, assessment) => {
    review({ assessment })
    expect(within(bar()).queryByText(MARK)).toBeNull()
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

  it("marks a German profile's draft lang=\"de\", and an English profile's not (issue 244)", () => {
    const missing = 'Ein Nachweis der Zugriffsprüfungen.'
    review({ assessment: { ...ACCEPTED, answer_language: 'de', remediation: [{ ...DRAFT, missing }] } })
    expect(within(item()).getByText(missing).getAttribute('lang')).toBe('de')
    cleanup()
    review({ assessment: { ...ACCEPTED, answer_language: 'en' } })
    expect(within(item()).getByText('A record of access reviews.').getAttribute('lang')).not.toBe('de')
  })

  it('shows the draft on its own row under its requirement, spanning the table, not in the quotes cell, issue 464', () => {
    review({ assessment: ACCEPTED })
    const own = item().closest('tr')!
    expect(own).not.toBe(row('S1'))
    expect(row('S1').nextElementSibling).toBe(own)
    expect(own.querySelectorAll('td')).toHaveLength(1)
    expect(own.querySelector('td')!.colSpan).toBe(screen.getAllByRole('columnheader').length)
    expect(row('S1').querySelector('.quotes-cell .remediation')).toBeNull()
  })

  it("shows the draft: what's missing, the owner role and the due date", () => {
    review({ assessment: ACCEPTED })
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

  it('shows no owner for an item Vouch suggested none for, as its owner field starts empty', () => {
    review({ assessment: { ...ACCEPTED, remediation: [{ ...DRAFT, status: 'discarded', owner_role: NO_OWNER }] } })
    expect(within(item()).getByText('Due 2027-02-01')).toBeTruthy()
    expect(within(item()).queryByText(/no owner suggested/)).toBeNull()
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
    expect(within(item()).getByText('Approve or discard it once the findings are confirmed.')).toBeTruthy()
  })
})

describe("a German profile's verdicts (ticket 200)", () => {
  const GERMAN = { ...OVERVIEW, company: 'Alpenwerk Präzision GmbH', answer_language: 'de',
    controls: CONTROLS.map((c) => c.id === '4.1.2' ? { ...c, status: 'waiting-for-review' as const,
      verdict: 'not-evidenced' as const } : c) }
  const chip = (scope: HTMLElement) => within(scope).getByText(/./, { selector: '.chip' })
  const rowChips = () => ['M1', 'S1', 'H1', 'S3'].map((id) => chip(row(id)))
  const indexChip = (id: string) =>
    chip(within(screen.getByRole('navigation', { name: 'Controls' })).getByRole('link', { name: new RegExp(`^${id}`) }))
  function showWith(overview: typeof OVERVIEW | typeof GERMAN, answer_language: string) {
    const card = { ...CONTROLS.find((c) => c.id === '5.1.2')!, question: QUESTION }
    render(<WorkingPaper overview={overview} card={card} running={null}
      run={{ status: 'done', steps: [], assessment: { ...ASSESSED, answer_language } }} onAssess={() => {}}
      onOpen={() => {}} onOverview={() => {}} onReviewed={() => {}} onRefresh={() => Promise.resolve(true)}
      onItemDecided={() => {}} onQuestionsChanged={() => {}} />)
  }

  it('shows each requirement row\'s verdict in German, marked lang="de"', () => {
    showWith(GERMAN, 'de')
    expect(rowChips().map((c) => [c.textContent, c.lang])).toEqual([
      ['Nachgewiesen', 'de'], ['Teilweise nachgewiesen', 'de'], ['Nicht nachgewiesen', 'de'], ['Nicht anwendbar', 'de']])
  })

  it('shows the control index\'s verdicts in German; Not assessed and the column names stay English', () => {
    showWith(GERMAN, 'de')
    expect(['1.1.1', '4.1.2', '5.1.2'].map(indexChip).map((c) => [c.textContent, c.lang])).toEqual([
      ['Nachgewiesen', 'de'], ['Nicht nachgewiesen', 'de'], ['Teilweise nachgewiesen', 'de']])
    expect(indexChip('2.1.3').textContent).toBe('Not assessed')
    expect(indexChip('2.1.3').hasAttribute('lang')).toBe(false)
    expect(within(screen.getByRole('table', { name: 'Requirements' })).getByRole('columnheader', { name: 'Verdict' }))
      .toBeTruthy()
  })

  it('shows an English profile\'s verdicts in English, with no lang', () => {
    showWith(OVERVIEW, 'en')
    expect(rowChips().map((c) => [c.textContent, c.hasAttribute('lang')])).toEqual([
      ['Evidenced', false], ['Partly evidenced', false], ['Not evidenced', false], ['Not applicable', false]])
    expect(['1.1.1', '5.1.2'].map(indexChip).map((c) => [c.textContent, c.hasAttribute('lang')])).toEqual([
      ['Evidenced', false], ['Partly evidenced', false]])
    // The head's run chip too: an English assessment's verdict is in the interface's own words.
    expect(chip(headVerdict()).hasAttribute('lang')).toBe(false)
  })
})

describe('the improvement plan (issue 410)', () => {
  afterEach(() => vi.unstubAllGlobals())

  const APPROVED: RemediationItem = { requirement_id: 'S1', missing: 'A record of access reviews.',
    owner_role: 'CISO', due_date: '2027-02-01', status: 'approved', priority: 'high', done: null }
  const REVIEWED = { decision: 'accept' as const, note: '', overrides: [], reviewer_id: 'user_1',
    reviewer_name: 'Jana Weber', reviewed_at: '2026-11-04T08:30:00+00:00' }
  const ACCEPTED: Assessment = { ...PROPOSED, status: 'accepted', review_step: null, review: REVIEWED,
    remediation: [APPROVED] }
  const NOTE = 'Quarterly access reviews are now recorded in the ticket system.'
  const DONE: RemediationItem = { ...APPROVED, done: { at: '2026-11-09T10:00:00+00:00', note: NOTE } }
  const item = () => screen.getByRole('region', { name: 'Remediation item S1' })
  const improving = (assessment: Assessment, rest: Shown = {}) =>
    review({ assessment, goal: 'internal-improvement', ...rest })

  /** The plan's routes answering `status` with `body`; what they got. */
  function planRoute(status = 200, body: unknown = {}) {
    const sent: { url: string; method?: string; body: unknown }[] = []
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      sent.push({ url, method: init?.method, body: JSON.parse(init!.body as string) })
      return Promise.resolve(Response.json(body, { status }))
    }))
    return sent
  }

  it("shows each item's priority beside its status, a draft's default too", () => {
    improving({ ...ACCEPTED, remediation: [{ ...APPROVED, status: 'draft', priority: 'medium' }] })
    expect(within(item()).getByText('Priority: Medium')).toBeTruthy()
    cleanup()
    improving(ACCEPTED)
    expect(within(item()).getByText('Priority: High')).toBeTruthy()
  })

  it('shows no priority and no plan under Audit readiness', () => {
    review({ assessment: ACCEPTED })
    expect(within(item()).queryByText(/Priority/)).toBeNull()
    expect(within(item()).queryByRole('button', { name: /Mark done/ })).toBeNull()
  })

  it("sends a priority change at once and hands on the kept item", async () => {
    const kept = { ...APPROVED, priority: 'low' }
    const sent = planRoute(200, { item: kept })
    const onItemDecided = vi.fn()
    improving(ACCEPTED, { onItemDecided })
    await userEvent.selectOptions(within(item()).getByLabelText('Priority'), 'low')
    expect(sent).toEqual([{ url: '/api/assessments/t1/remediation/S1/priority', method: 'PUT',
      body: { priority: 'low' } }])
    expect(onItemDecided).toHaveBeenCalledWith('t1', kept)
  })

  it("shows a refused priority change with the server's sentence, the select back at the kept value", async () => {
    const detail = 'This remediation item is done already.'
    planRoute(409, { detail })
    improving(ACCEPTED)
    await userEvent.selectOptions(within(item()).getByLabelText('Priority'), 'low')
    expect(within(item()).getByRole('alert').textContent).toBe(detail)
    expect((within(item()).getByLabelText('Priority') as HTMLSelectElement).value).toBe('high')
  })

  it('says a failed priority change could not be made just now', async () => {
    planRoute(500)
    improving(ACCEPTED)
    await userEvent.selectOptions(within(item()).getByLabelText('Priority'), 'medium')
    expect(within(item()).getByRole('alert').textContent)
      .toBe('The priority could not be changed just now. Please try again.')
  })

  it('refuses a blank done note in the browser, sending nothing', async () => {
    const sent = planRoute()
    improving(ACCEPTED)
    await userEvent.click(within(item()).getByRole('button', { name: 'Mark S1 done' }))
    await userEvent.type(within(item()).getByLabelText('What was done'), '   ')
    await userEvent.click(within(item()).getByRole('button', { name: 'Mark done' }))
    expect(within(item()).getByRole('alert').textContent).toBe('Say what was done first.')
    expect(sent).toEqual([])
  })

  it('marks an item done with the note, and hands on the kept item', async () => {
    const sent = planRoute(200, { item: DONE })
    const onItemDecided = vi.fn()
    improving(ACCEPTED, { onItemDecided })
    await userEvent.click(within(item()).getByRole('button', { name: 'Mark S1 done' }))
    await userEvent.type(within(item()).getByLabelText('What was done'), NOTE)
    await userEvent.click(within(item()).getByRole('button', { name: 'Mark done' }))
    expect(sent).toEqual([{ url: '/api/assessments/t1/remediation/S1/done', method: 'POST', body: { note: NOTE } }])
    expect(onItemDecided).toHaveBeenCalledWith('t1', DONE)
  })

  it('Cancel closes the done form, sending nothing', async () => {
    const sent = planRoute()
    improving(ACCEPTED)
    await userEvent.click(within(item()).getByRole('button', { name: 'Mark S1 done' }))
    await userEvent.click(within(item()).getByRole('button', { name: 'Cancel' }))
    expect(within(item()).queryByLabelText('What was done')).toBeNull()
    expect(sent).toEqual([])
  })

  it("shows a refused done mark with the server's sentence", async () => {
    const detail = 'Accept the control before marking its remediation items done.'
    planRoute(409, { detail })
    improving(ACCEPTED)
    await userEvent.click(within(item()).getByRole('button', { name: 'Mark S1 done' }))
    await userEvent.type(within(item()).getByLabelText('What was done'), NOTE)
    await userEvent.click(within(item()).getByRole('button', { name: 'Mark done' }))
    expect(within(item()).getByRole('alert').textContent).toBe(detail)
  })

  it('offers no Mark done before the control is accepted', () => {
    improving({ ...PROPOSED, review_step: 'accept', confirmed: [], remediation: [APPROVED] })
    expect(within(item()).getByLabelText('Priority')).toBeTruthy()
    expect(within(item()).queryByRole('button', { name: 'Mark S1 done' })).toBeNull()
  })

  it('shows a done item: the Done tag, the date and note, its priority read-only', () => {
    improving({ ...ACCEPTED, remediation: [DONE] })
    expect(within(item()).getByText('Done', { selector: '.tag' })).toBeTruthy()
    expect(within(item()).queryByText('Approved')).toBeNull()
    expect(within(item()).getByText(`Done on 9 November 2026: ${NOTE}`)).toBeTruthy()
    expect(within(item()).getByText('Priority: High')).toBeTruthy()
    expect(within(item()).queryByLabelText('Priority')).toBeNull()
    expect(within(item()).queryByRole('button')).toBeNull()
  })

  it('suggests assessing again once every approved item is done', () => {
    const sentence = 'Every approved remediation item is done. Assess 5.1.2 again: the control is Closed once that '
      + 'assessment is accepted with no gaps.'
    improving({ ...ACCEPTED, remediation: [DONE] })
    expect(screen.getByText(sentence)).toBeTruthy()
    cleanup()
    improving(ACCEPTED)
    expect(screen.queryByText(sentence)).toBeNull()
  })

  it("reads Closed in the head for a Closed control", () => {
    improving({ ...ACCEPTED, remediation: [] }, { flags: { reassess_suggested: false, closed: true } })
    expect(within(headVerdict()).getByText('Closed')).toBeTruthy()
    expect(within(headVerdict()).queryByText('Accepted')).toBeNull()
  })

  it('reads Accepted under Audit readiness, whatever the card says', () => {
    review({ assessment: { ...ACCEPTED, remediation: [] }, flags: { reassess_suggested: false, closed: true } })
    expect(within(headVerdict()).getByText('Accepted')).toBeTruthy()
  })

  describe("the open questions' reasons (ticket 414)", () => {
    const ANSWERED = 'An open question was answered. Assess 5.1.2 again so its verdict takes the answer into account.'
    const OVERDUE = 'An open question is overdue. Assess 5.1.2 again with what is known now.'
    const assessNotes = () => [...document.querySelectorAll('.assess .field-note')].map((p) => p.textContent)

    it('says the answered-question sentence above Assess again under Audit readiness', () => {
      review({ assessment: { ...ACCEPTED, remediation: [] },
        flags: { reassess_suggested: true, reassess_reasons: ['question-answered'], closed: false } })
      expect(assessNotes()).toEqual([ANSWERED])
      const sentence = screen.getByText(ANSWERED)
      const assess = screen.getByRole('button', { name: 'Assess again 5.1.2' })
      expect(sentence.compareDocumentPosition(assess) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

    it("says each reason's sentence beside the done fixes' under Internal improvement", () => {
      improving({ ...ACCEPTED, remediation: [DONE] }, { flags: { reassess_suggested: true,
        reassess_reasons: ['fixes-done', 'question-answered', 'question-overdue'], closed: false } })
      expect(assessNotes()).toEqual([
        'Every approved remediation item is done. Assess 5.1.2 again: the control is Closed once that assessment is '
          + 'accepted with no gaps.',
        ANSWERED, OVERDUE])
    })

    it('says neither while the newest assessment waits for review', () => {
      review({ assessment: { ...PROPOSED, review_step: 'accept', confirmed: [] },
        flags: { reassess_suggested: true, reassess_reasons: ['question-overdue'], closed: false } })
      expect(screen.queryByText(OVERDUE)).toBeNull()
    })
  })

  describe('the new evidence reason (ticket 586)', () => {
    const NEW = 'A new version of Visitor badge policy was added. Assess 5.1.2 again so its verdict takes it into '
      + 'account.'
    const assessNotes = () => [...document.querySelectorAll('.assess .field-note')].map((p) => p.textContent)
    const flags = { reassess_suggested: true, reassess_reasons: ['new-evidence' as const], closed: false }

    it('says the new-version sentence above Assess again, naming the document', () => {
      review({ assessment: { ...ACCEPTED, remediation: [] }, flags,
        newEvidence: { '5.1.2': ['Visitor badge policy'] } })
      expect(assessNotes()).toEqual([NEW])
      const assess = screen.getByRole('button', { name: 'Assess again 5.1.2' })
      expect(screen.getByText(NEW).compareDocumentPosition(assess) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

    it('says it while the newest assessment waits for review too', () => {
      review({ assessment: PROPOSED, flags, newEvidence: { '5.1.2': ['Visitor badge policy'] } })
      expect(screen.getByText(NEW)).toBeTruthy()
    })

    it('names each document with a newer version', () => {
      review({ assessment: { ...ACCEPTED, remediation: [] }, flags,
        newEvidence: { '5.1.2': ['Visitor badge policy', 'Key register'] } })
      expect(assessNotes()).toEqual([
        'A new version of Visitor badge policy and Key register was added. Assess 5.1.2 again so its verdict takes '
          + 'it into account.'])
    })
  })
})

describe('Assess again on a review in progress (ticket 437)', () => {
  afterEach(() => vi.restoreAllMocks())

  const WARNING = 'Assessing 5.1.2 again sets its review aside: the findings you confirmed and the remediation items you '
    + "decided won't carry over to the new assessment. Nothing is deleted; they stay in the audit trail."
  const item = (status: RemediationItem['status']): RemediationItem =>
    ({ requirement_id: 'M1', missing: 'A record.', owner_role: 'CISO', due_date: '2027-02-01', status })
  const AT_PLAN_FIXES: Assessment = { ...PROPOSED, review_step: 'plan-fixes', confirmed: [], remediation: [item('draft')] }
  const AT_ACCEPT: Assessment = { ...PROPOSED, review_step: 'accept', confirmed: [], remediation: [item('approved')] }
  const ACCEPTED: Assessment = { ...PROPOSED, status: 'accepted', review_step: null, confirmed: [],
    review: { decision: 'accept', note: '', overrides: [], reviewer_id: 'user_1', reviewer_name: 'Jana Weber',
      reviewed_at: '2026-11-04T08:30:00+00:00' } }
  const assessAgain = () => screen.getByRole('button', { name: 'Assess again 5.1.2' })

  it.each([['Plan fixes', AT_PLAN_FIXES], ['Accept', AT_ACCEPT]])(
    'at %s, the first click warns in the app and starts nothing; Keep the review closes the warning',
    async (_, assessment) => {
      const onAssess = vi.fn()
      const confirm = vi.spyOn(window, 'confirm')
      review({ assessment, onAssess })
      await userEvent.click(assessAgain())
      expect(onAssess).not.toHaveBeenCalled()
      expect(confirm).not.toHaveBeenCalled() // in the app, not the browser's confirm
      const warning = screen.getByRole('alert')
      expect(warning.textContent).toBe(WARNING)
      expect(warning.className).toBe('field-note')
      expect(screen.queryByRole('button', { name: 'Assess again 5.1.2' })).toBeNull() // in its place
      expect(screen.getByRole('button', { name: 'Assess 5.1.2 again anyway' }).className).toBe('btn btn-primary')
      await userEvent.click(screen.getByRole('button', { name: 'Keep the review' }))
      expect(onAssess).not.toHaveBeenCalled()
      expect(screen.queryByRole('alert')).toBeNull()
      expect(assessAgain()).toBeTruthy()
    })

  it('Assess <id> again anyway starts the run', async () => {
    const onAssess = vi.fn()
    review({ assessment: AT_PLAN_FIXES, onAssess })
    await userEvent.click(assessAgain())
    expect(screen.getByRole('button', { name: 'Keep the review' }).className).toBe('btn btn-ghost')
    await userEvent.click(screen.getByRole('button', { name: 'Assess 5.1.2 again anyway' }))
    expect(onAssess).toHaveBeenCalledOnce()
  })

  it.each([['at Confirm findings', PROPOSED], ['accepted', ACCEPTED]])(
    'on a control %s, Assess again starts the run in one click', async (_, assessment) => {
      const onAssess = vi.fn()
      review({ assessment, onAssess })
      await userEvent.click(assessAgain())
      expect(onAssess).toHaveBeenCalledOnce()
      expect(screen.queryByRole('button', { name: 'Keep the review' })).toBeNull()
    })
})
